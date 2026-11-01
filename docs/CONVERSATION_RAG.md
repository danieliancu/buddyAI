# Conversation RAG (on demand) — spec amendments and design

Status: in development on `feature/conversation-rag-on-demand`. Not merged, not deployed, off by default.

The task specification ("BuddyAI — On-Demand Conversation RAG (Production-Ready MVP)", 30 sections) applies in
full. This document closes the points it left open and records the design chosen against the actual code.
Where an amendment and the specification differ, the amendment wins.

## Spec amendments

**A1. Continuation turns are not ordinary turns (spec §4, §8, §11).**
A conversation has *active RAG context* for the `conversation_rag_continuation_turns` (default 2) turns that follow a
turn with sources, within the same `conversation_id`. The context lives in the device session only
(`DeviceConnection.rag_context`): knowing it is active costs no query. Only turns with active context
("continuation turns") may run RAG queries (re-read sources, re-check ownership and consent). Every other turn is
ordinary: zero new RAG queries, zero embedding calls.

**A2. "Sources actually used" means "put into the prompt" (spec §10, §11).**
The server cannot know whether the model used the context. A turn whose prompt received historical sources
(tool result or continuation context) gets `rag_derived = true` and direct references to those source turns.

**A3. Context expiry (spec §11).**
The context ends after the continuation turns, when a new conversation starts (`conversation_idle_minutes`), on
consent withdrawal, on history deletion, on ownership change and when the session closes. It is never persisted:
after a reconnect it starts empty.

**A4. Redaction representation (spec §12, §23).**
A redacted turn keeps `assistant_text = ""` and gets `turns.rag_redacted_at`. The placeholder
"Response removed because its source conversation was deleted." is rendered by the web app only, so it never
enters the model's history or the account export as fake content.

**A5. Existing watches (spec §6).**
A watch without an authorisation row is "wearer not declared, off". Nothing is indexed or searched until the
account holder declares "I wear this watch" and opts in.

**A6. Authorisation is not a `DeviceSettings` field (spec §6, §7).**
`PATCH /devices/{id}/settings` accepts any `DeviceSettings` field, so a flag stored there could be switched on
without the wearer rule. Authorisation has its own table and endpoints; device settings and `device_view()` are
untouched.

**A7. No separate job table (spec §17).**
The durable work state is "eligible turn without a vector for the current model key", plus a per-watch lease,
failure count and retry time on the authorisation row. That gives incremental indexing, controlled backfill,
restart recovery, idempotency (unique `turn_id, model_key`) and re-indexing after a model change without a queue
whose rows would also need deleting. Memory jobs are not touched.

**A8. Measured timeouts and dates (spec §13, §19, §22).**
`tools/rag_bench.py` measured the query embedding at 250-375 ms (p95 up to ~800 ms) from a laptop, so the spec's
suggested 350 ms sent most searches to word matching. On a real watch's turns, single calls took 730-810+ ms and
800 ms was still too short in 2 of 3 searches. Defaults: embedding 1200 ms, database 500 ms, whole tool 2000 ms.
These apply only when the tool runs. Requested dates narrow a search but do not exclude: the model read "about a month
ago" as a few days and found nothing, so an empty period falls back to all dates, and the result says so
(`outside_requested_dates`).

**A9. A RAG reply in the prompt's history counts as using its sources (spec §11, §14).**
A reply built from earlier conversations stays in the prompt (`history_turns`) after the continuation turns, and later
replies can repeat it. So a turn whose history holds a `rag_derived` turn is `rag_derived` too, with references to the
same original sources. Reading the flag costs nothing: those rows are already loaded. One query reads the references,
only in such a conversation, so these turns are not ordinary turns either. In effect the rest of that conversation is
neither learned from nor indexed, until the idle gap (`conversation_idle_minutes`) starts a new one.

## Design

### Data (migration 0033)

- `conversation_rag_authorizations` — one row per watch: `device_id` (PK, FK devices), `account_id`, `wearer`
  (`self`/`other`), `state` (`off`/`on`), `generation` (bumped on every change of state, wearer or owner),
  `notice_version`, `method`, `authorized_at`, `updated_at`, indexing lease/failure/retry fields.
- `conversation_rag_embeddings` — `turn_id` (FK turns **ON DELETE CASCADE**), `account_id`, `device_id`,
  `model_key`, `dims`, `text_hash`, `embedding` (pgvector / JSON on SQLite), `created_at`.
  Unique (`turn_id`, `model_key`); index (`device_id`, `account_id`, `model_key`). No text: passages are read
  from `turns`.
- `conversation_rag_sources` — (`consumer_turn_id`, `source_turn_id`), both FK turns **ON DELETE CASCADE**.
- `turns.rag_derived` (bool, default false), `turns.rag_redacted_at` (nullable).

### Flow

- **Offer:** at connect the session loads the watch's authorisation (one query). A turn offers
  `conversation_search` only when the server switch, the account allowlist and the watch's authorisation are on,
  and only in chat mode. The definition and its rule are fixed text (prompt caching).
