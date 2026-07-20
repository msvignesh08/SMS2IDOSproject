# SMS-2 IDOS v1.0 — Industrial Digital Operating System

**Steel Melting Shop SMS-2 · eLogbook · MES · Digital Command Center**

> People First. Production Always. Knowledge Forever.

A complete, self-contained industrial web application implementing the SMS-2
Master Architectural Constitution — built around the **SMART SWITCHABLE
INDUSTRIAL GANTT CHART**, the heart, soul and primary interface of SMS-2:
every module (stages, delays, shutdowns, validation, route decisions,
approvals, locks, aborts) feeds the live Gantt at the top of the Digital
Command Center, switchable between **Heat view** and **Machine view**
(HM Bay · EAF · ZPF · LRF · VD-2/3 · BC-2/3 · Quality Lab) and between
**shift** and **plant-day** windows, with a glowing live NOW cursor,
delay/shutdown overlays, event diamonds, hover intelligence, and one-click
jump into any heat. It implements: the eight-module heat lifecycle
(Hot Metal & Scrap → EAF → ZPF → LRF → Route Decision → VD → Billet Caster →
Quality → Review & Lock), Plant Day & Shift Engine, three-level digital
approval chain, deterministic Validation Engine, Metallurgical Calculation
Engine, Digital Command Center, Reports & Analytics, Universal Search,
Organizational Memory, Training Mode, and full Administration & Governance.

---

## 1 · Quick start (any intranet server or laptop)

Requires **Python 3.10+**. No internet needed at runtime; installation needs
pip access once (or a wheels folder).

```bash
./deploy/install.sh          # one time: pip install -r backend/requirements.txt
./deploy/run.sh              # starts on http://0.0.0.0:8020
```

Open **http://\<server-ip\>:8020** from any plant workstation browser
(Chrome/Edge/Firefox). Nothing to install on client machines.

### Demo accounts (change immediately in Admin → Users)

| Employee ID | Role              | Password    |
|-------------|-------------------|-------------|
| `OWNER`     | Owner             | `owner@123` |
| `ADMIN`     | Administrator     | `admin@123` |
| `SE001`     | Shift Engineer    | `steel@123` |
| `SI001`     | Shift Incharge    | `steel@123` |
| `OP001`     | Operator          | `steel@123` |
| `QE001`     | Quality Engineer  | `steel@123` |
| `PM001`     | Production Manager| `steel@123` |
| `VIEW01`    | View Only         | `steel@123` |

### Production deployment

- Copy the folder to `/opt/sms2-idos`, install, then use
  `deploy/sms2-idos.service` for auto-start on boot (systemd).
- Schedule `deploy/backup.sh` in cron (suggested 21:45, before the 22:00
  plant-day rollover). Backups can also be taken from Admin → Backup.
- **PostgreSQL** (recommended at scale): set
  `SMS2_DATABASE_URL=postgresql://user:pass@host/sms2` — no code changes.
  SQLite (default, WAL mode) is fully supported and crash-safe for a
  single-server plant deployment.
- Put the server on the plant UPS. The system has **zero cloud/internet
  dependency** — fonts, scripts, styles are all local.

---

## 2 · Constitutional conventions implemented

