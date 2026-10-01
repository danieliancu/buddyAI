"""Note edit mode: line operations, and a note-mode turn (one tool, no TTS, only STT + LLM billed)."""

from __future__ import annotations

import asyncio
import json

import pytest

from app import notes_edit
from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.items import device_snapshot
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest, ToolCall
from tests.test_items import FakeIO, FakeRouter, _account

L = ["milk", "bread", "eggs"]


def ops(*o):
    return list(o)


def test_note_lines_strip_markers_and_blanks() -> None:
    assert notes_edit.note_lines("- milk\n\n2. bread\n  • eggs  \n") == ["milk", "bread", "eggs"]


def test_append_insert_replace_delete_move() -> None:
    assert notes_edit.apply_ops(L, ops({"op": "append", "text": "- butter "}), None) == (L + ["butter"], 4)
    assert notes_edit.apply_ops(L, ops({"op": "insert", "line": 1, "text": "tea"}), None) == (["tea", *L], 1)
    assert notes_edit.apply_ops(L, ops({"op": "replace", "line": 2, "text": "rolls"}), None) == (
        ["milk", "rolls", "eggs"], 2
    )
    assert notes_edit.apply_ops(L, ops({"op": "delete", "line": 1}), None) == (["bread", "eggs"], None)
    assert notes_edit.apply_ops(L, ops({"op": "move", "line": 3, "to": 1}), None) == (["eggs", "milk", "bread"], 1)


def test_line_numbers_refer_to_the_note_before_the_call() -> None:
    # "delete 1 and 2": the second delete still means the line the user saw as 2.
    out, _ = notes_edit.apply_ops(L, ops({"op": "delete", "line": 1}, {"op": "delete", "line": 2}), None)
    assert out == ["eggs"]


def test_undo_and_errors() -> None:
    assert notes_edit.apply_ops(["x"], ops({"op": "undo"}), L) == (L, None)
    for bad in (
        ops({"op": "undo"}),  # nothing to undo
        ops({"op": "delete", "line": 9}),
        ops({"op": "append", "text": "  "}),
        ops({"op": "jump"}),
        [],
    ):
        with pytest.raises(ValueError):
            notes_edit.apply_ops(L, bad, None)


class NoteLLM(LLMProvider):
    name = "note-llm"

    def __init__(self, calls: list[ToolCall] | None, text: str = "") -> None:
        self.calls, self.text = calls, text
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        if self.text:
            yield LLMChunk(delta=self.text)
        yield LLMChunk(input_tokens=120, cached_input_tokens=100, output_tokens=12)
        if self.calls:
            yield LLMChunk(tool_calls=self.calls)


def _note_turn(acc: int, number: int, text: str) -> TurnContext:
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000, account_id=acc)
    turn.mode, turn.note_number, turn.user_text = "note", number, text
    return turn


def test_note_mode_turn_edits_the_note_without_tts() -> None:
    acc = _account()
    with session_scope() as db:
        ItemRepo(db).create(acc, "note", "- milk\n- bread")
    call = ToolCall("c1", "note_edit", json.dumps({"ops": [{"op": "append", "text": "Eggs."}]}))
    llm = NoteLLM([call])
    pipeline = ConversationPipeline(FakeRouter(llm), ChunkerConfig())
    turn = _note_turn(acc, 1, "eggs")
    io = FakeIO()
    asyncio.run(pipeline._note_reply(turn, io))

    req = llm.requests[0]
    assert [t["name"] for t in req.tools] == ["note_edit"]  # one tool only
    assert req.messages[0]["content"] == notes_edit.NOTE_SYSTEM  # minimal, cacheable prompt
    assert "Title: milk\n1. bread" in req.messages[1]["content"]  # line 1 is the title
    with session_scope() as db:
        assert ItemRepo(db).get(acc, "note", 1).text == "milk\nbread\nEggs."
    assert turn.items_changed and turn.pending_open["item"]["changed_line"] == 2  # numbered line 2
    assert not [t for t, _f in io.sent if t.startswith("tts")]  # nothing spoken
    assert {u.kind for u in turn.usage} == {"llm"}  # (STT is added while listening)
    # Undo goes back one step.
    undo = NoteLLM([ToolCall("c2", "note_edit", json.dumps({"ops": [{"op": "undo"}]}))])
    asyncio.run(ConversationPipeline(FakeRouter(undo), ChunkerConfig())._note_reply(_note_turn(acc, 1, "undo"), FakeIO()))
    with session_scope() as db:
        assert ItemRepo(db).get(acc, "note", 1).text == "milk\nbread"


def test_note_mode_unclear_command_shows_a_question() -> None:
    acc = _account()
    with session_scope() as db:
        ItemRepo(db).create(acc, "note", "milk")
    io = FakeIO()
    turn = _note_turn(acc, 1, "delete line nine")
    asyncio.run(ConversationPipeline(FakeRouter(NoteLLM(None, "Which line?")), ChunkerConfig())._note_reply(turn, io))
    assert ("llm_display", {"text": "Which line?"}) in io.sent
    with session_scope() as db:
        assert ItemRepo(db).get(acc, "note", 1).text == "milk"  # unchanged


def test_pinned_notes_come_first() -> None:
    acc = _account()
    with session_scope() as db:
        repo = ItemRepo(db)
        repo.create(acc, "note", "first")
        second = repo.create(acc, "note", "second")
        repo.update(second, pinned=True)
        snap = device_snapshot(repo.list(acc), "Europe/London")
    assert [(n["number"], n["pinned"]) for n in snap["notes"]] == [(2, True), (1, False)]


def test_watch_numbers_start_under_the_title() -> None:
    note = ["Shopping", "milk", "bread"]
    assert notes_edit.apply_note_ops(note, ops({"op": "delete", "line": 1}), None) == (["Shopping", "bread"], None)
    assert notes_edit.apply_note_ops(note, ops({"op": "move", "line": 2, "to": 1}), None) == (
        ["Shopping", "bread", "milk"], 1
    )
    assert notes_edit.apply_note_ops(note, ops({"op": "title", "text": "Groceries"}), None) == (
        ["Groceries", "milk", "bread"], 0
    )
    # An empty note: the first sentence becomes the title.
    assert notes_edit.apply_note_ops([], ops({"op": "append", "text": "Ideas"}), None) == (["Ideas"], 0)
    with pytest.raises(ValueError):
        notes_edit.apply_note_ops(note, ops({"op": "delete", "line": 0}), None)
