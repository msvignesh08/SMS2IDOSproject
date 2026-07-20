# SMS-2 IDOS — Emergency Recovery

What to do when something has gone wrong. Each section is a runbook: work
down it in order.

**Before anything else — do no further harm**

1. **Stop the server** if data may be damaged. Every minute it runs, more is
   written on top of the problem.
   ```bash
   sudo systemctl stop sms2-idos
   ```
2. **Copy the whole data directory somewhere else**, exactly as it is now —
   corrupt or not. It is your only chance to try a second approach if the
   first makes things worse.
   ```bash
   cp -r backend/data /root/sms2-incident-$(date +%Y%m%d_%H%M%S)
   ```
3. Only then start fixing.

Nothing in SMS-2 deletes production records. Chemistry, materials and delays
are *superseded*; users are *archived*; the audit log and heat timeline are
append-only. If data appears to be gone, it is far more likely to be a
database or filesystem problem than the application.

---

## 1 · Locked out of every administrator account

**Symptom:** nobody can sign in with an Administrator-class account.

Run **on the server console** — physical or SSH access is the security
boundary here, the same trust level as the database file itself.

```bash
cd /opt/sms2-idos

python3 deploy/emergency_admin.py --reactivate         # re-enable disabled/archived admins
python3 deploy/emergency_admin.py --create-recovery    # create a fresh RECOVERY admin
python3 deploy/emergency_admin.py --reset-password ADMIN
```

Every action is written to the permanent audit log as `EMERGENCY-CONSOLE`.
The script never deletes anything.

**Afterwards:** sign in, set a proper password in **Admin → Users**, and
disable the recovery account if you created one.

---

## 2 · Forgotten password (single user)

No console access needed.

1. User clicks **Forgot password?** on the login page — this raises a
   *request*, never an automatic reset, and never reveals whether the account
   exists.
2. An Administrator works it in **Admin → Approvals**: approve (issues a
   temporary password and revokes that user's existing sessions), reject, or
   disable the account.
3. Every outcome is audited and written to the credentials ledger.

---

## 3 · Restore the database from a backup

**Symptom:** data lost, corrupted, or a bad bulk change needs undoing.

```bash
sudo systemctl stop sms2-idos            # REQUIRED — see the warning below
cd /opt/sms2-idos

./deploy/restore.sh                      # list available backups
./deploy/restore.sh --latest             # restore the newest
./deploy/restore.sh backend/data/backups/sms2_backup_20260720_214500.db

sudo systemctl start sms2-idos
```

The script verifies the backup is a readable database *before* touching the
live one, saves the current database as `.pre-restore.<timestamp>`, and
removes the stale `-wal`/`-shm` journals. It refuses to run while port 8020
is still listening.

> **Never restore under a running server.** It keeps a handle on the old file
> and will replay its journal over the restored one, corrupting it.

**A restore that is not reversible is not safe.** The `.pre-restore` copy is
how you get back if you restored the wrong file — do not delete it until you
have confirmed the restored data is correct.

### Verify after restoring

Sign in and check: the heat count looks right, the newest heat is what you
expect, **Admin → Audit log** shows recent entries, and the Gantt draws.

---

## 4 · Server hardware failure — rebuild from scratch

You need: the backup files, and the source (GitHub or a copy).

```bash
# on the replacement machine
sudo mkdir -p /opt/sms2-idos && sudo chown "$USER" /opt/sms2-idos
# copy the source in, then:
cd /opt/sms2-idos
./deploy/install.sh

mkdir -p backend/data/backups
# copy your off-site backup files into backend/data/backups/

./deploy/restore.sh --latest

sudo cp deploy/sms2-idos.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sms2-idos
```

Set `SMS2_ADMIN_PASSWORD` / `SMS2_SEED_PASSWORD` in the unit file **only if
you are starting with an empty database**. Restoring brings the real accounts
and their passwords with it, and the variables are ignored for users that
already exist.

---

## 5 · Service will not start

```bash
sudo systemctl status sms2-idos
journalctl -u sms2-idos -n 60 --no-pager
```

| In the log | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError` | dependencies missing | `./deploy/install.sh` |
| `Address already in use` | port 8020 taken | `sudo ss -ltnp \| grep 8020`, stop the other process |
| `unable to open database file` | wrong working directory or permissions | check `WorkingDirectory` in the unit; `chown` the data directory |
| `could not translate host name` | bad `SMS2_DATABASE_URL` | fix or unset it — blank falls back to SQLite |
| `database disk image is malformed` | corruption | go to §6 |

Run it in the foreground to see the error directly:

```bash
cd /opt/sms2-idos && ./deploy/run.sh
```

---

## 6 · Database corruption

**Symptom:** `database disk image is malformed`, or reads fail unpredictably.

```bash
sudo systemctl stop sms2-idos
cp -r backend/data /root/sms2-incident-$(date +%s)       # preserve evidence first

sqlite3 backend/data/sms2_idos.db "PRAGMA integrity_check;"
```

- **`ok`** → the file is fine; the problem is elsewhere (permissions, disk full,
  wrong path). Check `df -h`.
- **Anything else** → attempt a salvage, then fall back to a restore:

```bash
sqlite3 backend/data/sms2_idos.db ".recover" | sqlite3 /tmp/recovered.db
sqlite3 /tmp/recovered.db "SELECT COUNT(*) FROM heats;"    # sanity-check it
# if the count looks right:
cp /tmp/recovered.db backend/data/sms2_idos.db
rm -f backend/data/sms2_idos.db-wal backend/data/sms2_idos.db-shm
```

If salvage yields less than the last good backup, restore instead (§3).
Corruption usually means failing storage — check `dmesg` and SMART before
trusting that disk with production data again.

---

## 7 · A heat is locked and wrong

Do **not** edit the database directly. Use the audited paths:

- **Correct a value:** open the heat → **Request correction** → an
  Administrator or Shift Incharge approves it. The old value is preserved as
  an immutable version; the requester cannot approve their own request.
- **Reopen the heat:** Administrator → **Unlock (audited)**, reason mandatory.
  This supersedes the approvals and is recorded on the permanent timeline.

Both paths keep the audit trail intact. Editing the database by hand does not,
and destroys the record's credibility as evidence.

---

## 8 · Cloud deployment lost its data

Expected on a free tier — the disk is erased on every restart and the database
is rebuilt from the seed. **There is nothing to recover.**

This is why a free tier must not hold real production data. Move to a
persistent database (see `DEPLOY.md`) or, better, run it on a plant server.

---

## Prevention

| | |
|---|---|
| Backups run nightly | `45 21 * * * /opt/sms2-idos/deploy/backup.sh` — before the 22:00 rollover |
| Backups copied **off** the server | a backup on the same disk is not a backup |
| **A restore has been tested** | on a copy, not in production — see below |
| Server on UPS | an unclean shutdown mid-write is how corruption starts |
| Disk space monitored | a full disk corrupts SQLite |

### Test your backups — quarterly

```bash
cp backend/data/backups/<newest>.db /tmp/verify.db
sqlite3 /tmp/verify.db "PRAGMA integrity_check;"
sqlite3 /tmp/verify.db "SELECT COUNT(*) FROM heats;"
```

The count must be close to production. If it is short, your backups are
lossy — **this exact fault existed in `backup.sh`**: it used `cp`, which
silently omitted transactions still sitting in the write-ahead log. A test
restore showed 16 heats backed up and 13 restored. It now uses SQLite's
online-backup API, verified lossless.

An untested backup is a hope, not a safety net.
