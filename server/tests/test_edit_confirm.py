"""Edit modes (note / reminder screen, mic open): changes and deletions apply at once (no confirmations on
an item's screen - those are only in the dialog); the previous question is remembered; background speech
changes nothing; another item is never changed from here. Gateway: an edit session is bound to the item the
server showed under that number."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from itertools import count

from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.providers.llm.base import ToolCall
from tests.test_items import FakeIO, FakeRouter, _account
from tests.test_notes_edit import NoteLLM

_TURNS = count(1)


def _say(acc: int, mode: str, number: int, text: str, call: tuple[str, dict] | None = None, reply: str = "",
         session: str = "s-edit"):
    llm = NoteLLM([ToolCall("c1", call[0], json.dumps(call[1]))] if call else None, reply)
    turn = TurnContext(next(_TURNS), session, "dev-edit", "ro", DeviceSettings(timezone="Europe/Bucharest"), 16000,
                       account_id=acc)
    turn.mode, turn.note_number, turn.user_text = mode, number, text
    io = FakeIO()
    asyncio.run(ConversationPipeline(FakeRouter(llm), ChunkerConfig()).respond(turn, io))
    return turn, io, llm


def _note(acc: int, text: str) -> tuple[int, str]:
    with session_scope() as db:
        it = ItemRepo(db).create(acc, "note", text)
        return it.number, it.uid


def _text(acc: int, uid: str) -> str | None:
    with session_scope() as db:
        it = ItemRepo(db).get_by_uid(acc, uid)
        return it.text if it else None


def _shown(io: FakeIO) -> list[str]:
    return [f["text"] for t, f in io.sent if t == "llm_display"]


def test_note_mode_line_delete_applies_at_once() -> None:
    acc = _account()
    n, uid = _note(acc, "Cumpărături\nlapte\npâine\nouă")
    turn, io, _ = _say(acc, "note", n, "șterge pâinea", ("note_edit", {"ops": [{"op": "delete", "line": 2}]}))
    assert _text(acc, uid) == "Cumpărături\nlapte\nouă" and not _shown(io) and not turn.expect_reply
    assert turn.items_changed and turn.pending_open["item"]["number"] == n  # the note stays open, updated


def test_note_mode_clear_and_ambiguous_line() -> None:
    acc = _account()
    n, uid = _note(acc, "Listă\nlapte\nlapte bio")
    _say(acc, "note", n, "golește nota", ("note_edit", {"ops": [{"op": "clear"}]}))
    assert _text(acc, uid) == "Listă"  # every line under the title, at once
    n2, uid2 = _note(acc, "Listă\nlapte\nlapte bio")
    _turn, io, _ = _say(acc, "note", n2, "șterge laptele", ("note_edit", {"ops": [{"op": "delete", "match": "lapte"}]}))
    assert _text(acc, uid2) == "Listă\nlapte\nlapte bio" and _shown(io)  # two lines match: asks which one


def test_note_mode_remembers_its_question() -> None:
    acc = _account()
    n, uid = _note(acc, "Listă\nlapte\nouă")
    _say(acc, "note", n, "schimbă-l", None, reply="Care rând?")
    _turn, _io, llm = _say(acc, "note", n, "pe al doilea, în pâine", ("note_edit", {"ops": [{"op": "replace", "line": 2, "text": "pâine"}]}))
    assert any("Care rând?" in m["content"] for m in llm.requests[0].messages if m["role"] == "system")
    assert _text(acc, uid) == "Listă\nlapte\npâine"


def test_note_mode_other_item_is_not_applied_to_open_note() -> None:
    acc = _account()
    n, uid = _note(acc, "Listă\nlapte")
    _turn, io, _ = _say(acc, "note", n, "adaugă la nota cu renovarea gresie", None, reply="OTHER")
    assert _text(acc, uid) == "Listă\nlapte" and _shown(io)


def test_note_mode_ignored_speech_changes_nothing() -> None:
    acc = _account()
    n, uid = _note(acc, "Listă\nlapte\nouă")
    _turn, io, _ = _say(acc, "note", n, "...și atunci i-am zis", None, reply="IGNORE")  # the TV
    assert _text(acc, uid) == "Listă\nlapte\nouă" and not _shown(io)


def test_reminder_mode_participant_drop_applies_at_once() -> None:
    acc = _account()
    with session_scope() as db:
        it = ItemRepo(db).create(acc, "reminder", "Ședință", datetime(2030, 5, 1, 7, 0, tzinfo=timezone.utc),
                                 participants="Ana, Mihai", location="Studio")
        n, uid = it.number, it.uid
    _say(acc, "reminder", n, "fără Mihai", ("reminder_edit", {"action": "change", "remove_participants": ["Mihai"]}))
    with session_scope() as db:
        it = ItemRepo(db).get_by_uid(acc, uid)
        assert it.participants == "Ana" and it.location == "Studio"


def test_dialog_question_is_dropped_on_an_item_screen() -> None:
    from app.items import AssistantTools
    from tests.test_voice_items import ChatLLM

    acc = _account()
    n, uid = _note(acc, "Listă\nlapte\nouă")

    def chat(text: str, steps: list) -> None:
        turn = TurnContext(next(_TURNS), "s-edit", "dev-edit", "ro", DeviceSettings(), 16000, account_id=acc)
        turn.user_text = text
        pipe = ConversationPipeline(FakeRouter(ChatLLM(steps)), ChunkerConfig(),
                                    messages_builder=lambda t, x: [{"role": "system", "content": "s"}, {"role": "user", "content": x}],
                                    tools=AssistantTools())
        asyncio.run(pipe.respond(turn, FakeIO()))

    # in the dialog a deletion is asked first...
    chat("șterge lista", [[ToolCall("d", "item_delete", json.dumps({"target": {"query": {"text": "lapte"}}}))], "Sigur?"])
    # ...the user opens the note's screen instead; a later "da" in the dialog deletes nothing
    _say(acc, "note", n, "...", None, reply="IGNORE")
    chat("da", ["?"])
    assert _text(acc, uid) == "Listă\nlapte\nouă"


# --- gateway -------------------------------------------------------------------------------------------


class FakeWS:
    def __init__(self) -> None:
        from starlette.websockets import WebSocketState

        self.client_state = WebSocketState.CONNECTED
        self.sent: list[dict] = []
        self.client = None

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _conn(acc: int):
    from app.gateway.device_ws import DeviceConnection
    from app.gateway.hub import DeviceHub

    conn = DeviceConnection(FakeWS(), DeviceHub(), pipeline=None)  # type: ignore[arg-type]
    conn.device_id, conn.account_id, conn.authenticated = "dev-gw", acc, True
    conn.env.session_id = "sess-gw"
    return conn


def test_edit_listen_start_refuses_a_reused_number() -> None:
    acc = _account()
    n, uid = _note(acc, "Vechi\nx")
    conn = _conn(acc)
    conn._shown[("note", n)] = uid  # the watch shows this note
    with session_scope() as db:  # deleted on the web, a new note takes the same number
        r = ItemRepo(db)
        r.delete(r.get_by_uid(acc, uid))
        assert r.create(acc, "note", "Nouă\ny").number == n
    asyncio.run(conn._on_listen_start({"turn_id": 1, "mode": "note", "note": n}))
    assert [m["type"] for m in conn.ws.sent] == ["error", "turn_end"] and conn.ws.sent[1]["status"] == "error"
    assert conn.active is None


def test_turn_end_asks_the_watch_to_listen_again_and_marks_changes_reported() -> None:
    from app.pipeline.turn import TurnResult

    acc = _account()
    conn = _conn(acc)
    turn = TurnContext(5, "sess-gw", "dev-gw", "ro", DeviceSettings(), 16000, account_id=acc)
    turn.expect_reply = True
    conn.active = turn
    asyncio.run(conn._finish_turn(turn, TurnResult("completed")))
    end = next(m for m in conn.ws.sent if m["type"] == "turn_end")
    assert end["status"] == "completed" and end["expect_reply"] is True


# --- "delete the milk" when milk is on two lines: ask which (the only question on an item's screen) ----------


def test_note_mode_word_on_two_lines_asks_which_then_applies() -> None:
    acc = _account()
    lines = ["Cumpărături", "Lapte", "pâine", "ouă", "unt", "brânză", "mere", "pere", "cafea", "zahăr", "Lapte gras"]
    n, uid = _note(acc, "\n".join(lines))
    # the model guesses line 1, but "lapte" is also on line 10: nothing changes, the watch asks
    turn, io, _ = _say(acc, "note", n, "șterge laptele", ("note_edit", {"ops": [{"op": "delete", "line": 1}]}))
    assert _text(acc, uid) == "\n".join(lines) and not turn.items_changed
    assert _shown(io) == ["Rândul 1 «Lapte» sau rândul 10 «Lapte gras»?"]
    # the answer names it: applied at once (no other confirmation on the note's screen)
    _turn, _io, llm = _say(acc, "note", n, "cel gras", ("note_edit", {"ops": [{"op": "delete", "line": 10}]}))
    assert any("Rândul 1" in m["content"] for m in llm.requests[0].messages if m["role"] == "system")
    assert _text(acc, uid) == "\n".join(lines[:10])


def test_note_mode_match_on_two_lines_asks_and_a_said_number_is_trusted() -> None:
    acc = _account()
    n, uid = _note(acc, "Listă\nLapte\nLapte gras")
    _turn, io, _ = _say(acc, "note", n, "șterge laptele", ("note_edit", {"ops": [{"op": "delete", "match": "lapte"}]}))
    assert "sau" in _shown(io)[0] and _text(acc, uid) == "Listă\nLapte\nLapte gras"
    _say(acc, "note", n, "șterge rândul 2", ("note_edit", {"ops": [{"op": "delete", "line": 2}]}))
    assert _text(acc, uid) == "Listă\nLapte"
    # a word that fits one line only: no question
    n2, uid2 = _note(acc, "Listă\nLapte\nouă")
    _say(acc, "note", n2, "șterge ouăle", ("note_edit", {"ops": [{"op": "delete", "line": 2}]}))
    assert _text(acc, uid2) == "Listă\nLapte"


def test_reminder_mode_person_named_twice_asks_which() -> None:
    acc = _account()
    with session_scope() as db:
        it = ItemRepo(db).create(acc, "reminder", "Ședință", datetime(2030, 5, 1, 7, 0, tzinfo=timezone.utc),
                                 participants="Mihai Pop, Mihai Ionescu, Ana")
        n, uid = it.number, it.uid
    _turn, io, _ = _say(acc, "reminder", n, "scoate-l pe Mihai", ("reminder_edit", {"action": "change", "remove_participants": ["Mihai"]}))
    assert _shown(io) == ["Mihai Pop sau Mihai Ionescu?"]
    _say(acc, "reminder", n, "pe Ionescu", ("reminder_edit", {"action": "change", "remove_participants": ["Mihai Ionescu"]}))
    with session_scope() as db:
        assert ItemRepo(db).get_by_uid(acc, uid).participants == "Mihai Pop, Ana"


def test_chat_word_on_two_lines_moves_to_the_note_screen_and_continues_there() -> None:
    from app.items import AssistantTools
    from tests.test_voice_items import ChatLLM

    acc = _account()
    n, uid = _note(acc, "Cumpărături\nLapte\nLapte gras")
    # in the dialog: "șterge laptele din cumpărături" - the word fits two lines
    turn = TurnContext(next(_TURNS), "s-edit", "dev-edit", "ro", DeviceSettings(timezone="Europe/Bucharest"), 16000,
                       account_id=acc)
    turn.user_text = "șterge laptele din cumpărături"
    llm = ChatLLM([[ToolCall("e", "item_note_edit", json.dumps(
        {"target": {"query": {"text": "cumparaturi"}}, "ops": [{"op": "delete", "line": 1}]}))],
        "Laptele apare de două ori, îți deschid nota."])
    pipe = ConversationPipeline(FakeRouter(llm), ChunkerConfig(),
                                messages_builder=lambda t, x: [{"role": "system", "content": "s"}, {"role": "user", "content": x}],
                                tools=AssistantTools())
    asyncio.run(pipe.respond(turn, FakeIO()))
    assert _text(acc, uid) == "Cumpărături\nLapte\nLapte gras"  # nothing changed in the dialog
    view = turn.pending_open["item"]
    assert view["number"] == n and view["listen"] is True and view["question"] == "Rândul 1 «Lapte» sau rândul 2 «Lapte gras»?"
    assert not turn.expect_reply  # the chat does not listen again: the note's screen does
    # on the note's screen: the answer, with the request and the question in its context, applies at once
    _t, _io, edit_llm = _say(acc, "note", n, "cel gras", ("note_edit", {"ops": [{"op": "delete", "line": 2}]}))
    ctx = " ".join(m["content"] for m in edit_llm.requests[0].messages if m["role"] == "system")
    assert "Rândul 1" in ctx and "șterge laptele din cumpărături" in ctx
    assert _text(acc, uid) == "Cumpărături\nLapte"
