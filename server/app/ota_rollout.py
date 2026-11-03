"""Automatic firmware updates: which watch gets which release, and when.

A release moves through stages, each by an operator's explicit approval: development (manual push to a test
watch), pilot (chosen watches), gradual 10 / 25 / 50 %, general 100 %. The server offers it when a watch of that
model and channel connects or reports in and is in a safe state; a watch offline meanwhile gets it when it
returns. Off by default (BUDDYAI_OTA_AUTO_ENABLED): nothing is offered until it is on and a release is promoted.

Outcome of one try (DeviceOta): only what the watch itself reports counts as a failure. A watch that disconnects
or stays silent becomes "unknown" and is tried again: it may simply not have got that far. "installed" needs the
watch's explicit confirmation that the new image passed its checks (firmware/components/ota); the new version
in a hello only means "verifying".

Confirmed failures pause a release automatically (evaluate_pause); it resumes only by an operator, after review.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import timedelta, timezone
from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from app import accounts
from app.api.system import base_url
from app.config import get_settings
from app.db.models import Device, DeviceOta, FirmwareRelease, utcnow
from app.db.session import session_scope

log = logging.getLogger(__name__)

DEVELOPMENT, PILOT, GENERAL = "development", "pilot", "general"
# Order of promotion; no stage can be skipped.
STAGES = (DEVELOPMENT, PILOT, "gradual_10", "gradual_25", "gradual_50", GENERAL)
PERCENT = {"gradual_10": 10, "gradual_25": 25, "gradual_50": 50, GENERAL: 100}

ACTIVE = ("offered", "accepted", "downloading", "installing", "rebooting")
TERMINAL = ("installed", "failed", "rolled_back")
# How far each state is along the way: a late message never moves an attempt backwards.
ORDER = {s: i for i, s in enumerate((*ACTIVE, "verifying", "unknown", *TERMINAL))}

TIMEOUT = timedelta(minutes=30)
RETRY_AFTER = (timedelta(minutes=30), timedelta(hours=2), timedelta(hours=12))
MAX_CONFIRMED_FAILURES = 5
MAX_UNKNOWN = 10
SAFE_BATTERY_PCT = 40
IDLE_S = 120.0
SWEEP_S = 300
# A rollback with no failed check, after one of these resets, means the watch was switched off: not the image's fault.
INNOCENT_RESETS = ("power_on", "usb")


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def version_key(version: str) -> tuple[int, ...] | None:
    """Numeric a.b.c, or None: a version we cannot compare is never offered automatically."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(candidate: str, running: str) -> bool:
    a, b = version_key(candidate), version_key(running)
    return a is not None and b is not None and a > b


def counts_as_failure(row: DeviceOta) -> bool:
    return row.state == "failed" or (row.state == "rolled_back" and not row.innocent)


def target_release(db: Session, hw_model: str, channel: str) -> FirmwareRelease | None:
    """The newest promoted release for this model and channel. If it is paused nothing is offered: an older
    one is not a safe substitute."""
    rows = db.exec(select(FirmwareRelease).where(
        FirmwareRelease.hw_model == hw_model, FirmwareRelease.channel == channel, FirmwareRelease.stage != DEVELOPMENT,
    )).all()
    rows = [r for r in rows if version_key(r.version) is not None]
    return max(rows, key=lambda r: version_key(r.version), default=None)


