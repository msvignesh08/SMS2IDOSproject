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
  # DO NOT `cp` the .db file. The database runs in WAL mode, so recent
  # transactions live in sms2_idos.db-wal and have not yet been checkpointed
  # into the main file. A plain copy silently omits them — a restore test
  # showed 16 heats backed up and only 13 restored.
  #
  # SQLite's online backup API copies a transactionally consistent snapshot
  # INCLUDING the WAL, and is safe while the server is running.
  SRC="backend/data/sms2_idos.db"
  OUT="$DEST/sms2_backup_${STAMP}.db"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$SRC" ".backup '$OUT'"
  else
    # No sqlite3 CLI (common on Windows) — the Python module has the same API.
    python3 -c "
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
with d: s.backup(d)
d.close(); s.close()
" "$SRC" "$OUT" 2>/dev/null || python -c "
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
with d: s.backup(d)
d.close(); s.close()
" "$SRC" "$OUT"
  fi
fi
# keep last 60 backups
ls -1t "$DEST" | tail -n +61 | while read -r f; do rm -f "$DEST/$f"; done
echo "Backup written: $DEST (${STAMP})"
