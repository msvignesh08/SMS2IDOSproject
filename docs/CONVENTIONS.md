# SMS-2 IDOS — Operating Conventions & Frozen Decisions

## Plant Day & Shifts (constitutional — do not modify)
- Plant Day D = 22:00 of calendar (D−1) → 21:59:59 of calendar D.
- Shift order within a plant day: **C (22–06, crosses midnight) → A (06–14) → B (14–22)**.
- Example: 16-Jul 23:30 → Plant Day 17-Jul, Shift C.
- `backend/app/core/plant_time.py` is the ONLY component allowed to compute
  plant day/shift. All stored `plant_day`/`shift` columns are derived by it.

## Data integrity rules
- **Universal No-Delete**: chemistry samples, material additions and delays are
  *superseded* (flagged), never removed; users are *archived*; heats are never
  physically deleted. `heat_events` and `audit_log` are append-only.
- **Corrections**: supersede + re-enter. The original stays visible in the
  timeline and audit trail forever.
- **Locked heats** are read-only (HTTP 423). Unlock = Administrator + mandatory
  reason; existing approvals are superseded and must be re-signed.

## Approval chain (configurable default)
L1 Operator → L2 Shift Engineer → L3 Shift Incharge → LOCK.
Order enforced; each approval writes a digital signature
`SIG:<employee_id>:<timestamp>` to the permanent record.

## Route decision
- Made at LRF by Shift Engineer / Shift Incharge / Administrator only.
- Requires at least one live LRF chemistry sample first.
- VD-2/VD-3 insert the VD module; BC-2/BC-3 bypass it. Re-selection is audited.

## Validation philosophy
Blocking validation is **deterministic** (always available offline, cannot
hallucinate). Findings carry level (error blocks, warning rides along),
tile and message. When inputs are missing, engines state exactly what is
required rather than guessing (rule 7.10).

## Training mode
Training heats: identical workflow, excluded from daily reports, KPIs,
analytics, Report Library and Organizational Memory. Marked with a banner
and a TRAINING tag everywhere they appear.

## Concurrency
Stage records use optimistic locking (`version`). A save from a stale
workstation returns HTTP 409; the browser preserves the local draft
(localStorage) so no operator entry is ever lost.

## Extending the system (configuration over coding)
- New grade / material / delay reason / equipment / heat series → Admin UI.
- New role with any permission set → Admin UI (no code).
- New field on a module → add one line to
  `backend/app/engines/stage_config.py` (F(...) helper). No schema change.
- New plant → `plant_id` already exists on Heat for future multi-plant use.
- PostgreSQL → set `SMS2_DATABASE_URL`; nothing else changes.


## Master Constitution S.1–S.7 (frozen)
- Add-only evolution: features are added, extended, integrated — never
  removed. Existing designs are assumed intentional.
- Protected classes (never zero active): Administrator-class
  (Owner/Super Administrator/Administrator), Shift Incharge, Shift Engineer.
- Corrections are the ONLY modification path for locked data:
  request → approval → apply → RecordVersion → audit. Requester ≠ approver
  (Administrator-class excepted).
- The Smart Gantt is the heart of SMS-2: any new module must define its
  bars/overlays/markers there (`_collect_window` + `_machine_for` in
  dashboard router) before it is considered complete.
- The AI is read-only forever. It analyzes, explains, recommends, predicts
  and teaches. AI failure must never affect entry, validation, approvals
  or reports.
- Emergency recovery: `deploy/emergency_admin.py` on the server console.

## Final directive (frozen)
- Grade fields: free entry + search, never dropdown-only; grades auto-
  register at first use and live in the master forever.
- Concurrency: same-level authorities never silently overwrite each other;
  409 responses carry message + server data + version + last editor, and
  the UI must offer keep / view / merge / request-approval. "Reload page"
  is prohibited vocabulary.
- Notification kinds emergency/security/system/delay (and anything
  critical) are mandatory and bypass user preferences by design.
- Every keystroke lands in a local draft before the debounced server save.

## Notification & Premium UI Laws (frozen)
- /api/notifications is windowed ({items, has_more}, limit ≤200, offset, q,
  kind). Never unbounded, never lossy (archive-only).
- Continuity-guard denials emit kind="continuity" alerts + CONTINUITY-GUARD
  audit rows; the denial itself still returns 400.
- Themes: dark · black · light · lowpower. Low power = static premium, not
  degraded. All theming flows through the CSS token contract in app.css.
- New features must not add clutter: extend the drawer/panel patterns, never
  bolt on extra pages or button rows.

## The Validation Law (frozen)
- Never save-then-error: submit buttons are disabled until the server-computed
  requirement checklist is empty; the checklist renders as "Please complete
  the following:" and refreshes live after every auto-save keystroke.
- Mandatory fields highlight inline (" — required") while empty; drafts keep
  auto-saving throughout — guidance never causes data loss.
