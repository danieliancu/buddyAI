"""Customer content and operators: metadata by default on every path; one conversation at a time, for the
principal role only, with a stated reason, password and two-factor code. Every attempt is audited."""

from __future__ import annotations

import asyncio
import re
import secrets
from datetime import timedelta

import pyotp
import pytest
from sqlmodel import select

from app import cli
from app.db.models import AuditLog, Conversation, Item, Memory, SearchCache, Turn, VoiceOperation, utcnow
from app.db.session import session_scope
from app.gateway.hub import DeviceHub
from app.main import app
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP
from tests.test_accounts import _customer, _give_watch
from tests.test_operator_security import PASSWORD, _enable_mfa, _signed_in

MARKER = "zebra-marker-7741"


@pytest.fixture(autouse=True)
def _no_limits():
    for limiter in (LOGIN_PER_ACCOUNT, LOGIN_PER_IP, SIGNUP_PER_IP):
        limiter._hits.clear()
    yield


def _seed() -> tuple[int, str, int]:
    """A customer with the marker in everything that holds their words."""
    _, me = _customer()
    dev = _give_watch(me["id"], with_history=False)
    with session_scope() as db:
        conv = Conversation(device_id=dev)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(Turn(device_id=dev, account_id=me["id"], conversation_id=conv.id, session_id="s", turn_no=1, language="en",
                    status="completed", user_text=f"asked {MARKER}", assistant_text=f"answered {MARKER}",
                    search_note=f"{MARKER} -> found"))
        db.add(Turn(device_id=dev, account_id=me["id"], session_id="s", turn_no=2, language="en", mode="note",
                    status="error", user_text=f"note {MARKER}", assistant_text="", error=f"provider said {MARKER}"))
        db.add(Item(account_id=me["id"], kind="note", number=1, text=f"note {MARKER}"))
        db.add(Memory(account_id=me["id"], kind="other", content=f"fact {MARKER}", content_hash=secrets.token_hex(16),
                      origin="explicit"))
        db.add(SearchCache(key=secrets.token_hex(16), scope=f"account:{me['id']}", category="general", query=MARKER,
                           answer=MARKER, expires_at=utcnow() + timedelta(days=1)))
        db.add(VoiceOperation(op_key=secrets.token_hex(16), account_id=me["id"], device_id=dev, session_id="s", turn_id=1,
                              tool="note_create", summary=f"note_create: {MARKER}"))
        db.commit()
        return me["id"], dev, conv.id


def _principal():
    c, name = _signed_in("principal")
    secret, _ = _enable_mfa(c)
    return c, name, pyotp.TOTP(secret)


def _body(totp, reason: str = "Customer asked us to check this conversation, ticket 1234.") -> dict:
    return {"reason": reason, "password": PASSWORD, "code": totp.now()}


def _audit(actor: str, action: str) -> list[AuditLog]:
    with session_scope() as db:
        return list(db.exec(select(AuditLog).where(AuditLog.actor == actor, AuditLog.action == action)).all())


@pytest.mark.parametrize("role", ["support", "admin", "principal"])
def test_no_operator_read_route_returns_customer_content(role):
    account_id, dev, conv = _seed()
    c, _ = _signed_in(role)
    if role == "principal":
        _enable_mfa(c)
    params = {"device_id": dev, "account_id": str(account_id), "id": dev, "conversation_id": str(conv)}
    checked, skipped = [], []
    for path, methods in app.openapi()["paths"].items():
        if "get" not in methods or not path.startswith("/api"):
            continue
        if path.startswith(("/api/me", "/api/auth/mfa")):
            continue  # customer routes are the owner's own; they refuse an operator session
        names = re.findall(r"{(\w+)}", path)
        if any(n not in params for n in names):
            skipped.append(path)
            continue
        url = path
        for n in names:
            url = url.replace("{%s}" % n, params[n])
        r = c.get(url, params={"device_id": dev, "account_id": account_id})
        checked.append(url)
        assert MARKER not in r.text, f"{url} ({role}) exposes customer content"
    assert len(checked) >= 25  # the sweep really ran
    assert f"/api/devices/{dev}/conversations" in checked and "/api/diagnostics" in checked
    assert f"/api/accounts/{account_id}" in checked and f"/api/accounts/{account_id}/audit" in checked
    # Routes that take another id must not return conversations: they are about incidents, orders or settings rows.
    assert all(re.search(r"incident|persona|pricing|order|firmware", p) for p in skipped), skipped


def test_operator_list_keeps_metadata_only():
    _, dev, _ = _seed()
    c, _ = _signed_in("admin")
    conversations = c.get(f"/api/devices/{dev}/conversations").json()
    turns = [t for g in conversations for t in g["turns"]]
    assert len(turns) == 2 and all(t["content_hidden"] and not t["user_text"] and not t["assistant_text"] for t in turns)
    assert all("cost_gbp" in t and "mode" in t for t in turns)
    failed = next(t for t in turns if t["status"] == "error")
    assert failed["error"] == "Failed (see Diagnostics)"


