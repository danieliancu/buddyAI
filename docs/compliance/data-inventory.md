# Personal data inventory

Status: working document, prepared from the code on branch `feature/uk-compliance` (12 October 2026). Lawful bases are **proposals** for review by a qualified adviser; nothing here is legal advice or a statement that processing is compliant.

Legend for verification: **Code** = confirmed in the repository; **Config** = depends on production configuration; **External** = needs confirmation outside the code.

## Data flow (summary)

```
Watch (button/mic) --TLS*--> device gateway (server/app/gateway/device_ws.py)
   --> speech-to-text: OpenAI (providers/stt/openai.py)
   --> reply: OpenAI language model (providers/llm/openai.py), with history, notes/reminders, memories
       (+ optional OpenAI hosted web search with approximate city)
   --> speech: OpenAI text-to-speech (providers/tts/openai.py) --> watch
   --> stored: turns (text), memories, items, usage records (PostgreSQL)
Customer web app (HTTPS) --> /api/me/* --> PostgreSQL ; Stripe Checkout / portal for payments
* release firmware only enforces wss:// (see psti-assessment.md)
```

## Categories

| # | Category | Data | Purpose | Proposed lawful basis | Storage | Recipients / processors | Transfers | Retention | Deletion | Verification |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Account | email, name, country, password hash (bcrypt), email-verified flag, created/last login | provide the account | contract | `accounts` table | hosting | none known | until account deletion; row kept anonymised (`deleted-<id>@deleted.invalid`) | `accounts.delete_account` | Code |
| 2 | Authentication | session cookie (signed), one-time email tokens (hash only), rate-limit counters (memory) | security | legitimate interests / contract | `auth_tokens` (hashes), process memory | — | — | tokens expire (48 h verify, 1 h reset, 7 d set-password) | deleted with account | Code |
| 3 | Device identifiers | device id (`buddy-<MAC>`), device token **hash**, pairing code (5 min), last IP, battery, RSSI, firmware version | operate the watch | contract | `devices` | hosting | — | while paired; revoked + unlinked on deletion | unpair / account deletion | Code |
| 4 | IP address & browser at billing consent | IP, user agent, consent version, timestamp | evidence of consent to recurring billing | legal obligation / legitimate interests (evidence) | `billing_consents` | — | — | **kept after account deletion** (period to be approved; proposal: 6 years with tax records) | not deleted automatically | Code; period External |
| 5 | Conversations | transcripts of what the user said, assistant replies, search notes | provide the assistant; let the user review history | contract | `turns`, `conversations` | OpenAI (processing) | US (OpenAI) | until the user deletes history or the account; optional automatic limit `conversation_retention_days` (default 0 = off) | history delete, unpair, account deletion | Code |
| 6 | Voice audio | microphone audio (Opus) during a turn | speech-to-text | contract | **not stored** on our servers (memory only) | OpenAI | US | none on our side; provider retention per OpenAI terms | n/a | Code; provider External |
| 7 | Long-term memory | facts the user asks olá to remember or that are learnt from conversations (on by default), embeddings | personalised answers | contract / legitimate interests (to review) | `memories`, embeddings, memory jobs | OpenAI (embeddings, extraction) | US | until forgotten / memory cleared / account deleted; superseded versions purged after 90 days | per item, clear all, account deletion | Code |
| 8 | Notes & reminders | text, times, place, participants | features requested by the user | contract | `items` | OpenAI (processing when used in conversation) | US | until deleted | user or account deletion | Code |
| 9 | Web search | query text, approximate city (from the watch's time zone), cached answer | answer current-events questions | contract / legitimate interests | `search_cache` (shared scope for general topics; account scope otherwise) | OpenAI hosted web search | US | cache expiry + 1 day; account-scope rows deleted with the account | retention job, account deletion | Code |
| 10 | Personas & settings | custom instructions, voice, language, watch settings | configure the assistant | contract | `personas`, `device_settings` | OpenAI (instructions sent with requests) | US | until changed/deleted | account deletion | Code |
| 11 | Billing | Stripe customer/subscription/payment-method IDs, order (shipping name/address, amounts), top-ups, revenue events | sell and bill | contract; legal obligation (tax records) | `orders`, `subscriptions`, `topups`, … | Stripe | US/EU (Stripe) | tax/accounting period (proposal: 6 years; to be approved) | not deleted on account deletion (legal retention) | Code; period External |
| 12 | Usage | usage records (counts, cost, no content after unlinking) | allowance and billing | contract / legitimate interests | `usage_records`, `usage_operations` | — | — | kept for accounting (unlinked from content) | turn link nulled on deletion | Code |
| 13 | Voice operation log | summary/result of changes made by voice | undo/confirmation of voice edits | contract | `voice_operations` | — | — | 7 days (job now applied) | retention job; account deletion | Code |
| 14 | Diagnostics | technical incidents (no conversation content), device issues | reliability | legitimate interests | `diag_incidents`, `device_issues` | — | — | 90 days | retention job | Code |
| 15 | Security / audit log | actor, action, account/device id, minimal detail | accountability, security | legitimate interests / legal obligation | `audit_log` | — | — | kept (`audit_retention_days`=0); proposal in retention-policy.md | not deleted with account | Code; period External |
| 16 | Application logs | operational events; personal content being removed (see security-controls.md) | operations, security | legitimate interests | container logs (Coolify/Docker) | hosting | — | set by the hosting/Docker log configuration — **not verified** | log rotation | Config/External |
| 17 | Backups | full database dumps | recovery | legitimate interests | VPS volume (14 days); optional encrypted off-site copy (30 days) | hosting; off-site storage provider when enabled | depends on provider | 14 days local, 30 days off-site (when enabled) | automatic expiry | Code (scripts); production External |
| 18 | Customer support | emails exchanged with ola@olacompanion.com | support | contract / legitimate interests | email provider mailbox | email provider | unknown | not defined | manual | External |
| 19 | Emails sent | recipient, subject, body (transactional) | account and billing notices | contract | SMTP provider; never logged in production after this change | email provider | unknown | provider's retention | — | Code; provider External |

## Items needing extra review

- **Sensitive conversations / special-category data (Art. 9 UK GDPR):** users may speak about health, beliefs, etc. Memory extraction does not learn sensitive facts automatically (`app/memory/policy.py`); conversations are stored as said. A DPIA is recommended (external action).
- **Children:** the privacy notice says the service is for adults (18+); site imagery and real use by family members should be reviewed. No age verification exists.
- **Approximate location** sent with web search: disclosed in the privacy notice (branch); consider a setting to turn it off.
- **Qwen/Azure providers** exist in code as an alternative profile but are not used in production (`BUDDYAI_AI_PROFILE` default `openai`); if ever enabled, the processor register and privacy notice must change first.
