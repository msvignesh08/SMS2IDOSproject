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

### Making data persist — PostgreSQL

**This is already set up.** `render.yaml` declares a PostgreSQL database
(`sms2-db`) and wires `SMS2_DATABASE_URL` to it automatically, so deploying the
blueprint creates both the web service and the database and connects them.
Nothing to paste, and no way to paste the *External* URL by mistake.

The driver (`psycopg2-binary`) is in `requirements.txt`, and `config.py`
normalises the `postgres://` scheme Render hands out — SQLAlchemy 2 removed
that alias and would otherwise fail at start-up with an error that names the
dialect rather than the cause.

Leaving `SMS2_DATABASE_URL` blank still falls back to SQLite, which is the
right choice for a throwaway demo.

> **The free database expires.** Render currently gives free PostgreSQL a
> 30-day life. When it goes, the heats go with it. For the plant's real
> deployment use a paid instance or your own server, and keep taking the
> backups described in RECOVERY.md.

**Backup and restore differ on PostgreSQL.** The in-app snapshot/restore in
Admin → Backup uses SQLite's online-backup API and reports plainly that it does
not apply; use `pg_dump` (see `deploy/backup.sh`) or your provider's snapshots.

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

---

## Reading the workbook straight from Microsoft 365

Admin → Masters carries an **Excel connection** panel. Configured, it pulls the
plant's live workbook from the company Microsoft account and loads it with the
same parser used for the file by hand — so the quirks that took a day to find
(multi-row headers, merged dates, transposed caster dates) are handled once.

IT registers an Azure AD application and grants **admin consent** for
`Files.Read.All` (or `Sites.Read.All` for a SharePoint library). Then set on the
server — never in the repository:

```
SMS2_GRAPH_TENANT         directory (tenant) ID
SMS2_GRAPH_CLIENT_ID      application (client) ID
SMS2_GRAPH_CLIENT_SECRET  client secret
SMS2_GRAPH_FILE           the sharing link, or a drive-item path
```

**Getting `SMS2_GRAPH_FILE`** — open the workbook in SharePoint or OneDrive,
press **Share → Copy link**, and paste the whole URL. That is all. Set the
link's audience to *People in your organisation* rather than *Anyone*: the app
signs in as itself and is authorised by the admin consent above, so an
anonymous link buys nothing and leaves the plant's production data reachable by
whoever holds the URL.

A drive-item path still works if you prefer one
(`drives/{drive-id}/root:/Plant/IDOS.xlsx:` — note the trailing colon).

The link only **addresses** the file. App-only tokens carry no user identity, so
"anyone with the link" does not apply to them and `Files.Read.All` consent is
required either way.

The panel shows which variables are set, the file it points at, and the last
sync result. **Check connection** fetches and parses without writing;
**Sync now** loads. Loading is idempotent — heat numbers already present are
skipped, never duplicated.

The secret is read only at call time. No endpoint returns it, it never reaches
the browser, and it is never written to the audit trail.

The server needs outbound access to `login.microsoftonline.com` and
`graph.microsoft.com`. That is fine on Render and commonly blocked on a plant
intranet — if it is, keep using the committed bundle instead.

---

## The AI Production Engineer's reasoning layer

Admin → the AI page reports which brain is answering. Two exist and they are a
floor and a ceiling, not alternatives:

- **Rules engine** (`engines/insights.py`) — deterministic, offline, always
  there. Window statistics, trend detection, alerting, keyword-matched
  answers. This is what a plant with no internet gets, and it never regresses.
- **Reasoning layer** (`engines/ai_brain.py`) — when a key is set, questions go
  to Claude with **read-only** tools over the plant's own database, so answers
  come from this plant's heats rather than from general steelmaking knowledge.

Set on the server, never in the repository:

```
ANTHROPIC_API_KEY   the key
SMS2_AI_MODEL       optional, default claude-opus-4-8
SMS2_AI_EFFORT      optional — low|medium|high|xhigh|max, default high
```

