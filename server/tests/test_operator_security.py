"""Operator security: roles (backend-enforced), revocable sessions, TOTP two-factor authentication, audit log.

Each test uses its own operators (never the shared admin/password123 operator of the other tests)."""

from __future__ import annotations

import secrets

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import cli, mfa
from app.config import get_settings
from app.db.models import AdminUser, AuditLog
from app.db.repositories import AdminRepo
from app.db.session import session_scope
from app.main import app
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP
from tests.test_accounts import _customer, _give_watch

PASSWORD = "operator-pass-123"


@pytest.fixture(autouse=True)
def _no_rate_limits_between_tests():
    for limiter in (LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP):
        limiter._hits.clear()
    yield


def _client() -> TestClient:
    c = TestClient(app)
    c.__enter__()
    return c


def _new_operator(role: str = "admin") -> str:
    name = f"op-{role}-{secrets.token_hex(4)}"
    with session_scope() as db:
        cli.create_operator(db, name, PASSWORD, role)
    return name


def _login(name: str, code: str | None = None) -> tuple[TestClient, object]:
    c = _client()
    body = {"username": name, "password": PASSWORD}
    if code is not None:
        body["code"] = code
    return c, c.post("/api/auth/login", json=body)


def _signed_in(role: str = "admin") -> tuple[TestClient, str]:
    name = _new_operator(role)
    c, r = _login(name)
    assert r.status_code == 200, r.text
    return c, name


def _audit(action: str, **where) -> list[AuditLog]:
    with session_scope() as db:
        q = select(AuditLog).where(AuditLog.action == action)
        for k, v in where.items():
            q = q.where(getattr(AuditLog, k) == v)
        return list(db.exec(q).all())


