"""Real-provider measurements of Conversation RAG (not part of the test suite: it uses the OpenAI key from
server/.env, costs a few pence, and its schema is wiped).

    python tools/rag_bench.py --db postgresql+psycopg://buddyai:test@localhost:55433/buddyai_test

Only a database on localhost is accepted: production is out of reach by design. TTS is mocked (nothing is spoken
or billed); the web_search tool is offered to the model but never executed.

Reports: indexing throughput, retrieval relevance and latency, which tool the model picks per language, the first
token with and without the tool in the request, and one question with a follow-up, end to end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import secrets
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))
sys.stdout.reconfigure(encoding="utf-8")

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--filler", type=int, default=2000)
ap.add_argument("--latency-runs", type=int, default=30)
ARGS = ap.parse_args()
if urlparse(ARGS.db).hostname not in ("localhost", "127.0.0.1"):
    sys.exit("refusing: rag_bench only runs on a database on localhost")
os.environ["BUDDYAI_DATABASE_URL"] = ARGS.db
os.environ["BUDDYAI_MOCK_PROVIDERS"] = "false"
os.environ["BUDDYAI_CONVERSATION_RAG_ENABLED"] = "true"
os.environ["BUDDYAI_CONVERSATION_RAG_ACCOUNTS"] = ""

# (key, days ago, what the user said, what ola said)
KNOWN = [
    ("crete", 35, "Vreau să merg în vacanță în Creta în iunie, ce îmi recomanzi?", "Îți recomand Chania și să rezervi feribotul din timp."),
    ("car", 14, "My car makes a grinding noise when I brake", "That sounds like worn brake pads. I recommend a garage this week."),
    ("project", 60, "My garden shed project starts in September, I need treated timber", "Treated pine works well; order it two weeks ahead."),
    ("doctor", 21, "Mi-a zis doctorul să fac analize la tiroidă", "Bine, fă-le dimineața, pe nemâncate."),
    ("gift", 90, "What should I get my sister Ana for her 40th birthday?", "A weekend spa voucher would suit her, you said she loves those."),
    ("book", 45, "Ich suche ein gutes Buch über die Geschichte Roms", "Ich empfehle SPQR von Mary Beard."),
    ("recipe", 7, "Cum fac o ciorbă de burtă bună?", "Fierbe burta separat și dreg-o cu smântână și gălbenuș."),
    ("phone", 120, "Should I buy the new phone or keep mine?", "Keep it another year: your battery is still fine."),
]
# (expected key, question)
QUERIES = [
    ("crete", "ce am discutat despre vacanță"),
    ("crete", "ce am discutat despre vacanta"),
    ("crete", "holiday in Greece"),
    ("car", "what did you say about my car"),
    ("car", "ce mi-ai zis despre frane la masina"),
    ("project", "my shed project"),
    ("doctor", "analize tiroida"),
    ("doctor", "what did the doctor tell me to do"),
    ("gift", "birthday present for my sister"),
    ("book", "Buch über Rom"),
    ("book", "book about ancient Rome"),
    ("recipe", "reteta de ciorba"),
    ("phone", "should I change my phone"),
]
# (language, historical?, what the user said, as a transcript would have it)
SELECTION = [
    ("en", True, "What did we discuss about my holiday last month?"),
    ("en", True, "what did you recommend about my car"),
    ("ro", True, "Ce am discutat luna trecută despre vacanță?"),
    ("ro", True, "ce am discutat despre masina luna trecuta"),
    ("ro", True, "ce mi ai recomandat acum doua saptamani"),
    ("de", True, "Was haben wir letzten Monat über meinen Urlaub besprochen?"),
    ("fr", True, "De quoi avons-nous parlé le mois dernier à propos de mes vacances ?"),
    ("pl", True, "O czym rozmawialiśmy w zeszłym miesiącu na temat moich wakacji?"),
    ("el", True, "Τι είπαμε τον προηγούμενο μήνα για τις διακοπές μου;"),
    ("en", True, "remember our conversation about the shed project"),
    ("en", False, "Tell me a joke."),
    ("en", False, "What's the capital of France?"),
    ("en", False, "What's the weather tomorrow?"),
    ("en", False, "Remind me to call Maria at five."),
    ("en", False, "How does Python work?"),
    ("ro", False, "Spune-mi o glumă."),
    ("ro", False, "Cât face 12 ori 12?"),
    ("ro", False, "Ce vreme e mâine?"),
    ("de", False, "Wie hoch ist der Mount Everest?"),
    ("fr", False, "Quelle heure est-il ?"),
]
TOPICS = ["pasta", "the football match", "my neighbour's dog", "tea", "train times", "a crossword clue", "my knee",
          "the news", "a film for tonight", "houseplants", "learning Spanish", "the bins", "a poem", "my garden",
          "my new shoes", "how tides work", "Mars", "a birthday card", "coffee machines", "walking routes"]


def pct(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1] if len(values) > 1 else values[0]


def seed():
    from sqlalchemy import text

    from app.conversation_rag import authz
    from app.db.models import Account, Turn, utcnow
    from app.db.repositories import ConversationRepo, DeviceRepo, PersonaRepo, SettingsRepo
    from app.db.session import get_engine, run_migrations, session_scope
    from app.security import hash_device_token

    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    run_migrations()
    rng = random.Random(7)
    with session_scope() as db:
        PersonaRepo(db).seed()
        acc = Account(email=f"bench-{secrets.token_hex(3)}@example.com")
        db.add(acc)
        db.commit()
        db.refresh(acc)
        acc_id = acc.id
        dev = "bench-watch"
        DeviceRepo(db).pair(dev, hash_device_token("t"), "Watch", "t", "t", acc_id)
        SettingsRepo(db).ensure(dev)
        authz.set_choice(db, acc_id, dev, "self", True)
        db.commit()
        conv = ConversationRepo(db).current(dev, 30)
        now = utcnow()
        ids = {}
        rows = []
        for i in range(ARGS.filler):
            topic = rng.choice(TOPICS)
            rows.append(Turn(device_id=dev, account_id=acc_id, conversation_id=conv.id, session_id="s", turn_no=i,
                             language="en", status="completed", user_text=f"Tell me something about {topic} ({i})",
                             assistant_text=f"Here is a short thought about {topic}, number {i}.",
                             created_at=now - timedelta(days=rng.randint(1, 180), minutes=i)))
        db.add_all(rows)
        db.commit()
        for key, days, user, reply in KNOWN:
            t = Turn(device_id=dev, account_id=acc_id, conversation_id=conv.id, session_id="s", turn_no=1, language="en",
                     status="completed", user_text=user, assistant_text=reply, created_at=now - timedelta(days=days))
            db.add(t)
            db.commit()
            db.refresh(t)
            ids[key] = t.id
        current = ConversationRepo(db).current(dev, 30).id
    return acc_id, dev, ids, current


async def index_all() -> dict:
    from app.conversation_rag import index
    from app.conversation_rag.embed import provider

    p = provider()
    started = time.monotonic()
    total = batches = 0
    while True:
        n = await index.index_once(p)
        if n == 0:
            break
        total += n
        batches += 1
    secs = time.monotonic() - started
    return {"vectors": total, "seconds": round(secs, 1), "per_second": round(total / secs, 1), "batches": batches,
            "model": p.model_key}


async def relevance(acc: int, dev: str, ids: dict) -> dict:
    from app.conversation_rag import authz, search
    from app.db.session import session_scope

    with session_scope() as db:
        gen = authz.access(db, acc, dev).generation
    top1 = top5 = 0
    embed_ms, db_ms, rows = [], [], []
    for key, q in QUERIES:
        out = await search.search(acc, dev, gen, q)
        got = [h.turn_id for h in out.hits]
        top1 += got[:1] == [ids[key]]
        top5 += ids[key] in got
        embed_ms.append(out.embed_ms)
        db_ms.append(out.db_ms)
        rows.append({"q": q, "rank": got.index(ids[key]) + 1 if ids[key] in got else None, "path": out.path})
    return {"queries": len(QUERIES), "top1": top1, "top5": top5, "embed_ms_p50": pct(embed_ms, 50),
            "embed_ms_p95": pct(embed_ms, 95), "db_ms_p50": pct(db_ms, 50), "db_ms_p95": pct(db_ms, 95), "rows": rows}


def _request(acc: int, dev: str, conv: int, text: str, with_tool: bool):
    from app.conversation_rag import tool
    from app.device_settings import DeviceSettings
    from app.items import AssistantTools
    from app.pipeline.conversation import build_messages_from_db
    from app.pipeline.turn import TurnContext
    from app.providers.llm.base import LLMRequest
    from app.search import SEARCH_RULE, SEARCH_TOOL

    s = DeviceSettings()
    turn = TurnContext(1, "bench", dev, "auto", s, 16000, account_id=acc, conversation_id=conv)
    turn.user_text = text
    messages = build_messages_from_db(turn, text)
    tools_obj = AssistantTools()
    rules = [SEARCH_RULE, *tools_obj.rules(acc), *([tool.RULE] if with_tool else [])]
    messages[0] = {**messages[0], "content": "\n".join([messages[0]["content"], *rules])}
    tools = [*tools_obj.definitions(acc), SEARCH_TOOL, *([tool.TOOL] if with_tool else [])]
    return LLMRequest(messages=messages, model="", max_tokens=200, params={}, web_search=None, tools=tools)


async def selection(router, acc: int, dev: str, conv: int) -> dict:
    from dataclasses import replace

    from app.conversation_rag import tool
    from app.device_settings import DeviceSettings

    llm, model = router.llm(DeviceSettings())
    rows = []
    for lang, historical, text in SELECTION:
        req = replace(_request(acc, dev, conv, text, True), model=model, params=router.llm_params())
        picked: list[str] = []
        for attempt in range(3):  # a dropped connection is not a tool choice
            try:
                async for chunk in llm.stream(req):
                    if chunk.tool_calls:
                        picked = [c.name for c in chunk.tool_calls]
                break
            except ProviderError:
                await asyncio.sleep(2)
        rows.append({"lang": lang, "historical": historical, "text": text, "tools": picked})
    hist = [r for r in rows if r["historical"]]
    norm = [r for r in rows if not r["historical"]]
    return {"historical_selected": sum(tool.NAME in r["tools"] for r in hist), "historical": len(hist),
            "ordinary_false_activation": sum(tool.NAME in r["tools"] for r in norm), "ordinary": len(norm), "rows": rows}


async def first_token(router, acc: int, dev: str, conv: int) -> dict:
    """The same ordinary question with and without the tool, alternating."""
    from dataclasses import replace

    from app.device_settings import DeviceSettings

    llm, model = router.llm(DeviceSettings())
    times: dict[bool, list[float]] = {True: [], False: []}
    failed = 0
    for i in range(ARGS.latency_runs * 2):
        with_tool = bool(i % 2)
        req = replace(_request(acc, dev, conv, "Tell me a short joke.", with_tool), model=model,
                      params=router.llm_params())
        started = time.monotonic()
        first = None
        try:
            async for chunk in llm.stream(req):
                if chunk.delta and first is None:
                    first = (time.monotonic() - started) * 1000
        except ProviderError:
            failed += 1
            continue
        if first is not None and i >= 2:  # the first pair warms the connection and the prompt cache
            times[with_tool].append(first)
    return {"failed_runs": failed, **{k: {"runs": len(v), "p50_ms": round(pct(v, 50)), "p95_ms": round(pct(v, 95))}
                                      for k, v in (("with_tool", times[True]), ("without_tool", times[False]))}}


class Router:
    """The real model; a silent TTS; no web search."""

    def __init__(self, real) -> None:
        self.real = real

    def llm(self, s):
        return self.real.llm(s)

    def llm_params(self):
        return self.real.llm_params()

    def tts(self, language, s):
        from app.providers.mock import MockTTS

        return SimpleNamespace(provider=MockTTS(first_audio_s=0), voice="v", instructions=None)

    def web_search(self, s, llm):
        return None


async def end_to_end(real, acc: int, dev: str) -> list[dict]:
    from app.conversation_rag import authz, context
    from app.db.repositories import ConversationRepo, TurnRepo
    from app.db.session import session_scope
    from app.device_settings import DeviceSettings
    from app.gateway.device_ws import DeviceConnection
    from app.items import AssistantTools
    from app.pipeline.chunker import ChunkerConfig
    from app.pipeline.conversation import ConversationPipeline
    from app.pipeline.turn import TurnContext, TurnResult

    class IO:
        async def send(self, turn, type_, **fields):
            return True

        async def send_audio(self, turn, packet):
            return True

    pipeline = ConversationPipeline(Router(real), ChunkerConfig(), tools=AssistantTools())
    with session_scope() as db:
        access = authz.access(db, acc, dev)
        from app.db.models import Conversation

        conv = Conversation(device_id=dev)  # a new conversation: everything earlier is out of the prompt
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conv_id = conv.id
    out, ctx = [], None
    for i, text in enumerate(["Ce am discutat despre vacanță acum vreo lună?", "Și ce altceva mi-ai mai recomandat atunci?"]):
        with session_scope() as db:
            row = TurnRepo(db).create(device_id=dev, account_id=acc, conversation_id=conv_id, session_id="e2e",
                                      turn_no=i, language="ro")
            db_id = row.id
        turn = TurnContext(i, "e2e", dev, "ro", DeviceSettings(timezone="Europe/Bucharest"), 16000, account_id=acc,
                           conversation_id=conv_id, rag=access, rag_context=ctx, db_id=db_id)
        turn.user_text = text
        started = time.monotonic()
        await pipeline._reply(turn, IO())
        total = (time.monotonic() - started) * 1000
        stored = DeviceConnection._persist(None, turn, TurnResult(status="completed"))
        ctx = context.after_turn(ctx, conv_id, turn.rag_found, stored, 2)
        out.append({"question": text, "answer": turn.assistant_text, "tools": turn.tools_used,
                    "rag_derived": turn.rag_derived, "sources": len(turn.rag_sources),
                    "first_token_ms": turn.marks.llm_first_token - turn.marks.llm_request,
                    "total_ms": round(total)})
    return out


async def main() -> None:
    global ProviderError
    from app.config import load_providers_config
    from app.providers.base import ProviderError
    from app.providers.router import ProviderRouter
    from app.config import get_settings

    acc, dev, ids, conv = seed()
    report: dict = {"filler_turns": ARGS.filler, "known_turns": len(KNOWN)}
    report["indexing"] = await index_all()
    report["relevance"] = await relevance(acc, dev, ids)
    real = ProviderRouter(get_settings(), load_providers_config())
    report["selection"] = await selection(real, acc, dev, conv)
    report["first_token"] = await first_token(real, acc, dev, conv)
    report["end_to_end"] = await end_to_end(real, acc, dev)
    print(json.dumps(report, ensure_ascii=False, indent=1))


asyncio.run(main())
