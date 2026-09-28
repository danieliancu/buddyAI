"""Customer accounts and tenant isolation (plan M7).

Each customer must never read or change another customer's watches, settings, history,
personas, usage or live events — whatever route they try.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.db.models import Conversation, Turn
from app.db.repositories import DeviceRepo, SettingsRepo
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.main import app
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP
from app.security import hash_device_token


@pytest.fixture(autouse=True)
def _no_rate_limits_between_tests():
    for limiter in (LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP):
        limiter._hits.clear()
    yield


def _client() -> TestClient:
    c = TestClient(app)
    c.__enter__()
    return c


def _last_token(to: str) -> str:
    mail = [m for m in ConsoleEmailSender.sent if m.to == to][-1]
    return mail.text.split("token=")[1].split()[0]


def _customer(verified: bool = True) -> tuple[TestClient, dict]:
    c = _client()
    addr = f"user-{secrets.token_hex(4)}@example.com"
    r = c.post("/api/me/signup", json={"email": addr, "password": "correct-horse-1", "name": "Test"})
    assert r.status_code == 200, r.text
    if verified:
        assert c.post("/api/me/verify-email", json={"token": _last_token(addr)}).status_code == 200
    return c, c.get("/api/me").json()


def _give_watch(account_id: int, with_history: bool = True) -> str:
    device_id = f"dev-{secrets.token_hex(4)}"
    with session_scope() as db:
        DeviceRepo(db).pair(device_id, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", account_id)
        SettingsRepo(db).ensure(device_id)
        if with_history:
            conv = Conversation(device_id=device_id)
            db.add(conv)
            db.commit()
            db.refresh(conv)
            db.add(Turn(device_id=device_id, account_id=account_id, conversation_id=conv.id, session_id="s",
                        turn_no=1, language="en", status="completed", user_text=f"secret question {device_id}",
                        assistant_text="secret answer"))
            db.commit()
    return device_id


def test_signup_verify_login_logout():
    c, me = _customer(verified=False)
    assert me["email_verified"] is False
    assert c.post("/api/me/devices/pair", json={"code": "123456"}).status_code == 403  # must verify first
    assert c.post("/api/me/verify-email", json={"token": _last_token(me["email"])}).status_code == 200
    assert c.get("/api/me").json()["email_verified"] is True
    assert c.post("/api/me/logout").status_code == 200
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/me/login", json={"email": me["email"].upper(), "password": "correct-horse-1"}).status_code == 200
    assert c.post("/api/me/login", json={"email": me["email"], "password": "wrong-password"}).status_code == 401


def test_duplicate_email_and_weak_password():
    c, me = _customer()
    other = _client()
    assert other.post("/api/me/signup", json={"email": me["email"], "password": "another-pass-1"}).status_code == 409
    assert other.post("/api/me/signup", json={"email": "x@example.com", "password": "short"}).status_code == 422
    assert other.post("/api/me/signup", json={"email": "not-an-email", "password": "long-enough-1"}).status_code == 422


def test_customers_cannot_touch_each_other():
    a, acc_a = _customer()
    b, acc_b = _customer()
    dev_a = _give_watch(acc_a["id"])
    dev_b = _give_watch(acc_b["id"])

    assert [d["id"] for d in a.get("/api/me/devices").json()] == [dev_a]
    assert [d["id"] for d in b.get("/api/me/devices").json()] == [dev_b]

    # Every device route answers 404 for someone else's watch.
    assert b.get(f"/api/me/devices/{dev_a}/settings").status_code == 404
    assert b.patch(f"/api/me/devices/{dev_a}/settings", json={"volume": 1}).status_code == 404
    assert b.get(f"/api/me/devices/{dev_a}/conversations").status_code == 404
    assert b.delete(f"/api/me/devices/{dev_a}/conversations").status_code == 404
    assert b.patch(f"/api/me/devices/{dev_a}", json={"name": "mine now"}).status_code == 404
    assert b.delete(f"/api/me/devices/{dev_a}").status_code == 404

    # A's data is untouched and visible to A.
    hist = a.get(f"/api/me/devices/{dev_a}/conversations").json()
    assert hist and hist[0]["turns"][0]["user_text"] == f"secret question {dev_a}"
    assert a.get(f"/api/me/devices/{dev_a}/settings").json()["settings"]["volume"] == 70

    # Personas: B can't see, edit, delete or use A's persona.
    pa = a.post("/api/me/personas", json={"name": "A's", "system_prompt": "A private prompt"}).json()
    assert pa["own"] is True
    assert pa["id"] not in [p["id"] for p in b.get("/api/me/personas").json()]
    assert b.put(f"/api/me/personas/{pa['id']}", json={"name": "x", "system_prompt": "y"}).status_code == 404
    assert b.delete(f"/api/me/personas/{pa['id']}").status_code == 404
    assert b.patch(f"/api/me/devices/{dev_b}/settings", json={"persona_id": pa["id"]}).status_code == 422
    assert a.patch(f"/api/me/devices/{dev_a}/settings", json={"persona_id": pa["id"]}).status_code == 200

    # Customers can't use operator routes.
    for path in ("/api/devices", "/api/accounts", "/api/usage", "/api/system/info"):
        assert b.get(path).status_code == 401, path

    # Export contains only the caller's data.
    export = b.get("/api/me/export").text
    assert f"secret question {dev_a}" not in export and dev_a not in export
    assert f"secret question {dev_b}" in export


def test_live_events_are_filtered_per_account():
    _, acc_a = _customer()
    _, acc_b = _customer()
    dev_a = _give_watch(acc_a["id"], with_history=False)
    hub = app.state.hub
    q_a, q_b, q_op = hub.subscribe(acc_a["id"]), hub.subscribe(acc_b["id"]), hub.subscribe(None)
    try:
        hub.publish({"type": "device_state", "device_id": dev_a, "state": "listening"})
        assert q_a.qsize() == 1 and q_op.qsize() == 1
        assert q_b.qsize() == 0
    finally:
        for q in (q_a, q_b, q_op):
            hub.unsubscribe(q)


def test_transferred_watch_does_not_show_old_history():
    _, acc_a = _customer()
    b, acc_b = _customer()
    dev = _give_watch(acc_a["id"])
    with session_scope() as db:
        DeviceRepo(db).assign(dev, acc_b["id"])
    assert b.get(f"/api/me/devices/{dev}/conversations").json() == []


def test_password_reset_logs_out_other_sessions():
    c, me = _customer()
    other = _client()
    assert other.post("/api/me/login", json={"email": me["email"], "password": "correct-horse-1"}).status_code == 200
    anon = _client()
    assert anon.post("/api/me/password/forgot", json={"email": me["email"]}).status_code == 200
    assert anon.post("/api/me/password/forgot", json={"email": "nobody@example.com"}).status_code == 200  # no leak
    token = _last_token(me["email"])
    assert anon.post("/api/me/password/reset", json={"token": token, "password": "brand-new-pass"}).status_code == 200
    assert anon.post("/api/me/password/reset", json={"token": token, "password": "again-new-pass"}).status_code == 400
    assert other.get("/api/me").status_code == 401  # old session invalidated
    assert c.get("/api/me").status_code == 401


def test_login_rate_limit():
    _, me = _customer()
    c = _client()
    codes = [c.post("/api/me/login", json={"email": me["email"], "password": "nope-nope"}).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]


def test_suspend_and_delete_account():
    c, me = _customer()
    dev = _give_watch(me["id"])
    op = _client()
    status = op.get("/api/auth/status").json()
    if status["needs_setup"]:
        op.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    else:
        op.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    listed = op.get("/api/accounts", params={"q": me["email"]}).json()["accounts"]
    assert listed[0]["devices"] == 1
    assert op.patch(f"/api/accounts/{me['id']}", json={"status": "suspended"}).status_code == 200
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/me/login", json={"email": me["email"], "password": "correct-horse-1"}).status_code == 403
    assert op.patch(f"/api/accounts/{me['id']}", json={"status": "active"}).status_code == 200

    assert c.post("/api/me/login", json={"email": me["email"], "password": "correct-horse-1"}).status_code == 200
    assert c.request("DELETE", "/api/me", json={"password": "wrong"}).status_code == 403
    assert c.request("DELETE", "/api/me", json={"password": "correct-horse-1"}).status_code == 200
    with session_scope() as db:
        d = DeviceRepo(db).get(dev)
        assert d.account_id is None and d.token_hash is None
        from sqlmodel import select

        assert db.exec(select(Turn).where(Turn.device_id == dev)).all() == []  # history erased
    assert c.post("/api/me/login", json={"email": me["email"], "password": "correct-horse-1"}).status_code == 401