- **Search:** account and device come from the session, never from the model. The query is embedded (timeout),
  then searched in SQL filtered by account **and** device (pgvector `<=>`), and merged by rank with word matching,
  which also covers turns without a vector and a slow or failing embedding. Consent and ownership are re-checked
  against the database before and after. Results are bounded and labelled as historical data, never instructions;
  answers from web searches are flagged as possibly outdated.
- **Follow-up:** the session keeps source turn ids for the next 2 turns of the conversation; those turns re-read the
  text under the same checks and get it as a temporary system message. A reply whose source was deleted meanwhile is
  not stored (`rag_redacted_at`).
- **Indexing:** a background loop, one watch at a time, pauses while voice turns are running. Consent is checked
  before the embedding call and again (same `generation`) in the transaction that stores the vectors; a deleted
  turn cannot be re-indexed because its foreign key rejects the row.
- **Eligible turns:** `status = completed`, `mode = chat`, `rag_derived = false`, non-empty question, watch
  authorised. Web-search turns are indexed by the question only.
- **Memory:** automatic learning skips `rag_derived` turns (the only change to memory code).
- **Deletion:** vectors and source references follow their turns by cascade on every path (history deletion,
  owner change, account deletion, retention). Owner change and account deletion also delete the authorisation
  row. Retention redacts surviving dependent answers before it deletes their sources.

## Phases and checkpoints

0. Branch and this document.
1. A+B — schema, migration, models, schema tests (SQLite + PostgreSQL with pgvector). **Checkpoint 1.**
2. C+D — indexing, search, tool, continuation, query-count invariant tests, first measurements. **Checkpoint 2.**
3. E–G — memory exclusion, retention redaction, concurrency tests, web controls, compliance docs, benchmarks,
   final report. Awaiting review: not merged, not deployed.

## Measurements

From a laptop, real OpenAI embeddings and model, local PostgreSQL + pgvector, 2,008 synthetic turns on one watch.
Reproduce with `python tools/rag_bench.py --db <localhost url>` (it wipes that database). The real watch's results are
below. **Not measured on the production network.** Samples are small: an indication, not a guarantee.

| Measure | Result |
|---|---|
| Right turn in the top 5 / first | 13 of 13 / 11 of 13 questions |
| Tool chosen for a question about the past (en, ro incl. no diacritics, de, fr, pl, el) | 10 of 10 |
| Tool chosen wrongly for an ordinary question | 0 of 10 |
| Query embedding | p50 282-358 ms, p95 727-802 ms |
| Database search, including word matching | p50 266-359 ms, p95 421-487 ms |
| First token, question that searches (two model rounds) | 3.3-4.1 s |
| First token, follow-up without a search | about 1.3 s |
| Indexing | 24-32 vectors/s for one watch |

**Ordinary turn: the tool in the request.** The definition and rule add about 135 tokens to a prompt of about 6,300,
inside the cached prefix. Same questions, first token, order rotated:

| Runs per variant | No tool, p50 / p95 | With tool, p50 / p95 |
|---|---|---|
| 42 (definition before it was shortened) | 1227 / 2829 ms | 1242 / 2495 ms |
| 42 (short definition) | as above | 1204 / 2138 ms |
| 67, tool measured twice as a control | 1469 / 2417 ms | 1422 / 2245 and 1391 / 2232 ms |

The tool was slower in about half the pairs, and the two identical variants differ from each other by 31 ms at p50.
Through the whole pipeline with real TTS, 24 runs per variant: first token p50 1438 vs 1610 ms, first audio p50 2398 vs
2500 ms, p95 2701 vs 3685 ms, leaning the other way. Earlier runs with a fixed order leaned that way too (p50 +94 to
+233 ms). **Conclusion: no systematic cost detected in the larger rotated samples; the 24-run p95 is not explained, and
small effects or occasional slow turns cannot be excluded from here.** Check it on a real watch during rollout, using
`ttfa_server_ms`, `llm_first_token_ms` and `tts_first_audio_ms`. A watch that has not opted in does not receive the tool.

**While indexing** (45 s each, backfill at 500 vectors in the window, one process):

| | Idle | Indexing |
|---|---|---|
| Event loop delay, p50 / p99 | 11 / 27 ms | 11 / 27 ms |
| Turn row write, p50 / p95 | 47 / 91 ms | 39 / 92 ms |
| Model first token, p50 | 1195 ms | 1211 ms |
| Process CPU | 3% | 7% |
| Database connections | 1 | 2 |

Not measured: many watches backfilling together on the production database. Only one batch runs at a time per process,
and indexing waits while `conversation_rag_index_pause_turns` voice turns are running.

## Real watch

2026-11-01: a real watch on a local server of this branch, with 307 synthetic turns; laptop and watch on a phone hotspot,
so production's network will differ. `tts_first_audio` and the rest come from the `turns` table.

Spoken questions, one each, first audio at the device (`ttfa_device_ms`). All six answers were right, including "nothing
found" for a topic that does not exist:

