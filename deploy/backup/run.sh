#!/bin/sh
# Backup loop (the backup container's command): one backup.sh run, then sleep BACKUP_INTERVAL_S
# (default 86400 = daily). A failed run is logged ("BACKUP FAILED: ...") and retried next interval.
set -u

interval=${BACKUP_INTERVAL_S:-86400}
case "$interval" in
    '' | *[!0-9]*)
        echo "BACKUP FAILED: config: BACKUP_INTERVAL_S must be a number of seconds, using 86400" >&2
        interval=86400
        ;;
esac
[ "$interval" -ge 60 ] || interval=60

while true; do
    if /usr/local/bin/backup.sh; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup run finished OK, next in ${interval}s"
    else
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) BACKUP FAILED (see the line above), next try in ${interval}s" >&2
    fi
    sleep "$interval"
done
