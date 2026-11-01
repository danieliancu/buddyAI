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
  then searched in SQL filtered by account **and** device (pgvector `<=>`), with word matching as fallback.
  Consent and ownership are re-checked against the database first. Results are bounded and labelled as historical
  data, never instructions; answers from web searches are flagged as possibly outdated.
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
   final report.

## Running the PostgreSQL tests

```
docker build -f deploy/postgres.Dockerfile -t buddyai-pgvector deploy
docker run -d --name buddyai-pg-rag -p 55433:5432 -e POSTGRES_USER=buddyai -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=buddyai_test buddyai-pgvector
BUDDYAI_PG_TEST_URL=postgresql+psycopg://buddyai:test@localhost:55433/buddyai_test pytest tests/pg
```