| Question | First audio | What happened |
|---|---|---|
| Holiday, about a month ago | 9.6 s | searched |
| "And what else?" | 5.8 s | follow-up, no new search |
| What the doctor said | 8.4 s | searched |
| The car | 6.1 s | answered from the sources already found |
| The cat (not in history) | 8.5 s | searched, said it found nothing |
| A joke | 5.0 s | no search |

In 2 of the 3 searches the embedding missed the 800 ms limit then in force, and word matching found the answer.

The same fixed console question, switch on and off, order rotated, indexing off, 11 runs each (24 of 24 completed, the
first pair discarded):

| | Off, p50 / max | On, p50 / max |
|---|---|---|
| First audio at the device | 4594 / 9547 ms | 4906 / 8906 ms |
| Model first token | 1968 / 6625 ms | 1750 / 6390 ms |
| TTS first audio | 1109 / 1953 ms | 1109 / 1719 ms |
| Speech to text | 1329 / 2141 ms | 1250 / 2875 ms |

The stages do not lean towards a cost, and the +312 ms in first audio is smaller than the spread between runs. With 11 runs
a difference of that size can be neither confirmed nor excluded. Two earlier runs are not counted: switching off deleted
the vectors, so "on" turns ran during a re-index, and a reply ending in a question kept the microphone open for the next one.

Moving the watch for such a test is delicate: the token is global, and the first hello to a server that does not know it
erases it. For this test the server got a device row with the same token hash, and the URL was written to and removed from
NVS, so the production pairing survived. Over USB, `esptool` sometimes could not reach the running watch; the cause was
not found, and the watch's screen froze once around those attempts until a USB reset.

## Deployment

Nothing here is enabled by deploying the code. **Before enabling for anyone:**

1. Sign the OpenAI DPA and complete the transfer assessment (`docs/compliance/processor-register.md`), and the DPIA with the questions in `docs/compliance/data-inventory.md`. Legal review has not happened.
2. Check in Coolify: the database runs `deploy/postgres.Dockerfile` (`SELECT extversion FROM pg_extension WHERE extname = 'vector'`), and migration 0022 has run. Not verified from here: no access to Coolify or production.
3. Back up the database, deploy server and website, and let migration 0033 run. It only adds tables and columns.
4. Deploy the site's privacy text together with the rollout, not before.

**Rollout**, with `BUDDYAI_OPENAI_API_KEY` set (required even under the `qwen` profile, which does not send this text to DashScope):

1. `BUDDYAI_CONVERSATION_RAG_ACCOUNTS=<your account id>`, `BUDDYAI_CONVERSATION_RAG_ENABLED=true`, `BUDDYAI_CONVERSATION_RAG_INDEXING_ENABLED=true`.
2. On the watch's Settings page: "I wear this watch", then switch on. A connected watch needs no restart. Ask about an old topic, and compare first audio with the "Real watch" figures.
3. Watch `GET /api/diagnostics` → `conversation_rag`, usage kind `embedding`, and the log lines `conversation_rag …` (never content).
4. Empty `BUDDYAI_CONVERSATION_RAG_ACCOUNTS` opens it to every account, once the checks above are done.

Cost: one vector costs about 30 tokens at $0.02 per million, roughly £0.0005 per 1,000 turns, plus about 2 KB of storage each; a question adds one more vector. On a question that searches, the real cost is the extra model round.

## Rollback

- **Stop it, keeping the data:** `BUDDYAI_CONVERSATION_RAG_ENABLED=false` (the tool, its rule and the website option go on the next turn or page load). `..._INDEXING_ENABLED=false` stops only the indexing.
- **Remove the derived data as well:** `alembic downgrade 0032`. Conversations stay, but vectors, references, choices and the two turn columns go, and a reply already redacted is not restored. Old releases do not start on a newer schema, so downgrade only with the matching image.
- A user's own switch-off deletes vectors for that watch; the kill switch does not.

## Known limitations

- Models choose the tool well in the sample measured, not in every phrasing or language; a miss means an ordinary answer without the history.
- The current conversation is excluded from the search (it is in the prompt, except beyond `history_turns`).
- Questions in another language than the conversation match less well, and distances do not separate relevant from irrelevant reliably: the model judges up to 5 passages.
- A new fact said in a RAG-dependent turn, or later in that conversation, is not learned automatically; "remember that…" still works.
- Audio already spoken cannot be recalled when a source is deleted during the reply; the stored reply is removed.
- The rest of a conversation after a search is not indexed (A9).
- Search reads vectors exactly, without an approximate index. Measured on 2,008 vectors; if one watch reaches far more, measure again.
- If retention is ever enabled, individual-conversation deletion is added, or RAG answers are kept elsewhere, revisit the redaction: it covers retention only, by design.

## Running the PostgreSQL tests

```
docker build -f deploy/postgres.Dockerfile -t buddyai-pgvector deploy
docker run -d --name buddyai-pg-rag -p 55433:5432 -e POSTGRES_USER=buddyai -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=buddyai_test buddyai-pgvector
BUDDYAI_PG_TEST_URL=postgresql+psycopg://buddyai:test@localhost:55433/buddyai_test pytest tests/pg
```