| Rule | Implementation |
|------|----------------|
| Plant Day begins 22:00 | `backend/app/core/plant_time.py` — the **only** component allowed to compute plant days/shifts. Plant Day *D* = 22:00 of *D−1* → 22:00 of *D*; **C shift is the first shift of the plant day** (16-Jul 23:30 → Plant Day 17-Jul, Shift C). Unit-tested incl. midnight/month/year rollovers. |
| Shifts A/B/C | A 06–14, B 14–22, C 22–06 (crosses midnight; durations always computed on absolute datetimes). |
| Heat = aggregate root | Unique heat numbers (series-issued or manual, duplicates rejected), everything keyed to the heat. |
| Gated workflow | `stage_gates()` computes availability; caster locked until route decided; VD appears only on VD routes. |
| Route decision | Shift Engineer/Incharge/Admin only; requires LRF chemistry first; re-selection audited. |
| Approval chain | Operator → Shift Engineer → Shift Incharge, order enforced, digital signatures, auto-lock at final level. |
| Locked = read-only | HTTP 423 on any edit; Administrator unlock requires a mandatory reason, supersedes approvals, fully audited. |
| Universal No-Delete | Chemistry/materials/delays are **superseded**, never deleted; users **archived**, never deleted; audit log & heat events are append-only. |
| Nothing without a timeline | `heat_events` spine powers heat timelines, dashboard activity, Gantt, and shift handover. |
| Validation | Deterministic Validation Engine (mandatory fields, chemistry presence & grade limits, chronology incl. "no tapping before charging complete", plausibility ranges, delay completeness, rejected-needs-remarks). Blocking validation never depends on an AI service — production continuity guaranteed. |
| Never guess (7.10) | The Calculation Engine returns the exact list of missing inputs instead of a number when data is incomplete. |
| Auto-calculations | Total charge weight, power-on duration, process/casting durations, temperature deltas — computed, never typed. |
| Training/Sandbox mode | Training heats behave identically but are excluded from reports, analytics, dashboards KPIs and knowledge. |
| Security | PBKDF2-HMAC-**SHA256** (240k iterations, per-user salt), DB-backed sessions, RBAC per module, password re-authentication for critical admin operations, full audit log. |
| Configuration over coding | Grades, materials (with composition & recovery), delay reasons, equipment, heat series, roles & permissions — all editable in Admin, no code changes. |
| Reports as knowledge assets | Every generated Excel is archived immutably in the Report Library (Digital Time Machine). |

## 3 · Architecture

```
backend/
  app/core/        plant_time (constitutional clock) · security · config · database
  app/models.py    Heat, StageRecord, ChemistrySample, MaterialAddition, Delay,
                   Approval, HeatEvent (append-only spine), AuditLog (append-only),
                   masters, users/roles/sessions, notifications, report archive,
                   knowledge notes  — plant_id present for multi-plant future
  app/engines/     stage_config (8 modules as configuration) · validation ·
                   lifecycle (state machine) · calculator · reports (openpyxl)
  app/routers/     auth · heats (stage framework API) · dashboard · reports/search/
                   knowledge/analytics · admin
  tests/           plant-time unit tests · full end-to-end workflow smoke test
frontend/          zero-build vanilla SPA: index.html + app.css + app.js
                   (Smart Switchable Industrial Gantt hero, Section-17
                   ultra-premium UI: living aurora background, per-module
                   color personalities, gradient/glow/glass effects,
                   animated login ring, micro-animations — plus the Heat
                   Rail, tile workspaces, auto-save with optimistic locking,
                   SVG analytics; system fonts only, intranet-safe, all
                   effects degrade under prefers-reduced-motion)
deploy/            install.sh · run.sh · backup.sh · systemd unit
docs/              conventions & decisions
```

**Stack:** FastAPI + SQLAlchemy 2 + SQLite/PostgreSQL · openpyxl · vanilla JS.
Chosen deliberately for single-maintainer sustainability: no build chain, no
node_modules, no cloud services — a plant engineer can maintain every file.

## 4 · Running the tests

```bash
cd backend
python3 tests/test_plant_time.py      # Plant Date Engine (boundary cases)
# start the server, then in another terminal:
python3 tests/smoke_e2e.py            # full heat lifecycle through the live API
```

The E2E test drives a complete heat from creation to LOCKED and asserts 35+
constitutional behaviors (gating, chronology rules, RBAC, route rules,
calculator, approval chain, lock/unlock, training exclusion, search,
handover, analytics, audit, backup).

## 5 · Master Constitution S.1–S.7 layer

