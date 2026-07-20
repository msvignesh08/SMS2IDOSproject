# SMS-2 IDOS — Deployment & Handover Guide

Everything needed to install, secure, run and hand over the system.
Written to be followed by someone who has not seen the code.

**Contents**
1. [Choose your deployment](#1-choose-your-deployment)
2. [Plant server — Linux](#2-plant-server--linux-recommended)
3. [Plant server — Windows](#3-plant-server--windows)
4. [Cloud (demo only)](#4-cloud--demo-only)
5. [Passwords — read before first boot](#5-passwords--read-this-before-first-boot)
6. [Database choice](#6-database-choice)
7. [Backups](#7-backups-not-optional)
8. [Go-live checklist](#8-go-live-checklist)
9. [Administrator runbook](#9-administrator-runbook)
10. [Troubleshooting](#10-troubleshooting)
11. [Handover checklist](#11-handover-checklist)

---

## 1 · Choose your deployment

| | Plant server | Cloud free tier | Laptop |
|---|---|---|---|
| Real production use | **yes** | **no** | no |
| Data survives restart | yes | **no** | yes |
| Always available | yes | sleeps after 15 min | no |
| Works without internet | **yes** | no | yes |

SMS-2 was built for a server **inside the plant**: no cloud, no internet, data
never leaves the site. That is the supported production deployment.

> **A cloud free tier is a demo, not a delivery.** Its disk is erased on every
> restart. If anyone enters real heats there, those records are lost — silently.
> Never hand a free-tier URL to a client as their production system.

**Requirements:** Python 3.10+, ~200 MB disk, any modern browser on the
workstations. No internet at runtime. No Node, no build step.

---

## 2 · Plant server — Linux (recommended)

```bash
sudo mkdir -p /opt/sms2-idos
sudo chown "$USER" /opt/sms2-idos
# copy the project into /opt/sms2-idos, then:
cd /opt/sms2-idos
./deploy/install.sh          # installs Python dependencies (needs pip once)
```

**Set the first-boot passwords before starting** — see §5:

```bash
export SMS2_ADMIN_PASSWORD='<owner/admin password>'
export SMS2_SEED_PASSWORD='<shop-floor accounts password>'
./deploy/run.sh              # starts on port 8020
```

Open `http://<server-ip>:8020` from any plant workstation.

### Start automatically on boot

```bash
sudo cp deploy/sms2-idos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sms2-idos
sudo systemctl status sms2-idos      # confirm it is running
```

Put passwords and any database URL in the unit file as `Environment=` lines
(there is a commented example inside it), then `daemon-reload` and restart.

### Firewall

```bash
sudo ufw allow 8020/tcp              # plant LAN only — never expose to the internet
```

Put the server on the plant UPS. It should not be reachable from outside.

---

## 3 · Plant server — Windows

```bat
cd C:\sms2-idos
python -m pip install -r backend\requirements.txt

set SMS2_ADMIN_PASSWORD=<owner/admin password>
set SMS2_SEED_PASSWORD=<shop-floor accounts password>

cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8020 --workers 1
```

To run it as a service, use **NSSM** (`nssm install SMS2-IDOS`) with:

- **Application:** the full path to `python.exe`
- **Arguments:** `-m uvicorn app.main:app --host 0.0.0.0 --port 8020 --workers 1`
- **Startup directory:** `C:\sms2-idos\backend`
- **Environment:** the two password variables

Allow TCP 8020 in Windows Firewall for the plant network profile only.

---

## 4 · Cloud — demo only

For portfolio or client preview. **Not for production.** See `DEPLOY.md`.

- **Render:** `New → Blueprint`, pick the repo, `render.yaml` does the rest.
- **Vercel / Netlify / GitHub Pages: will not work.** They run static sites and
  serverless functions; SMS-2 needs a process that stays up and a disk it can
  write to. Vercel returns `404: NOT_FOUND` because the frontend is served *by*
  the Python backend, so there is nothing at the repo root to publish.

**Set `TZ` to your plant's timezone** (e.g. `Asia/Kolkata`). Cloud servers run
UTC, which shifts every plant day and shift boundary by hours.

---

## 5 · Passwords — read this before first boot

Two environment variables set the initial credentials:

| Variable | Accounts |
|---|---|
| `SMS2_ADMIN_PASSWORD` | `OWNER`, `ADMIN` |
| `SMS2_SEED_PASSWORD` | `SE001`, `SI001`, `OP001`, `QE001`, `PM001`, `VIEW01` |

**They apply only when an account is first created.** The seeder skips users
that already exist, so after the first boot the database is the authority:

| Situation | Which password works |
|---|---|
| Fresh database | the environment variable |
| Changed in **Admin → Users** | **the new one** — the variable is ignored |
| Restart, database intact | **the new one** — the change persists |
| Restart, database erased *(cloud free tier)* | back to the variable — **the change is lost** |

That last row is why a free tier is unsuitable for production: a password
changed in the app silently reverts on the next restart.

> If neither variable is set, the system falls back to the published defaults
> in `backend/app/seed.py` (`admin@123`, `steel@123`). **Those are public.**
> Always set both variables on any deployment reachable by anyone else.

### `credentials.txt`

Created in the project root at first boot. It lists **issued passwords in plain
text** and is Administrator-only. It is excluded from git and must stay that
way. Once a user sets their own password the entry becomes `(user-managed)`.

---

## 6 · Database choice

**SQLite (default)** — zero setup, one file at `backend/data/sms2_idos.db`,
crash-safe in WAL mode. Correct for a single-server plant deployment.

**PostgreSQL** — for scale or if you want the database on separate hardware:

```bash
pip install psycopg2-binary
export SMS2_DATABASE_URL='postgresql://user:pass@host/sms2'
```

No code changes. Leave the variable unset (or blank) for SQLite.

---

## 7 · Backups (not optional)

```bash
crontab -e
45 21 * * *  /opt/sms2-idos/deploy/backup.sh
```

**21:45 is deliberate** — just before the 22:00 plant-day rollover, so each
backup contains a complete plant day. The script keeps the last 60 and handles
both SQLite and PostgreSQL. Backups can also be taken from **Admin → Backup**.

**Copy them off the server.** A backup on the same disk as the database is not
a backup. Test a restore before go-live — an untested backup is a hope.

---

## 8 · Go-live checklist

- [ ] Delete any demo data: stop the server, remove `backend/data/sms2_idos.db*`, restart
- [ ] `SMS2_ADMIN_PASSWORD` and `SMS2_SEED_PASSWORD` set to real secrets
- [ ] Logged in once with each role and confirmed access is correct
- [ ] Real users created in **Admin → Users**; demo accounts disabled or archived
- [ ] Steel grades, materials, delay reasons reviewed in **Admin → Masters**
- [ ] Heat number series set to the plant's real numbering
- [ ] Backup cron installed **and a restore tested**
- [ ] `systemctl enable` (or NSSM) confirmed — survives a reboot
- [ ] Server on UPS, fixed IP, firewall limited to the plant LAN
- [ ] Workstations can reach `http://<server-ip>:8020`
- [ ] Timezone correct — check the Plant Day and Shift in the status bar
- [ ] Operators trained on one full heat, entry through approval

**Run in parallel with the existing process for at least two weeks.** Do not
retire paper or the old system until the same heats have been logged in both
and the numbers agree.

---

## 9 · Administrator runbook

| Task | Where |
|---|---|
| Add / disable a user | Admin → Users |
| Reset a password | Admin → Users → Manage (requires re-authentication) |
| Approve a recovery request | Admin → Approvals |
| Change grades, materials, delay reasons | Admin → Masters |
| Heat number series | Admin → Masters → Heat number series |
| Read the audit log | Admin → Audit log |
| Take a backup now | Admin → Backup |
| Unlock a locked heat | open the heat → Unlock (reason mandatory, audited) |
| Correct a locked record | Request correction → approved by Incharge/Admin |

### Locked out of every administrator account

Run **on the server console** (physical or SSH access is the security boundary):

```bash
python3 deploy/emergency_admin.py --reactivate          # re-enable admin accounts
python3 deploy/emergency_admin.py --create-recovery     # make a recovery admin
python3 deploy/emergency_admin.py --reset-password ADMIN
```

Every action is written to the permanent audit log. It never deletes anything.

### Things the system will not let you do (by design)

- Delete a production record — chemistry, materials and delays are *superseded*;
  users are *archived*. The audit log and heat timeline are append-only.
- Edit a locked heat — returns HTTP 423 until an Administrator unlocks it with
  a reason.
- Remove the last active Administrator, Owner or Shift Engineer — refused as
  *"ACTION DENIED — Business Continuity"*.
- Approve out of order — Operator → Shift Engineer → Shift Incharge is enforced.

---

## 10 · Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Blank page after deploying an update | Stale browser cache. Hard-refresh with `Ctrl+Shift+R`. |
| "Incorrect password" for a known-good account | On a wiped database the seed passwords were restored — see §5. |
| Password change did not stick | Ephemeral disk (cloud free tier). Change the env var and redeploy, or move to a persistent database. |
| Plant Day or Shift looks wrong | Server timezone. Set `TZ`. The plant day runs 22:00 → 22:00 and **C shift opens it**. |
| Gantt looks stale | SQLite WAL — restart the service. |
| Machine lane missing | It is not missing: **Machine** view shows all 12 lanes. **Heat** view shows only the stages that heat used — a ZPF-bypassed heat correctly has no ZPF bar. |
| Cannot reach the server from a workstation | Firewall on 8020, or the service is not running (`systemctl status sms2-idos`). |
| `smoke_e2e.py` fails at login | It permanently rotates `OP001`'s password and needs a **fresh database** each run. Expected, not a fault. |
| Report downloads but will not open | It is a self-contained `.html`. Open it in a browser, not Excel. |

**Logs:** `journalctl -u sms2-idos -f` (systemd) or the terminal running `run.sh`.

### Verifying an installation

```bash
cd backend
python3 tests/test_plant_time.py     # plant date engine
python3 tests/smoke_e2e.py           # full lifecycle — needs a FRESH database
```

The smoke test drives a heat from creation to LOCKED and asserts 65 behaviours.

Demo data for training or a walkthrough:

```bash
python3 tools/seed_demo.py --days 14
```

**Never run that on a production database** — it creates 38 fictional heats in
a permanent, append-only audit trail.

---

## 11 · Handover checklist

**Deliver**

- [ ] Source code — repository access transferred or granted
- [ ] This guide, `DEPLOY.md`, `README.md`, `docs/CONVENTIONS.md`
- [ ] The installed, running system on their server
- [ ] Administrator credentials, handed over **in person or via their password
      manager** — not email, not chat
- [ ] Confirmation that `credentials.txt` exists, what it contains, and that it
      is confidential
- [ ] Backup schedule installed and a restore demonstrated

**Then remove your own access.** After handover the Owner account is theirs.
Delete your copies of their credentials.

**Agree in writing**

- **Ownership or licence.** Who owns the source, and what the client is allowed
  to do with it. If no licence is stated, the client has no rights by default —
  "I built it for them" does not settle this.
- **Support.** Who fixes a fault during C shift, expected response time, and
  what counts as a fault versus a new feature.
- **Maturity.** State plainly that this is a version 1 with a single
  maintainer. It has real validation, a three-level approval chain and an
  append-only audit trail — and it has not yet run a plant for a year. That
  belongs in the conversation before it becomes the system of record.
