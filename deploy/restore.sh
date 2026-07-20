#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  SMS-2 IDOS — RESTORE FROM BACKUP
#  ─────────────────────────────────────────────────────────────────────────
#  Companion to backup.sh. Restoring is the half that actually matters: a
#  backup nobody has ever restored is a hope, not a safety net.
#
#  Usage:
#      ./deploy/restore.sh                       # list available backups
#      ./deploy/restore.sh <backup-file>         # restore that one
#      ./deploy/restore.sh --latest              # restore the newest
#
#  Safeguards, because this overwrites production data:
#    · refuses to run while the server is still up (stale WAL corrupts data)
#    · verifies the backup is a valid database BEFORE touching anything
#    · saves the current database as .pre-restore first, so a mistaken
#      restore is itself reversible
#    · deletes the -wal and -shm sidecars; leaving them would let SQLite
#      replay the OLD journal over the RESTORED file and silently corrupt it
#    · requires you to type 'restore' to proceed
# ═══════════════════════════════════════════════════════════════════════════
set -e
cd "$(dirname "$0")/.."

DB="backend/data/sms2_idos.db"
DEST="backend/data/backups"
STAMP=$(date +%Y%m%d_%H%M%S)

# ── PostgreSQL ────────────────────────────────────────────────────────────
if [ -n "$SMS2_DATABASE_URL" ] && [[ "$SMS2_DATABASE_URL" == postgresql* ]]; then
  if [ -z "$1" ] || [ "$1" = "--latest" ]; then
    FILE=$(ls -1t "$DEST"/*.sql 2>/dev/null | head -1 || true)
  else
    FILE="$1"
  fi
  [ -n "$FILE" ] && [ -f "$FILE" ] || { echo "No .sql backup found in $DEST"; exit 1; }
  echo "PostgreSQL restore from: $FILE"
  echo "This REPLACES the current database contents."
  read -r -p "Type 'restore' to proceed: " ok
  [ "$ok" = "restore" ] || { echo "Cancelled."; exit 1; }
  psql "$SMS2_DATABASE_URL" < "$FILE"
  echo "Restored. Start the service again."
  exit 0
fi

# ── SQLite ────────────────────────────────────────────────────────────────
# List mode
if [ -z "$1" ]; then
  echo "Available backups in $DEST:"
  ls -1t "$DEST"/*.db "$DEST"/*.db.enc 2>/dev/null | while read -r f; do
    printf "  %-56s %s\n" "$f" "$(du -h "$f" | cut -f1)"
  done || echo "  (none)"
  echo
  echo "Restore with:  ./deploy/restore.sh <file>   or   ./deploy/restore.sh --latest"
  exit 0
fi

if [ "$1" = "--latest" ]; then
  FILE=$(ls -1t "$DEST"/*.db "$DEST"/*.db.enc 2>/dev/null | head -1 || true)
else
  FILE="$1"
fi
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "Backup not found: $1"; exit 1; }

# Encrypted backup (.enc): decrypt to a temp file first, restore from that.
CLEANUP=""
if [[ "$FILE" == *.enc ]]; then
  [ -n "$SMS2_BACKUP_PASSPHRASE" ] || {
    echo "ERROR: $FILE is encrypted. Set SMS2_BACKUP_PASSPHRASE (the same one used"
    echo "       to create it) and run again."; exit 1; }
  command -v openssl >/dev/null 2>&1 || { echo "ERROR: openssl needed to decrypt."; exit 1; }
  DEC="$(mktemp)"
  if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -pass env:SMS2_BACKUP_PASSPHRASE -in "$FILE" -out "$DEC" 2>/dev/null; then
    rm -f "$DEC"
    echo "ERROR: could not decrypt — wrong passphrase, or the file is damaged."
    echo "       Nothing was changed."; exit 1
  fi
  FILE="$DEC"; CLEANUP="$DEC"
  trap '[ -n "$CLEANUP" ] && rm -f "$CLEANUP"' EXIT
  echo "Decrypted the backup for restore."
fi

# The server MUST be stopped. Restoring underneath a running server leaves it
# holding a file handle to the old database and a live WAL that will be
# replayed over the restored file.
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ':8020'; then
  echo "ERROR: something is still listening on port 8020."
  echo "Stop the server first:  sudo systemctl stop sms2-idos"
  exit 1
fi

# Verify the backup is actually a database before destroying the live one.
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$FILE" "PRAGMA integrity_check;" >/dev/null 2>&1 \
    || { echo "ERROR: $FILE is not a readable SQLite database. Nothing changed."; exit 1; }
  HEATS=$(sqlite3 "$FILE" "SELECT COUNT(*) FROM heats;" 2>/dev/null || echo "?")
  USERS=$(sqlite3 "$FILE" "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "?")
  echo "Backup contents: $HEATS heats, $USERS users"
fi

echo
echo "Restoring : $FILE"
echo "Over      : $DB"
echo "The current database will be saved as ${DB}.pre-restore.${STAMP}"
read -r -p "Type 'restore' to proceed: " ok
[ "$ok" = "restore" ] || { echo "Cancelled — nothing changed."; exit 1; }

[ -f "$DB" ] && cp "$DB" "${DB}.pre-restore.${STAMP}"
cp "$FILE" "$DB"
# Stale journals must go, or SQLite replays the old ones over the new file.
rm -f "${DB}-wal" "${DB}-shm"

echo
echo "Restored."
echo "Previous database kept at: ${DB}.pre-restore.${STAMP}"
echo "Start the service:  sudo systemctl start sms2-idos   (or ./deploy/run.sh)"
