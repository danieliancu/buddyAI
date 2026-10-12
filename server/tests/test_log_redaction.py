"""Sensitive data stays out of the logs: note / reminder edits, tool calls and web searches log only tool
names, argument keys and lengths; the console email backend withholds bodies (one-time links) on a public
server; the log filter redacts tokens in query strings and bearer tokens."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.items import AssistantTools
from app.logging_redact import RedactingFilter, redact
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.device_settings import DeviceSettings
from app.providers.llm.base import ToolCall
from app.search import WebSearch
from tests.test_accounts import _client, _customer, _last_token, _no_rate_limits_between_tests  # noqa: F401
from tests.test_edit_confirm import _note, _say, _text
from tests.test_items import FakeRouter, _account

SECRET = "zanzibar-pickles"


def _logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_note_edit_does_not_log_the_text(caplog) -> None:
    caplog.set_level(logging.INFO)
    acc = _account()
    n, uid = _note(acc, "Shopping\nmilk")
    _say(acc, "note", n, f"change it to {SECRET}", ("note_edit", {"ops": [{"op": "replace", "line": 1, "text": SECRET}]}))
    assert SECRET in _text(acc, uid)  # applied
    logged = _logged(caplog)
    assert f"note #{n} edit keys=[ops]" in logged
    assert SECRET not in logged


def test_refused_note_edit_logs_only_the_exception_type(caplog) -> None:
    caplog.set_level(logging.INFO)
    acc = _account()
    n, _uid = _note(acc, "Shopping\nmilk")
    _say(acc, "note", n, "change line nine", ("note_edit", {"ops": [{"op": "replace", "line": 9, "text": SECRET}]}))
    assert SECRET not in _logged(caplog)


def test_reminder_edit_does_not_log_the_text(caplog) -> None:
    caplog.set_level(logging.INFO)
    acc = _account()
    with session_scope() as db:
        it = ItemRepo(db).create(acc, "reminder", "Meeting", datetime(2030, 5, 1, 7, 0, tzinfo=timezone.utc))
        n = it.number
    _say(acc, "reminder", n, f"call it {SECRET}", ("reminder_edit", {"action": "change", "text": SECRET}))
    logged = _logged(caplog)
    assert f"reminder #{n} edit keys=" in logged
    assert SECRET not in logged


def _turn(acc: int) -> TurnContext:
    return TurnContext(1, "s-log", "dev-log", "en", DeviceSettings(timezone="Europe/London"), 16000, account_id=acc)


def test_web_search_and_tools_do_not_log_arguments_or_results(caplog) -> None:
    caplog.set_level(logging.INFO)
    acc = _account()

    async def search_fn(query, location, language):
        return f"Answer about {SECRET}.", 10, 5, 1

    pipeline = ConversationPipeline(FakeRouter(None), ChunkerConfig(), tools=AssistantTools())
    turn = _turn(acc)
    turn.searcher = WebSearch(search_fn, provider="p", model="m")
    result = asyncio.run(pipeline._run_tool(turn, ToolCall("c1", "web_search", json.dumps(
        {"query": f"opening hours of {SECRET}", "category": "other"}))))
    assert SECRET in result
    asyncio.run(pipeline._run_tool(turn, ToolCall("c2", "item_create", json.dumps(
        {"kind": "note", "text": f"Ideas\n{SECRET}"}))))
    logged = _logged(caplog)
    assert "web_search(keys=[category,query]" in logged and "tool item_create(keys=[kind,text]" in logged
    assert SECRET not in logged


def test_search_not_found_logs_only_the_query_length(caplog) -> None:
    caplog.set_level(logging.INFO)

    async def nothing(query, location, language):
        return "", 1, 1, 1

    s = WebSearch(nothing)
    asyncio.run(s.run(json.dumps({"query": SECRET}), account_id=None, device_id="d", tz="Europe/London",
                      language="en", default_location=""))
    logged = _logged(caplog)
    assert f"query {len(SECRET)} chars" in logged and SECRET not in logged


def test_password_reset_email_body_is_not_logged_on_a_public_server(caplog, monkeypatch) -> None:
    s = get_settings()
    _c, me = _customer()
    monkeypatch.setattr(s, "public_url", "https://app.example.com")
    monkeypatch.setattr(s, "email_backend", "console")
    caplog.set_level(logging.INFO)
    anon = _client()
    assert anon.post("/api/me/password/forgot", json={"email": me["email"]}).status_code == 200
    token = _last_token(me["email"])  # still captured for tests, never logged
    logged = _logged(caplog)
    assert "body withheld" in logged
    assert token not in logged


def test_console_bodies_setting(monkeypatch, caplog) -> None:
    from app import email

    s = get_settings()
    monkeypatch.setattr(s, "public_url", "")
    monkeypatch.setattr(s, "email_console_bodies", None)
    assert email.console_bodies() is True  # local development: links visible
    monkeypatch.setattr(s, "public_url", "https://app.example.com")
    assert email.console_bodies() is False
    monkeypatch.setattr(s, "email_console_bodies", True)
    assert email.console_bodies() is True
    monkeypatch.setattr(s, "email_backend", "sendgrid")
    caplog.set_level(logging.INFO)
    sender = email.get_sender()
    assert isinstance(sender, ConsoleEmailSender) and sender.bodies is False
    asyncio.run(sender.send(email.Email("x@example.com", "Hi", f"secret link ?token={SECRET}")))
    assert SECRET not in _logged(caplog)


def test_console_sender_keeps_only_the_latest_100() -> None:
    from app import email

    sender = ConsoleEmailSender(bodies=False)
    for i in range(130):
        asyncio.run(sender.send(email.Email(f"cap-{i}@example.com", "s", "t")))
    assert len(ConsoleEmailSender.sent) == 100
    assert ConsoleEmailSender.sent[-1].to == "cap-129@example.com"


def test_filter_redacts_query_tokens_and_bearer() -> None:
    assert redact("GET /reset-password?token=abc123&x=1") == "GET /reset-password?token=[redacted]&x=1"
    assert redact("/cb?code=xyz&state=s&session_id=cs_test_1") == "/cb?code=[redacted]&state=s&session_id=[redacted]"
    assert redact("/x?a=1&api_key=k1&password=p&secret=s") == "/x?a=1&api_key=[redacted]&password=[redacted]&secret=[redacted]"
    assert redact("Authorization: Bearer sk-abc.def") == "Authorization: Bearer [redacted]"
    assert redact("close code=1000 reason ok") == "close code=1000 reason ok"  # not a query string

    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/me/verify?token=t0k3n", "1.1", 200), None)
    assert RedactingFilter().filter(rec) is True
    assert rec.args[2] == "/api/me/verify?token=[redacted]"  # args stay a tuple (uvicorn's formatter unpacks it)
    assert "t0k3n" not in rec.getMessage()

    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "auth header Bearer %s", ("abc",), None)
    RedactingFilter().filter(rec)
    assert rec.getMessage() == "auth header Bearer [redacted]"


def test_installed_filter_redacts_handler_output(caplog) -> None:
    from app import logging_redact

    handler = logging.Handler()
    seen: list[str] = []
    handler.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging_redact.install()
        logging.getLogger("buddyai.test").warning("link %s", "https://x/reset?token=s3cr3t")
    finally:
        root.removeHandler(handler)
    assert seen and "s3cr3t" not in seen[-1] and "token=[redacted]" in seen[-1]
