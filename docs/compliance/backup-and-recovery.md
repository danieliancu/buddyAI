# Backup and recovery

The operating procedure lives in `deploy/README.md` §8 (not duplicated here). This page records the control, its evidence and its status.

| Control | Implementation | Evidence | Test evidence | Status | Outstanding action | Owner |
|---|---|---|---|---|---|---|
| Daily database backup | `deploy/backup/run.sh` → `backup.sh`: `pg_dump -Fc` every `BACKUP_INTERVAL_S` (default 24 h) | `deploy/backup/*`, both compose files | Docker test against a throwaway Postgres (12 Oct 2026) | IMPLEMENTED_NOT_VERIFIED in production | deploy | Operator |
| Integrity check | `pg_restore --list` before keeping a dump; `.sha256` file | `backup.sh` | Docker test | IMPLEMENTED_NOT_VERIFIED in production | — | — |
| Encryption before leaving the server | `age` to `BACKUP_AGE_RECIPIENT` public key(s); plaintext removed only after successful encryption; upload refused for unencrypted dumps | `backup.sh` | Docker test (refusal + encrypted upload) | IMPLEMENTED_NOT_VERIFIED in production, **not enabled** until the variable is set | generate key pair offline; set recipient | Business owner |
| Keys separate from backups | only the public recipient on the server; private identity offline (two encrypted copies) | `deploy/README.md` §8.1 | — | PROCEDURE | do it | Business owner |
| Off-site copy | `rclone` to `BACKUP_RCLONE_REMOTE`, remote expiry `BACKUP_REMOTE_KEEP_DAYS` (30) | `backup.sh` | `:local:` remote test only | NOT ENABLED | choose UK/EU storage, sign DPA, configure | Business owner |
| Local retention | `BACKUP_LOCAL_KEEP_DAYS` (14, unchanged) | `backup.sh` | Docker test | IMPLEMENTED | — | — |
| `serverdata` (cookie secret, provider keys, firmware images) | optional `BACKUP_SERVERDATA=1`, encrypted only | `backup.sh` | Docker test | NOT ENABLED | enable with encryption | Operator |
| Restore | `deploy/backup/restore.sh`: explicit `--target`, refuses production without `--force-production`, refuses non-empty target without `--clean`, checksum + list check, single transaction | `restore.sh` | Docker tests (refusals + restores) | IMPLEMENTED_NOT_VERIFIED in production | monthly restore test into a separate database | Operator |
| Deleted data not reintroduced | transactional outbox `deletion_events` (`server/app/deletion_log.py`), off-site encrypted ledger (`sync_deletions.sh`), `python -m app.cli reconcile-deletions`; `restore.sh` blocks server start until it has run | `deploy/README.md` §8.5, `server/tests/test_deletion_log.py` | automated tests; Docker test of backup, deletion, sync, restore, block and re-apply (3 Nov 2026) | IMPLEMENTED_NOT_VERIFIED in production; **off-site copy not enabled** until external action #6 | configure remote, set `BUDDYAI_DELETION_LEDGER_ENABLED=true`, include in the monthly restore test | Operator |
| Same-VPS risk | local dumps stay on the VPS for 14 days | — | — | REMAINS until off-site enabled | enable off-site | Business owner |
| Encryption at rest of the live database | not implemented; depends on VPS disk/volume encryption | — | — | NOT_IMPLEMENTED | check provider options | Business owner |

Backups cannot delete individual records: deleted customer data can remain in backups until they expire (14 days locally, 30 days off-site when enabled). The privacy notice in the repository says so.
