# Compliance evidence — Ola Companion (Great Britain launch)

Prepared 12 October 2026 on branch `feature/uk-compliance` (not deployed). **Nothing here states that Ola is legally compliant.** Engineering recommendations are separated from legal requirements; anything legal needs a qualified adviser.

Business context: Ola Technologies London Ltd imports the Waveshare ESP32-S3-Touch-AMOLED-2.06 board, fits an EEMB LP402535 (3.7 V, 320 mAh, as stated by the business; datasheet not in the repository) battery and its own firmware, and sells the finished watch under the olácompanion brand. It does not manufacture electronic components, but as importer and brand owner of a modified product it may carry manufacturer-equivalent duties under UK product rules (UKCA, PSTI, WEEE, batteries) — to confirm with an adviser. Initial market: Great Britain only.

| Document | Content |
|---|---|
| [data-inventory.md](data-inventory.md) | personal data categories, flows, proposed lawful bases, retention, deletion |
| [processor-register.md](processor-register.md) | OpenAI, Stripe, email, hosting, backups — DPA status (none signed) |
| [international-transfers.md](international-transfers.md) | transfer mechanisms to confirm |
| [retention-policy.md](retention-policy.md) | central retention settings, account deletion, data subject requests |
| [security-controls.md](security-controls.md) | every security control with evidence and status |
| [backup-and-recovery.md](backup-and-recovery.md) | backups, encryption, restore |
| [psti-assessment.md](psti-assessment.md) | PSTI requirements, draft statement (not approved), RED cyber note |
| [consumer-rights.md](consumer-rights.md) | GB distance selling and subscription audit |
| [external-actions.md](external-actions.md) | actions the software cannot complete |

Related existing docs: `docs/BILLING.md`, `docs/MEMORY.md`, `deploy/README.md` (§8 backups, §10 launch checklist), `firmware/README.md` (§7–8 release and production security), `firmware/keys/README.md`.

## Audit matrix

| Requirement | Existing / new implementation | Gap | Risk | Action | Status |
|---|---|---|---|---|---|
| Personal data inventory (UK GDPR Art. 30 style record) | docs/compliance/data-inventory.md | lawful bases not approved | M | adviser review | PARTIALLY_IMPLEMENTED |
| Privacy notice accurate | `site/src/pages/legal/privacy.astro` rewritten (memory, search + city, retention, deletion limits, ICO) | legal review; transfer safeguards depend on DPAs | H | review; sign DPAs | IMPLEMENTED_NOT_VERIFIED (branch) |
| Processor contracts | processor register | no DPA evidenced | H | external-actions #2–5 | REQUIRES_EXTERNAL_ACTION |
| International transfers | international-transfers.md | mechanisms unconfirmed | H | external-actions #2 | REQUIRES_EXTERNAL_ACTION |
| ICO fee | — | not paid | H | external-actions #1 | REQUIRES_EXTERNAL_ACTION |
| Central retention policy | `server/app/retention.py`, settings, hourly job | periods need approval | M | approve | IMPLEMENTED (branch) |
| Voice operations purge | retention job (was never run) | — | L | — | IMPLEMENTED (branch) |
| Account deletion cancels Stripe | `billing.cancel_all_for_account`, `accounts.delete_account` | DB failure after Stripe cancel leaves account (retryable) | M | — | IMPLEMENTED (branch) |
| Deletion of secondary data (search cache, voice ops, usage notices) | `accounts.delete_account` | backups up to 14/30 days | L | — | IMPLEMENTED (branch) |
| Export completeness | `accounts.export` (+ orders, subscriptions, consents, top-ups, usage, events) | — | L | — | IMPLEMENTED (branch) |
| DSAR tracking | `POST /api/accounts/{id}/requests` → audit | no reminders for the one-month deadline | L | process | IMPLEMENTED (branch) |
| OpenAI application-state storage | `store=false` (Chat Completions, Responses) | abuse-monitoring retention, ZDR, residency unverified | M | DPA + account review | IMPLEMENTED (branch) |
| Sensitive data out of logs | redaction filter, tool/search/provider/email log changes | real access log unverified; log rotation outside app | H→L | verify after deploy | IMPLEMENTED (branch) |
| Production email backend | warnings; bodies withheld on https | production must use SMTP | H | set env | REQUIRES_EXTERNAL_ACTION |
| Operator least privilege | roles admin/support, backend-enforced | — | H→L | assign roles | IMPLEMENTED (branch) |
| Operator MFA | TOTP + recovery codes, opt-in enforcement | not enforced yet | H | enrol, enforce | IMPLEMENTED (branch) |
| Security audit log | events + admin viewer | retention unapproved | M | approve | IMPLEMENTED (branch) |
| Transport encryption (web) | HTTPS/HSTS verified live | — | — | — | IMPLEMENTED_AND_VERIFIED |
| Transport encryption (watch) | release firmware wss-only | units on dev firmware | H | ship release firmware | IMPLEMENTED_NOT_VERIFIED |
| Encryption at rest | none | DB/volumes unencrypted | H | provider disk encryption | NOT_IMPLEMENTED |
| Encrypted off-site backups | `deploy/backup/` (age + rclone), restore script | not enabled; same-VPS copies | H | external-actions #6 | PARTIALLY_IMPLEMENTED |
| PSTI: no default passwords | per-device random setup password | dev firmware on units (debug server) | M | release firmware | IMPLEMENTED (code) |
| PSTI: vulnerability reporting | `/security/`, `security.txt` | not deployed | H | deploy | IMPLEMENTED (branch) |
| PSTI: support period | "at least 2 years from purchase" on `/security/` | wording legal check; statement unsigned | H | external-actions #12 | IMPLEMENTED (branch) |
| Production firmware hardening | `firmware/sdkconfig.production`, README checklist | untested, irreversible | H | spare-unit tests | NOT_IMPLEMENTED on units |
| UKCA / RED (incl. cyber) / WEEE / battery / RoHS | — | no evidence | Blocker | external-actions #8–11 | REQUIRES_EXTERNAL_ACTION |
| Company identity on site | name only; address incomplete; no company number | — | Blocker | external-actions #13 | NOT_IMPLEMENTED |
| VAT wording | "including VAT" removed (not VAT-registered) | — | — | — | IMPLEMENTED (branch) |
| Trial and subscription disclosures | 30-day wording aligned; subscription terms explicit | 14-day right for the service: legal review | M | review | IMPLEMENTED_NOT_VERIFIED |
| Trust page | updated items, no certification claims | — | L | — | IMPLEMENTED (branch) |
| DPIA | — | none | H | external-actions #16 | REQUIRES_EXTERNAL_ACTION |
| Penetration test | — | none | H | external-actions #18 | REQUIRES_EXTERNAL_ACTION |
