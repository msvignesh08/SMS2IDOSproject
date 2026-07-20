#!/usr/bin/env bash
# SMS-2 IDOS — scheduled backup (add to cron, e.g. nightly at 21:45 before plant-day rollover):
#   45 21 * * *  /opt/sms2-idos/deploy/backup.sh
set -e
cd "$(dirname "$0")/.."
STAMP=$(date +%Y%m%d_%H%M%S)
DEST="backend/data/backups"
mkdir -p "$DEST"
if [ -n "$SMS2_DATABASE_URL" ] && [[ "$SMS2_DATABASE_URL" == postgresql* ]]; then
  pg_dump "$SMS2_DATABASE_URL" > "$DEST/sms2_backup_${STAMP}.sql"
else
  cp backend/data/sms2_idos.db "$DEST/sms2_backup_${STAMP}.db"
fi
# keep last 60 backups
ls -1t "$DEST" | tail -n +61 | while read -r f; do rm -f "$DEST/$f"; done
echo "Backup written: $DEST (${STAMP})"
