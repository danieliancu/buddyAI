"""Long-term memory: policy, account isolation, voice tools, recall, privacy paths, jobs and learning."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import timedelta

import pytest
from sqlmodel import select

from app.config import get_settings
from app.db.models import Account, Conversation, Device, Memory, MemoryEmbedding, MemoryJob, Turn, utcnow
from app.db.repositories import ConversationRepo, DeviceRepo, SettingsRepo
from app.db.session import session_scope
from app.device_settings import DeviceSettings
from app.items import AssistantTools
from app.memory import policy
from app.memory.repo import MemoryConflictError, MemoryLimitError, MemoryRepo
from app.pipeline.chunker import ChunkerConfig
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.turn import TurnContext
from app.providers.llm.base import LLMChunk, LLMProvider, LLMRequest, ToolCall
from app.security import hash_device_token
from app.voice_context import STORE
from tests.test_items import FakeIO, FakeRouter, _account
from tests.voice_helpers import Voice


@pytest.fixture(autouse=True)
def memory_on():
    s = get_settings()
    saved = {k: getattr(s, k) for k in ("memory_enabled", "memory_accounts", "memory_embeddings_enabled",
                                        "memory_vector_retrieval", "memory_inference_enabled", "memory_max_active")}
    s.memory_enabled, s.memory_accounts = True, ""
    yield s
    for k, v in saved.items():
        setattr(s, k, v)


def _save(acc: int, text: str, kind: str = "other", **kw) -> Memory:
    with session_scope() as db:
        res = MemoryRepo(db).save(acc, text, kind, kw.pop("origin", "explicit"), **kw)
        db.refresh(res.memory)
        db.expunge(res.memory)
        return res.memory


def _rows(acc: int, statuses=("active", "pending", "superseded")) -> list[Memory]:
    with session_scope() as db:
        rows = MemoryRepo(db).list(acc, statuses)
        for r in rows:
            db.expunge(r)
        return rows


def _device(acc: int) -> str:
    device_id = f"dev-{secrets.token_hex(4)}"
    with session_scope() as db:
        DeviceRepo(db).pair(device_id, hash_device_token(secrets.token_hex(8)), "Watch", "t", "t", acc)
        SettingsRepo(db).ensure(device_id)
    return device_id


# --- policy -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "My card is 4111 1111 1111 1111",
    "my PIN is 1234",
    "The wifi password is hunter2",
    "Parola mea este floare",
    "IBAN GB29 NWBK 6016 1331 9268 19",
    "my api key sk-abcdefghijklmnopqrstuvwx",
    "my passport number is 123456789",
])
def test_block_list_refuses_secrets(text: str) -> None:
    with pytest.raises(policy.MemoryRefused):
        policy.check(text)


def test_policy_cleans_and_flags_special() -> None:
    assert policy.check("  User likes\n\ttea \x00 with milk ") == "User likes tea with milk"
    assert len(policy.check("x " * 400)) <= policy.CONTENT_MAX
    assert policy.is_special("User is allergic to penicillin")
    assert not policy.is_special("User likes gardening")
    assert policy.content_hash("Maria, BUNICA") == policy.content_hash("maria bunica")


# --- repository -------------------------------------------------------------------------------------------


def test_isolation_between_accounts() -> None:
    a, b = _account(), _account()
    m = _save(a, "User's granddaughter is called Maria", "person")
    with session_scope() as db:
        repo = MemoryRepo(db)
        assert repo.get(b, m.uid) is None
        assert repo.forget(b, m.uid) == 0
        with pytest.raises(MemoryConflictError):
            repo.update(b, m.uid, content="hacked")
        assert repo.for_device(b, "dev") == []
    assert [r.uid for r in _rows(a)] == [m.uid]


def test_duplicate_and_supersede_by_key() -> None:
    acc = _account()
    first = _save(acc, "User's favourite drink is tea", "preference", subject="user", attribute="favourite drink")
    with session_scope() as db:
        again = MemoryRepo(db).save(acc, "user's favourite drink is TEA", "preference", "explicit")
        assert again.outcome == "duplicate" and again.memory.uid == first.uid
        new = MemoryRepo(db).save(acc, "User's favourite drink is coffee", "preference", "explicit",
                                  subject="user", attribute="favourite_drink")
        assert new.outcome == "superseded" and new.replaced.uid == first.uid
    rows = {r.content: r.status for r in _rows(acc)}
    assert rows == {"User's favourite drink is tea": "superseded", "User's favourite drink is coffee": "active"}


def test_correction_keeps_history_and_forget_removes_chain() -> None:
    acc = _account()
    m = _save(acc, "User lives in Leeds", "profile")
    with session_scope() as db:
        res = MemoryRepo(db).update(acc, m.uid, content="User lives in York")
        assert res.outcome == "superseded"
        new_uid = res.memory.uid
    assert {r.status for r in _rows(acc)} == {"active", "superseded"}
    with session_scope() as db:
        assert MemoryRepo(db).forget(acc, new_uid) == 2  # the correction and the version it replaced
    assert _rows(acc) == []


def test_limit(memory_on) -> None:
    memory_on.memory_max_active = 2
    acc = _account()
    _save(acc, "Fact one about gardens")
    _save(acc, "Fact two about boats")
    with pytest.raises(MemoryLimitError):
        _save(acc, "Fact three about trains")


def test_pending_confirm_supersedes_target() -> None:
    acc = _account()
    stated = _save(acc, "User's dog is called Rex", "person", subject="person:dog", attribute="name")
    with session_scope() as db:
        res = MemoryRepo(db).save(acc, "User's dog is called Max", "person", "inferred", subject="person:dog",
                                  attribute="name", status="pending", confidence=0.7)
        pending_uid = res.memory.uid
    assert {r.content: r.status for r in _rows(acc)}["User's dog is called Rex"] == "active"
    with session_scope() as db:
        MemoryRepo(db).confirm(acc, pending_uid)
    statuses = {r.content: r.status for r in _rows(acc)}
    assert statuses == {"User's dog is called Rex": "superseded", "User's dog is called Max": "active"}
    assert stated.uid


def test_device_scope() -> None:
    acc = _account()
    dev_a, dev_b = _device(acc), _device(acc)
    _save(acc, "Wearer likes jazz", device_id=dev_a)
    _save(acc, "The family dog is Rex")
    with session_scope() as db:
        assert {m.content for m in MemoryRepo(db).for_device(acc, dev_a)} == {"Wearer likes jazz", "The family dog is Rex"}
        assert {m.content for m in MemoryRepo(db).for_device(acc, dev_b)} == {"The family dog is Rex"}


def test_expired_memories_are_not_recalled() -> None:
    acc = _account()
    _save(acc, "User is in Spain this week", valid_until=utcnow() - timedelta(hours=1))
    with session_scope() as db:
        assert MemoryRepo(db).for_device(acc, "dev") == []


# --- voice tools --------------------------------------------------------------------------------------------


def test_tools_offered_only_when_enabled(memory_on) -> None:
    acc = _account()
    names = [t["name"] for t in AssistantTools().definitions(acc)]
    assert "memory_save" in names
    memory_on.memory_accounts = "999999999"  # rollout allowlist without this account
    assert "memory_save" not in [t["name"] for t in AssistantTools().definitions(acc)]
    assert json.loads(Voice(acc).run("memory_save", {"fact": "x is y", "kind": "other"}).result)["ok"] is False
    memory_on.memory_accounts = ""
    memory_on.memory_enabled = False
    assert "memory_save" not in [t["name"] for t in AssistantTools().definitions(acc)]


def test_voice_save_list_change_forget() -> None:
    acc = _account()
    v = Voice(acc)
    saved = v.body("memory_save", {"fact": "User's granddaughter is called Maria", "kind": "person"})
    assert saved == {"ok": True, "status": "saved", "id": saved["id"]}
    assert v.body("memory_save", {"fact": "user's granddaughter is called maria", "kind": "person"})["status"] == "already_known"
    listed = v.body("memory_list", {})
    assert listed["total"] == 1 and listed["memories"][0]["fact"] == "User's granddaughter is called Maria"
    upd = v.body("memory_change", {"id": saved["id"], "action": "update", "fact": "User's granddaughter is called Mara"})
    assert upd["ok"] and upd["status"] == "updated"
    assert v.body("memory_change", {"id": upd["id"], "action": "forget"})["status"] == "forgotten"
    assert _rows(acc) == []


def test_voice_refuses_secrets_and_other_accounts() -> None:
    a, b = _account(), _account()
    body = Voice(a).body("memory_save", {"fact": "my PIN is 4321", "kind": "other"})
    assert body["ok"] is False and body["refused"] is True and _rows(a) == []
    uid = Voice(a).body("memory_save", {"fact": "User loves sailing", "kind": "preference"})["id"]
    assert Voice(b).body("memory_change", {"id": uid, "action": "forget"})["ok"] is False
    assert len(_rows(a)) == 1


def test_save_failure_is_never_reported_as_saved(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    acc = _account()

    def boom(self, *a, **k):
        raise OperationalError("commit", {}, Exception("db down"))

    monkeypatch.setattr(MemoryRepo, "save", boom)
    body = Voice(acc).body("memory_save", {"fact": "User likes tea", "kind": "preference"})
    assert body["ok"] is False and "could not be saved" in body["error"]


def test_forget_all_needs_spoken_confirmation() -> None:
    from app.voice_tools import execute_confirmation

    acc = _account()
    v = Voice(acc, session=f"s-{secrets.token_hex(3)}")
    v.body("memory_save", {"fact": "User likes tea", "kind": "preference"})
    v.body("memory_save", {"fact": "User's cat is Tom", "kind": "person"})
    out = v.run("memory_forget_all", {})
    body = json.loads(out.result)
    assert body["status"] == "confirmation_required" and out.awaits_answer and len(_rows(acc)) == 2
    ctx = STORE.get(acc, v.device, v.session)
    pc = STORE.pending_confirmation(ctx)
    assert pc is not None and pc.op == "forget_memories"
    taken = STORE.take_confirmation(ctx, pc.id, v.next_turn(), "chat", None)
    res = execute_confirmation(v.call(), taken)
    assert res.status == "done" and _rows(acc) == []


# --- recall ------------------------------------------------------------------------------------------------


def test_recall_small_set_goes_in_whole() -> None:
    from app.memory.retrieve import HEADER, recall

    acc = _account()
    _save(acc, "User's name is Ion", "profile")
    _save(acc, "User likes walking by the lake", "preference")
    rec = asyncio.run(recall(None, acc, "dev", "anything", []))
    assert rec.path == "small" and len(rec.memories) == 2
    msg = rec.message()
    assert msg["role"] == "system" and msg["content"].startswith(HEADER)
    data = json.loads(msg["content"][len(HEADER) + 1:])
    assert data[0]["fact"] == "User's name is Ion"  # profile first


def test_recall_large_set_uses_words_without_vectors() -> None:
    from app.memory.retrieve import recall

    acc = _account()
    for i in range(35):
        _save(acc, f"Neutral fact number {i} about topic{i}", "other")
    _save(acc, "User's granddaughter Maria studies medicine in Cluj", "person")
    rec = asyncio.run(recall(None, acc, "dev", "how is Maria doing?", []))
    assert rec.path == "words" and [m.content for m in rec.memories][0].startswith("User's granddaughter Maria")
    assert len(rec.memories) <= 8 + 4


def test_recall_vectors_on_sqlite_with_mock_embeddings(memory_on) -> None:
    from app.memory.jobs import backfill, run_job, _claim
    from app.memory.retrieve import recall
    from app.providers.router import ProviderRouter

    from app.memory.repo import has_vectors

    with session_scope() as db:
        if not has_vectors(db):
            pytest.skip("memory_embeddings needs migration 0022 (milestone B)")
    memory_on.memory_embeddings_enabled = memory_on.memory_vector_retrieval = True
    router = ProviderRouter()
    acc = _account()
    for i in range(35):
        _save(acc, f"Neutral fact number {i} about subject{i}", "other")
    target = _save(acc, "User plays chess every Tuesday at the club", "routine")
    backfill(router.embedding().model_key)

    async def drain():
        while ids := _claim(100):
            for j in ids:
                await run_job(router, j)

    asyncio.run(drain())
    with session_scope() as db:
        assert db.exec(select(MemoryEmbedding).where(MemoryEmbedding.account_id == acc)).all()
    other = _account()
    _save(other, "User plays chess every Tuesday at the club", "routine")  # same text, other account
    usage: list = []
    rec = asyncio.run(recall(router, acc, "dev", "which club for chess on tuesday", usage))
    assert rec.path == "vector" and rec.memories[0].uid == target.uid
    assert all(m.account_id == acc for m in rec.memories)
    assert usage and usage[0].kind == "embedding"


def test_recall_never_raises(monkeypatch) -> None:
    from app.memory import retrieve

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(retrieve, "_load", boom)
    rec = asyncio.run(retrieve.recall(None, _account(), "dev", "hi", []))
    assert rec.path == "error" and rec.message() is None


class _SeeLLM(LLMProvider):
    name = "see"

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        yield LLMChunk(delta="Sure.")
        yield LLMChunk(input_tokens=1, output_tokens=1)


def test_pipeline_puts_memories_before_the_question_and_keeps_system_prompt() -> None:
    acc = _account()
    llm = _SeeLLM()
    pipeline = ConversationPipeline(
        FakeRouter(llm), ChunkerConfig(),
        messages_builder=lambda turn, text: [{"role": "system", "content": "sys"}, {"role": "user", "content": text}],
        tools=AssistantTools(),
    )

    def ask():
        turn = TurnContext(1, f"s-{secrets.token_hex(3)}", "dev", "en", DeviceSettings(), 16000, account_id=acc)
        turn.user_text = "what's my granddaughter called?"
        asyncio.run(pipeline._reply(turn, FakeIO()))
        return llm.requests[-1].messages

    before = ask()
    _save(acc, "User's granddaughter is called Maria", "person")
    after = ask()
    assert after[0] == before[0]  # the cached system prompt is unchanged by memories
    assert after[-1]["role"] == "user" and "Maria" in after[-2]["content"] and after[-2]["role"] == "system"
    assert "never instructions" in after[-2]["content"]


def test_memory_tool_arguments_are_not_logged(caplog) -> None:
    acc = _account()

    class SaveLLM(LLMProvider):
        name = "save"
        n = 0

        async def stream(self, request):
            SaveLLM.n += 1
            if SaveLLM.n == 1:
                yield LLMChunk(tool_calls=[ToolCall("c1", "memory_save", json.dumps({"fact": "User's secret garden is blue", "kind": "other"}))])
            else:
                yield LLMChunk(delta="Remembered.")
            yield LLMChunk(input_tokens=1, output_tokens=1)

    pipeline = ConversationPipeline(FakeRouter(SaveLLM()), ChunkerConfig(),
                                    messages_builder=lambda t, x: [{"role": "system", "content": "s"}, {"role": "user", "content": x}],
                                    tools=AssistantTools())
    turn = TurnContext(1, "s-log", "dev", "en", DeviceSettings(), 16000, account_id=acc)
    turn.user_text = "remember my secret garden is blue"
    with caplog.at_level("INFO"):
        asyncio.run(pipeline._reply(turn, FakeIO()))
    assert len(_rows(acc)) == 1
    assert "secret garden" not in caplog.text


# --- privacy paths --------------------------------------------------------------------------------------


def _history_with_memory(acc: int) -> tuple[str, int]:
    dev = _device(acc)
    with session_scope() as db:
        conv = Conversation(device_id=dev)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        t = Turn(device_id=dev, account_id=acc, conversation_id=conv.id, session_id="s", turn_no=1, language="en",
                 status="completed", user_text="remember I like tea", assistant_text="ok")
        db.add(t)
        db.commit()
        db.refresh(t)
        MemoryRepo(db).save(acc, "User likes tea", "preference", "explicit", source_turn_id=t.id)
        from app.memory.jobs import enqueue

        enqueue(db, "extract", acc, conversation_id=conv.id, upto_turn_id=t.id)
        db.commit()
        return dev, conv.id


def test_clearing_history_keeps_memories_and_unlinks_them() -> None:
    acc = _account()
    dev, conv_id = _history_with_memory(acc)
    with session_scope() as db:
        assert ConversationRepo(db).delete_device_history(dev) == 1
        m = db.exec(select(Memory).where(Memory.account_id == acc)).one()
        assert m.source_turn_id is None and m.status == "active"
        assert db.exec(select(MemoryJob).where(MemoryJob.conversation_id == conv_id)).all() == []


def test_account_delete_and_export_cover_memories() -> None:
    from app import accounts

    acc = _account()
    _history_with_memory(acc)
    with session_scope() as db:
        account = db.get(Account, acc)
        data = accounts.export(db, account)
        assert [m["fact"] for m in data["memories"]] == ["User likes tea"]
        accounts.delete_account(db, account)
        assert db.exec(select(Memory).where(Memory.account_id == acc)).all() == []
        assert db.exec(select(MemoryJob).where(MemoryJob.account_id == acc)).all() == []


def test_watch_changing_owner_does_not_hand_over_memories() -> None:
    a, b = _account(), _account()
    dev = _device(a)
    _save(a, "Wearer likes jazz", device_id=dev)
    with session_scope() as db:
        DeviceRepo(db).pair(dev, hash_device_token("x" * 16), "Watch", "t", "t", b)
        assert MemoryRepo(db).for_device(b, dev) == []


# --- web API ------------------------------------------------------------------------------------------------


def test_memory_api_is_scoped_and_clear_needs_password() -> None:
    from tests.test_accounts import _customer

    c1, me1 = _customer()
    c2, me2 = _customer()
    r = c1.post("/api/me/memories", json={"fact": "User likes long walks", "kind": "preference"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    assert c1.get("/api/me/memories").json()["memories"][0]["fact"] == "User likes long walks"
    assert c2.get("/api/me/memories").json()["memories"] == []
    assert c2.delete(f"/api/me/memories/{uid}").status_code == 404
    assert c2.patch(f"/api/me/memories/{uid}", json={"fact": "hacked"}).status_code == 404
    assert c1.post("/api/me/memories", json={"fact": "my password is abc", "kind": "other"}).status_code == 422
    r = c1.patch(f"/api/me/memories/{uid}", json={"fact": "User likes short walks"})
    assert r.status_code == 200 and r.json()["outcome"] == "superseded"
    assert c1.post("/api/me/memories/clear", json={"password": "wrong-one"}).status_code == 403
    assert c1.post("/api/me/memories/clear", json={"password": "correct-horse-1"}).json()["deleted"] == 2
    assert c1.put("/api/me/memories/settings", json={"learn": True}).json() == {"remember_requests": True, "learn": True}


def test_memory_api_hidden_when_disabled(memory_on) -> None:
    from tests.test_accounts import _customer

    memory_on.memory_enabled = False
    c, _ = _customer()
    assert c.get("/api/me/memories").json() == {"available": False}
    assert c.post("/api/me/memories", json={"fact": "User likes tea"}).status_code == 404


# --- jobs ------------------------------------------------------------------------------------------------------


def test_job_retries_then_dies_without_content(monkeypatch, memory_on) -> None:
    from app.memory import jobs
    from app.memory.repo import has_vectors

    with session_scope() as db:
        if not has_vectors(db):
            pytest.skip("embedding jobs need migration 0022 (milestone B)")
    memory_on.memory_embeddings_enabled = True
    acc = _account()
    m = _save(acc, "User grows tomatoes")

    class Broken:
        name, model, dims, model_key = "x", "y", 4, "x:y:4"

        async def embed(self, texts):
            raise RuntimeError("User grows tomatoes")  # content in an exception must not be stored

    class R:
        def embedding(self):
            return Broken()

    with session_scope() as db:
        job = jobs.enqueue(db, "embed", acc, memory_id=m.id)
        db.commit()
        job_id = job.id
    for _ in range(jobs.MAX_ATTEMPTS):
        with session_scope() as db:
            j = db.get(MemoryJob, job_id)
            j.run_after = utcnow() - timedelta(seconds=1)
            db.add(j)
            db.commit()
        for jid in jobs._claim(100):
            asyncio.run(jobs.run_job(R(), jid))
    with session_scope() as db:
        j = db.get(MemoryJob, job_id)
        assert j.state == "dead" and j.last_error == "RuntimeError"


def test_expired_lease_is_picked_up_again() -> None:
    from app.memory import jobs

    acc = _account()
    m = _save(acc, "User reads poetry")
    with session_scope() as db:
        job = jobs.enqueue(db, "embed", acc, memory_id=m.id)
        job.state, job.lease_expires_at = "running", utcnow() - timedelta(seconds=5)  # a crashed process
        db.add(job)
        db.commit()
        job_id = job.id
    assert job_id in jobs._claim(100)


# --- learning -------------------------------------------------------------------------------------------------


def test_extraction_policy_decisions() -> None:
    from app.memory.extract import decide, parse

    raw = 'Sure: ```{"facts": [{"fact": "User\'s daughter Ana lives in Leeds", "kind": "person", "confidence": 0.9},' \
          '{"fact": "User might try yoga", "kind": "goal", "confidence": 0.65},' \
          '{"fact": "User has diabetes", "kind": "profile", "confidence": 0.99},' \
          '{"fact": "my PIN is 1234", "kind": "other", "confidence": 0.99},' \
          '{"fact": "low", "kind": "preference", "confidence": 0.2}]}```'
    proposals = parse(raw)
    assert len(proposals) == 3  # at most MAX_NEW read
    assert decide(proposals[0]) == ("User's daughter Ana lives in Leeds", "active")
    assert decide(proposals[1]) == ("User might try yoga", "pending")
    assert decide(proposals[2]) is None  # special category: never learned
    assert decide({"fact": "User's PIN is 1234", "kind": "profile", "confidence": 1}) is None
    assert parse("not json") == []


def test_learning_job_end_to_end(memory_on) -> None:
    from app.memory import jobs
    from app.memory.extract import schedule

    memory_on.memory_inference_enabled = True
    acc = _account()
    with session_scope() as db:
        MemoryRepo(db).set_prefs(acc, learn=True)
    dev = _device(acc)
    stated = _save(acc, "User's dog is called Rex", "person")
    with session_scope() as db:
        conv = Conversation(device_id=dev)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        t = Turn(device_id=dev, account_id=acc, conversation_id=conv.id, session_id="s", turn_no=1, language="en",
                 status="completed", user_text="My daughter Ana moved to Leeds last year", assistant_text="Lovely!")
        db.add(t)
        db.commit()
        db.refresh(t)
        schedule(db, acc, conv.id, t.id, 0)
    reply = json.dumps({"facts": [
        {"fact": "User's daughter Ana lives in Leeds", "kind": "person", "subject": "person:ana",
         "attribute": "home_town", "confidence": 0.92},
        {"fact": "User's dog is called Max", "kind": "person", "confidence": 0.95, "replaces": stated.uid},
    ]})

    class ExtractLLM(LLMProvider):
        name = "x"

        async def stream(self, request):
            assert "Known facts" in request.messages[1]["content"]
            yield LLMChunk(delta=reply)
            yield LLMChunk(input_tokens=100, output_tokens=40)

    class R:
        def llm_default(self):
            return ExtractLLM(), "m"

        def llm_params(self):
            return {}

        def embedding(self):
            return None

    async def drain():
        for jid in jobs._claim(100):
            await jobs.run_job(R(), jid)

    asyncio.run(drain())
    rows = {r.content: (r.status, r.origin) for r in _rows(acc)}
    assert rows["User's daughter Ana lives in Leeds"] == ("active", "inferred")
    assert rows["User's dog is called Max"] == ("pending", "inferred")  # never overwrites a stated fact
    assert rows["User's dog is called Rex"] == ("active", "explicit")


def test_operator_diagnostics_show_memory_counts_not_content() -> None:
    from tests.test_accounts import _client

    acc = _account()
    _save(acc, "User's secret recipe uses saffron", "preference")
    op = _client()
    if op.get("/api/auth/status").json()["needs_setup"]:
        op.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    else:
        op.post("/api/auth/login", json={"username": "admin", "password": "password123"})
    r = op.get("/api/diagnostics")
    assert r.status_code == 200
    mem = r.json()["memory"]
    assert mem["enabled"] is True and mem["by_status"]["active"] >= 1 and mem["per_account_max"] >= 1
    assert "saffron" not in r.text