`pip install -r requirements.txt` brings the client in. **With no key the app
behaves exactly as before** — the deterministic engine answers, and the screen
says so rather than letting an operator assume they got reasoning.

**What leaves the plant when it is on.** The question, plus whatever the tools
return for it: heat numbers, process values, chemistry, delays. No
credentials, no passwords, no audit trail. Nothing is written back — every
tool is a `SELECT`, and there is no writing tool at all, which is how the
Constitution's read-only rule for the AI Production Engineer is enforced.

That is a real disclosure decision, not a footnote: on a plant intranet with
no outbound access the reasoning layer simply never engages. Leave the key
unset and SMS-2 remains fully offline.

**Cost.** Opus 4.8 is $5 / $25 per million tokens in/out. The persona and tool
definitions are prompt-cached, so repeat questions bill the cached prefix at
about a tenth of that. `claude-fable-5` is available via `SMS2_AI_MODEL` for
the hardest reasoning — it is roughly double the price and requires 30-day
data retention on the account, so it is not the default.

---

## Running the AI on the plant's own server (no internet)

`engines/ai_local.py` talks to a model served on your hardware, so the question
and the heat data behind it never leave the site. It speaks the
OpenAI-compatible `/v1/chat/completions` shape over urllib — **no extra Python
dependency** — so Ollama, llama.cpp's server, vLLM, LM Studio and LocalAI all
work.

```
SMS2_LOCAL_LLM_URL     http://127.0.0.1:11434/v1     <- setting this turns it on
SMS2_LOCAL_LLM_MODEL   qwen2.5:7b-instruct
SMS2_LOCAL_LLM_KEY     optional; most local servers need none
```

Resolution order is **local → cloud → rules engine**. Local wins even though
the cloud model reasons better, because keeping plant data on site is the
reason the local path exists. A site that wants the stronger model just leaves
`SMS2_LOCAL_LLM_URL` unset. If the local server is down the app degrades to the
next tier rather than erroring, and the AI page names which one answered.

### What it takes to run

**Not Render's free tier.** 512 MB RAM and no GPU — the smallest useful model
needs several times that. This runs on a plant server or workstation.

| Model | Disk | RAM needed | CPU-only speed | Verdict |
|---|---|---|---|---|
| Qwen2.5 3B (Q4) | ~2 GB | 4 GB | ~10–15 tok/s | Usable; weak tool calling |
| **Qwen2.5 7B (Q4)** | ~4.7 GB | **8 GB** | ~4–8 tok/s | **The sensible floor** |
| Qwen2.5 14B (Q4) | ~9 GB | 16 GB | ~2–4 tok/s | Good; slow without a GPU |
| Qwen2.5 32B (Q4) | ~20 GB | 32 GB | painful | Needs a GPU |

Any 16 GB GPU (RTX 4060 Ti / 4070 / A4000) runs the 14B comfortably at 30+
tok/s. On CPU alone, expect to wait 30–90 seconds for an answer — which is why
`TIMEOUT` is 180 s.

Setup with Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct
```

Then set `SMS2_LOCAL_LLM_URL=http://127.0.0.1:11434/v1` and restart SMS-2.
`GET /api/ai/local-status` reports whether the server is reachable and which
models it holds.

### The specialisation

`app/data/metallurgy.md` is injected into every local request: the route a heat
takes here, de-P vs de-S chemistry, deoxidation, degassing, casting defects,
the plant day rule, and typical operating ranges. A 7B model does not know how
this shop runs — this is how it finds out. **Edit that file when plant practice
changes; no code involved.**

### Honest about the ceiling

A 7B local model reasons less well than Opus 4.8 and calls tools less
reliably. It is well ahead of a keyword match and behind a frontier model.
For the metallurgy tutoring and "read this heat and tell me what happened"
work, a 7–14B model with the knowledge pack is genuinely useful. For subtle
multi-step diagnosis across many heats, the cloud model is still better.
