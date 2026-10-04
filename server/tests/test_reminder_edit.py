"""Reminder edit mode: one reminder changed by voice (one tool, no TTS, low-cost model), undo, delete."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app import reminder_edit
from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.providers.llm.base import ToolCall
from tests.test_items import FakeIO, FakeRouter, _account
from tests.test_notes_edit import NoteLLM

TZ = "Europe/Bucharest"


def _reminder(acc: int) -> int:
    with session_scope() as db:
        it = ItemRepo(db).create(
            acc, "reminder", "Dentist", datetime(2030, 5, 1, 6, 30, tzinfo=timezone.utc), None, None,
            location="Main Street", participants="Ana",
        )
        return it.number


_TURN = iter(range(1, 10**6))  # turns of one edit session (a confirmation needs a later turn)


def _turn(acc: int, number: int, text: str) -> TurnContext:
    turn = TurnContext(next(_TURN), "s-rem", "dev-rem", "ro", DeviceSettings(timezone=TZ), 16000, account_id=acc)
    turn.mode, turn.note_number, turn.user_text = "reminder", number, text
    return turn


def _run(acc: int, number: int, text: str, args: dict | None, reply: str = "") -> tuple[TurnContext, FakeIO, NoteLLM]:
    """One edit-mode sentence through the whole pipeline step (confirmation gate first)."""
    llm = NoteLLM([ToolCall("c1", "reminder_edit", json.dumps(args))] if args is not None else None, reply)
    turn, io = _turn(acc, number, text), FakeIO()
    asyncio.run(ConversationPipeline(FakeRouter(llm), ChunkerConfig()).respond(turn, io))
    return turn, io, llm


def test_reminder_mode_changes_only_that_reminder_without_tts() -> None:
    acc = _account()
    n = _reminder(acc)
    turn, io, llm = _run(acc, n, "mută-l la 10 și adaugă pe Mihai",
                         {"action": "change", "due_local": "2030-05-01 10:00", "participants": ["Ana", "Mihai"]})
    req = llm.requests[0]
    assert [t["name"] for t in req.tools] == ["reminder_edit"]  # one tool only
    assert req.messages[0]["content"] == reminder_edit.REMINDER_SYSTEM  # minimal, cacheable prompt
    assert '"due_local": "2030-05-01 09:30"' in req.messages[1]["content"] and "Now:" in req.messages[1]["content"]
    with session_scope() as db:
        it = ItemRepo(db).get(acc, "reminder", n)
        assert it.due_at.replace(tzinfo=timezone.utc) == datetime(2030, 5, 1, 7, 0, tzinfo=timezone.utc)
        assert it.participants == "Ana, Mihai" and it.location == "Main Street" and it.text == "Dentist"
    assert turn.items_changed and turn.pending_open["item"]["due_local"] == "2030-05-01 10:00"
    assert not [t for t, _f in io.sent if t.startswith("tts")]  # nothing spoken
    assert {u.kind for u in turn.usage} == {"llm"}

    # Removing a detail applies at once on the reminder's screen; then mark it done and undo the last change.
    turn, _, _ = _run(acc, n, "fără locație", {"action": "change", "remove": ["location"]})
    assert turn.pending_open["item"]["location"] is None
    turn, _, _ = _run(acc, n, "gata", {"action": "change", "done": True})
    assert turn.pending_open["item"]["done"] is True and not turn.pending_open["item"]["location"]
    turn, _, _ = _run(acc, n, "anulează", {"action": "undo"})
    assert turn.pending_open["item"]["done"] is False and not turn.pending_open["item"]["location"]


def test_reminder_mode_delete_opens_the_list() -> None:
    acc = _account()
    n = _reminder(acc)
    turn, _, _ = _run(acc, n, "șterge reminderul", {"action": "delete"})
    assert turn.pending_open == {"list": "reminder"} and turn.items_changed  # at once, on its own screen
    with session_scope() as db:
        assert ItemRepo(db).get(acc, "reminder", n) is None


def test_reminder_mode_unclear_or_invalid_changes_nothing() -> None:
    acc = _account()
    n = _reminder(acc)
    _, io, _ = _run(acc, n, "hmm", None, "Ce oră?")
    assert ("llm_display", {"text": "Ce oră?"}) in io.sent
    _, io, _ = _run(acc, n, "mâine", {"action": "change", "due_local": "2030-05-02"})  # no time of day
    assert ("llm_display", {"text": "?"}) in io.sent
    _, io, _ = _run(acc, n, "anulează", {"action": "undo"})  # nothing to undo yet
    assert ("llm_display", {"text": "?"}) in io.sent
    with session_scope() as db:
        it = ItemRepo(db).get(acc, "reminder", n)
        assert it.due_at.replace(tzinfo=timezone.utc) == datetime(2030, 5, 1, 6, 30, tzinfo=timezone.utc)


def test_reminder_mode_ignores_speech_that_is_not_an_instruction() -> None:
    # The mic stays open: a stray word ("amor", a TV) must not become the reminder's text.
    acc = _account()
    n = _reminder(acc)
    turn, io, llm = _run(acc, n, "amor", None, "IGNORE")
    assert "IGNORE" in llm.requests[0].messages[0]["content"]  # the rule is in the prompt
    assert io.sent == [] and not turn.items_changed and turn.pending_open is None
    assert turn.assistant_text == "(ignored: not an instruction)"
    with session_scope() as db:
        assert ItemRepo(db).get(acc, "reminder", n).text == "Dentist"
