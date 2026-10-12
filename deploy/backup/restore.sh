#!/bin/sh
# Restore a backup into a SEPARATE database (restore test, or recovery into a fresh database).
# See deploy/README.md section 8.
#
#   restore.sh --target postgresql://USER:PASSWORD@HOST:5432/DBNAME [--identity /key/key.txt] \
#              [--clean] [--force-production] [--yes] FILE
#
#   FILE                 buddyai-YYYYMMDD-HHMM.dump or buddyai-YYYYMMDD-HHMM.dump.age
#   --target URL         REQUIRED. The database to restore into. There is no default.
#                        The database must already exist (createdb / CREATE DATABASE).
#   --identity FILE      age identity (private key) file for .age backups; or BACKUP_AGE_IDENTITY.
#                        Keep it offline: run restores on a trusted machine, not on the server.
#   --clean              drop existing objects in the target first (otherwise the target must be empty)
#   --force-production   allow a target that looks like the production database (DANGEROUS)
#   --yes                do not ask for confirmation
#
# Steps: decrypt (age -d) to a private temp dir, verify the .sha256 checksum if present, check the
# archive with `pg_restore --list`, refuse production targets, restore in one transaction.
# The production database is identified by PGHOST / PGUSER / PGDATABASE (default postgres / buddyai /
# buddyai) and PGPASSWORD, as in the backup container.
set -eu
umask 077

die() {
    echo "RESTORE FAILED: $*" >&2
    exit 1
}
warn() {
    echo "WARNING: $*" >&2
}
usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

TARGET=""
IDENTITY=${BACKUP_AGE_IDENTITY:-}
CLEAN=""
FORCE_PROD=""
YES=""
FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --target) [ $# -ge 2 ] || usage; TARGET=$2; shift 2 ;;
        --target=*) TARGET=${1#--target=}; shift ;;
        --identity | -i) [ $# -ge 2 ] || usage; IDENTITY=$2; shift 2 ;;
        --identity=*) IDENTITY=${1#--identity=}; shift ;;
        --clean) CLEAN=1; shift ;;
        --force-production) FORCE_PROD=1; shift ;;
        --yes | -y) YES=1; shift ;;
        -h | --help) usage ;;
        -*) echo "unknown option: $1" >&2; usage ;;
        *) [ -z "$FILE" ] || usage; FILE=$1; shift ;;
    esac
done

[ -n "$FILE" ] || usage
[ -n "$TARGET" ] || die "--target is required (there is deliberately no default database)"
[ -f "$FILE" ] || die "$FILE not found"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
trap 'rm -rf "$WORK"; exit 1' INT TERM

# --- 1. decrypt --------------------------------------------------------------------------------------------
case "$FILE" in
    *.age)
        [ -n "$IDENTITY" ] || die "$FILE is encrypted: pass --identity <age identity file>"
        [ -f "$IDENTITY" ] || die "identity file $IDENTITY not found"
        base=$(basename "$FILE" .age)
        DUMP="$WORK/$base"
        age -d -i "$IDENTITY" -o "$DUMP" "$FILE" || die "decrypt: age -d failed (wrong key or damaged file)"
        echo "decrypted $FILE"
        SUMFILE="${FILE%.age}.sha256"
        ;;
    *)
        DUMP=$FILE
        SUMFILE="$FILE.sha256"
        ;;
esac

# --- 2. checksum -------------------------------------------------------------------------------------------
if [ -f "$SUMFILE" ]; then
    expected=$(cut -d' ' -f1 <"$SUMFILE")
    actual=$(sha256sum "$DUMP" | cut -d' ' -f1)
    [ "$expected" = "$actual" ] || die "checksum mismatch: $(basename "$SUMFILE") does not match the dump"
    echo "checksum OK ($(basename "$SUMFILE"))"
else
    warn "no checksum file $(basename "$SUMFILE"); integrity is only checked by pg_restore --list"
fi

# --- 3. archive check --------------------------------------------------------------------------------------
pg_restore --list "$DUMP" >"$WORK/toc.txt" || die "pg_restore --list cannot read the archive"
created=$(sed -n 's/^; *Archive created at //p' "$WORK/toc.txt" | head -n 1)
echo "archive OK, created at: ${created:-unknown}"

# --- 4. refuse production ----------------------------------------------------------------------------------
q() { psql "$@" -X -q -A -t -v ON_ERROR_STOP=1 2>/dev/null; }

target_db=$(q "$TARGET" -c 'select current_database()') || die "cannot connect to the target database"
target_sysid=$(q "$TARGET" -c 'select system_identifier from pg_control_system()' || true)
prod_db=${PGDATABASE:-buddyai}
prod_sysid=""
if [ -n "${PGPASSWORD:-}" ]; then
    prod_sysid=$(q -h "${PGHOST:-postgres}" -U "${PGUSER:-buddyai}" -d "$prod_db" \
        -c 'select system_identifier from pg_control_system()' || true)
fi

looks_prod=""
if [ -n "$target_sysid" ] && [ -n "$prod_sysid" ]; then
    # Same PostgreSQL cluster and same database name = production.
    [ "$target_sysid" = "$prod_sysid" ] && [ "$target_db" = "$prod_db" ] && looks_prod=1
else
    # Production not reachable from here: be conservative, judge by the database name.
    [ "$target_db" = "$prod_db" ] && looks_prod=1
fi

if [ -n "$looks_prod" ]; then
    if [ -z "$FORCE_PROD" ]; then
        die "the target ($target_db) looks like the PRODUCTION database. Restore into a separate database" \
            "(e.g. buddyai_restore_test). Only for disaster recovery: --force-production"
    fi
    warn "RESTORING INTO WHAT LOOKS LIKE PRODUCTION ($target_db). Data written after ${created:-the backup}"
    warn "will be lost, and data deleted since then comes back (see README section 8, deleted data)."
fi

tables=$(q "$TARGET" -c "select count(*) from information_schema.tables where table_schema = 'public'") ||
    die "cannot read the target database"
if [ "$tables" != "0" ] && [ -z "$CLEAN" ]; then
    die "the target database $target_db is not empty ($tables tables). Use an empty database, or --clean"
fi

echo "restore $(basename "$FILE") -> database $target_db${CLEAN:+ (dropping existing objects first)}"
if [ -z "$YES" ]; then
    printf 'Type the target database name to continue: '
    read -r answer || die "no confirmation"
    [ "$answer" = "$target_db" ] || die "not confirmed"
fi

# --- 5. restore (all or nothing) ---------------------------------------------------------------------------
set -- --no-owner --no-privileges --exit-on-error --single-transaction
[ -z "$CLEAN" ] || set -- "$@" --clean --if-exists
pg_restore "$@" -d "$TARGET" "$DUMP" || die "pg_restore (nothing was committed)"

echo "restore OK into $target_db"
q "$TARGET" -c "select 'tables: ' || count(*) from information_schema.tables where table_schema = 'public'" || true
q "$TARGET" -c "select 'accounts: ' || count(*) from accounts" || true
q "$TARGET" -c "select 'latest audit_log entry: ' || coalesce(max(created_at)::text, 'none') from audit_log" || true
cat <<EOF

Next: deleted data must not come back. Before using this database for real, re-apply every deletion
recorded in the production audit_log after ${created:-the backup time} (deploy/README.md section 8).
EOF
