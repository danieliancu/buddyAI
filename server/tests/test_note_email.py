"""A note sent to the account's own email: by voice (item_email) and from the watch's send button."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import Account
from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.email import ConsoleEmailSender
from app.note_email import NOTE_EMAILS_PER_HOUR, reset_limits, send_note
from tests.test_items import _account
from tests.voice_helpers import Voice


def _verified(acc: int) -> str:
    with session_scope() as db:
        a = db.get(Account, acc)
        a.email_verified_at = datetime.now(timezone.utc)
        db.add(a)
        db.commit()
        return a.email


def _note(acc: int, text: str):
    with session_scope() as db:
        return ItemRepo(db).create(acc, "note", text)


def _sent_to(addr: str):
    return [m for m in ConsoleEmailSender.sent if m.to == addr]


def test_voice_sends_the_note_to_the_own_address() -> None:
    reset_limits()
    acc = _account()
    to = _verified(acc)
    _note(acc, "Cumpărături\nlapte\nouă")
    body = Voice(acc).body("item_email", {"target": {"query": {"text": "cumparaturi"}}})
    assert body["ok"] and body["status"] == "sent"
    [mail] = _sent_to(to)
    assert mail.subject == "Cumpărături" and "lapte\nouă" in mail.text
    assert "lapte" in mail.html and "Open my notes" in mail.html


def test_two_matching_notes_ask_which_one() -> None:
    reset_limits()
    acc = _account()
    to = _verified(acc)
    _note(acc, "Cumpărături luni\nlapte")
    _note(acc, "Cumpărături vineri\npâine")
    out = Voice(acc).run("item_email", {"target": {"query": {"text": "cumparaturi"}}})
    assert out.awaits_answer and not _sent_to(to)


def test_unconfirmed_address_gets_nothing() -> None:
    reset_limits()
    acc = _account()
    it = _note(acc, "Idei\nx")
    assert send_note(acc, it) == "unverified"


def test_at_most_a_few_an_hour() -> None:
    reset_limits()
    acc = _account()
    _verified(acc)
    it = _note(acc, "Idei\nx")
    assert [send_note(acc, it) for _ in range(NOTE_EMAILS_PER_HOUR)] == ["sent"] * NOTE_EMAILS_PER_HOUR
    assert send_note(acc, it) == "limit"


def test_reminders_are_not_emailed() -> None:
    reset_limits()
    acc = _account()
    to = _verified(acc)
    with session_scope() as db:
        ItemRepo(db).create(acc, "reminder", "Dentist", datetime(2030, 5, 1, 9, 30, tzinfo=timezone.utc))
    body = Voice(acc).body("item_email", {"target": {"query": {"text": "dentist"}}})
    assert not body["ok"] and not _sent_to(to)