def _enable_mfa(c: TestClient) -> tuple[str, list[str]]:
    setup = c.post("/api/auth/mfa/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and "issuer=ol" in setup["otpauth_uri"]
    r = c.post("/api/auth/mfa/enable", json={"code": pyotp.TOTP(setup["secret"]).now()})
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    return setup["secret"], codes


def test_existing_admin_operator_is_admin_with_full_access():
    c = _client()
    if c.get("/api/auth/status").json()["needs_setup"]:
        c.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    else:
        c.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    me = c.get("/api/auth/me").json()
    assert me["role"] == "admin" and me["mfa_enabled"] is False and me["mfa_required"] is False
    assert c.get("/api/audit").status_code == 200
    assert c.get("/api/billing/settings").status_code == 200


def test_support_cannot_use_admin_only_routes():
    c, _ = _signed_in("support")
    assert c.put("/api/system/keys", json={"openai_api_key": "sk-nope"}).status_code == 403
    assert c.post("/api/system/test/llm").status_code == 403
    fw = c.post("/api/firmware", files={"file": ("fw.bin", b"\xe9" + b"\x00" * 64)}, data={"version": "9.9.9"})
    assert fw.status_code == 403
    assert c.post("/api/devices/any/ota", json={"release_id": 1}).status_code == 403
    assert c.put("/api/billing/settings", json={}).status_code == 403
    assert c.put("/api/pricing/currency", json={"currency": "GBP", "usd_rate": 0.8}).status_code == 403
    assert c.post("/api/accounts/1/complimentary", json={"days": 30}).status_code == 403
    assert c.delete("/api/incidents").status_code == 403
    assert c.delete("/api/issues").status_code == 403
    assert c.get("/api/audit").status_code == 403
    assert c.get("/api/auth/me").json()["role"] == "support"


def test_support_can_view_accounts_and_history():
    _, me = _customer()
    dev = _give_watch(me["id"])
    c, _ = _signed_in("support")
    listed = c.get("/api/accounts", params={"q": me["email"]})
    assert listed.status_code == 200 and listed.json()["accounts"][0]["id"] == me["id"]
    assert c.get(f"/api/accounts/{me['id']}").status_code == 200
    hist = c.get(f"/api/devices/{dev}/conversations")
    assert hist.status_code == 200, hist.text
    assert c.get("/api/finance").status_code == 200
    assert c.get("/api/orders").status_code == 200


def test_mfa_login_flow_and_recovery_code_used_once():
    c, name = _signed_in()
    secret, codes = _enable_mfa(c)
    assert c.get("/api/auth/me").json()["mfa_enabled"] is True  # this session survives the enrolment
    assert c.get("/api/accounts").status_code == 200
    with session_scope() as db:
        stored = AdminRepo(db).get(name)
        assert secret not in (stored.totp_secret_enc or "")  # encrypted at rest
        assert mfa.decrypt_secret(stored.totp_secret_enc) == secret
        assert all(code not in (stored.recovery_codes or "") for code in codes)

    _, r = _login(name)
    assert r.status_code == 401 and r.json()["detail"]["mfa_required"] is True
    _, r = _login(name, "000000" if pyotp.TOTP(secret).now() != "000000" else "111111")
    assert r.status_code == 401 and r.json()["detail"]["mfa_required"] is True
    c2, r = _login(name, pyotp.TOTP(secret).now())
    assert r.status_code == 200 and c2.get("/api/accounts").status_code == 200

    _, r = _login(name, codes[0])
    assert r.status_code == 200
    LOGIN_PER_ACCOUNT._hits.clear()
    _, r = _login(name, codes[0])
    assert r.status_code == 401  # used once
    _, r = _login(name, codes[1].lower().replace("-", ""))
    assert r.status_code == 200  # case and dash do not matter
    assert c2.get("/api/auth/me").json()["recovery_codes_left"] == 8


def test_wrong_codes_count_toward_the_rate_limit():
    c, name = _signed_in()
    _enable_mfa(c)
    statuses = [_login(name, "abcdef")[1].status_code for _ in range(7)]
    assert statuses[:5] == [401] * 5 and 429 in statuses[5:]


def test_disable_mfa_needs_password_and_code():
    c, name = _signed_in()
    secret, codes = _enable_mfa(c)
    assert c.post("/api/auth/mfa/disable", json={"password": "wrong-password", "code": pyotp.TOTP(secret).now()}).status_code == 403
    assert c.post("/api/auth/mfa/disable", json={"password": PASSWORD, "code": "123456" if pyotp.TOTP(secret).now() != "123456" else "654321"}).status_code == 403
    r = c.post("/api/auth/mfa/disable", json={"password": PASSWORD, "code": codes[0]})
    assert r.status_code == 200 and r.json()["me"]["mfa_enabled"] is False
    assert c.get("/api/accounts").status_code == 200
    _, r = _login(name)
    assert r.status_code == 200  # no code needed any more
    assert _audit("operator.mfa_enabled", actor=name) and _audit("operator.mfa_disabled", actor=name)


def test_mfa_required_blocks_everything_but_enrolment(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_mfa_required", True)
    c, _ = _signed_in()
    me = c.get("/api/auth/me").json()
    assert me["mfa_required"] is True and me["mfa_setup_needed"] is True
    r = c.get("/api/accounts")
    assert r.status_code == 403 and r.json()["detail"] == "Set up two-factor authentication first."
    assert c.get("/api/audit").status_code == 403
    _enable_mfa(c)
    assert c.get("/api/accounts").status_code == 200
    assert c.get("/api/auth/me").json()["mfa_setup_needed"] is False
    assert c.post("/api/auth/logout").status_code == 200


def test_logout_all_invalidates_other_sessions():
    a, name = _signed_in()
    b, r = _login(name)
    assert r.status_code == 200
    old_cookie = a.cookies.get("buddyai_session")
    assert b.post("/api/auth/logout-all").status_code == 200
    assert a.get("/api/accounts").status_code == 401
    assert b.get("/api/auth/me").status_code == 401
    replay = _client()
    replay.cookies.set("buddyai_session", old_cookie)
    assert replay.get("/api/accounts").status_code == 401  # an old cookie stays invalid
    assert _audit("operator.logout_all", actor=name)


def test_role_change_and_disable_via_cli_invalidate_sessions():
    c, name = _signed_in("support")
    with session_scope() as db:
        cli.set_role(db, name, "admin")
    assert c.get("/api/accounts").status_code == 401
    c, r = _login(name)
    assert c.get("/api/audit").status_code == 200
    assert _audit("operator.role_change")[-1].detail.endswith("support -> admin")
    with session_scope() as db:
        cli.set_disabled(db, name, True)
    assert c.get("/api/accounts").status_code == 401
    assert _login(name)[1].status_code == 401
    with session_scope() as db:
        cli.set_disabled(db, name, False)
        cli.reset_mfa(db, name)
    assert _login(name)[1].status_code == 200


def test_live_websocket_needs_a_valid_session():
    c, _ = _signed_in()
    with c.websocket_connect("/api/live"):
        pass  # accepted
    c.post("/api/auth/logout-all")
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/live") as ws:
            ws.receive_json()


def test_audit_rows_for_login_failures_and_key_changes():
    c, name = _signed_in()
    assert _audit("operator.login", actor=name)
    bad = _client().post("/api/auth/login", json={"username": name, "password": "not-the-password"})
    assert bad.status_code == 401
    failed = [r for r in _audit("operator.login_failed") if name in r.detail]
    assert failed and "not-the-password" not in failed[-1].detail

    keys_file = get_settings().data_dir / "provider_keys.json"
    before = keys_file.read_text(encoding="utf-8") if keys_file.exists() else None
    try:
        value = "azure-secret-value-" + secrets.token_hex(6)
        assert c.put("/api/system/keys", json={"azure_speech_key": value}).status_code == 200
    finally:
        if before is None:
            keys_file.unlink(missing_ok=True)
        else:
            keys_file.write_text(before, encoding="utf-8")
    rows = _audit("system.keys_change", actor=name)
    assert rows and rows[-1].detail == "azure_speech_key" and value not in rows[-1].detail

    page = c.get("/api/audit", params={"action": "operator.", "actor": name, "limit": 2}).json()
    assert page["limit"] == 2 and page["total"] >= 1
    assert all(e["action"].startswith("operator.") and e["actor"] == name for e in page["events"])
    assert set(page["events"][0]) == {"id", "created_at", "actor", "action", "account_id", "device_id", "detail"}
    assert c.get("/api/audit", params={"limit": 501}).status_code == 422


def test_incident_clear_is_audited():
    c, name = _signed_in()
    assert c.delete("/api/incidents", params={"device_id": "no-such-device"}).status_code == 200
    assert c.delete("/api/issues", params={"device_id": "no-such-device"}).status_code == 200
    assert _audit("incidents.clear", actor=name) and _audit("issues.clear", actor=name)


def test_cli_rejects_unknown_role():
    with session_scope() as db:
        with pytest.raises(cli.OperatorError):
            cli.create_operator(db, f"op-{secrets.token_hex(3)}", PASSWORD, "superuser")
        assert db.exec(select(AdminUser).where(AdminUser.role == "superuser")).first() is None
