#!/bin/sh
# Copies new deletion events (server/app/deletion_log.py) to the off-site ledger: encrypted file first, then
# marked replicated. A crash in between sends them again next run; the reader drops repeats by uid.
# The events hold ids only. Needs BACKUP_AGE_RECIPIENT and BACKUP_RCLONE_REMOTE (same as backup.sh).
#
#   DELETION_SYNC_BATCH   events per file, default 1000
set -u

export PGHOST="${PGHOST:-postgres}"
export PGUSER="${PGUSER:-buddyai}"
export PGDATABASE="${PGDATABASE:-buddyai}"
BATCH=${DELETION_SYNC_BATCH:-1000}
REMOTE=${BACKUP_RCLONE_REMOTE:-}
RECIPIENTS=${BACKUP_AGE_RECIPIENT:-}

fail() {
    echo "DELETION SYNC FAILED: $*" >&2
    exit 1
}

[ -n "$REMOTE" ] || fail "config: BACKUP_RCLONE_REMOTE is empty"
[ -n "$RECIPIENTS" ] || fail "config: BACKUP_AGE_RECIPIENT is empty"
REMOTE=${REMOTE%/}

if [ -n "${BACKUP_RCLONE_CONFIG_B64:-}" ] && [ -z "${RCLONE_CONFIG:-}" ]; then
    RCLONE_CONFIG=$(mktemp) || fail "config: temp file"
    export RCLONE_CONFIG
    printf '%s' "$BACKUP_RCLONE_CONFIG_B64" | base64 -d >"$RCLONE_CONFIG" || fail "config: BACKUP_RCLONE_CONFIG_B64"
    trap 'rm -f "$RCLONE_CONFIG"' EXIT
fi

AGE_ARGS=""
for r in $(printf '%s' "$RECIPIENTS" | tr ',' ' '); do
    AGE_ARGS="$AGE_ARGS -r $r"
done

work=$(mktemp -d) || fail "temp dir"
# shellcheck disable=SC2064
trap "rm -rf '$work'; [ -z \"\${RCLONE_CONFIG:-}\" ] || rm -f \"\$RCLONE_CONFIG\"" EXIT

while true; do
    psql -v ON_ERROR_STOP=1 -Atq -c "select row_to_json(e) from (
        select uid, kind, account_id, device_id, target, occurred_at from deletion_events
        where replicated_at is null order by id limit $BATCH) e" >"$work/events.jsonl" ||
        fail "reading deletion_events"
    [ -s "$work/events.jsonl" ] || exit 0

    uids=$(sed -n 's/.*"uid":"\([0-9a-f]*\)".*/'"'"'\1'"'"'/p' "$work/events.jsonl" | paste -sd, -)
    [ -n "$uids" ] || fail "no uid in the exported events"
    name="deletions-$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n').jsonl.age"

    # shellcheck disable=SC2086 # AGE_ARGS is a list of "-r age1..." words
    age $AGE_ARGS -o "$work/$name" "$work/events.jsonl" || fail "age"
    rclone copyto --quiet "$work/$name" "$REMOTE/deletions/$name" || fail "rclone copy of $name"
    psql -v ON_ERROR_STOP=1 -Atq -c "update deletion_events set replicated_at = now() where uid in ($uids)" ||
        fail "marking events replicated (they will be sent again)"
    rm -f "$work/$name"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) deletion ledger: $name"
done
