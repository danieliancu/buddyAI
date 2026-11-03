# Operator access to customer content: audit

Audit of every path by which an operator could read what a customer said, and the decision for each. Done 3 November 2026 by reading the code and by an automated sweep. This is an engineering record, not a legal opinion.

**Decision:** customers see, export and delete their own history. Operators see statistics and technical data by default. Exceptional access is limited to the principal administrator, one conversation at a time, with a reason, password and two-factor code, and every attempt is audited. The block is in the backend.

## Evidence

`server/tests/test_content_access.py` seeds a marker into turns, notes, memories, the search cache and voice operations, then requests every operator GET route in the OpenAPI schema as `support`, `admin` and `principal`. It fails if the marker appears. Routes with other id parameters (incidents, orders and similar) are not requested with real ids; they are covered by reading the code below.

## Paths

| Path | Who could read content before | Decision | Status |
|---|---|---|---|
| `GET /api/devices/{id}/conversations` | any operator, incl. `support` | metadata only; words, search notes and failure messages removed | closed, tested |
| `WS /api/live` (`turn_end`) | any operator, live | `user_text` and `assistant_text` removed for operator listeners in `Hub.publish`; customers keep them on `/api/me/live` | closed, tested |
| `POST /api/devices/{id}/conversations/{conversation_id}/reveal` | new | principal only; one conversation; reason, password, TOTP; limit 10 per 24 h; audited | exceptional route, tested |
| `DELETE /api/devices/{id}/conversations` | any operator | replaced by `POST .../conversations/delete` with the same checks | closed, tested |
| `GET /api/diagnostics` (`recent[].error`) | any operator; could quote provider output | fixed text for failures; abort reasons are fixed strings | closed, tested |
| `GET /api/accounts/{id}`, `/audit` | no conversation fields | no change; reasons typed by the principal do appear in the audit log, which only `admin`/`principal` can read in full | checked, tested |
| `GET /api/incidents*`, `/api/issues`, live `incident` events | technical events only (`reason`, `detail`) | no change. Server exceptions are stored as class names only (`_exc_detail`), not messages | checked by reading |
| `Memory`, `Item` (notes, reminders) | customer routes only; operator routes return counts | no change | tested |
| `SearchCache.query`, `.answer` | no API route reads them | no change | tested |
| `VoiceOperation.summary` | no API route reads it; it holds up to 250 characters of tool arguments | no change; removed by the retention job | tested; direct database access still sees it |
| `Turn.search_note`, `Turn.error` | see first and diagnostics rows | hidden | tested |
| Account export `GET /api/me/export` | the account owner only; no operator variant exists | no change | checked by reading |
| Application logs | designed not to log content: searches log length, tools log argument shape, note and reminder errors log the type only | no change | see open points |
| Email bodies in logs (`email.py`) | only with the `console` backend when `console_bodies()` allows; withheld on https | no change; production must use SMTP (`external-actions.md` #4) | open, external |
| `server/tools/search_report.py` | printed what the customer asked | hidden unless `--show-text` | closed; needs database access anyway |
| `server/tools/cost_report.py`, `bench_turn.py` | read text from a database file to estimate sizes or run a local benchmark | no change; `cost_report.py` prints no customer text | needs database access anyway |
| Direct database, backups, server shell | whoever administers the server | cannot be closed in the application; disclosed in the privacy notice | residual risk |

## Open points

- Provider exception text is logged in a few places (`pipeline/conversation.py`), for example STT retries. A provider could in principle quote input in an error. Real production logs have not been reviewed.
- Log retention sits outside the application (`retention-policy.md`).
- `Turn.error` keeps the original failure message in the database; it is shown only through `/reveal`.
- The role and the daily limit protect the application only. Choose as principal a person who has a reason to hold it, and review `content.reveal` and `content.access_refused` in the audit log.
- Legal review of the privacy notice wording is still outstanding (`external-actions.md` #15).
