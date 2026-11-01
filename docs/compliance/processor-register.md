# Processor register

Prepared from the code (12 October 2026). **No DPA is marked as signed: there is no evidence of any signed agreement in the repository.** Do not accept contractual terms on the business's behalf without review.

| Processor | Purpose | Data shared | Processing location | DPA status | Transfer mechanism | Sub-processors | Retention settings | Actions required |
|---|---|---|---|---|---|---|---|---|
| **OpenAI** (API) | speech-to-text (Realtime + `/audio/transcriptions` fallback), replies (Chat Completions; Responses API for web search), text-to-speech, embeddings, hosted web search | voice audio, transcripts, conversation history, notes/reminders and memories used as context, persona instructions, approximate city for search | US (not verified for this account; no data-residency setting found) | **Not signed** (deploy/README §10 lists it as a to-do) | to be confirmed: OpenAI DPA incl. UK Addendum/IDTA | per OpenAI's published list (to check) | Responses and Chat Completions sent with `store=false` on this branch (no application state); abuse-monitoring retention per OpenAI terms (not verified); Zero Data Retention **not** requested | sign/execute the OpenAI DPA; check the account's data controls; record the transfer mechanism; decide on ZDR/data residency |
| **Stripe** | checkout, subscriptions, customer portal, payments | name, email, billing/shipping address, card data (entered on Stripe only), IP at checkout | US/EU | Stripe's DPA is part of its services agreement — **acceptance not verified** | Stripe's terms (to confirm) | Stripe's list | Stripe's own retention (financial records) | confirm the Stripe account's DPA acceptance and UK transfer terms |
| **Email delivery** (SMTP provider configured via `BUDDYAI_SMTP_*`) | verification, password reset, billing notices | recipient email, message content (may include one-time links) | unknown | **unknown** | unknown | unknown | unknown | identify the provider in production; sign/accept its DPA; note: production must use `BUDDYAI_EMAIL_BACKEND=smtp` (console backend now never logs bodies on https) |
| **Hosting / VPS** (Coolify on a VPS, reported as UK/EU) | runs server, database, backups | all stored data | **not verified** (UK/EU reported) | **unknown** | depends on provider | provider's | disks, snapshots per provider | confirm provider, region and DPA; check whether provider snapshots exist and their retention |
| **Off-site backup storage** (optional, via rclone) | encrypted backup copies | encrypted dumps only (age) | depends on choice | not yet chosen | depends | depends | 30 days (script default) | choose provider (UK/EU), sign DPA, configure `BACKUP_RCLONE_REMOTE` |
| **Delivery carriers** | ship the watch | name, address, phone/email if given | UK | carrier terms | n/a | — | carrier | record which carriers are used |
| **Alibaba Model Studio (Qwen), Azure Speech** | alternative AI profile in code | — | Singapore / West Europe | n/a | n/a | — | — | **not used in production**; must be added here and to the privacy notice before enabling |

## Search Past Conversations (Conversation RAG) — not yet live

Off by default (`BUDDYAI_CONVERSATION_RAG_ENABLED=false`) and opt-in per watch; design in `docs/CONVERSATION_RAG.md`.

- **New processing by OpenAI, if enabled:** the text of a watch's completed chat turns (what the user said and the reply; only the question when the reply came from a web search) is sent to the embeddings API (`text-embedding-3-small`), and so is each search question. OpenAI is the only provider allowed for this, whatever the AI profile: `qwen` does not send this text to DashScope, and a missing key stops the feature instead of falling back.
- **Before enabling:** the OpenAI DPA above must be signed. This processing makes it more pressing, because it adds older conversations of users who opted in. Check OpenAI's retention terms for embedding requests and record them here; they have not been verified.