def in_stage(dev: Device, release: FirmwareRelease) -> bool:
    if dev.ota_pilot:
        return True
    pct = PERCENT.get(release.stage)
    if pct is None:
        return False
    digest = hashlib.sha256(f"{dev.id}|{release.id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < pct


def _attempts(db: Session, device_id: str, release_id: int) -> list[DeviceOta]:
    return list(db.exec(select(DeviceOta).where(DeviceOta.device_id == device_id, DeviceOta.release_id == release_id)
                        .order_by(col(DeviceOta.created_at))).all())


def expire_silent(db: Session, device_id: str | None = None) -> None:
    """Offered or running, with no word for too long: unknown, not failed."""
    limit = utcnow() - TIMEOUT
    q = select(DeviceOta).where(col(DeviceOta.state).in_((*ACTIVE, "verifying")))
    if device_id:
        q = q.where(DeviceOta.device_id == device_id)
    for row in db.exec(q).all():
        if _utc(row.updated_at) < limit:
            row.state, row.reason, row.updated_at = "unknown", "no_report", utcnow()
            db.add(row)
    db.commit()


def retry_allowed(rows: list[DeviceOta], now: datetime) -> bool:
    if not rows:
        return True
    if any(r.state in (*ACTIVE, "verifying", "installed") for r in rows):
        return False
    failures = sum(1 for r in rows if counts_as_failure(r))
    unknown = sum(1 for r in rows if r.state == "unknown")
    if failures >= MAX_CONFIRMED_FAILURES or unknown >= MAX_UNKNOWN:
        return False
    wait = RETRY_AFTER[min(len(rows) - 1, len(RETRY_AFTER) - 1)]
    return now - max(_utc(r.updated_at) for r in rows) >= wait


def choose_release(db: Session, dev: Device, busy: bool, idle_s: float, now: datetime | None = None) -> FirmwareRelease | None:
    """The release to offer this watch now, or None."""
    if not get_settings().ota_auto_enabled or not dev.fw_channel or not dev.hw_model:
        return None  # firmware that reports no channel cannot report its outcome either
    release = target_release(db, dev.hw_model, dev.fw_channel)
    if release is None or release.paused or not is_newer(release.version, dev.fw_version) or not in_stage(dev, release):
        return None
    if busy or idle_s < IDLE_S:
        return None
    if dev.battery_pct is None or (dev.battery_pct < SAFE_BATTERY_PCT and not dev.charging):
        return None
    return release if retry_allowed(_attempts(db, dev.id, release.id), now or utcnow()) else None


def new_attempt(db: Session, device_id: str, release: FirmwareRelease, previous_version: str) -> DeviceOta:
    row = DeviceOta(attempt_id=uuid.uuid4().hex, device_id=device_id, release_id=release.id,
                    previous_version=previous_version, state="offered")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def offer_of(release: FirmwareRelease, attempt_id: str) -> dict[str, Any]:
    return {"version": release.version, "url": f"{base_url()}/fw/{release.id}.bin", "sha256": release.sha256,
            "size": release.size, "attempt_id": attempt_id, "hw_model": release.hw_model}


async def consider(hub: Any, conn: Any) -> bool:
    """Offer an update to this connected watch if it should get one now."""
    if not get_settings().ota_auto_enabled or not conn.authenticated:
        return False
    with session_scope() as db:
        expire_silent(db, conn.device_id)
        dev = db.get(Device, conn.device_id)
        if dev is None:
            return False
        release = choose_release(db, dev, conn.active is not None, time.monotonic() - conn.last_activity_s)
        if release is None:
            return False
        attempt = new_attempt(db, dev.id, release, dev.fw_version)
        offer = offer_of(release, attempt.attempt_id)
    if not await hub.offer_ota(conn.device_id, offer):
        with session_scope() as db:
            _set(db, offer["attempt_id"], "unknown", "offline_at_offer")
        return False
    log.info("ota: offered %s to %s", offer["version"], conn.device_id)
    return True


def _set(db: Session, attempt_id: str, state: str, reason: str = "", innocent: bool = False) -> DeviceOta | None:
    row = db.get(DeviceOta, attempt_id)
    if row is None:
        return None
    # A late or repeated message cannot undo a final outcome, nor move an attempt backwards.
    if row.state in TERMINAL or ORDER[state] < ORDER[row.state]:
        return None
    row.state, row.reason, row.innocent, row.updated_at = state, reason[:64], innocent, utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def on_status(db: Session, device_id: str, msg: dict[str, Any], hub: Any = None) -> None:
    """ota_status from the watch. Only its newest attempt counts: an older one's late message is ignored."""
    attempt_id, state, reason = str(msg.get("attempt_id") or ""), str(msg.get("state") or ""), str(msg.get("reason") or "")
    row = db.get(DeviceOta, attempt_id)
    if row is None or row.device_id != device_id:
        return
    newer = db.exec(select(DeviceOta.attempt_id).where(DeviceOta.device_id == device_id,
                                                       col(DeviceOta.created_at) > row.created_at)).first()
    if newer is not None:
        return
    if state == "refused" or (state == "failed" and reason in ("network", "busy", "bad_offer")):
        # Not the image's fault: "not done yet", tried again later. The reason stays visible.
        _set(db, attempt_id, "unknown", reason or state)
    elif state == "failed":
        _finish(db, attempt_id, "failed", reason, hub)
    elif state == "installed":
        _finish(db, attempt_id, "installed", "", hub)
    elif state in ACTIVE:
        _set(db, attempt_id, state)


def on_hello(db: Session, device_id: str, fw: str, msg: dict[str, Any], hub: Any = None) -> None:
    """What a hello says about the last update. Repeated in every hello: applying it twice changes nothing."""
    rollback, confirmed = msg.get("ota_rollback"), msg.get("ota_confirmed")
    if isinstance(rollback, dict):
        row = db.get(DeviceOta, str(rollback.get("attempt_id") or ""))
        if row is not None and row.device_id == device_id:
            check, reset = str(rollback.get("check") or ""), str(rollback.get("reset_reason") or "")
            _finish(db, row.attempt_id, "rolled_back", f"{check or 'reset'} ({reset})" if reset else check, hub,
                    innocent=not check and reset in INNOCENT_RESETS)
    if isinstance(confirmed, dict):
        row = db.get(DeviceOta, str(confirmed.get("attempt_id") or ""))
        if row is not None and row.device_id == device_id:
            _finish(db, row.attempt_id, "installed", "", hub)
    for row in db.exec(select(DeviceOta).where(DeviceOta.device_id == device_id, col(DeviceOta.state).in_(ACTIVE))).all():
        release = db.get(FirmwareRelease, row.release_id)
        if release is not None and release.version == fw:
            _set(db, row.attempt_id, "verifying")


def _finish(db: Session, attempt_id: str, state: str, reason: str, hub: Any, innocent: bool = False) -> None:
    row = _set(db, attempt_id, state, reason, innocent)
    if row is not None and state != "installed":
        release = db.get(FirmwareRelease, row.release_id)
        if release is not None:
            evaluate_pause(db, release, hub)


def tally(db: Session, release_id: int) -> dict[str, int]:
    """Attempts per state, and the unreviewed confirmed failures."""
    out: dict[str, int] = {}
    failures = rollbacks = 0
    for row in db.exec(select(DeviceOta).where(DeviceOta.release_id == release_id)).all():
        out[row.state] = out.get(row.state, 0) + 1
        if counts_as_failure(row) and row.reviewed_at is None:
            failures += 1
            rollbacks += row.state == "rolled_back"
    out["unreviewed_failures"], out["unreviewed_rollbacks"] = failures, rollbacks
    return out


def pause_reason(stage: str, t: dict[str, int]) -> str | None:
    """Why this tally must stop the rollout, or None. Ordinary disconnects ("unknown") never count."""
    s = get_settings()
    failures, rollbacks = t.get("unreviewed_failures", 0), t.get("unreviewed_rollbacks", 0)
    if stage == DEVELOPMENT or failures == 0:
        return None
    if stage == PILOT:
        return f"pilot: {failures} confirmed failure(s)"
    if rollbacks >= s.ota_pause_rollbacks:
        return f"{rollbacks} rollbacks"
    finished = t.get("installed", 0) + t.get("failed", 0) + t.get("rolled_back", 0)
    if failures >= s.ota_pause_min_failures and failures / max(finished, 1) >= s.ota_pause_failure_rate:
        return f"{failures} confirmed failures of {finished} finished"
    return None


def evaluate_pause(db: Session, release: FirmwareRelease, hub: Any = None) -> bool:
    if release.paused:
        return False
    why = pause_reason(release.stage, tally(db, release.id))
    if why is None:
        return False
    release.paused, release.paused_reason = True, why
    db.add(release)
    db.commit()
    accounts.audit(db, "system", "firmware.rollout_paused", detail=f"release {release.id} {release.version}: {why}")
    log.error("ota: release %s paused: %s", release.version, why)
    if hub is not None:
        hub.publish({"type": "ota_paused", "release_id": release.id, "version": release.version, "reason": why}, operator_only=True)
    return True


class PromotionRefused(Exception):
    pass


def next_stage(stage: str) -> str | None:
    i = STAGES.index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def promote(db: Session, release: FirmwareRelease, to: str) -> None:
    """One stage forward, on an operator's approval. Raises PromotionRefused with the reason."""
    if to != next_stage(release.stage):
        raise PromotionRefused(f"The next stage after {release.stage} is {next_stage(release.stage)}; stages cannot be skipped.")
    if release.paused:
        raise PromotionRefused("This release is paused. Review the failures and resume it first.")
    if not release.hw_model or version_key(release.version) is None:
        raise PromotionRefused("A release needs a watch model and an a.b.c version before it can be rolled out.")
    t = tally(db, release.id)
    if t.get("installed", 0) < 1:
        raise PromotionRefused("No watch has confirmed this release in the current stage yet.")
    if t["unreviewed_failures"]:
        raise PromotionRefused(f"{t['unreviewed_failures']} failed attempt(s) still need review.")
    if release.channel == "release":
        if not base_url().startswith("https://"):
            raise PromotionRefused("The release channel needs an https public URL.")
        if not release.signature_verified:
            raise PromotionRefused("The server has not verified this image's signature.")
        if not release.signing_evidence:
            raise PromotionRefused("Record the signed-update tests on a real watch first.")
    release.stage = to
    db.add(release)
    db.commit()


async def sweep_loop(hub: Any) -> None:
    """Watches that were busy or low on battery at their last report, and attempts gone silent."""
    while True:
        await asyncio.sleep(SWEEP_S)
        if not get_settings().ota_auto_enabled:
            continue
        for conn in list(hub.connections.values()):
            try:
                await consider(hub, conn)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one watch must not stop the others
                log.warning("ota: sweep failed for a watch (%s)", type(exc).__name__)
        try:
            with session_scope() as db:
                expire_silent(db)
        except Exception as exc:  # noqa: BLE001
            log.warning("ota: expiry failed (%s)", type(exc).__name__)
