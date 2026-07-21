# Deploying SMS-2 IDOS

## Why Vercel showed `404: NOT_FOUND`

Vercel, Netlify and GitHub Pages are built for **static sites and serverless
functions**. SMS-2 is neither. It is a long-running Python service that:

- keeps a **uvicorn server running continuously** (serverless functions wake
  per request and are then destroyed),
- **writes to a database on disk** (serverless filesystems are read-only and
  wiped between invocations),
- **serves the frontend from the backend** — `frontend/index.html` is returned
  by FastAPI, so there is nothing at the repository root for a static host to
  publish. That is the literal cause of the 404.

This is not a configuration problem. Those platforms cannot run this kind of
application. Use a host that runs a real web service instead.

---

## Recommended: Render (free)

1. Go to <https://render.com> and sign in **with GitHub**.
2. Click **New → Blueprint**.
3. Choose the `SMS2IDOSproject` repository. Render reads `render.yaml`
   from the repository root and fills everything in.
4. Click **Apply**. The first build takes 2–4 minutes.
5. You get a URL like `https://sms2-idos.onrender.com`.

Sign in with the demo accounts from the README (`ADMIN` / `admin@123`).

### Two things to expect on the free tier

- **It sleeps.** After ~15 minutes with no traffic the instance shuts down.
  The next visit takes ~30 seconds to wake it. This is normal.
- **Data resets.** The free disk is ephemeral, so the SQLite database is
  rebuilt on every deploy or restart. The app re-seeds masters and demo users
  automatically, so it always comes back working — but production entries you
  create will not survive a restart.

### Making data persist

Create a free PostgreSQL instance on Render, then:

1. Add `psycopg2-binary>=2.9` to `backend/requirements.txt`.
2. In the web service → **Environment**, set `SMS2_DATABASE_URL` to the
   **Internal Database URL** Render shows for the database.
3. Redeploy.

No code changes are needed — `backend/app/core/config.py` already reads that
variable and `database.py` switches drivers automatically.

---

## Loading the plant's production history

The real history from the company's workbook ships with the repo as a compact
bundle (`backend/app/data/history.json.gz`, ~150 KB), so the server never needs
the spreadsheet.

**On Render's free tier there is no Shell**, so set an environment variable and
the service loads it itself on the next boot:

```
SMS2_SEED_HISTORY = 1
```

The log then shows `SMS-2: history backfill seeded 667 heats`. It runs once —
later restarts print `history already present — backfill skipped`.

**If you do have shell access** (a paid instance, or your own server), run it
explicitly instead — same result, with a preview available:

1. `cd backend`
2. `python tools/seed_history.py --dry-run`   ← optional preview
3. `python tools/seed_history.py`

It loads 667 heats with their consumption, refining, casting and delay records,
then advances the heat-number series past them so the next heat an operator
creates cannot collide with a historical one.

- Run it **after** the app has started once — the masters (materials, delay
  codes, users) must exist first. The seeder checks and refuses rather than
  writing records that point at nothing.
- **Safe to re-run.** Heat numbers already present are skipped, never
  duplicated, so an interrupted deploy just needs the command again.
- Preview first with `python tools/seed_history.py --dry-run`.

Imported heats are flagged as archived history: they carry no approval
signatures, and the app does not judge them against current entry rules —
it says so on screen rather than implying they passed.

**When the plant revises the workbook**, regenerate the bundle on a machine
that has the file, then commit and redeploy:

```
python tools/import_history.py "path/to/IDOS.xlsx" --export
```

---

## Alternatives

| Host | Notes |
|---|---|
| **Railway** | Same model as Render, similar free allowance. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, root directory `backend`. |
| **Fly.io** | Gives a real persistent volume, so SQLite survives. More setup (needs a Dockerfile). |
| **A plant server** | What SMS-2 was actually designed for — see the README. Zero internet dependency, data never leaves the site. |

---

## Security before any public deployment

The repository ships with **demo credentials** (`admin@123`, `steel@123`)
documented in the README and shown on the login page. They are fine for a
portfolio demo. Before this is used for anything real:

1. Change every password in **Admin → Users**.
2. Remove the demo-account hint from `frontend/index.html` (the
   `.login-demo` block).
3. Keep `credentials.txt` out of git — it is already in `.gitignore` and must
   stay there. It stores issued passwords in plain text.
