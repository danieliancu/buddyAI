# Retention policy (proposal) and data subject requests

The periods below are **proposals that need business and legal approval**. They are implemented centrally in `server/app/retention.py` (`RetentionPolicy.from_settings`) with settings in `server/app/config.py`; defaults equal the behaviour before this branch, so nothing new is deleted until a value is changed. The job `retention_loop` runs hourly (first run 60 s after start), each step in its own transaction, counts-only logging, a PostgreSQL advisory lock so only one instance runs it.

| Data | Setting (env `BUDDYAI_…`) | Default / behaviour | Proposal for approval | Where enforced | Tests |
|---|---|---|---|---|---|
| Conversations (turns) | `CONVERSATION_RETENTION_DAYS` | 0 = until the user deletes them or the account | keep 0, or an automatic limit (e.g. 2 years) once agreed | `retention.run_retention_once` | `tests/test_retention.py` |
| Long-term memory | — | until forgotten / cleared / account deleted | keep | memory API, `delete_account` | `test_memory.py` |
| Superseded memory versions | `MEMORY_SUPERSEDED_DAYS` | 90 days | keep | `memory/jobs.py` housekeeping | `test_retention.py` |
| Finished memory jobs | `MEMORY_JOBS_DAYS` | 7 days | keep | `memory/jobs.py` | `test_retention.py` |
| Notes & reminders | — | until deleted | keep | items API, `delete_account` | existing tests |
| Voice operation log | `VOICE_OPS_RETENTION_DAYS` | 7 days (**now applied**; previously never purged) | keep | retention job | `test_retention.py` |
| Web search cache | `SEARCH_CACHE_GRACE_DAYS` | expiry + 1 day; account-scope rows deleted with the account | keep | retention job, `delete_account` | `test_retention.py`, `test_account_deletion.py` |
| Diagnostics / incidents | `INCIDENT_RETENTION_DAYS` | 90 days | keep | `incidents/service.py` | existing |
| Audit log | `AUDIT_RETENTION_DAYS` | 0 = keep | proposal: 2 years, longer for account-deletion records | retention job | `test_retention.py` |
| Application logs | hosting/Docker log rotation | **not controlled by the application; not verified** | 30 days | Coolify / Docker log driver settings | — (external) |
| Backups | `BACKUP_LOCAL_KEEP_DAYS`, `BACKUP_REMOTE_KEEP_DAYS` | 14 days local; 30 days off-site (when enabled) | keep | `deploy/backup/backup.sh` | Docker test |
| Orders, subscriptions, top-ups, revenue, billing consents (incl. IP + user agent) | — | **kept after account deletion** | 6 years from the end of the financial year (UK tax records) — to confirm with an accountant; consider minimising IP/UA after the consent dispute window | not deleted | `test_account_deletion.py` (kept) |
| Usage records | — | kept, unlinked from conversation content | as billing records | `delete_account`, history delete | existing |
| OpenAI copies | provider | Chat Completions / Responses sent with `store=false`; abuse-monitoring retention per OpenAI terms (not verified) | per DPA | `providers/llm/openai.py` | `tests/test_openai_store.py` |

## Account deletion (what happens)

`DELETE /api/me` (password required) → `accounts.delete_account`:
1. Cancels every live Stripe subscription immediately (`billing.cancel_all_for_account`). If Stripe fails, **nothing is deleted** and the customer sees "We couldn't cancel your olácare subscription, so nothing was deleted…" (HTTP 502).
2. Deletes conversations, memories (and embeddings/jobs), personas, notes/reminders, auth tokens, device settings, account-scope search cache, voice operations, usage notices; unpairs and revokes watches; anonymises the account row (`deleted-<id>@deleted.invalid`, name/password/country cleared).
3. Writes `account.delete` to the audit log in the same transaction.
4. Keeps billing records (above). Webhooks no longer email deleted accounts.
Data remains in backups until they expire (14/30 days); after any restore, deletions made after the backup must be re-applied (deploy/README.md §8.5).

## Data subject requests

- **Self-service:** download data (`GET /api/me/export`, now including orders, subscriptions, consents, top-ups, monthly usage and the account's events; audited `account.export`), delete history, memory controls (switch off, forget, clear — audited `memory.clear`), edit profile, delete account.
- **By email** (ola@olacompanion.com): verify identity (reply from the account email or confirm a code sent to it), log the request, respond **within one month** (extendable by two months for complex requests, with notice). Record each stage with `POST /api/accounts/{id}/requests` (`kind`: access/erasure/rectification/restriction/objection/portability/other; `stage`: received/identity_verified/completed/refused/extended; short note without personal data) — written to the account's audit trail as `dsar.<kind>.<stage>` (test: `test_support_records_a_data_subject_request_in_the_audit_trail`). Exceptions to explain: billing records under legal retention, backups until expiry.
- **Rectification:** profile edit; other corrections by email.
- **Objection / restriction / withdrawal of consent:** memory can be switched off; other requests handled manually and logged.
- Children: the service is for adults; requests from parents handled case by case.
