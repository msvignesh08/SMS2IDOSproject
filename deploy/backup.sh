#!/usr/bin/env bash
# SMS-2 IDOS — scheduled backup (add to cron, e.g. nightly at 21:45 before plant-day rollover):
#   45 21 * * *  /opt/sms2-idos/deploy/backup.sh
#
# ENCRYPTION (recommended): set SMS2_BACKUP_PASSPHRASE and the backup is
# written as a .enc file — AES-256, key derived with PBKDF2 (200k iterations)
# via openssl. A backup holds every password hash and the whole audit trail;
# if it is ever copied off-site it must not be readable plaintext. Keep the
# passphrase somewhere OTHER than the server — a locked backup whose key sits
# next to it is not protected. restore.sh decrypts it with the same passphrase.
set -e
cd "$(dirname "$0")/.."
STAMP=$(date +%Y%m%d_%H%M%S)
DEST="backend/data/backups"
mkdir -p "$DEST"

encrypt_if_configured() {   # $1 = plaintext file; encrypts in place to $1.enc
  [ -z "$SMS2_BACKUP_PASSPHRASE" ] && return 0
  if ! command -v openssl >/dev/null 2>&1; then
    echo "  WARNING: SMS2_BACKUP_PASSPHRASE set but openssl not found — backup left UNENCRYPTED"
    return 0
  fi
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
    -pass env:SMS2_BACKUP_PASSPHRASE -in "$1" -out "$1.enc"
  shred -u "$1" 2>/dev/null || rm -f "$1"     # remove the plaintext
  echo "  encrypted -> $1.enc"
}
if [ -n "$SMS2_DATABASE_URL" ] && [[ "$SMS2_DATABASE_URL" == postgresql* ]]; then
  SQL="$DEST/sms2_backup_${STAMP}.sql"
  pg_dump "$SMS2_DATABASE_URL" > "$SQL"
  encrypt_if_configured "$SQL"
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
  encrypt_if_configured "$OUT"
fi
# keep last 60 backups
ls -1t "$DEST" | tail -n +61 | while read -r f; do rm -f "$DEST/$f"; done
echo "Backup written: $DEST (${STAMP})"