- Supersede/correct are the only removal-shaped verbs anywhere in the UI;
  physical deletion of production records does not exist in any code path.
- Modal action buttons sit on a solid sticky footer inside the scroll
  region — no viewport, zoom level or device can hide them.

## Dialog & action-gating fixes (frozen)
- UI.modal() structurally splits every dialog into a scrollable content
  region and a pinned action footer — the footer never enters the scroll
  area, so buttons cannot be hidden on any device or content length.
- Record/Add/Submit buttons in tiles gate on their own mandatory inputs
  (disabled + guiding tooltip until satisfied). Click-then-error paths are
  removed at the source.
- All removal-shaped controls read "⊘ Supersede" with an audit-history
  tooltip; red delete-styled ✕ buttons do not exist.

## The Reachability Law (frozen)
- A field may be mandatory ONLY if the interface renders an input for it.
  Tiles that combine data fields with a chemistry/materials/delay block must
  render BOTH: the process-reading inputs and the block editor.
- `tests/smoke_e2e.py` audits every stage definition on each run and fails
  the build if any required, non-auto field sits in a tile whose editor does
  not render inputs. Adding a field to `stage_config.py` therefore cannot
  reintroduce an unreachable mandatory field.
- Process readings inside chemistry tiles (e.g. EAF Bath Temperature °C)
  carry the full Auto Save Engine: local draft per keystroke, debounced
  server save, optimistic-lock conflict handling via the Synchronization
  Engine, live required-field highlighting, and immutable version snapshots.

## The Process Route Law (frozen)
- `required_stages(route, refining, has_zpf_data)` is the single source of
  truth for which stages a heat follows. ZPF appears only when the post-EAF
  decision selected it (or legacy data exists); VD only on a VD route.
- Never call `required_stages(heat.route)` directly — use
  `lifecycle.heat_required_stages(db, heat)` so routing stays heat-specific
  in gates, rails, validation, reports, Gantt and the machine board.
- New process modules must add their own decision point rather than being
  inserted into a fixed sequence.

## Logout / Sound / Credentials (frozen)
- `doLogout(false)` must always route through `confirmLogout()`; only session
  expiry may call `finishLogout(true)` directly.
- `SFX.play()` may be called from exactly two places: the new-notification
  handler (gated by `SFX.qualifies`) and the explicit test button. Never from
  auto-save, polling, Gantt, or report code paths.
- Only issued credentials are ever written to credentials.txt; a user-set
  password must always be recorded as `(user-managed)`.
- Password recovery is request → admin decision → issue/reject/disable, with
  session revocation on reset. No code path may reset a password without an
  administrator decision.

## UI system (frozen)
- Never hardcode a font-size, padding, radius, duration or easing. Use the
  Phase-1 tokens (--sp-*, --fs-*, --radius*, --dur-*, --ease).
- Every theme must define the full token contract, including --elev-1..3,
  --sh-1..3, --line-soft and --focus-ring. Test all four when adding one.
- Real-time updates go through PULSE.on(domain, fn). A handler must patch
  the smallest possible region and must call patchable()/swapRegion() so it
  cannot repaint over a focused input or an open dialog. Never call route()
  or re-render a whole view from a pulse handler.
- /api/pulse returns counters and signatures ONLY. Never add a payload to
  it; add a narrow endpoint and fetch it from the domain's handler instead.
- The logout control is a square key; the approval dialog always offers a
  real APPROVE and a real REJECT.

## Frontend emergency fixes (frozen)
- boot() wires every control through wire(): a missing element logs and is
  skipped, and a window error reveals the login view — a blank screen is
  structurally impossible. Never attach boot listeners directly.
- Bundle links carry a version query (?v51-...). Bump it in index.html on
  every frontend change so a stale cache can never mismatch HTML and JS.
- 401 from /api/auth/login or /forgot-password is a credential error shown
  verbatim; only other 401s mean session expiry. Login messages are fixed
  by the Login Page Law.
- Theme canvases are intentionally distinct (indigo dark, purple/cyan pure
  black, paper white with an amber hairline, single-gradient low power).
  Do not converge them.

## Execution-order law (frozen)
- app.js is one top-to-bottom script: const/let declarations (SFX, PULSE,
  ND, ...) must precede every top-level statement that references them.
  The debug expose lives at the END of the file, wrapped in try/catch,
  immediately before boot(). Never add a top-level reference above a
  declaration.
- The fail-safe error listener is the FIRST statement in app.js and must
  stay first: it reveals the login view on any startup error.
- Sound calls are wrapped: a broken sound system mutes itself and nothing
  else (Fail Safe Law).
- `node tests/frontend_boot_test.js` EXECUTES the bundle in a stub DOM and
  is part of every verification run — `node --check` alone is insufficient
  because it cannot catch temporal-dead-zone/order failures.
- Bump the ?v cache stamp in index.html on every frontend change.