def test_live_feed_hides_the_words_from_operators_only():
    async def go():
        hub = DeviceHub()
        hub.owners["w"] = 7
        operator, owner, other = hub.subscribe(), hub.subscribe(7), hub.subscribe(8)
        hub.publish({"type": "turn_end", "device_id": "w", "status": "completed", "user_text": MARKER, "assistant_text": MARKER})
        seen = operator.get_nowait()
        assert seen["status"] == "completed" and "user_text" not in seen and "assistant_text" not in seen
        assert owner.get_nowait()["user_text"] == MARKER
        assert other.empty()

    asyncio.run(go())


def test_principal_opens_one_conversation_only_and_it_is_audited():
    _, dev, conv = _seed()
    c, name, totp = _principal()
    r = c.post(f"/api/devices/{dev}/conversations/{conv}/reveal", json=_body(totp))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == conv and MARKER in r.json()["turns"][0]["user_text"]
    assert MARKER not in c.get(f"/api/devices/{dev}/conversations").text  # nothing stays unlocked
    rows = _audit(name, "content.reveal")
    assert len(rows) == 1 and f"conversation {conv}" in rows[0].detail and "ticket 1234" in rows[0].detail


@pytest.mark.parametrize("role", ["support", "admin"])
def test_other_roles_are_refused_and_audited(role):
    _, dev, conv = _seed()
    c, name = _signed_in(role)
    secret, _ = _enable_mfa(c)
    r = c.post(f"/api/devices/{dev}/conversations/{conv}/reveal", json=_body(pyotp.TOTP(secret)))
    assert r.status_code == 403 and MARKER not in r.text
    assert len(_audit(name, "content.access_refused")) == 1
    assert c.post(f"/api/devices/{dev}/conversations/delete", json=_body(pyotp.TOTP(secret))).status_code == 403


def test_principal_without_mfa_reason_password_or_code_is_refused():
    _, dev, conv = _seed()
    url = f"/api/devices/{dev}/conversations/{conv}/reveal"
    no_mfa, name = _signed_in("principal")
    assert no_mfa.post(url, json={"reason": "x" * 30, "password": PASSWORD, "code": "000000"}).status_code == 403
    assert "two-factor" in _audit(name, "content.access_refused")[0].detail

    c, name, totp = _principal()
    assert c.post(url, json=_body(totp, reason="too short")).status_code == 403
    assert c.post(url, json={**_body(totp), "password": "wrong-password"}).status_code == 403
    assert c.post(url, json={**_body(totp), "code": "000000"}).status_code == 403
    assert len(_audit(name, "content.access_refused")) == 3
    assert _audit(name, "content.reveal") == []


def test_another_watchs_conversation_is_not_found():
    _, dev, _ = _seed()
    _, _, other_conv = _seed()
    c, name, totp = _principal()
    r = c.post(f"/api/devices/{dev}/conversations/{other_conv}/reveal", json=_body(totp))
    assert r.status_code == 404 and MARKER not in r.text
    assert _audit(name, "content.reveal") == []


def test_daily_limit(monkeypatch):
    from app.config import get_settings

    _, dev, conv = _seed()
    c, name, totp = _principal()
    monkeypatch.setattr(get_settings(), "content_reveal_daily_limit", 1)
    url = f"/api/devices/{dev}/conversations/{conv}/reveal"
    assert c.post(url, json=_body(totp)).status_code == 200
    assert c.post(url, json=_body(totp)).status_code == 429
    assert "daily limit" in _audit(name, "content.access_refused")[0].detail


def test_operator_history_delete_needs_the_same_checks():
    _, dev, _ = _seed()
    c, name, totp = _principal()
    assert c.delete(f"/api/devices/{dev}/conversations").status_code == 405
    assert c.post(f"/api/devices/{dev}/conversations/delete", json=_body(totp, reason="short")).status_code == 403
    r = c.post(f"/api/devices/{dev}/conversations/delete", json=_body(totp))
    assert r.status_code == 200 and r.json()["deleted_turns"] == 2
    assert "ticket 1234" in _audit(name, "history.delete")[0].detail


def test_customer_history_delete_is_audited():
    from tests.test_accounts import _customer as customer

    c, me = customer()
    dev = _give_watch(me["id"])
    assert c.delete(f"/api/me/devices/{dev}/conversations").status_code == 200
    with session_scope() as db:
        rows = db.exec(select(AuditLog).where(AuditLog.device_id == dev, AuditLog.action == "history.delete")).all()
    assert len(rows) == 1 and rows[0].actor == f"account:{me['id']}"


def test_principal_role_can_be_set_from_the_cli_only():
    c, name = _signed_in("admin")
    with session_scope() as db:
        cli.set_role(db, name, "principal")
    assert c.get("/api/auth/me").status_code == 401  # a role change signs the operator out everywhere
