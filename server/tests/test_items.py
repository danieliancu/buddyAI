"""Notes and reminders: numbering, web API + isolation, assistant tools, tool loop, reminder delivery."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace as NS

from app.db.models import Account
from app.db.repositories import ItemRepo
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.gateway.hub import DeviceHub
from app.items import AssistantTools, device_snapshot
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest, ToolCall
from app.providers.llm.openai import OpenAILLM
from app.providers.mock import MockTTS
from app.reminders import deliver_due
from tests.test_accounts import _customer
from tests.test_web_search import FakeStream


def _account() -> int:
    with session_scope() as db:
        acc = Account(email=f"items-{secrets.token_hex(4)}@example.com")
        db.add(acc)
        db.commit()
        db.refresh(acc)
        return acc.id


# --- numbering ---------------------------------------------------------------------------------


def test_lowest_free_number_is_reused() -> None:
    acc = _account()
    with session_scope() as db:
        repo = ItemRepo(db)
        assert [repo.create(acc, "note", f"n{i}").number for i in range(3)] == [1, 2, 3]
        repo.delete(repo.get(acc, "note", 1))
        assert [i.number for i in repo.list(acc, "note")] == [2, 3]
        assert repo.create(acc, "note", "again").number == 1
        assert repo.create(acc, "note", "next").number == 4
        repo.delete(repo.get(acc, "note", 3))
        assert repo.create(acc, "note", "gap").number == 3


def test_numbers_are_per_kind_and_per_account() -> None:
    a, b = _account(), _account()
    due = datetime.now(timezone.utc) + timedelta(hours=1)
    with session_scope() as db:
        repo = ItemRepo(db)
        assert repo.create(a, "note", "x").number == 1
        assert repo.create(a, "reminder", "y", due_at=due).number == 1
        assert repo.create(b, "note", "z").number == 1
        assert repo.create(a, "note", "w").number == 2


# --- web API -------------------------------------------------------------------------------------


def test_web_crud_and_isolation() -> None:
    c, me = _customer()
    other, _ = _customer()
    r = c.post("/api/me/items", json={"kind": "note", "text": "milk, bread\n" + "x" * 5000})
    assert r.status_code == 200, r.text
    assert r.json()["number"] == 1
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = c.post("/api/me/items", json={"kind": "reminder", "text": "dentist", "due_at": due})
    assert r.json()["number"] == 1 and r.json()["overdue"] is False
    assert c.post("/api/me/items", json={"kind": "reminder", "text": "no time"}).status_code == 422
    too_long = {"kind": "reminder", "text": "x" * 81, "due_at": due}
    assert c.post("/api/me/items", json=too_long).status_code == 422

    r = c.put("/api/me/items/note/1", json={"text": "milk"})
    assert r.json()["text"] == "milk"
    assert len(c.get("/api/me/items").json()) == 2
    assert [i["kind"] for i in c.get("/api/me/items?kind=reminder").json()] == ["reminder"]

    # another customer sees nothing and cannot touch these items
    assert other.get("/api/me/items").json() == []
    assert other.put("/api/me/items/note/1", json={"text": "hacked"}).status_code == 404
    assert other.delete("/api/me/items/note/1").status_code == 404

    assert c.delete("/api/me/items/note/1").json() == {"ok": True}
    assert c.delete("/api/me/items/note/1").status_code == 404
    assert c.post("/api/me/items", json={"text": "first again"}).json()["number"] == 1

    export = c.get("/api/me/export").json()
    assert len(export["notes_and_reminders"]) == 2


# --- assistant tools -----------------------------------------------------------------------------


def test_tools_create_show_update_delete() -> None:
    acc = _account()
    tools = AssistantTools()
    tz = "Europe/Bucharest"
    # Without an owner only the settings tool is offered; with one, the notes / reminders tools too.
    assert [t["name"] for t in tools.definitions(None)] == ["watch_settings"]
    assert "item_create" in [t["name"] for t in tools.definitions(acc)]

    out = tools.execute(acc, tz, "item_create", json.dumps({"kind": "note", "text": "Cumpără lapte"}))
    assert out.changed and json.loads(out.result)["number"] == 1

    out = tools.execute(
        acc, tz, "item_create", json.dumps({"kind": "reminder", "text": "Sună la dentist", "due_local": "2030-05-01 09:30"})
    )
    body = json.loads(out.result)
    assert body["ok"] and body["due_local"] == "2030-05-01 09:30"
    assert out.open["item"]["kind"] == "reminder" and out.open["item"]["number"] == 1  # the new item opens
    with session_scope() as db:
        it = ItemRepo(db).get(acc, "reminder", 1)
        assert it.due_at == datetime(2030, 5, 1, 6, 30, tzinfo=timezone.utc)  # EEST = UTC+3

    out = tools.execute(acc, tz, "item_show", json.dumps({"kind": "note", "number": 1}))
    assert not out.changed and out.open is None  # a lookup: the watch stays where it is
    out = tools.execute(acc, tz, "item_show", json.dumps({"kind": "note", "number": 1, "show_on_watch": True}))
    assert out.open["item"]["text"] == "Cumpără lapte" and out.open["item"]["number"] == 1

    out = tools.execute(acc, tz, "item_update", json.dumps({"kind": "reminder", "number": 1, "due_local": "2030-05-01 10:00"}))
    assert json.loads(out.result)["due_local"] == "2030-05-01 10:00"
    assert out.open["item"]["number"] == 1 and out.open["item"]["due_local"] == "2030-05-01 10:00"

    out = tools.execute(acc, tz, "item_list", json.dumps({"kind": "note"}))
    assert json.loads(out.result)["items"] == [{"number": 1, "preview": "Cumpără lapte"}]
    assert out.open is None
    assert tools.execute(acc, tz, "item_list", json.dumps({"kind": "note", "show_on_watch": True})).open == {
        "list": "note"
    }

    long_rem = {"kind": "reminder", "text": "y" * 81, "due_local": "2030-05-01 09:30"}
    assert "limit" in json.loads(tools.execute(acc, tz, "item_create", json.dumps(long_rem)).result)["error"]

    out = tools.execute(acc, tz, "item_delete", json.dumps({"kind": "note", "number": 1}))
    assert json.loads(out.result)["ok"] and out.open == {"list": "note"}  # deleted: back to the list
    missing = json.loads(tools.execute(acc, tz, "item_show", json.dumps({"kind": "note", "number": 1})).result)
    assert missing["ok"] is False
    assert json.loads(tools.execute(acc, tz, "item_create", json.dumps({"kind": "reminder", "text": "x"})).result)["ok"] is False
    assert json.loads(tools.execute(acc, tz, "item_create", "not json").result)["ok"] is False


def test_device_snapshot_orders_reminders() -> None:
    acc = _account()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        repo = ItemRepo(db)
        repo.create(acc, "reminder", "later", due_at=now + timedelta(hours=5))
        repo.create(acc, "reminder", "past", due_at=now - timedelta(hours=1))
        repo.create(acc, "reminder", "soon", due_at=now + timedelta(minutes=5))
        repo.create(acc, "note", "a note with a first line\nand more")
        snap = device_snapshot(repo.list(acc), "Europe/London")
    assert [r["text"] for r in snap["reminders"]] == ["past", "soon", "later"]
    assert [r["overdue"] for r in snap["reminders"]] == [True, False, False]
    # title = first line, subtitle = the next one
    assert snap["notes"] == [{"number": 1, "preview": "a note with a first line", "subtitle": "and more", "pinned": False}]


# --- providers: tool calls ------------------------------------------------------------------------


def test_openai_chat_accumulates_tool_call_deltas() -> None:
    def ev(content=None, tool_calls=None, usage=None):
        choices = [] if usage else [NS(delta=NS(content=content, tool_calls=tool_calls))]
        return NS(choices=choices, usage=usage)

    tc = lambda i, id=None, name=None, args=None: NS(index=i, id=id, function=NS(name=name, arguments=args))  # noqa: E731
    events = [
        ev(tool_calls=[tc(0, "call_a", "item_create", '{"kind":"no')]),
        ev(tool_calls=[tc(0, args='te","text":"lapte"}')]),
        ev(usage=NS(prompt_tokens=100, completion_tokens=20)),
    ]
    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)
        return FakeStream(events)

    llm = OpenAILLM("sk-test", "https://api.openai.com/v1")
    llm._client = NS(chat=NS(completions=NS(create=create)))
    req = LLMRequest(messages=[{"role": "user", "content": "x"}], model="m", tools=[{"name": "item_create", "parameters": {}}])

    async def collect():
        return [c async for c in llm.stream(req)]

    chunks = asyncio.run(collect())
    assert chunks[-1].tool_calls == [ToolCall("call_a", "item_create", '{"kind":"note","text":"lapte"}')]
    assert sent["tools"] == [{"type": "function", "function": {"name": "item_create", "parameters": {}}}]


# --- pipeline tool loop ---------------------------------------------------------------------------


class ScriptedLLM(LLMProvider):
    """Round 1: asks for item_create. Round 2: answers with text, having seen the tool result."""

    name = "scripted"

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield LLMChunk(input_tokens=50, output_tokens=10)
            yield LLMChunk(tool_calls=[ToolCall("c1", "item_create", json.dumps({"kind": "note", "text": "lapte"}))])
        else:
            result = json.loads(request.messages[-1]["content"])
            yield LLMChunk(delta=f"Am salvat nota {result['number']}.")
            yield LLMChunk(input_tokens=80, output_tokens=8)


class FakeRouter:
    def __init__(self, llm):
        self._llm = llm

    def llm(self, s):
        return self._llm, "model-x"

    def tts(self, language, s):
        return NS(provider=MockTTS(first_audio_s=0), voice="v", instructions=None)

    def web_search(self, s, llm):
        return None

    def llm_params(self):
        return {}


class FakeIO:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, turn, type_, **fields):
        self.sent.append((type_, fields))
        return True

    async def send_audio(self, turn, packet):
        return True


def test_pipeline_runs_tools_then_speaks_answer() -> None:
    acc = _account()
    llm = ScriptedLLM()
    pipeline = ConversationPipeline(
        FakeRouter(llm),
        ChunkerConfig(),
        messages_builder=lambda turn, text: [{"role": "system", "content": "sys"}, {"role": "user", "content": text}],
        tools=AssistantTools(),
    )
    turn = TurnContext(1, "s", "dev", "ro", DeviceSettings(), 16000, account_id=acc)
    turn.user_text = "notează lapte"
    io = FakeIO()
    asyncio.run(pipeline._reply(turn, io))

    assert turn.assistant_text == "Am salvat nota 1."
    assert turn.items_changed is True
    assert llm.requests[0].tools and "item_" in llm.requests[0].messages[0]["content"]
    second = llm.requests[1].messages
    assert second[-2]["tool_calls"][0]["function"]["name"] == "item_create"
    assert second[-1]["role"] == "tool" and json.loads(second[-1]["content"])["ok"] is True
    tokens = {u.unit: u.quantity for u in turn.usage if u.kind == "llm"}
    assert tokens["input_token"] == 130 and tokens["output_token"] == 18  # both rounds billed
    with session_scope() as db:
        assert ItemRepo(db).get(acc, "note", 1).text == "lapte"


def test_no_tools_without_account() -> None:
    pipeline = ConversationPipeline(
        FakeRouter(_TextLLM()), ChunkerConfig(), messages_builder=lambda t, x: [{"role": "user", "content": x}],
        tools=AssistantTools(),
    )
    turn = TurnContext(1, "s", "dev", "en", DeviceSettings(), 16000, account_id=None)
    turn.user_text = "hi"
    asyncio.run(pipeline._reply(turn, FakeIO()))
    assert [t["name"] for t in pipeline.router._llm.seen_tools] == ["watch_settings"]  # no notes tools


class _TextLLM(LLMProvider):
    name = "text"
    seen_tools: object = "unset"

    async def stream(self, request: LLMRequest):
        self.seen_tools = request.tools
        yield LLMChunk(delta="Hello.")
        yield LLMChunk(input_tokens=1, output_tokens=1)


# --- reminder delivery ----------------------------------------------------------------------------


class FakeConn:
    def __init__(self, account_id: int, online: bool = True):
        self.account_id, self.authenticated, self.online = account_id, True, online
        self.settings = DeviceSettings(timezone="Europe/London")
        self.sent: list[tuple[str, dict]] = []

    async def send_json(self, type_, turn_id=None, **fields):
        if not self.online:
            return False
        self.sent.append((type_, fields))
        return True


def test_due_reminder_fires_once_and_waits_for_a_watch() -> None:
    acc = _account()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        repo = ItemRepo(db)
        repo.create(acc, "reminder", "due now", due_at=now - timedelta(seconds=5))
        repo.create(acc, "reminder", "future", due_at=now + timedelta(hours=1))
        repo.create(acc, "reminder", "too old", due_at=now - timedelta(days=2))
    hub = DeviceHub()
    conn = FakeConn(acc, online=False)
    hub.connections["w1"] = conn

    assert asyncio.run(deliver_due(hub, acc)) == 0  # watch offline: stays pending
    conn.online = True
    assert asyncio.run(deliver_due(hub, acc)) == 1
    fired = [f for t, f in conn.sent if t == "reminder_fire"]
    assert [f["item"]["text"] for f in fired] == ["due now"]
    assert any(t == "items" for t, _ in conn.sent)  # list refreshed (reminder now done)
    assert asyncio.run(deliver_due(hub, acc)) == 0  # never twice


# --- completed reminders --------------------------------------------------------------------------


def test_completed_reminder_is_not_overdue_does_not_fire_and_sorts_last() -> None:
    acc = _account()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        repo = ItemRepo(db)
        past = repo.create(acc, "reminder", "past", due_at=now - timedelta(minutes=5))
        repo.create(acc, "reminder", "soon", due_at=now + timedelta(minutes=5))
        repo.update(past, done=True)
        snap = device_snapshot(repo.list(acc), "Europe/London")
    assert [(r["text"], r["done"], r["overdue"]) for r in snap["reminders"]] == [
        ("soon", False, False),
        ("past", True, False),
    ]
    hub = DeviceHub()
    hub.connections["w1"] = FakeConn(acc)
    assert asyncio.run(deliver_due(hub, acc)) == 0  # completed early: never fires

    tools = AssistantTools()
    out = tools.execute(acc, "Europe/London", "item_update", json.dumps({"kind": "reminder", "number": 1, "done": False}))
    assert json.loads(out.result)["done"] is False and json.loads(out.result)["overdue"] is True
    with session_scope() as db:
        repo = ItemRepo(db)
        it = repo.update(repo.get(acc, "reminder", 1), done=True)
        assert it.done_at is not None
        it = repo.update(it, due_at=now + timedelta(days=1))  # rescheduled: open again
        assert it.done_at is None


def test_web_marks_reminder_done() -> None:
    client, _me = _customer()
    due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    client.post("/api/me/items", json={"kind": "reminder", "text": "pay rent", "due_at": due})
    r = client.put("/api/me/items/reminder/1/done", json={"done": True})
    assert r.status_code == 200 and r.json()["done"] is True and r.json()["overdue"] is False
    assert client.put("/api/me/items/reminder/9/done", json={"done": True}).status_code == 404


def test_reminder_time_range() -> None:
    acc = _account()
    tools = AssistantTools()
    tz = "Europe/London"
    out = tools.execute(
        acc,
        tz,
        "item_create",
        json.dumps({"kind": "reminder", "text": "Team sync", "due_local": "2030-05-01 09:30", "end_local": "2030-05-01 10:00"}),
    )
    body = json.loads(out.result)
    assert body["ok"] and body["end_local"] == "2030-05-01 10:00"
    # Moving only the start keeps the length of the range.
    out = tools.execute(acc, tz, "item_update", json.dumps({"kind": "reminder", "number": 1, "due_local": "2030-05-01 11:00"}))
    assert json.loads(out.result)["end_local"] == "2030-05-01 11:30"
    # An end before the start is refused; an empty end_local removes it.
    out = tools.execute(acc, tz, "item_update", json.dumps({"kind": "reminder", "number": 1, "end_local": "2030-05-01 10:00"}))
    assert json.loads(out.result)["ok"] is False
    out = tools.execute(acc, tz, "item_update", json.dumps({"kind": "reminder", "number": 1, "end_local": ""}))
    assert json.loads(out.result)["end_local"] is None
    with session_scope() as db:
        snap = device_snapshot(ItemRepo(db).list(acc), tz)
    assert snap["reminders"][0]["end_local"] is None and snap["reminders"][0]["due_local"] == "2030-05-01 11:00"


def test_web_reminder_end_time() -> None:
    client, _me = _customer()
    due = datetime(2030, 5, 1, 9, 30, tzinfo=timezone.utc)
    body = {"kind": "reminder", "text": "dentist", "due_at": due.isoformat(), "end_at": (due + timedelta(minutes=45)).isoformat()}
    r = client.post("/api/me/items", json=body)
    assert r.status_code == 200 and r.json()["end_at"].startswith("2030-05-01T10:15")
    r = client.put("/api/me/items/reminder/1", json={**body, "end_at": (due - timedelta(minutes=5)).isoformat()})
    assert r.status_code == 422
    r = client.put("/api/me/items/reminder/1", json={**body, "end_at": None})
    assert r.status_code == 200 and r.json()["end_at"] is None


def test_reminder_needs_a_time_of_day() -> None:
    acc = _account()
    tools = AssistantTools()
    tz = "Europe/London"
    for args in ({"kind": "reminder", "text": "dentist"}, {"kind": "reminder", "text": "dentist", "due_local": "2030-05-01"}):
        body = json.loads(tools.execute(acc, tz, "item_create", json.dumps(args)).result)
        assert body["ok"] is False and "ask the user" in body["error"].lower()
    with session_scope() as db:
        assert ItemRepo(db).list(acc, "reminder") == []
    assert "never pick a time yourself" in tools.rules(acc)[1]


def test_advance_notice_fires_before_and_at_the_start() -> None:
    acc = _account()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        repo = ItemRepo(db)
        repo.create(acc, "reminder", "meeting", due_at=now + timedelta(minutes=10), notify_before_min=15)
        repo.create(acc, "reminder", "later", due_at=now + timedelta(hours=2), notify_before_min=15)
    hub = DeviceHub()
    conn = FakeConn(acc)
    hub.connections["w1"] = conn
    assert asyncio.run(deliver_due(hub, acc)) == 1  # "meeting": 15 min before is already here
    fired = [f for t, f in conn.sent if t == "reminder_fire"]
    assert len(fired) == 1 and fired[0]["early"] is True and fired[0]["item"]["notify_before"] == 15
    assert asyncio.run(deliver_due(hub, acc)) == 0  # the advance notice is sent once
    with session_scope() as db:  # at the start it fires again, as before
        repo = ItemRepo(db)
        it = repo.get(acc, "reminder", 1)
        it.due_at = now - timedelta(seconds=5)
        db.add(it)
        db.commit()
    assert asyncio.run(deliver_due(hub, acc)) == 1
    assert [f["early"] for t, f in conn.sent if t == "reminder_fire"] == [True, False]

    tools = AssistantTools()
    out = tools.execute(acc, "Europe/London", "item_update", json.dumps({"kind": "reminder", "number": 2, "notify_before_minutes": 0}))
    assert json.loads(out.result)["notify_before_minutes"] is None


def test_reminder_location_and_participants() -> None:
    acc = _account()
    tools = AssistantTools()
    tz = "Europe/London"
    args = {
        "kind": "reminder",
        "text": "Team sync with Ana and Mihai at Studio Office",
        "due_local": "2030-05-01 09:30",
        "location": " Studio  Office ",
        "participants": ["Ana", " Mihai", ""],
    }
    body = json.loads(tools.execute(acc, tz, "item_create", json.dumps(args)).result)
    assert body["location"] == "Studio Office" and body["participants"] == "Ana, Mihai"
    with session_scope() as db:
        snap = device_snapshot(ItemRepo(db).list(acc), tz)
    assert snap["reminders"][0]["location"] == "Studio Office"
    # Leaving them out of an update keeps them; empty values remove them.
    body = json.loads(tools.execute(acc, tz, "item_update", json.dumps({"kind": "reminder", "number": 1, "text": "Team sync"})).result)
    assert body["participants"] == "Ana, Mihai"
    upd = {"kind": "reminder", "number": 1, "location": "", "participants": []}
    body = json.loads(tools.execute(acc, tz, "item_update", json.dumps(upd)).result)
    assert body["location"] is None and body["participants"] is None


def test_reminder_list_has_only_what_the_watch_shows() -> None:
    # The voice talks about today and later only: older days are in the web account, not on the watch.
    from app.items import on_watch

    acc = _account()
    tools = AssistantTools()
    tz = "Europe/London"
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz))
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d 09:00")
    today = now.strftime("%Y-%m-%d 23:58")
    for text, due in (("Old meeting", yesterday), ("Tonight", today), ("Next year", "2030-01-02 10:00")):
        tools.execute(acc, tz, "item_create", json.dumps({"kind": "reminder", "text": text, "due_local": due}))
    rows = json.loads(tools.execute(acc, tz, "item_list", json.dumps({"kind": "reminder"})).result)["items"]
    assert [r["text"] for r in rows] == ["Tonight", "Next year"]
    with session_scope() as db:
        old = next(it for it in ItemRepo(db).list(acc, "reminder") if it.text == "Old meeting")
        assert not on_watch(old, tz)
