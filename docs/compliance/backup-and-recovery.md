# Backup and recovery

The operating procedure lives in `deploy/README.md` §8 (not duplicated here). This page records the control, its evidence and its status.

**Decision, 3 November 2026 (business owner):** no off-site storage provider. The business owner keeps encrypted copies of the backups on their own computer and manages copying, storage and deletion themselves. This is a decision, not evidence that it has been done, and the application cannot see or check those copies.

Accepted consequences:

- A lost server can lose all data since the last copy, up to about a day with daily copies.
- Deletion events are not copied off the server between backups. If the server is lost, whatever customers deleted after the last copy comes back on restore and must be handled by hand (`reconcile-deletions --no-ledger`, `deploy/README.md` §8.5), as a personal data incident.
- Recovery depends on the business owner being available.

Commitments that follow from the privacy notice: copies stay encrypted, are held on a device under the company's control, and are deleted within 14 days of the backup. Losing that device is a possible personal data breach (`incident-response.md`).

| Control | Implementation | Evidence | Test evidence | Status | Outstanding action | Owner |
|---|---|---|---|---|---|---|
| Daily database backup | `deploy/backup/run.sh` → `backup.sh`: `pg_dump -Fc` every `BACKUP_INTERVAL_S` (default 24 h) | `deploy/backup/*`, both compose files | Docker test against a throwaway Postgres (12 Oct 2026) | IMPLEMENTED_NOT_VERIFIED in production | deploy | Operator |
| Integrity check | `pg_restore --list` before keeping a dump; `.sha256` file | `backup.sh` | Docker test | IMPLEMENTED_NOT_VERIFIED in production | — | — |
| Encryption before leaving the server | `age` to `BACKUP_AGE_RECIPIENT` public key(s); plaintext removed only after successful encryption; upload refused for unencrypted dumps | `backup.sh` | Docker test (refusal + encrypted upload) | IMPLEMENTED_NOT_VERIFIED in production, **not enabled** until the variable is set | generate key pair offline; set recipient | Business owner |
| Keys separate from backups | only the public recipient on the server; private identity offline (two encrypted copies) | `deploy/README.md` §8.1 | — | PROCEDURE | do it | Business owner |
| Copy away from the server | business owner's own computer, by the owner's own process; the code's `rclone` upload stays available but unused | decision above | none in the repository | OWNER-MANAGED | keep encrypted copies, delete within 14 days, keep evidence of restore tests outside this repository | Business owner |
| Local retention | `BACKUP_LOCAL_KEEP_DAYS` (14, unchanged) | `backup.sh` | Docker test | IMPLEMENTED | — | — |
| `serverdata` (cookie secret, provider keys, firmware images) | optional `BACKUP_SERVERDATA=1`, encrypted only | `backup.sh` | Docker test | NOT ENABLED | enable with encryption | Operator |
| Restore | `deploy/backup/restore.sh`: explicit `--target`, refuses production without `--force-production`, refuses non-empty target without `--clean`, checksum + list check, single transaction | `restore.sh` | Docker tests (refusals + restores) | IMPLEMENTED_NOT_VERIFIED in production | monthly restore test into a separate database | Operator |
| Deleted data not reintroduced | transactional outbox `deletion_events` (`server/app/deletion_log.py`), off-site encrypted ledger (`sync_deletions.sh`), `python -m app.cli reconcile-deletions`; `restore.sh` blocks server start until it has run | `deploy/README.md` §8.5, `server/tests/test_deletion_log.py` | automated tests; Docker test of backup, deletion, sync, restore, block and re-apply (3 Nov 2026) | IMPLEMENTED_NOT_VERIFIED in production; **off-site ledger not used** (decision above): the outbox protects only while the old database is readable or through the last backup | include in the monthly restore test | Operator |
| Same-VPS risk | local dumps stay on the VPS for 14 days | — | — | REDUCED only by the owner's copies | see decision above | Business owner |
| Encryption at rest of the live database | not implemented; depends on VPS disk/volume encryption | — | — | NOT_IMPLEMENTED | check provider options | Business owner |

Backups cannot delete individual records: deleted customer data can remain in backups until they expire (14 days, including the business owner's copies). The privacy notice in the repository says so.
