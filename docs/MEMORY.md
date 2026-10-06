# Long-term memory

Operator and developer reference. Code: `server/app/memory/`. Rollout steps: `deploy/README.md` section 9.

## What it does

- **Remember on request.** "Remember that my granddaughter is called Maria" → the model calls the
  `memory_save` tool. The server checks the text, stores one fact and commits it. Only then does the tool
  report `ok`, so the assistant never says "I'll remember" for something that was not saved.
- **Recall.** In every chat turn, the account's relevant memories go into the prompt as data.
- **Inspect, correct, forget.** By voice (`memory_list`, `memory_change`, `memory_forget_all` with a spoken
  yes) and on the web Memory page (edit, confirm, forget, "forget everything" with the account password).
- **Learn (on by default, the customer can switch it off).** After a conversation goes quiet, one background model call may propose up to 3 lasting
  facts. The server accepts only safe, confident ones.

Conversation history (`conversations`, `turns`) is untouched: it stays the archive. `history_turns` and
`conversation_idle_minutes` are prompt limits, not retention.

## Data

| Table | Purpose |
|---|---|
| `memories` | One atomic fact per row. Owner `account_id` (every query filters on it); optional `device_id` = only that watch's wearer. `kind` (profile, person, preference, routine, goal, project, other), `origin` (explicit, inferred, web), `status` (active, pending = learned and waiting for the user, superseded = replaced by a correction), `sensitivity` (normal, special), optional `subject` + `attribute` (a new value for the same key supersedes the old one), `supersedes_id`, `source_turn_id`, `valid_until`, `confidence` (learned only), `confirmed_at`. |
| `memory_embeddings` | Vectors (migration 0022). One row per memory and `model_key` (`provider:model:dims`); `text_hash` says which text it was made from. Searches only compare vectors of the current `model_key`. PostgreSQL: pgvector `vector`, exact `<=>` search (an account has at most `BUDDYAI_MEMORY_MAX_ACTIVE` = 300 memories, so no approximate index). SQLite: JSON, searched in Python. |
| `memory_jobs` | Durable background work: `embed` and `extract`. A job is claimed with a lease (as usage operations do), retried with backoff and ends as `dead` after 6 failures. Errors are stored as a class name only. |
| `accounts.memory_explicit` / `memory_use` / `memory_learn` | The customer's three separate switches: "Remember what I ask" (default on: `memory_save` is offered), "Use my memories in conversations" (default on: recall), "Learn from conversations" (default on; the customer can switch it off). With saving off, listing / correcting / forgetting still work and the assistant says saving can be switched on in the app; with using off, memories are kept but never put into conversations. |

Forgetting deletes the row, its earlier versions, its vectors and its jobs. Superseded versions are purged
after 90 days. There are no `ON DELETE` rules in this schema: deleting turns or conversations first clears
the memory links (`MemoryRepo.detach_turns` / `detach_conversations`).

## Rules

**Never stored**, whatever is asked (deterministic, `app/memory/policy.py`): payment card numbers (Luhn
check), IBANs, passwords / PINs / codes, secret keys, identity document numbers.

**Special categories** (health, religion, politics, sexuality, ethnicity, union membership, criminal record):
- kept only when the user explicitly asks, marked `special` and shown as "Sensitive" on the Memory page;
- never learned by inference.

**Learning:**
- Only these kinds can be learned: profile, person, preference, routine, goal, project.
- Confidence 0.85 or more → active; 0.6 to 0.85 → pending (the user confirms it on the web); below that →
  dropped.
- At most 3 per conversation.
- A learned change to a fact the user stated or confirmed is always pending.
- Learning is admitted against the account's allowance (operation kind `memory`). If it is refused, it is
  skipped.

**Recall** (`app/memory/retrieve.py`):
1. **Small set.** 30 facts or fewer, and 2,500 characters or fewer: all of them go in, with no embedding call.
2. **Vector search.** When `BUDDYAI_MEMORY_VECTOR_RETRIEVAL` is on, the question is embedded with a 350 ms
   timeout. The search returns the top 8 within distance 0.55, plus up to 4 profile facts. Facts that have no
   vector yet are matched by words.
3. **Word matching.** Used otherwise, or if anything above fails.
4. **Nothing.** If even that fails, no memories go in. The turn always continues.

**Prompt placement.** Memories go in one system message just before the user's sentence, as a JSON list with
a header that says they are data and never instructions. The cached system prompt and the history stay
byte-identical.

## Switches (environment)

| Variable | Default | Effect |
|---|---|---|
| `BUDDYAI_MEMORY_ENABLED` | true | Tools, recall, Memory page, job loop (false = off for everyone) |
| `BUDDYAI_MEMORY_ACCOUNTS` | empty | Comma-separated account ids during rollout (empty = all) |
| `BUDDYAI_MEMORY_EMBEDDINGS_ENABLED` | false | Background vectors (needs migration 0022 / pgvector) |
| `BUDDYAI_MEMORY_VECTOR_RETRIEVAL` | false | Semantic recall above the small-set size |
| `BUDDYAI_MEMORY_INFERENCE_ENABLED` | true | Learning (each account can switch it off) |
| `BUDDYAI_MEMORY_MAX_ACTIVE` | 300 | Active memories per account |
| `BUDDYAI_MEMORY_EMBED_TIMEOUT_MS` | 350 | Query embedding budget inside a turn |
| `BUDDYAI_MEMORY_MAX_DISTANCE` | 0.55 | Relevance cut-off (cosine distance) |

The embedding model is set per AI profile: the `embedding` section of `server/config/providers.*.json`.
- `openai` profile: `text-embedding-3-small`, 512 dimensions.
- `qwen` profile: `text-embedding-v4`, 512 dimensions, through DashScope's OpenAI-compatible endpoint. Check
  this one with a live key before enabling it.

Changing the model or size creates a new `model_key`: the job loop re-embeds everything in the background.
Until it finishes, recall uses word matching.

## Costs

- **Database.** About 0.5 KB per memory and about 2 KB per 512-dimension vector.
- **Embeddings.** About 20 tokens per memory and per question (questions are embedded only above the
  small-set size). At about $0.02 per million tokens, this is negligible.
- **Prompt (the real cost).** Recalled memories add about 150 to 600 uncached input tokens to each chat
  turn. The tool definitions add about 400 tokens to the cached prefix.
- **Learning.** About 1.5k input and 150 output tokens per conversation (on by default).

Measure the real numbers in **Usage** (kinds `embedding`, `memory`) and the time to first audio in
**Diagnostics**.

## Observability

- **Logs.** `memory save|retrieve|forget|extract|job …` lines carry the account id, path, counts and
  timings, never memory text. Memory tool arguments are not logged.
- **`GET /api/diagnostics` → `memory`.** Switches, counts by status, memories per account (median and max),
  and job counts by kind and state.
