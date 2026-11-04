"""Automatic firmware updates: who gets what and when, what counts as a failure, promotion, pause, signatures."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import struct
import time
import zlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from sqlmodel import select

from app import firmware_signature, ota_rollout
from app.config import get_settings
from app.db.models import AuditLog, Device, DeviceOta, FirmwareRelease, utcnow
from app.db.session import session_scope
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP
from tests.test_operator_security import _signed_in

MODEL = "ota-test-model"


@pytest.fixture(autouse=True)
def _auto_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "ota_auto_enabled", True)
    for limiter in (LOGIN_PER_ACCOUNT, LOGIN_PER_IP):
        limiter._hits.clear()
    yield


def _model() -> str:
    return f"{MODEL}-{secrets.token_hex(4)}"


def _release(model: str, version: str = "1.0.0", stage: str = "pilot", channel: str = "dev", **kw) -> int:
    with session_scope() as db:
        r = FirmwareRelease(version=version, filename="f.bin", sha256="ab" * 32, size=10, hw_model=model, channel=channel, stage=stage, **kw)
        db.add(r)
        db.commit()
        return r.id


def _device(model: str, fw: str = "0.9.0", channel: str = "dev", pilot: bool = True, battery: int | None = 80, charging: bool = False) -> str:
    device_id = f"ota-{secrets.token_hex(4)}"
    with session_scope() as db:
        db.add(Device(id=device_id, hw_model=model, fw_version=fw, fw_channel=channel, ota_pilot=pilot, battery_pct=battery, charging=charging))
        db.commit()
    return device_id


def _choose(device_id: str, busy: bool = False, idle_s: float = 1000.0):
    with session_scope() as db:
        rel = ota_rollout.choose_release(db, db.get(Device, device_id), busy, idle_s)
        return rel.id if rel else None


def _attempt(device_id: str, release_id: int) -> str:
    with session_scope() as db:
        return ota_rollout.new_attempt(db, device_id, db.get(FirmwareRelease, release_id), "0.9.0").attempt_id


def _state(attempt_id: str) -> str:
    with session_scope() as db:
        return db.get(DeviceOta, attempt_id).state


def _status(device_id: str, attempt_id: str, state: str, reason: str = "") -> None:
    with session_scope() as db:
        ota_rollout.on_status(db, device_id, {"attempt_id": attempt_id, "state": state, "reason": reason})


def _hello(device_id: str, fw: str, **fields) -> None:
    with session_scope() as db:
        ota_rollout.on_hello(db, device_id, fw, fields)


def _paused(release_id: int) -> bool:
    with session_scope() as db:
        return db.get(FirmwareRelease, release_id).paused


# --- who gets it, and when ----------------------------------------------------------------------------------------


def test_off_by_default_offers_nothing(monkeypatch):
    assert type(get_settings()).model_fields["ota_auto_enabled"].default is False
    monkeypatch.setattr(get_settings(), "ota_auto_enabled", False)
    model = _model()
    _release(model)
    assert _choose(_device(model)) is None


def test_eligible_watch_gets_the_newest_promoted_release():
    model = _model()
    _release(model, "1.0.0")
    newest = _release(model, "1.2.0")
    _release(model, "2.0.0", stage="development")  # not promoted
    assert _choose(_device(model)) == newest


@pytest.mark.parametrize("change", [
    {"channel": "release"}, {"channel": ""}, {"fw": "1.0.0"}, {"fw": "2.0.0"}, {"pilot": False},
    {"battery": 39}, {"battery": None},
])
def test_not_offered(change):
    model = _model()
    _release(model)
    assert _choose(_device(model, **change)) is None


def test_other_model_and_unparsable_version_are_not_offered():
    model = _model()
    _release(model, "1.0.0-beta")
    assert _choose(_device(model)) is None
    _release(_model())
    assert _choose(_device(_model())) is None


def test_low_battery_is_fine_while_charging():
    model = _model()
    rel = _release(model)
    assert _choose(_device(model, battery=10, charging=True)) == rel


def test_waits_for_a_conversation_and_two_quiet_minutes():
    model = _model()
    rel = _release(model)
    dev = _device(model)
    assert _choose(dev, busy=True) is None
    assert _choose(dev, idle_s=60) is None
    assert _choose(dev) == rel


def test_paused_newest_release_offers_nothing_not_even_an_older_one():
    model = _model()
    _release(model, "1.0.0")
    _release(model, "1.1.0", paused=True)
    assert _choose(_device(model)) is None


def test_gradual_stages_are_stable_and_roughly_the_right_size():
    rel = SimpleNamespace(id=7, stage="gradual_10")
    devices = [SimpleNamespace(id=f"w{i}", ota_pilot=False) for i in range(2000)]
    ten = {d.id for d in devices if ota_rollout.in_stage(d, rel)}
    assert 120 < len(ten) < 280
    rel.stage = "gradual_50"
    fifty = {d.id for d in devices if ota_rollout.in_stage(d, rel)}
    assert ten <= fifty and 850 < len(fifty) < 1150  # a watch never drops out when the stage grows
    rel.stage = "general"
    assert all(ota_rollout.in_stage(d, rel) for d in devices)
    rel.stage = "pilot"
    assert not any(ota_rollout.in_stage(d, rel) for d in devices)


# --- attempts: what the states mean -------------------------------------------------------------------------------


def _full_success(device_id: str, attempt_id: str, version: str = "1.0.0") -> None:
    for state in ("accepted", "downloading", "installing", "rebooting"):
        _status(device_id, attempt_id, state)
        assert _state(attempt_id) == state
    _hello(device_id, version)
    assert _state(attempt_id) == "verifying"  # the new version alone proves nothing yet
    _status(device_id, attempt_id, "installed")
    assert _state(attempt_id) == "installed"


def test_success_path_needs_the_watchs_confirmation():
    model = _model()
    rel, dev = _release(model), _device(model)
    _full_success(dev, _attempt(dev, rel))
    assert _choose(dev) is None


def test_lost_confirmation_is_recovered_at_the_next_hello():
    model = _model()
    rel, dev = _release(model), _device(model)
    a = _attempt(dev, rel)
    _status(dev, a, "rebooting")
    _hello(dev, "1.0.0")
    assert _state(a) == "verifying"
    _hello(dev, "1.0.0", ota_confirmed={"attempt_id": a, "version": "1.0.0"})
    assert _state(a) == "installed"


def test_disconnect_before_install_is_unknown_not_failed():
    model = _model()
    rel, dev = _release(model), _device(model)
    a = _attempt(dev, rel)
    _status(dev, a, "downloading")
    _hello(dev, "0.9.0")  # came back on the old version: it may simply not have finished
    assert _state(a) == "downloading"
    with session_scope() as db:
        row = db.get(DeviceOta, a)
        row.updated_at = utcnow() - ota_rollout.TIMEOUT - timedelta(minutes=1)
        db.add(row)
        db.commit()
        ota_rollout.expire_silent(db)
    assert _state(a) == "unknown"
    with session_scope() as db:
        assert ota_rollout.tally(db, rel)["unreviewed_failures"] == 0
    assert not _paused(rel)


def test_network_errors_and_refusals_are_not_failures():
    model = _model()
    rel, dev = _release(model), _device(model)
    for state, reason in (("failed", "network"), ("failed", "no_memory"), ("refused", "battery"), ("refused", "in_conversation")):
        a = _attempt(dev, rel)
        _status(dev, a, state, reason)
        assert _state(a) == "unknown"
    with session_scope() as db:
        assert ota_rollout.tally(db, rel)["unreviewed_failures"] == 0
        assert db.exec(select(DeviceOta).where(DeviceOta.device_id == dev)).first().reason == "network"
    assert not _paused(rel)


def test_an_older_attempts_late_message_is_ignored():
    model = _model()
    rel, dev = _release(model), _device(model)
    old = _attempt(dev, rel)
    time.sleep(0.01)
    new = _attempt(dev, rel)
    _status(dev, old, "failed", "integrity")
    assert _state(old) == "offered" and not _paused(rel)
    _status(dev, new, "failed", "integrity")
    assert _state(new) == "failed"


def test_two_attempts_with_the_same_timestamp_are_both_heard():
    model = _model()
    rel, dev = _release(model), _device(model)
    first, second = _attempt(dev, rel), _attempt(dev, rel)
    with session_scope() as db:
        at = utcnow()
        for attempt in (first, second):
            row = db.get(DeviceOta, attempt)
            row.created_at = at
            db.add(row)
        db.commit()
    _status(dev, first, "failed", "network")
    _status(dev, second, "failed", "network")
    assert _state(first) == "unknown" and _state(second) == "unknown"


def test_another_watch_cannot_report_this_attempt():
    model = _model()
    rel, dev, other = _release(model), _device(model), _device(model)
    a = _attempt(dev, rel)
    _status(other, a, "failed", "integrity")
    _hello(other, "0.9.0", ota_confirmed={"attempt_id": a, "version": "1.0.0"})
    assert _state(a) == "offered"


def test_a_final_state_stays_final_and_nothing_moves_backwards():
    model = _model()
    rel, dev = _release(model), _device(model)
    a = _attempt(dev, rel)
    _status(dev, a, "installing")
    _status(dev, a, "downloading")
    assert _state(a) == "installing"
    _status(dev, a, "installed")
    _status(dev, a, "failed", "integrity")
    assert _state(a) == "installed"


def test_retry_waits_longer_each_time_and_stops_at_the_limit():
    model = _model()
    rel, dev = _release(model), _device(model)
    assert _choose(dev) == rel
    a = _attempt(dev, rel)
    assert _choose(dev) is None  # one at a time
    _status(dev, a, "failed", "network")
    assert _choose(dev) is None  # too soon
    now = utcnow()
    with session_scope() as db:
        device = db.get(Device, dev)
        assert ota_rollout.choose_release(db, device, False, 1000, now + timedelta(minutes=29)) is None
        assert ota_rollout.choose_release(db, device, False, 1000, now + timedelta(minutes=31)).id == rel
    rows = [SimpleNamespace(state="failed", innocent=False, updated_at=now) for _ in range(ota_rollout.MAX_CONFIRMED_FAILURES)]
    assert not ota_rollout.retry_allowed(rows, now + timedelta(days=30))
    rows = [SimpleNamespace(state="unknown", innocent=False, updated_at=now) for _ in range(ota_rollout.MAX_UNKNOWN)]
    assert not ota_rollout.retry_allowed(rows, now + timedelta(days=30))


# --- rollback ----------------------------------------------------------------------------------------------------


def test_rollback_with_a_failed_check_counts_and_is_named():
    model = _model()
    rel, dev = _release(model), _device(model)
    a = _attempt(dev, rel)
    _hello(dev, "0.9.0", ota_rollback={"attempt_id": a, "version": "1.0.0", "check": "audio", "reset_reason": "software"})
    with session_scope() as db:
        row = db.get(DeviceOta, a)
        assert row.state == "rolled_back" and "audio" in row.reason and ota_rollout.counts_as_failure(row)


def test_rollback_after_a_crash_counts():
    model = _model()
    rel, dev = _release(model, stage="gradual_10"), _device(model)
    a = _attempt(dev, rel)
    _hello(dev, "0.9.0", ota_rollback={"attempt_id": a, "version": "1.0.0", "check": "", "reset_reason": "panic"})
    with session_scope() as db:
        assert ota_rollout.counts_as_failure(db.get(DeviceOta, a))


@pytest.mark.parametrize("reset", ["power_on", "usb"])
def test_switching_off_during_verification_is_not_held_against_the_release(reset):
    model = _model()
    rel, dev = _release(model), _device(model)
    a = _attempt(dev, rel)
    _hello(dev, "0.9.0", ota_rollback={"attempt_id": a, "version": "1.0.0", "check": "", "reset_reason": reset})
    assert _state(a) == "rolled_back" and not _paused(rel)
    with session_scope() as db:
        assert ota_rollout.tally(db, rel)["unreviewed_failures"] == 0


def test_repeated_rollback_report_changes_nothing():
    model = _model()
    rel, dev = _release(model, stage="gradual_10"), _device(model)
    a = _attempt(dev, rel)
    report = {"attempt_id": a, "version": "1.0.0", "check": "memory", "reset_reason": "software"}
    _hello(dev, "0.9.0", ota_rollback=report)
    _hello(dev, "0.9.0", ota_rollback=report)
    with session_scope() as db:
        assert ota_rollout.tally(db, rel)["unreviewed_rollbacks"] == 1


# --- automatic pause ------------------------------------------------------------------------------------------------


def test_pilot_pauses_at_the_first_confirmed_failure():
    model = _model()
    rel, dev = _release(model), _device(model)
    _status(dev, _attempt(dev, rel), "failed", "integrity")
    assert _paused(rel)
    with session_scope() as db:
        assert db.exec(select(AuditLog).where(AuditLog.action == "firmware.rollout_paused", AuditLog.detail.contains(f"release {rel} "))).first()


def test_two_rollbacks_pause_at_any_stage():
    assert ota_rollout.pause_reason("general", {"unreviewed_failures": 2, "unreviewed_rollbacks": 2, "installed": 1000, "rolled_back": 2})
    assert ota_rollout.pause_reason("general", {"unreviewed_failures": 1, "unreviewed_rollbacks": 1, "installed": 1000, "rolled_back": 1}) is None


def test_the_fifty_watches_example():
    t = {"installed": 47, "failed": 3, "unreviewed_failures": 3}  # 3 of 50 finished = 6 %
    assert "3 confirmed failures of 50" in ota_rollout.pause_reason("gradual_25", t)
    assert ota_rollout.pause_reason("gradual_25", {"installed": 48, "failed": 2, "unreviewed_failures": 2}) is None
    # at least 3 AND at least 5 %
    assert ota_rollout.pause_reason("general", {"installed": 997, "failed": 3, "unreviewed_failures": 3}) is None


def test_disconnects_never_pause():
    t = {"unknown": 500, "installed": 2, "unreviewed_failures": 0}
    for stage in ota_rollout.STAGES:
        assert ota_rollout.pause_reason(stage, t) is None


def test_development_stage_never_pauses():
    assert ota_rollout.pause_reason("development", {"failed": 9, "unreviewed_failures": 9}) is None


def test_thresholds_are_configurable(monkeypatch):
    monkeypatch.setattr(get_settings(), "ota_pause_min_failures", 1)
    monkeypatch.setattr(get_settings(), "ota_pause_failure_rate", 0.5)
    assert ota_rollout.pause_reason("general", {"failed": 1, "installed": 1, "unreviewed_failures": 1})
    assert ota_rollout.pause_reason("general", {"failed": 1, "installed": 2, "unreviewed_failures": 1}) is None


# --- promotion, through the API --------------------------------------------------------------------------------


def _installed_one(model: str, rel: int) -> None:
    dev = _device(model)
    a = _attempt(dev, rel)
    _status(dev, a, "installed")
    assert _state(a) == "installed"


def test_promotion_goes_one_stage_at_a_time_and_needs_a_confirmed_install():
    c, name = _signed_in("admin")
    model = _model()
    rel = _release(model, stage="development")
    assert "No watch" in c.post(f"/api/firmware/{rel}/promote", json={"to": "pilot"}).json()["detail"]
    _installed_one(model, rel)
    assert "cannot be skipped" in c.post(f"/api/firmware/{rel}/promote", json={"to": "general"}).json()["detail"]
    for stage in ota_rollout.STAGES[1:]:
        r = c.post(f"/api/firmware/{rel}/promote", json={"to": stage})
        assert r.status_code == 200 and r.json()["stage"] == stage, r.text
    assert c.post(f"/api/firmware/{rel}/promote", json={"to": "general"}).status_code == 409  # already at the end
    with session_scope() as db:
        assert len(db.exec(select(AuditLog).where(AuditLog.actor == name, AuditLog.action == "firmware.promote")).all()) == 5


def test_support_cannot_promote_pause_or_review():
    c, _ = _signed_in("support")
    assert c.post("/api/firmware/1/promote", json={"to": "pilot"}).status_code == 403
    assert c.post("/api/firmware/1/pause", json={}).status_code == 403
    assert c.post("/api/firmware/1/resume").status_code == 403
    assert c.post("/api/firmware/attempts/x/review").status_code == 403
    assert c.put("/api/devices/x/ota-pilot", json={"pilot": True}).status_code == 403


def test_failures_block_promotion_and_resume_until_reviewed():
    c, _ = _signed_in("admin")
    model = _model()
    rel = _release(model)
    _installed_one(model, rel)
    dev = _device(model)
    a = _attempt(dev, rel)
    _status(dev, a, "failed", "image_invalid")
    assert _paused(rel)
    assert "paused" in c.post(f"/api/firmware/{rel}/promote", json={"to": "gradual_10"}).json()["detail"]
    assert c.post(f"/api/firmware/{rel}/resume").status_code == 409
    assert c.post(f"/api/firmware/attempts/{a}/review").status_code == 200
    assert c.post(f"/api/firmware/{rel}/resume").status_code == 200
    assert c.post(f"/api/firmware/{rel}/promote", json={"to": "gradual_10"}).status_code == 200


def test_rollout_view_shows_how_many_watches_a_stage_really_reaches():
    c, _ = _signed_in("support")
    model = _model()
    rel = _release(model, stage="pilot")
    _device(model, pilot=True)
    _device(model, pilot=False)
    body = c.get(f"/api/firmware/{rel}/rollout").json()
    assert body["fleet"] == 2 and body["in_stage"] == 1 and body["next_stage"] == "gradual_10" and body["auto_enabled"] is True


def test_manual_push_of_a_release_for_another_model_is_refused():
    c, _ = _signed_in("admin")
    rel = _release(_model())
    assert "for " in c.post(f"/api/devices/{_device(_model())}/ota", json={"release_id": rel}).json()["detail"]


# --- signatures ---------------------------------------------------------------------------------------------------


KEY = rsa.generate_private_key(public_exponent=65537, key_size=3072)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=3072)


def _pem(key) -> str:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _sign(body: bytes, key=KEY) -> bytes:
    """The esp-idf signature block (espsecure sign_data --version 2)."""
    digest = hashlib.sha256(body).digest()
    sig = key.sign(digest, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), utils.Prehashed(hashes.SHA256()))
    n = key.public_key().public_numbers()
    block = bytes((0xE7, 2, 0, 0)) + digest + n.n.to_bytes(384, "little") + struct.pack("<I", n.e) + bytes(384) + bytes(4) + sig[::-1]
    block += struct.pack("<I", zlib.crc32(block) & 0xFFFFFFFF) + bytes(16)
    assert len(block) == 1216
    return body + block + b"\xff" * (4096 - 1216)


BODY = b"\xe9" + bytes(65535)


def test_signature_valid_unsigned_foreign_and_modified():
    public = firmware_signature.load_public_key(_pem(KEY))
    assert firmware_signature.verify(_sign(BODY), public) == (True, "")
    assert firmware_signature.verify(BODY, public)[1] == "the image is not signed"
    assert firmware_signature.verify(_sign(BODY, OTHER), public)[1] == "signed with a different key"
    tampered = bytearray(_sign(BODY))
    tampered[100] ^= 1
    assert firmware_signature.verify(bytes(tampered), public)[1] == "the image was changed after signing"
    forged = BODY + _flip_signature(_sign(BODY)[len(BODY):])  # inside the signature itself, CRC recomputed
    assert firmware_signature.verify(forged, public)[1] == "the signature is not valid"


def _flip_signature(sector: bytes) -> bytes:
    block = bytearray(sector[:1216])
    block[900] ^= 1
    block[1196:1200] = struct.pack("<I", zlib.crc32(bytes(block[:1196])) & 0xFFFFFFFF)
    return bytes(block) + sector[1216:]


def test_upload_to_the_release_channel_needs_a_good_signature(monkeypatch):
    c, _ = _signed_in("admin")
    def post(data: bytes):
        return c.post("/api/firmware", files={"file": ("fw.bin", data)}, data={"version": "3.0.0", "hw_model": _model(), "channel": "release"})

    assert "SIGNING_PUBKEY" in post(_sign(BODY)).json()["detail"]
    monkeypatch.setattr(get_settings(), "firmware_signing_pubkey", _pem(KEY))
    assert "not signed" in post(BODY + b"\xff" * 4096).json()["detail"]
    assert "different key" in post(_sign(BODY, OTHER)).json()["detail"]
    good = post(_sign(BODY))
    assert good.status_code == 200 and good.json()["signature_verified"] is True and good.json()["channel"] == "release"


def test_dev_channel_upload_needs_no_signature():
    c, _ = _signed_in("admin")
    r = c.post("/api/firmware", files={"file": ("fw.bin", BODY)}, data={"version": "3.0.1"})
    assert r.status_code == 200 and r.json()["channel"] == "dev" and r.json()["signature_verified"] is False


def test_release_channel_promotion_needs_https_signature_and_test_evidence(monkeypatch):
    import app.ota_rollout as module

    c, _ = _signed_in("admin")
    model = _model()
    rel = _release(model, stage="development", channel="release")
    _installed_one(model, rel)
    monkeypatch.setattr(module, "base_url", lambda: "http://lan")
    assert "https" in c.post(f"/api/firmware/{rel}/promote", json={"to": "pilot"}).json()["detail"]
    monkeypatch.setattr(module, "base_url", lambda: "https://api.example.com")
    assert "signature" in c.post(f"/api/firmware/{rel}/promote", json={"to": "pilot"}).json()["detail"]
    with session_scope() as db:
        row = db.get(FirmwareRelease, rel)
        row.signature_verified = True
        db.add(row)
        db.commit()
    assert "tests on a real watch" in c.post(f"/api/firmware/{rel}/promote", json={"to": "pilot"}).json()["detail"]
    results = {"unit_mac": "AA:BB:CC:00:11:22", "valid_image_accepted": True, "unsigned_image_refused": True,
               "foreign_key_refused": True, "modified_image_refused": False}
    assert c.post(f"/api/firmware/{rel}/signed-test", json=results).status_code == 409
    assert c.post(f"/api/firmware/{rel}/signed-test", json={**results, "modified_image_refused": True}).status_code == 200
    assert c.post(f"/api/firmware/{rel}/promote", json={"to": "pilot"}).status_code == 200


# --- offering, end to end at the connection ------------------------------------------------------------------------


class _Hub:
    def __init__(self, online: bool = True) -> None:
        self.online, self.offers = online, []

    async def offer_ota(self, device_id: str, offer: dict) -> bool:
        self.offers.append((device_id, offer))
        return self.online


def _conn(device_id: str, active=None, last_activity_s: float = 0.0):
    return SimpleNamespace(device_id=device_id, authenticated=True, active=active, last_activity_s=last_activity_s)


def test_a_watch_that_was_offline_gets_the_offer_when_it_reports_in():
    model = _model()
    rel, dev = _release(model), _device(model)
    hub = _Hub()
    assert asyncio.run(ota_rollout.consider(hub, _conn(dev))) is True
    device_id, offer = hub.offers[0]
    assert device_id == dev and offer["version"] == "1.0.0" and offer["hw_model"] == model and offer["url"].endswith(f"/fw/{rel}.bin")
    assert _state(offer["attempt_id"]) == "offered"
    assert asyncio.run(ota_rollout.consider(hub, _conn(dev))) is False  # one at a time
    assert len(hub.offers) == 1


def test_no_offer_during_a_conversation_or_right_after_one():
    model = _model()
    _release(model)
    dev = _device(model)
    hub = _Hub()
    assert asyncio.run(ota_rollout.consider(hub, _conn(dev, active=object()))) is False
    assert asyncio.run(ota_rollout.consider(hub, _conn(dev, last_activity_s=time.monotonic()))) is False
    assert hub.offers == []


def test_a_watch_gone_at_the_moment_of_the_offer_is_unknown():
    model = _model()
    rel, dev = _release(model), _device(model)
    assert asyncio.run(ota_rollout.consider(_Hub(online=False), _conn(dev))) is False
    with session_scope() as db:
        assert db.exec(select(DeviceOta).where(DeviceOta.device_id == dev)).one().state == "unknown"
    assert not _paused(rel)
