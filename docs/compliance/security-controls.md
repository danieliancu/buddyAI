# Security controls

Status values: IMPLEMENTED_AND_VERIFIED · IMPLEMENTED_NOT_VERIFIED · PARTIALLY_IMPLEMENTED · NOT_IMPLEMENTED · REQUIRES_EXTERNAL_ACTION · NOT_APPLICABLE. "Branch" = on `feature/uk-compliance`, not deployed.

| Control | Implementation | Evidence | Test evidence | Status | Outstanding action | Owner |
|---|---|---|---|---|---|---|
| HTTPS, HTTP→HTTPS redirect, HSTS, security headers (site, app, api) | Coolify/Traefik + app headers | live `curl -I` checks 12 Oct 2026; `server/app/main.py` headers | manual | IMPLEMENTED_AND_VERIFIED | — | — |
| Watch ↔ server TLS (wss only, CA bundle) | release firmware only | `firmware/sdkconfig.release`, `protocol_client.c:1355` | code review | IMPLEMENTED_NOT_VERIFIED (units run dev firmware) | ship release firmware | Engineering |
| Server ↔ PostgreSQL transport | internal Docker network, no TLS | compose `DATABASE_URL` | — | NOT_APPLICABLE on one host (no TLS claimed) | revisit if the DB moves off-host | — |
| Server ↔ providers (OpenAI, Stripe, SMTP) | HTTPS / WSS / SMTP STARTTLS setting | provider clients | — | IMPLEMENTED_NOT_VERIFIED (SMTP depends on config) | confirm SMTP TLS in production | Operator |
| Customer passwords | bcrypt, min 8 chars, constant-time login | `app/security.py`, `accounts.py` | existing tests | IMPLEMENTED_AND_VERIFIED (code + prod = master) | — | — |
| Customer sessions | signed cookie, HttpOnly, SameSite=Strict, Secure on https, version-based revocation | `main.py`, `api/me.py` | existing | IMPLEMENTED | — | — |
| Rate limiting | login/signup/reset, in memory per process | `app/ratelimit.py` | existing | PARTIALLY_IMPLEMENTED (not shared across instances) | move to DB/Redis if scaling out | Engineering |
| Device auth | random token, hash stored; 6-digit pairing code 5 min; revocation | `security.py`, `hub.py` | existing | IMPLEMENTED | — | — |
| **Operator roles** (admin / support) | backend `require_role("admin")` on keys, firmware/OTA, billing settings, pricing, complimentary/allowance, incident/issue delete, audit log | `app/security.py` (role matrix), routers | `tests/test_operator_security.py` | IMPLEMENTED (branch) | assign roles after deploy | Operators |
| **Operator MFA (TOTP)** | pyotp; secret Fernet-encrypted (key HKDF from `data/secret.key`); 10 single-use recovery codes (bcrypt); `BUDDYAI_ADMIN_MFA_REQUIRED` default false | `app/mfa.py`, `api/auth.py`, migration 0029 | `test_operator_security.py` | IMPLEMENTED (branch), **not enforced** | enrol all operators, then enforce | Operators |
| Operator session revocation | `session_version` checked on every request; bumped on role/MFA/password/disable/sign-out-everywhere | `security.py` | tests | IMPLEMENTED (branch) | — | — |
| Operator account management | CLI only (create, role, reset password/MFA, disable) | `app/cli.py` | tests | IMPLEMENTED (branch) | — | — |
| Audit log | operator login/failed login, MFA, role, account create/status, history view/delete, account delete/export, memory clear, keys change (names only), firmware upload/OTA, incident clear, billing actions; viewer `GET /api/audit` (admin only) | `accounts.audit()`, `api/audit.py`, `web/src/pages/AuditPage.tsx` | tests | IMPLEMENTED (branch) | approve audit retention | Business owner |
| Sensitive data in logs | tool calls log keys/lengths only; search logs length; provider/Stripe errors reduced to type/status/code; email bodies withheld on https; redaction filter for `token=`, `code=`, `session_id=`, `key=`, `password=`, `secret=`, `Bearer` | `pipeline/conversation.py`, `search.py`, `providers/base.py`, `email.py`, `app/logging_redact.py` | `tests/test_log_redaction.py` | IMPLEMENTED (branch) | check real uvicorn access log after deploy | Operator |
| Email backend in production | console backend never logs bodies on https and warns; must be `smtp` | `email.py` | tests | REQUIRES_EXTERNAL_ACTION | set `BUDDYAI_EMAIL_BACKEND=smtp` | Operator |
| OpenAI application-state storage | `store=false` on Chat Completions and Responses API | `providers/llm/openai.py` | `tests/test_openai_store.py` | IMPLEMENTED (branch) | DPA, abuse-monitoring/ZDR review | Business owner |
| Secrets handling | env / Coolify; provider keys may also be saved from the UI to `data/provider_keys.json` (plain JSON) | `config.py` | — | PARTIALLY_IMPLEMENTED | prefer env vars; restrict `serverdata` access; consider encrypting that file | Engineering |
| Encryption at rest (database, volumes) | none in the application | — | — | NOT_IMPLEMENTED | VPS/volume encryption (provider) | Business owner |
| Field-level encryption | not introduced: conversations/memories are searched and embedded (pgvector); would need key management, rotation, re-encryption | assessment | — | NOT_IMPLEMENTED (by decision) | revisit after DPIA | Engineering |
| Backups | see backup-and-recovery.md | `deploy/backup/` | Docker tests | PARTIALLY_IMPLEMENTED | enable encryption/off-site | Business owner |
| Firmware: signed OTA, rollback, https-only | release config | `sdkconfig.release`, `ota.c` | code review | IMPLEMENTED_NOT_VERIFIED | spare-unit tests | Engineering |
| Firmware: Secure Boot, flash & NVS encryption, Secure Download Mode, JTAG off | `firmware/sdkconfig.production` (irreversible on first boot) | committed overlay, README §8 | none | NOT_IMPLEMENTED on units | spare-unit tests, then production | Engineering |
| Firmware: OTA downgrade protection | not blocked (same version ignored only) | `protocol_client.c:961` | — | NOT_IMPLEMENTED (MEDIUM) | version check / anti-rollback | Engineering |
| Firmware: no default passwords / vuln reporting / support period | see psti-assessment.md | — | — | IMPLEMENTED (code) / branch | publish | Business owner |
| Firmware image download `/fw/{id}.bin` | public, integrity from signature (release) | `api/firmware.py:69` | — | ACCEPTED RISK (images not secret) | — | — |
| Independent security testing | none | — | — | REQUIRES_EXTERNAL_ACTION | pen test | Business owner |

## Known limitations from this work

- TOTP code can be reused inside its ~90 s window (no last-used step stored).
- Rotating `data/secret.key` invalidates stored TOTP secrets (operators must re-enrol via `reset-operator-mfa`).
- Stripe is cancelled before the database step of account deletion; if the database step then fails, the subscription stays cancelled and the account remains (the user can retry).
- PostgreSQL advisory-lock path and the "already cancelled in Stripe" path are not covered by automated tests (suite runs on SQLite with a faked Stripe).
