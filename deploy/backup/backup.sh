#!/bin/sh
# One backup run (called by run.sh every BACKUP_INTERVAL_S seconds). See deploy/README.md section 8.
#
#  1. pg_dump -Fc into /backups/buddyai-YYYYMMDD-HHMM.dump (written to a hidden temp file first)
#  2. verify the dump with `pg_restore --list`
#  3. write buddyai-YYYYMMDD-HHMM.dump.sha256 (checksum of the plaintext dump)
#  4. BACKUP_AGE_RECIPIENT set: encrypt to <dump>.age, then delete the plaintext dump
#  5. BACKUP_RCLONE_REMOTE set: upload <dump>.age + .sha256, delete remote files older than
#     BACKUP_REMOTE_KEEP_DAYS. Unencrypted dumps are NEVER uploaded.
#  6. BACKUP_SERVERDATA=1: the same for a tar of /serverdata (secret key, provider keys, firmware)
#  7. local retention: delete buddyai-* backup files older than BACKUP_LOCAL_KEEP_DAYS
#
# Any failure prints "BACKUP FAILED: <step>" and exits non-zero.
#
# Environment (all optional except the database password):
#   PGPASSWORD                  database password (set by the compose file)
#   PGHOST / PGUSER / PGDATABASE  default postgres / buddyai / buddyai
#   BACKUP_DIR                  default /backups
#   BACKUP_AGE_RECIPIENT        age public key(s) "age1...", several separated by commas or spaces
#   BACKUP_RCLONE_REMOTE        e.g. "offsite:buddyai-backups" (rclone remote:path)
#   BACKUP_RCLONE_CONFIG_B64    base64 of an rclone.conf (alternative to RCLONE_CONFIG_* variables
#                               or a mounted /root/.config/rclone/rclone.conf)
#   BACKUP_REMOTE_KEEP_DAYS     default 30
#   BACKUP_LOCAL_KEEP_DAYS      default 14
#   BACKUP_SERVERDATA           1 = also back up /serverdata (mounted read-only)
set -eu
umask 077

BACKUP_DIR=${BACKUP_DIR:-/backups}
export PGHOST="${PGHOST:-postgres}"
export PGUSER="${PGUSER:-buddyai}"
export PGDATABASE="${PGDATABASE:-buddyai}"
AGE_RECIPIENT=${BACKUP_AGE_RECIPIENT:-}
REMOTE=${BACKUP_RCLONE_REMOTE:-}
REMOTE_KEEP_DAYS=${BACKUP_REMOTE_KEEP_DAYS:-30}
LOCAL_KEEP_DAYS=${BACKUP_LOCAL_KEEP_DAYS:-14}
SERVERDATA=${BACKUP_SERVERDATA:-}
SERVERDATA_DIR=${BACKUP_SERVERDATA_DIR:-/serverdata}

TMP_FILES=""
cleanup() {
    for t in $TMP_FILES; do rm -f "$t"; done
}
trap cleanup EXIT
trap 'cleanup; exit 1' INT TERM

fail() {
    echo "BACKUP FAILED: $*" >&2
    exit 1
}

log() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
}

is_number() {
    case "$1" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac
}

is_number "$LOCAL_KEEP_DAYS" || fail "config: BACKUP_LOCAL_KEEP_DAYS must be a number of days"
is_number "$REMOTE_KEEP_DAYS" || fail "config: BACKUP_REMOTE_KEEP_DAYS must be a number of days"
[ -d "$BACKUP_DIR" ] || fail "config: $BACKUP_DIR does not exist"

# --- age recipients: "age1..." only (validated, never printed in full) ------------------------------
AGE_ARGS=""
if [ -n "$AGE_RECIPIENT" ]; then
    for r in $(printf '%s' "$AGE_RECIPIENT" | tr ',' ' '); do
        case "$r" in
            age1*) AGE_ARGS="$AGE_ARGS -r $r" ;;
            *) fail "config: BACKUP_AGE_RECIPIENT must contain age public keys (age1...), never a private key" ;;
        esac
    done
    command -v age >/dev/null 2>&1 || fail "config: age is not installed"
fi

# --- rclone configuration ------------------------------------------------------------------------------
UPLOAD_REFUSED=""
if [ -n "$REMOTE" ]; then
    command -v rclone >/dev/null 2>&1 || fail "config: rclone is not installed"
    if [ -n "${BACKUP_RCLONE_CONFIG_B64:-}" ]; then
        conf=$(mktemp)
        TMP_FILES="$TMP_FILES $conf"
        printf '%s' "$BACKUP_RCLONE_CONFIG_B64" | base64 -d >"$conf" 2>/dev/null ||
            fail "config: BACKUP_RCLONE_CONFIG_B64 is not valid base64"
        export RCLONE_CONFIG="$conf"
    fi
    if [ -z "$AGE_ARGS" ]; then
        UPLOAD_REFUSED=1
        echo "BACKUP ERROR: BACKUP_RCLONE_REMOTE is set but BACKUP_AGE_RECIPIENT is not;" \
            "refusing to upload unencrypted backups (off-site upload skipped)" >&2
    fi