- **Role hierarchy & continuity (S.2)** — Owner → Super Administrator →
  Administrator → Shift Incharge → Shift Engineer → Operator → View Only.
  Business-continuity guards make it impossible to archive, disable, or
  demote the last active member of any protected class ("ACTION DENIED —
  Business Continuity"). Ownership transfer (Admin API + audit) demotes the
  previous Owner to Super Administrator. `deploy/emergency_admin.py`
  provides console-level emergency admin restoration and password reset,
  fully audited as EMERGENCY-CONSOLE.
- **Correction & Version Control system (S.3)** — old value preserved →
  correction request (any entry-authorized user, works on LOCKED heats) →
  approval (Shift Incharge / Administrator-class, requester cannot
  self-approve) → new value applied → immutable RecordVersion → audit +
  timeline event. Every stage submission also snapshots a version. The
  Versions button on any heat is the Digital Time Machine.
- **Smart Gantt v2 (S.4)** — five switchable modes (Heat / Machine / Shift /
  Delays / Production vs time), constitutional machine lanes (HM · EAF ·
  ZPF · LRF-5 · LRF-6 · VD-2/3 · LTC-1/2 · BC-2/3 · QLAB), 1–31-day
  historical windows (Today / Yesterday / 7d / 30d / custom), zoom,
  drag-to-pan, lane filter chips.
- **Live Machine Intelligence (S.5)** — per-machine board: RUNNING /
  READY / IDLE / DELAYED / MAINTENANCE / OFFLINE, current heat & running
  minutes, delay burden, utilization %, caster production tonnes, next
  heat. Maintenance/offline are declared in governance (audited),
  the rest is computed live.
- **AI Production Engineer (S.6)** — strictly read-only. Live insight cards
  (alerts, recommendations, predictions, facts), five personas
  (Production Engineer / Consultant / Trainer / Management / Analytical),
  and question answering over real records: production drops, delay
  Pareto, shift ranking, underutilization, maintenance and bottleneck
  predictions, kWh/t — plus a Digital Trainer that teaches EAF/ZPF/LRF/VD/
  LTC/BC/quality/reports/delays/utilization. High-severity findings become
  AI notifications. Deterministic by design: it cannot hallucinate and it
  cannot block production (S.7).

## 6 · Final directive layer — Grade Freedom, Sync, Preferences, Mobile

- **Grade Entry System** — grade fields are never dropdown-locked. Heat
  creation takes free text with live search over every grade ever used;
  unknown grades (IS2062, E250 BR, any future grade) register themselves at
  entry — audited, no software update required. Specs can be added later in
  Admin → Masters, at which point chemistry-window validation activates.
- **Production Synchronization Engine** — a stale save never says "reload
  page" and never overwrites silently. The workstation receives the full
  conflict context (who saved, their version, their data) and the operator
  chooses: Keep current draft · View their version · Merge changes · Request
  approval (routes conflicting values through the Correction System to a
  higher authority). Ownership transfer, role changes and this engine all
  respect the continuity guards — promoting the plant's last Shift Engineer
  to Owner is refused.
- **Auto Save Engine** — instant local drafts on every keystroke, debounced
  database saves, restore-on-return notice, beforeunload/visibility flush.
  Refresh, logout, browser crash or power loss: the draft survives on the
  workstation and merges when the operator returns.
- **Notification suite + Preference Engine** — production, heat, approval,
  administrative, delay, auto-save, synchronization, AI, report, emergency,
  shift and system notifications are all real. First login opens the
  preference panel; optional kinds and channels (browser pop-ups, email) are
  user-controlled, while emergency / security / system / production-delay
  alerts can never be disabled. Browser notifications fire for high and
  critical alerts when enabled; critical emails send via plant SMTP once
  `smtp_host` is configured in Admin → Settings (threaded, can never block
  production).
- **Mobile support** — fully responsive: the icon rail becomes a bottom
  command bar on phones, grids and forms reflow, tables scroll, touch
  targets enlarge, iOS focus-zoom is prevented, and the Smart Gantt gains
  pinch-to-zoom, smooth touch panning, sticky time headers and sticky heat
  numbers. Same glassmorphism, same animations, zero design degradation.

## 7 · The Notification & Premium UI Laws

- **Notification Center** — scales past 1000 notifications with windowed
  paging ("load older"), server-side search, one-tap **mark all read**, and
  grouping into Production · Approvals · AI · Conflict · Reports · System ·
  **Business Continuity** (continuity-guard denials now broadcast an alert so
  governance knows someone tried). The drawer is a flex column whose list
  region alone scrolls — controls and preferences can never be pushed off
  screen on any device. It live-refreshes while open.
- **Low Power Mode** — a fourth first-class theme: the animated aurora is
  replaced by a premium static gradient, looping animations stop after their
  entrance, and backdrop blur yields to solid premium surfaces — identical
  typography, spacing and industrial elegance at a fraction of the GPU/battery
  cost. Cycle themes with the ◐ button: dark → pure black → light → low power.
- **Custom industrial themes** — every component reads exclusively from the
  documented CSS token contract (see the end of `app.css`); a plant can drop
  a `custom-theme.css` overriding those tokens for full branding with zero
  risk to layout or functionality.

## 8 · The Validation Law

Save-then-error is designed out: every stage board shows a live "Please
complete the following:" checklist computed by the Validation Engine, the
submit button stays disabled (with guidance, not punishment) until the
checklist empties, mandatory fields highlight inline while empty, and the
whole board refreshes after each auto-saved keystroke — so the operator
watches the button unlock as the record completes. Drafts keep every
keystroke throughout. Modal action buttons live on a solid sticky footer
that no device, zoom level or scroll position can hide. Nothing anywhere in
SMS-2 deletes a production record — supersede and correct are the only
verbs that exist.

## 9 · The Reachability Law

Mandatory fields are guaranteed to be enterable. Tiles that mix process
readings with a chemistry block — EAF's *Temperature & Chemistry*, carrying
the mandatory **Bath Temperature (°C)** — now render both the reading inputs
and the sample editor in one dialog, with real-time validation, inline
"required" highlighting, full auto-save, and a *Record Sample* button that
stays disabled until the bath temperature and at least one element value are
present. The value flows into the stage record, the immutable version
snapshots, the audit trail and the heat/daily Excel reports.

A standing audit in the smoke suite walks every stage definition on each run
and fails if any required field lives where the UI cannot render an input —
so this class of defect cannot return as new fields are added.

## 10 · The Process Route Law

Every heat follows **its own** route — SMS-2 never assumes two heats are alike.
After EAF the system asks *"Where shall this heat go next?"* with three real
choices: **ZPF · LRF-5 · LRF-6**.

- Choosing **ZPF** unlocks the ZPF module, then ladle refining.
- Choosing **LRF-5/LRF-6** takes the heat straight to that furnace: ZPF is
  marked *not in route*, never appears as required, never blocks submission,
  never appears in the heat's validation, Digital DNA report or Gantt row —
  and the LRF unit is pre-filled from the decision.
- After LRF the existing decision continues to offer **VD-2 · VD-3 · BC-2 ·
  BC-3**; vacuum degassing is never mandatory.
- Gantt lanes for ZPF, VD and the transfer cars hide themselves when no heat
  in the window used them.

Integrity is preserved in both directions: a locked heat's route is immutable,
and a heat that already carries ZPF production data cannot be re-routed to
erase it (the Correction System exists for genuine mistakes).

## 11 · Logout, Sound, Credentials & Recovery

- **Logout Safety** — the sign-out control never acts on a single tap. It opens
  a confirmation showing the SMS-2 identity, the signed-in user, the last
  auto-save with its timestamp, and an explicit list of any unsaved drafts
  still held on that workstation, with **Cancel** and **Sign out**. Session
  expiry still exits immediately, as it must.
- **Sound Notification Law** — the supplied industrial tone
  (`frontend/assets/approval-notification.mp3`) plays **once**, and only for
  approval requests, critical alerts and important production alerts. It is
  unreachable from auto-save, live data refresh, Gantt updates, report
  generation and the polling cycle — verified by a call-site audit. Users
  control it fully: *approvals + critical + important* (default), *approvals
  only*, *critical only*, or *silent*, plus a volume slider and a test button.
  An arriving approval opens a dialog with the details and routes straight to
  the decision.
- **User Management Law** — `credentials.txt` is created at first boot in the
  project root and rewritten automatically on user creation, password reset,
  enable, disable and role change, carrying username · issued password · role
  · account status · created · last updated. It records **only issued
  credentials**: the moment a user sets their own password the entry becomes
  `(user-managed)`, so private secrets never reach the disk. Reachable over
  HTTP only by Administrator-class roles with password re-authentication, and
  written atomically with owner-only permissions.
- **Password & Recovery Laws** — every user can change their own password
  (current / new / confirm, live policy feedback). *Forgot password* raises a
  **request**, never an automatic reset, and never reveals whether an account
  exists. Administrators work the request in **Admin → Approvals**: approve
  (issuing a system-generated or chosen temporary password, revoking existing
  sessions), reject, or disable the account — each outcome audited, notified
  and written to the ledger.

## 12 · The UI Improvement Law — premium pass

Executed in six verified phases; no feature, workflow, database rule or
authentication path was changed.

1. **Design system** — a 4px spacing rhythm, a seven-step type scale, one
   easing curve, an elevation ladder and a focus system replace what were
   21 ad-hoc font sizes and 22 ad-hoc padding values. Applied at existing
   computed values, so the foundation landed with no visual disturbance.
2. **Themes** — White mode moves to the Notion / Apple / Linear register:
   near-white canvas, hairline rules, near-black type, restrained shadows,
   and an ambient wash reduced to a whisper — industrial character carried
   by the molten accent and tabular data rather than by grey. Dark mode
   deepens toward the Halo Lab / Tesla register with real elevation
   separation. Black and Low Power keep the identical token contract.
3. **Components** — buttons gained press, focus and disabled feedback;
   tables gained breathing room, sticky headers and tabular numerals;
   cards and KPIs sit on the elevation ladder; forms share one label
   rhythm and a calm focus treatment; scrollbars and selection are styled.
4. **Dialogs** — the specified *Confirm logout?* dialog (retaining the
   auto-save disclosure the Logout Safety Law requires), and the specified
   approval alert: **New approval request → Heat → role and level →
   APPROVE / REJECT**, wired to the real approval endpoint. Rejection
   returns the heat through the audited Correction System — SMS-2 has no
   silent rejection path. The logout control is locked to a square key
   (`aspect-ratio:1`) at every breakpoint.
5. **Real-time** — a 2-second **pulse** (~450 bytes, ~11 ms) returns
   counters and signatures for eight domains: notifications, approvals,
   heat status, production, machine status, reports, users and quality.
   The client refreshes **only the domain whose signature changed**, and
   only the smallest region within it. A region is skipped while it holds
   keyboard focus, while a dialog is open over it, or while the tab is
   hidden — so the page never reloads, nothing flickers, and auto-save is
   never interrupted. Verified: a machine change moves the machine
   signature and leaves the heat component untouched.
6. **Responsive** — overflow, wrapping, table scrolling, dialog centring,
   chip and tab strips, landscape phones and 360px handsets hardened
   across 20 media queries.

## 13 · Frontend emergency fixes

The blank login page is fixed at the root: boot wiring is defensive (a
missing element logs and skips instead of killing the script), a global
error handler always reveals the login view, and the bundles are
version-stamped so a stale cached index.html can never pair with a newer
bundle again — after deploying, one hard refresh (Ctrl+Shift+R) clears any
previously stuck browser. The Login Page Law messages are exact ("Account
not found…", "This account has been disabled…", "Incorrect password…") and
"Session expired" can only appear for a genuine session problem. The four
themes are now visibly distinct the moment they open: indigo control-room
dark with violet/cyan ambient light, true-black with amber accents, purple
ambience and a soft cyan under-glow, pure-paper white with a single amber
hairline, and a one-gradient low-power mode. Alignment hardening covers the
login centring, rail icons, status-bar baselines, equal-height cards, Gantt
labels, and the phone drawer.

## 14 · Emergency: "Cannot access 'SFX' before initialization"

Root cause: the debug expose statement (`Object.assign(window, {... SFX,
PULSE ...})`) sat 290 lines above the `const SFX` declaration. A `const`
cannot be referenced before its line (temporal dead zone), so the whole
script halted there, boot() never ran, and the login page never appeared.

Fixes, in depth order: the expose now lives at the end of the file, after
every declaration, wrapped in try/catch; the fail-safe error listener is
the first statement in app.js, so any future startup error reveals the
login view instead of a blank screen; sound calls are individually guarded
so a broken sound system only mutes itself; the bundle version bumped to
?v52 so every browser fetches the fix on next load. And a new permanent
test, `backend/tests/frontend_boot_test.js`, EXECUTES the entire bundle in
a stub DOM on every verification run — proven to catch exactly this fault
class, which pure syntax checks cannot.

## 15 · Version 1 scope notes (honest engineering)

- **Live sync** uses efficient polling (dashboard refresh + 30 s notification
  poll) rather than WebSockets — simpler, firewall-proof on plant intranets,
  and easily upgraded later behind the same API.
- **AI Advisor** ships as the deterministic engines (validation, calculator,
  anomaly ranges, handover summaries). The `SMS2_AI_PROVIDER` seam exists for
  a future local LLM adapter; per the constitution AI is advisory-only, so v1
  ships with production-safe determinism and zero hallucination by construction.
- **PDF export**: reports render as print-clean pages (Ctrl+P) and Excel;
  dedicated PDF generation is a clean v1.1 addition.

— Built for SMS-2. Designed and implemented end-to-end from the Master
Architectural Constitution (M S Vignesh).