fi
REMOTE=${REMOTE%/}

# encrypt <plain> : writes <plain>.age, then removes <plain>
encrypt() {
    plain=$1
    part="$plain.age.partial"
    TMP_FILES="$TMP_FILES $part"
    # shellcheck disable=SC2086 # AGE_ARGS is a list of "-r age1..." words
    age $AGE_ARGS -o "$part" "$plain" || fail "encrypt: age failed for $(basename "$plain")"
    [ -s "$part" ] || fail "encrypt: empty output for $(basename "$plain")"
    mv "$part" "$plain.age" || fail "encrypt: rename"
    rm -f "$plain" || fail "encrypt: could not remove the plaintext $(basename "$plain")"
}

# upload <file> ... : copy to the remote (only called for encrypted files + checksums)
upload() {
    for f in "$@"; do
        case "$f" in
            *.age | *.sha256) ;;
            *) fail "upload: refusing to upload a file that is not encrypted: $(basename "$f")" ;;
        esac
        rclone copyto --quiet "$f" "$REMOTE/$(basename "$f")" ||
            fail "upload: rclone copy of $(basename "$f")"
    done
}

# checksum <file> : <file>.sha256 in `sha256sum -c` format (relative file name)
checksum() {
    (cd "$(dirname "$1")" && sha256sum "$(basename "$1")" >"$(basename "$1").sha256") ||
        fail "checksum: $(basename "$1")"
}

stamp=$(date +%Y%m%d-%H%M)
UPLOADS=""

# --- 1-3. database dump ----------------------------------------------------------------------------------
dump="$BACKUP_DIR/buddyai-$stamp.dump"
if [ -e "$dump" ] || [ -e "$dump.age" ]; then
    fail "dump: $(basename "$dump") already exists"
fi
tmp="$BACKUP_DIR/.buddyai-$stamp.dump.partial"
TMP_FILES="$TMP_FILES $tmp"
pg_dump -Fc -f "$tmp" || fail "dump: pg_dump"
pg_restore --list "$tmp" >/dev/null || fail "verify: pg_restore --list cannot read the dump"
mv "$tmp" "$dump" || fail "dump: rename"
checksum "$dump"
log "backup ok: $dump ($(wc -c <"$dump" | tr -d ' ') bytes, verified)"

if [ -n "$AGE_ARGS" ]; then
    encrypt "$dump"
    log "encrypted: $dump.age"
    UPLOADS="$UPLOADS $dump.age $dump.sha256"
fi

# --- 6. serverdata (secret key, provider keys, firmware images) -------------------------------------------
if [ "$SERVERDATA" = "1" ]; then
    [ -d "$SERVERDATA_DIR" ] || fail "serverdata: $SERVERDATA_DIR is not mounted"
    sd="$BACKUP_DIR/buddyai-serverdata-$stamp.tar.gz"
    sdtmp="$BACKUP_DIR/.buddyai-serverdata-$stamp.tar.gz.partial"
    TMP_FILES="$TMP_FILES $sdtmp"
    tar -czf "$sdtmp" -C "$SERVERDATA_DIR" . || fail "serverdata: tar"
    tar -tzf "$sdtmp" >/dev/null || fail "serverdata: verify (tar -t)"
    mv "$sdtmp" "$sd" || fail "serverdata: rename"
    checksum "$sd"
    log "serverdata ok: $sd"
    if [ -n "$AGE_ARGS" ]; then
        encrypt "$sd"
        log "encrypted: $sd.age"
        UPLOADS="$UPLOADS $sd.age $sd.sha256"
    fi
fi

# --- 5. off-site copy (encrypted only) -------------------------------------------------------------------
if [ -n "$REMOTE" ] && [ -z "$UPLOAD_REFUSED" ]; then
    # shellcheck disable=SC2086
    upload $UPLOADS
    rclone delete --quiet --max-depth 1 --min-age "${REMOTE_KEEP_DAYS}d" \
        --include 'buddyai-*' "$REMOTE" || fail "remote retention: rclone delete"
    log "uploaded off-site ($(echo $UPLOADS | wc -w | tr -d ' ') files), remote keeps ${REMOTE_KEEP_DAYS} days"
fi

# --- 7. local retention ----------------------------------------------------------------------------------
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'buddyai-*.dump*' -o -name 'buddyai-serverdata-*.tar.gz*' \) \
    -mtime +"$LOCAL_KEEP_DAYS" -delete || fail "local retention: find -delete"
# leftovers of interrupted runs
find "$BACKUP_DIR" -maxdepth 1 -type f -name '.buddyai-*.partial' -mtime +1 -delete 2>/dev/null || true

[ -z "$UPLOAD_REFUSED" ] || fail "upload: refused, BACKUP_AGE_RECIPIENT is not set (local backup was written)"
exit 0
