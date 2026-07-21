/* ═══════════════════════════════════════════════════════════════════════
   FAIL SAFE LAW — armed before any other statement can execute.
   Whatever fails below (sounds, pulse, any optional component), the login
   page ALWAYS loads: on any uncaught error during startup, reveal the
   login view and keep going. One component may die; the plant may not.
   ═══════════════════════════════════════════════════════════════════════ */
window.addEventListener("error", function smsFailSafe(e) {
  try {
    var app = document.getElementById("app");
    var login = document.getElementById("login-view");
    if (login && (!app || app.classList.contains("hidden")))
      login.classList.remove("hidden");
    console.error("SMS-2 fail-safe caught:", e && e.message);
  } catch (_) {}
});

/* ================================================================
   SMS-2 IDOS v1.0 — Digital Command Center SPA
   Vanilla JS, zero build chain, zero internet dependency (11.3).
   Single-maintainer sustainable by design (Simplicity over Complexity).
   ================================================================ */
"use strict";

/* ───────────────────────── helpers ───────────────────────── */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtN = (v, d = 1) => (v === null || v === undefined || v === "") ? "—"
  : Number(v).toLocaleString("en-IN", {maximumFractionDigits: d});
const fmtT = iso => iso ? String(iso).replace("T", " ").slice(0, 16) : "—";
const nowLocalISO = () => {
  const d = new Date(); d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

const S = {           // app state
  token: localStorage.getItem("sms2_token") || null,
  user: null, plant: null,
  stageDefs: null, sequence: [], masters: null,
  pollTimer: null, clockTimer: null,
};

async function api(method, path, body) {
  const opts = {method, headers: {"Content-Type": "application/json"}};
  if (S.token) opts.headers["Authorization"] = "Bearer " + S.token;
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (r.status === 401) {
    // "Session expired" may only appear for a genuine session problem —
    // never for a failed sign-in or recovery request (Login Page Law).
    if (path.startsWith("/api/auth/login") || path.startsWith("/api/auth/forgot-password")) {
      const d = await r.json().catch(() => ({}));
      throw Object.assign(new Error(d.detail || "Sign-in failed"), {status: 401, data: d});
    }
    doLogout(true); throw new Error("Session expired — please sign in again.");
  }
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    const msg = data && (data.detail || data.message) || `Request failed (${r.status})`;
    const e = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    e.status = r.status; e.data = data;
    throw e;
  }
  return data;
}
const GET = p => api("GET", p);
const POST = (p, b) => api("POST", p, b ?? {});
const PUT = (p, b) => api("PUT", p, b);
const DEL = p => api("DELETE", p);

async function download(path, fallbackName) {
  try {
    const r = await fetch(path, {headers: {"Authorization": "Bearer " + S.token}});
    if (!r.ok) { const d = await r.json().catch(() => null); throw new Error(d?.detail || "Download failed"); }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = /filename="?([^";]+)"?/.exec(cd);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : fallbackName;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("Report downloaded", "ok");
  } catch (e) { toast(e.message, "err"); }
}

function toast(msg, kind = "ok", ms = 3200) {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  $("#toast-root").appendChild(t);
  setTimeout(() => t.remove(), ms);
}

const UI = {
  modal(html, onMount) {
    // THE RESPONSIVE/NOTIFICATION LAW: every dialog = scrollable content region
    // + permanently visible action footer. Buttons can never leave the screen.
    const cut = html.lastIndexOf('<div class="modal-foot"');
    const inner = cut > -1
      ? `<div class="modal-scroll">${html.slice(0, cut)}</div>${html.slice(cut)}`
      : `<div class="modal-scroll">${html}</div>`;
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = `<div class="modal glass">${inner}</div>`;
    back.addEventListener("mousedown", e => { if (e.target === back) UI.closeModal(); });
    $("#modal-root").appendChild(back);
    document.addEventListener("keydown", UI._esc);
    if (onMount) onMount(back);
    return back;
  },
  _esc(e) { if (e.key === "Escape") UI.closeModal(); },
  closeModal() {
    const m = $("#modal-root").lastElementChild;
    if (m) m.remove();
    if (!$("#modal-root").children.length) document.removeEventListener("keydown", UI._esc);
  },
  closeDrawer() { $("#drawer").classList.add("hidden"); },
};

function confirmModal(title, body, okLabel, danger) {
  return new Promise(res => {
    const m = UI.modal(`
      <h3>${esc(title)}</h3><div class="sub">${body}</div>
      <div class="modal-foot">
        <button class="btn" data-a="no">Cancel</button>
        <button class="btn ${danger ? "danger" : "primary"}" data-a="yes">${esc(okLabel || "Confirm")}</button>
      </div>`);
    m.addEventListener("click", e => {
      const a = e.target.dataset?.a;
      if (a) { UI.closeModal(); res(a === "yes"); }
    });
  });
}
function promptModal(title, sub, placeholder) {
  return new Promise(res => {
    const m = UI.modal(`
      <h3>${esc(title)}</h3><div class="sub">${esc(sub || "")}</div>
      <label class="fld"><span>${esc(placeholder || "Reason")}</span><textarea id="pm-v"></textarea></label>
      <div class="modal-foot">
        <button class="btn" data-a="no">Cancel</button>
        <button class="btn primary" data-a="yes">Confirm</button>
      </div>`);
    m.addEventListener("click", e => {
      const a = e.target.dataset?.a;
      if (a === "no") { UI.closeModal(); res(null); }
      if (a === "yes") { const v = $("#pm-v", m).value; UI.closeModal(); res(v); }
    });
  });
}

const perms = () => S.user?.permissions || [];
const can = p => perms().includes(p) || perms().includes("admin");
const isAdmin = () => S.user?.role === "Administrator";

/* ───────────────────────── boot / auth ───────────────────────── */
/* Charging is now recorded on the EAF stage. Any unsaved local draft still
   filed under the retired "hot_metal" key is folded into that heat's EAF
   draft — an operator's typing is never silently dropped, and the logout
   safety check stops warning about a draft nothing can open. */
function migrateChargeDrafts() {
  const stale = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith("sms2_draft_") && k.endsWith("_hot_metal")) stale.push(k);
  }
  stale.forEach(k => {
    const target = k.replace(/_hot_metal$/, "_eaf");
    try {
      const old = JSON.parse(localStorage.getItem(k) || "{}");
      if (Object.keys(old).length) {
        const cur = JSON.parse(localStorage.getItem(target) || "{}");
        localStorage.setItem(target, JSON.stringify({ ...old, ...cur }));  // newer EAF entry wins
      }
    } catch {}
    localStorage.removeItem(k);
  });
}

async function boot() {
  migrateChargeDrafts();
  // Defensive wiring: a control room must come up even if one control is
  // missing (stale cache, partial deploy). Each listener attaches
  // independently; a failure is logged, never fatal.
  const wire = (sel, ev, fn) => {
    const el = $(sel);
    if (el) el.addEventListener(ev, fn);
    else console.error(`SMS-2 boot: ${sel} missing — hard-refresh (Ctrl+Shift+R) to clear a stale cache`);
  };
  wire("#login-btn", "click", doLogin);
  wire("#forgot-btn", "click", forgotPasswordDialog);
  wire("#login-pw", "keydown", e => { if (e.key === "Enter") doLogin(); });
  wire("#logout-btn", "click", () => doLogout(false));
  wire("#theme-btn", "click", cycleTheme);
  wire("#bell-btn", "click", () => { location.hash = "#/notifications"; });
  wire("#rail-more", "click", openMoreSheet);
  wire("#ms-close", "click", closeMoreSheet);
  wire("#more-sheet", "click", e => { if (e.target.id === "more-sheet") closeMoreSheet(); });
  window.addEventListener("hashchange", () => { closeMoreSheet(); route(); });
  window.addEventListener("keydown", e => { if (e.key === "Escape") closeMoreSheet(); });
  // Last line of defence: whatever else happens, the operator sees the login.
  window.addEventListener("error", () => {
    if ($("#app")?.classList.contains("hidden")) $("#login-view")?.classList.remove("hidden");
  });
  installContentProtection();
  wire("#contact-admin-btn", "click", contactAdminDialog);

  fetch("/health").then(r => r.json()).then(h =>
    $("#login-plant").textContent = `${h.app} v${h.version} · server online`).catch(() => {});

  if (S.token) {
    try {
      const me = await GET("/api/auth/me");
      S.user = me.user; S.plant = me.plant;
      enterApp();
      return;
    } catch { S.token = null; localStorage.removeItem("sms2_token"); }
  }
  $("#login-view").classList.remove("hidden");
}

/* ══ CONTENT PROTECTION ═══════════════════════════════════════════════════
   Production data is not casually copyable: selection, copy, cut, right-click
   and drag are suppressed across the interface.

   Deliberate exemptions, because the order also requires that this NEVER
   break workflows, exports, mobile or accessibility:
     · form fields stay fully selectable and editable — an operator must be
       able to correct a heat number, and Ctrl+C inside an input they are
       typing into is part of data entry, not exfiltration;
     · the credentials ledger and issued-password box stay selectable, since
       an administrator has to be able to hand a password over;
     · export buttons are untouched — Excel/PDF remain the sanctioned way
       data leaves the system;
     · keyboard navigation, focus and screen readers are unaffected: nothing
       here touches the accessibility tree, and text remains readable.

   Honest scope: this is a deterrent against casual copying, not a security
   control. Anything rendered in a browser can still be reached through
   devtools, view-source or a screenshot. It raises friction; it does not
   make data exfiltration impossible. */
function installContentProtection() {
  const EXEMPT = 'input, textarea, select, [contenteditable="true"], .cred-box, .cred-file, .selectable';
  const inExempt = t => !!(t && t.closest && t.closest(EXEMPT));

  document.addEventListener('copy',  e => { if (!inExempt(e.target)) e.preventDefault(); });
  document.addEventListener('cut',   e => { if (!inExempt(e.target)) e.preventDefault(); });
  document.addEventListener('contextmenu', e => { if (!inExempt(e.target)) e.preventDefault(); });
  document.addEventListener('dragstart',   e => { if (!inExempt(e.target)) e.preventDefault(); });

  document.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if ((k === 'c' || k === 'x') && !inExempt(document.activeElement)) e.preventDefault();
  });
}

/* Contact administrator — reachable from the login page, before any session
   exists, so it cannot depend on an authenticated endpoint. */
function contactAdminDialog() {
  UI.modal(`
    <h3>Contact administrator</h3>
    <div class="sub">Account, access and password issues are handled by the plant
      administrator — SMS-2 never resets a password automatically.</div>
    <div class="ca-grid">
      <div class="ca-row"><span>Account locked or disabled</span>
        <b>Administrator · Admin → Users</b></div>
      <div class="ca-row"><span>Forgotten password</span>
        <b>Raise a request from “Forgot password?”</b></div>
      <div class="ca-row"><span>New employee access</span>
        <b>Shift Incharge → Administrator</b></div>
      <div class="ca-row"><span>Plant system fault</span>
        <b>Control room · SMS-2 support desk</b></div>
    </div>
    <div class="finding info" style="margin-top:var(--sp-3)"><b>note</b>
      <div>A recovery request is a request, never an automatic reset. An
      administrator approves it and issues a temporary password.</div></div>
    <div class="modal-foot"><button class="btn primary" onclick="UI.closeModal()">Close</button></div>`);
}

/* ══ PHONE OVERFLOW NAVIGATION ══
   bottom-nav-limit: the bar carries 5 slots (4 primary + More); every other
   destination lives here, so nothing is removed from the product — only
   re-tiered. The sheet is built FROM the rail on each open, so editing the
   nav in index.html can never leave this stale, and it inherits the same
   role-based hiding (#nav-admin). Sign out is a separated destructive group
   rather than one more item in a scrolling strip. */
function buildMoreSheet() {
  const links = $("#ms-links"), util = $("#ms-utility");
  if (!links || !util) return;
  const items = $$('.rail a[data-tier="secondary"]').filter(a => a.style.display !== "none");
  links.innerHTML = items.map(a => `
    <a href="${a.getAttribute("href")}" data-nav="${a.dataset.nav}">
      <i aria-hidden="true">${esc((a.firstChild?.textContent || "").trim())}</i>
      <span>${esc(a.querySelector("span")?.textContent || "")}</span></a>`).join("");
  util.innerHTML = `
    <button class="ms-util" id="ms-theme"><i aria-hidden="true">◐</i><span>Switch theme</span></button>
    <button class="ms-util danger" id="ms-logout"><i aria-hidden="true">⏻</i><span>Sign out</span></button>`;
  const t = $("#ms-theme"), lo = $("#ms-logout");
  if (t) t.onclick = () => cycleTheme();
  if (lo) lo.onclick = () => { closeMoreSheet(); doLogout(false); };
}

function openMoreSheet() {
  buildMoreSheet();
  const s = $("#more-sheet");
  if (!s) return;
  s.classList.remove("hidden");
  $("#rail-more")?.setAttribute("aria-expanded", "true");
  s.querySelector("a,button")?.focus();
}

function closeMoreSheet() {
  const s = $("#more-sheet");
  if (!s || s.classList.contains("hidden")) return;
  s.classList.add("hidden");
  $("#rail-more")?.setAttribute("aria-expanded", "false");
}

async function doLogin() {
  const emp = $("#login-emp").value.trim(), pw = $("#login-pw").value;
  $("#login-err").textContent = "";
  if (!emp || !pw) { $("#login-err").textContent = "Enter employee ID and password."; return; }
  $("#login-btn").disabled = true;
  try {
    const r = await POST("/api/auth/login", {employee_id: emp, password: pw});
    S.token = r.token; S.user = r.user; S.plant = r.plant;
    localStorage.setItem("sms2_token", r.token);
    enterApp();
  } catch (e) { $("#login-err").textContent = e.message; }
  $("#login-btn").disabled = false;
}

function doLogout(expired) {
  // THE LOGOUT SAFETY LAW: never sign out on a single tap
  if (!expired) return confirmLogout();
  return finishLogout(true);
}

function finishLogout(expired) {
  if (!expired && S.token) POST("/api/auth/logout").catch(() => {});
  S.token = null; S.user = null;
  localStorage.removeItem("sms2_token");
  clearInterval(S.pollTimer); clearInterval(S.clockTimer); PULSE.stop();
  $("#app").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  if (expired) toast("Session expired — please sign in again.", "err");
}

function enterApp() {
  $("#login-view").classList.add("hidden");
  $("#app").classList.remove("hidden");
  document.body.dataset.theme = S.user.theme || "dark";
  $("#sb-name").textContent = S.user.name;
  $("#sb-role").textContent = `${S.user.role} · ${S.user.employee_id}`;
  $("#nav-admin").style.display = isAdmin() ? "" : "none";
  startClock(); pollNotifications(); PULSE.start();
  if (S.user.notification_prefs === null || S.user.notification_prefs === undefined)
    setTimeout(() => showNotifPrefs(true), 700);
  S.pollTimer = setInterval(pollNotifications, 60000);   // slow safety net; the 2s pulse leads
  if (!location.hash || location.hash === "#/") location.hash = "#/dashboard";
  route();
}

function startClock() {
  clearInterval(S.clockTimer);
  const tick = async () => {
    const d = new Date();
    $("#sb-clock").textContent =
      d.toLocaleTimeString("en-IN", {hour12: false});
    if (d.getSeconds() === 0 || !$("#sb-day").textContent.includes("-")) {
      try {
        const me = await GET("/api/auth/me");
        S.plant = me.plant;
      } catch {}
    }
    if (S.plant) { $("#sb-day").textContent = S.plant.plant_day; $("#sb-shift").textContent = S.plant.shift; }
  };
  tick();
  S.clockTimer = setInterval(tick, 1000);
}

function cycleTheme() {
  const order = ["dark", "black", "light", "lowpower", "signature"];
  const next = order[(order.indexOf(document.body.dataset.theme) + 1) % order.length];
  document.body.dataset.theme = next;
  POST("/api/auth/theme", {theme: next}).catch(() => {});
  const LABEL = {black:"pure black", lowpower:"low power (premium static)", signature:"Signature — showcase mode"};
  toast(`Theme: ${LABEL[next] || next}`, "ok", 1500);
}

/* ───────────────────────── notifications ───────────────────────── */
async function pollNotifications() {
  try {
    const list = (await GET("/api/notifications")).items;
    const unread = list.filter(n => !n.read).length;
    const b = $("#bell-count");
    b.classList.toggle("hidden", unread === 0);
    b.textContent = unread;
    S._notifs = list;
    onNewNotifications(list);
    if (!$("#drawer").classList.contains("hidden") && ND.offset === 0 && !ND.q && !ND.busy)
      loadNotifs(true);
    // Keep the dedicated page live under the same 2s engine.
    if (location.hash.startsWith("#/notifications") && $("#np-list") && !NP.busy && !NP.offset && !NP.q)
      loadNotifPage(true);
  } catch {}
}


/* ══ AUDIT & PERFORMANCE REPORT ════════════════════════════════════════════
   Produces a self-contained, presentation-ready dashboard as a single .html
   file: KPI tiles, production trend, delay Pareto, shift comparison, route
   usage, machine utilisation and an audit-trail summary.

   Deliberately dependency-free. SMS-2 runs on a plant intranet with zero
   internet access, so a BI library from a CDN would render a blank page on
   the very machine this is meant to be presented from. Every chart below is
   inline SVG built from the same data the dashboard already serves, and the
   file opens in any browser with no server, no plugin and no network.

   It is printed light-on-white regardless of the operator's theme: this is a
   deliverable for a projector, a PDF and a printer, not another screen. */

const RPT = {
  esc: s => String(s ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])),
  n: (v, d = 0) => (v === null || v === undefined || v === "" || isNaN(v))
    ? "—" : Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}),
};

/* Horizontal bar chart — used for Pareto and any ranked breakdown. */
function rptBars(rows, opts) {
  const o = Object.assign({unit: "", colour: "#C2410C", height: 26, labelW: 150}, opts || {});
  if (!rows.length) return `<p class="rpt-empty">No data in this period.</p>`;
  const max = Math.max(...rows.map(r => r.v), 1);
  const W = 640, H = rows.length * o.height + 8;
  return `<svg class="rpt-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="chart">
    ${rows.map((r, i) => {
      const y = i * o.height + 4, bw = Math.max((r.v / max) * (W - o.labelW - 74), 2);
      return `<g>
        <text x="0" y="${y + 14}" class="rpt-lbl">${RPT.esc(r.k).slice(0, 22)}</text>
        <rect x="${o.labelW}" y="${y + 3}" width="${bw}" height="14" rx="3" fill="${o.colour}"/>
        <text x="${o.labelW + bw + 7}" y="${y + 14}" class="rpt-val">${RPT.n(r.v, o.dp || 0)}${o.unit}</text>
      </g>`;
    }).join("")}
  </svg>`;
}

/* Vertical column chart — used for the day-by-day production trend. */
function rptCols(rows, opts) {
  const o = Object.assign({unit: "", colour: "#0F766E", dp: 0}, opts || {});
  if (!rows.length) return `<p class="rpt-empty">No data in this period.</p>`;
  const max = Math.max(...rows.map(r => r.v), 1);
  const W = 640, H = 190, P = 26, bw = (W - P * 2) / rows.length;
  return `<svg class="rpt-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="trend">
    <line x1="${P}" y1="${H - 26}" x2="${W - P}" y2="${H - 26}" class="rpt-axis"/>
    ${rows.map((r, i) => {
      const h = Math.max((r.v / max) * (H - 62), 1);
      const x = P + i * bw, y = H - 26 - h;
      return `<g>
        <rect x="${x + bw * 0.18}" y="${y}" width="${bw * 0.64}" height="${h}" rx="2" fill="${o.colour}"/>
        <text x="${x + bw / 2}" y="${y - 4}" class="rpt-col-v">${RPT.n(r.v, o.dp)}</text>
        <text x="${x + bw / 2}" y="${H - 12}" class="rpt-col-k">${RPT.esc(r.k)}</text>
      </g>`;
    }).join("")}
  </svg>`;
}

function rptKPI(label, value, unit, note) {
  return `<div class="rpt-kpi">
    <span class="rpt-kpi-l">${RPT.esc(label)}</span>
    <b class="rpt-kpi-v">${value}${unit ? `<i>${RPT.esc(unit)}</i>` : ""}</b>
    ${note ? `<span class="rpt-kpi-n">${RPT.esc(note)}</span>` : ""}
  </div>`;
}

async function buildAuditReport(days) {
  days = days || 14;
  const [dash, an, machines] = await Promise.all([
    GET("/api/dashboard").catch(() => null),
    GET(`/api/analytics?days=${days}`).catch(() => null),
    GET("/api/machines").catch(() => null),
  ]);
  // The audit trail is administrator-only; a non-admin gets the performance
  // report without it rather than a failed download.
  let audit = null;
  if (isAdmin()) audit = await GET("/api/admin/audit?limit=400").catch(() => null);

  const k = (dash && dash.kpis) || {};
  const trend = (an && an.trend) || [];
  const pareto = (an && an.delay_pareto) || [];
  const shifts = (an && an.shift_comparison) || {};
  const routes = (an && an.route_usage) || {};
  const window = (an && an.window) || ["—", "—"];

  const auditByCat = {};
  (audit || []).forEach(a => { auditByCat[a.category] = (auditByCat[a.category] || 0) + 1; });

  const totalDelay = trend.reduce((s, t) => s + (t.delay_min || 0), 0);
  const totalTonnes = trend.reduce((s, t) => s + (t.billet_t || 0), 0);
  const totalEnergy = trend.reduce((s, t) => s + (t.energy_kwh || 0), 0);
  const totalHeats = trend.reduce((s, t) => s + (t.heats || 0), 0);
  const kwhPerT = totalTonnes ? totalEnergy / totalTonnes : null;

  const gen = new Date().toLocaleString();
  const who = `${S.user?.name || "—"} · ${S.user?.role || ""}`;

  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMS-2 IDOS — Plant Audit &amp; Performance Report</title>
<style>
  /* Self-contained: system fonts only, no network, prints cleanly. */
  *{box-sizing:border-box}
  body{margin:0;background:#EEF1F5;color:#12181F;
    font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif;
    -webkit-font-smoothing:antialiased}
  .wrap{max-width:1180px;margin:0 auto;padding:26px 22px 44px}
  .hd{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;flex-wrap:wrap;
    padding:22px 26px;border-radius:12px;color:#fff;
    background:linear-gradient(120deg,#121A24 0%,#1D2A38 58%,#2B1B10 100%);
    box-shadow:0 10px 30px rgba(16,24,40,.18)}
  .hd h1{margin:0;font-size:23px;letter-spacing:-.02em}
  .hd .sub{margin:4px 0 0;opacity:.78;font-size:12.5px;letter-spacing:.12em;text-transform:uppercase}
  .hd .meta{text-align:right;font-size:12px;opacity:.86;line-height:1.7}
  .hd .meta b{font-weight:600}
  .accent{height:3px;border-radius:2px;margin:0 0 20px;
    background:linear-gradient(90deg,#F59E0B,#EA580C 46%,#BE123C)}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin:18px 0 22px}
  .rpt-kpi{background:#fff;border:1px solid #DCE2EA;border-radius:11px;padding:14px 16px;
    box-shadow:0 1px 2px rgba(16,24,40,.05)}
  .rpt-kpi-l{display:block;font-size:10.5px;font-weight:700;letter-spacing:.14em;
    text-transform:uppercase;color:#5A6673}
  .rpt-kpi-v{display:block;margin-top:5px;font-size:27px;line-height:1.15;font-weight:650;
    font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:#0E1520}
  .rpt-kpi-v i{font-style:normal;font-size:13px;color:#5A6673;margin-left:4px;font-weight:500}
  .rpt-kpi-n{display:block;margin-top:3px;font-size:11.5px;color:#5A6673}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .card{background:#fff;border:1px solid #DCE2EA;border-radius:12px;padding:16px 18px;
    box-shadow:0 1px 2px rgba(16,24,40,.05);break-inside:avoid}
  .card.full{grid-column:1/-1}
  .card h2{margin:0 0 3px;font-size:12px;font-weight:700;letter-spacing:.13em;
    text-transform:uppercase;color:#5A6673}
  .card .cap{margin:0 0 12px;font-size:12px;color:#79838F}
  .rpt-svg{width:100%;height:auto;display:block}
  .rpt-lbl{font-size:11px;fill:#3C4753}
  .rpt-val{font-size:11px;fill:#0E1520;font-weight:600}
  .rpt-col-v{font-size:9.5px;fill:#0E1520;text-anchor:middle;font-weight:600}
  .rpt-col-k{font-size:9px;fill:#79838F;text-anchor:middle}
  .rpt-axis{stroke:#DCE2EA;stroke-width:1}
  .rpt-empty{margin:8px 0;color:#79838F;font-size:12.5px}
  table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}
  th{text-align:left;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:#5A6673;
    border-bottom:1px solid #DCE2EA;padding:7px 8px}
  td{padding:7px 8px;border-bottom:1px solid #EDF0F4}
  tr:last-child td{border-bottom:none}
  .mono{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace}
  .ft{margin-top:26px;padding-top:16px;border-top:1px solid #DCE2EA;
    text-align:center;color:#79838F;font-size:12px;line-height:1.7}
  .ft b{color:#12181F}
  .ft .legal{font-size:10.5px;opacity:.8;margin-top:3px}
  @media (max-width:820px){.grid{grid-template-columns:1fr}.hd{flex-direction:column;align-items:flex-start}.hd .meta{text-align:left}}
  @media print{body{background:#fff}.wrap{max-width:none;padding:0}
    .card,.rpt-kpi{box-shadow:none}.hd{box-shadow:none}}
</style></head><body><div class="wrap">

<div class="hd">
  <div>
    <h1>Plant Audit &amp; Performance Report</h1>
    <p class="sub">SMS-2 IDOS · Steel Melting Shop</p>
  </div>
  <div class="meta">
    <div>Period <b>${RPT.esc(window[0])} → ${RPT.esc(window[1])}</b> (${days} plant days)</div>
    <div>Generated <b>${RPT.esc(gen)}</b></div>
    <div>By <b>${RPT.esc(who)}</b></div>
  </div>
</div>
<div class="accent"></div>

<div class="kpis">
  ${rptKPI("Heats produced", RPT.n(totalHeats), "", `${days}-day window`)}
  ${rptKPI("Billet output", RPT.n(totalTonnes, 1), "t", "cast and verified")}
  ${rptKPI("Energy", RPT.n(totalEnergy, 0), "kWh", "EAF total")}
  ${rptKPI("Specific energy", kwhPerT ? RPT.n(kwhPerT, 0) : "—", "kWh/t", "energy per tonne")}
  ${rptKPI("Delay burden", RPT.n(totalDelay, 0), "min", "all recorded delays")}
  ${rptKPI("Active heats", RPT.n(k.active_heats), "", "at time of export")}
</div>

<div class="grid">
  <div class="card full">
    <h2>Production trend</h2>
    <p class="cap">Heats completed per plant day. Plant day runs 22:00 → 22:00; C shift opens the day.</p>
    ${rptCols(trend.map(t => ({k: String(t.plant_day).slice(5), v: t.heats})), {colour: "#0F766E"})}
  </div>

  <div class="card full">
    <h2>Billet output per plant day</h2>
    <p class="cap">Tonnes cast, excluding training heats.</p>
    ${rptCols(trend.map(t => ({k: String(t.plant_day).slice(5), v: t.billet_t})), {colour: "#B45309", dp: 1})}
  </div>

  <div class="card">
    <h2>Delay Pareto</h2>
    <p class="cap">Ranked by total minutes lost — the largest bar is where recovery pays most.</p>
    ${rptBars(pareto.map(p => ({k: p.category, v: p.minutes})), {unit: " min", colour: "#BE123C"})}
  </div>

  <div class="card">
    <h2>Shift comparison</h2>
    <p class="cap">Heats started per shift, with the count already locked.</p>
    ${rptBars(Object.entries(shifts).map(([s, v]) =>
        ({k: `Shift ${s}  (${v.locked} locked)`, v: v.heats})), {unit: " heats", colour: "#1D4ED8"})}
  </div>

  <div class="card">
    <h2>Route usage</h2>
    <p class="cap">Every heat follows its own route — ZPF is never mandatory.</p>
    ${rptBars(Object.entries(routes).map(([r, v]) => ({k: r, v})), {unit: "", colour: "#7C3AED"})}
  </div>

  <div class="card">
    <h2>Machine utilisation</h2>
    <p class="cap">Live utilisation at time of export.</p>
    ${rptBars(((machines && machines.machines) || []).map(m => ({k: m.code, v: m.utilization_pct || 0})),
              {unit: " %", colour: "#0F766E"})}
  </div>

  ${audit ? `
  <div class="card">
    <h2>Audit trail — activity by category</h2>
    <p class="cap">Append-only log. Records are superseded and corrected, never deleted.</p>
    ${rptBars(Object.entries(auditByCat).sort((a, b) => b[1] - a[1]).map(([c, n]) => ({k: c, v: n})),
              {unit: "", colour: "#475569"})}
  </div>
  <div class="card full">
    <h2>Audit trail — most recent entries</h2>
    <p class="cap">Latest ${Math.min(25, audit.length)} of ${audit.length} records in the export window.</p>
    <table><thead><tr><th>Timestamp</th><th>User</th><th>Category</th><th>Action</th><th>Heat</th></tr></thead>
    <tbody>${audit.slice(0, 25).map(a => `<tr>
      <td class="mono">${RPT.esc(a.at)}</td><td>${RPT.esc(a.user)}</td>
      <td>${RPT.esc(a.category)}</td><td>${RPT.esc(a.action)}</td>
      <td class="mono">${RPT.esc(a.heat_no || "—")}</td></tr>`).join("")}
    </tbody></table>
  </div>` : `
  <div class="card">
    <h2>Audit trail</h2>
    <p class="cap">The audit log is restricted to Administrator-class roles and is
    therefore not included in this export.</p>
  </div>`}
</div>

<div class="ft">
  <div><b>SMS-2 IDOS</b> · Steel Plant Digital Production Management System</div>
  <div class="legal">Generated from live production records. Training heats are excluded throughout.</div>
</div>

</div></body></html>`;
}

async function downloadAuditReport(days) {
  const btn = $("#rp-audit");
  const old = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = "Building report…"; }
  try {
    const html = await buildAuditReport(days || 14);
    const blob = new Blob([html], {type: "text/html;charset=utf-8"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `SMS2_Audit_Report_${(S.plant?.plant_day || new Date().toISOString().slice(0, 10))}.html`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast("Audit report downloaded — open it in any browser to present", "ok", 3600);
  } catch (e) {
    toast("Could not build the report: " + e.message, "err", 5000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
}

/* ══ NOTIFICATION CENTRE — the dedicated page ══════════════════════════════
   The bell now opens a full page rather than only the side drawer, because a
   control room needs to READ notifications, not peek at them.

   Four categories, ordered by how urgently a human must act. Every kind the
   backend emits is mapped, and anything unmapped falls through to Updates, so
   a new notification kind can never silently disappear from the centre:

     EMERGENCIES  emergency · security · continuity, plus ANY critical
                  priority regardless of kind — a critical delay is an
                  emergency even though `delay` normally sits under Alerts.
     APPROVALS    approval · correction · submit — someone is waiting.
     ALERTS       delay(s) · ai · sync · machine, plus any remaining high
                  priority — attention needed, production not stopped.
     UPDATES      production · heat · shift · report · daily · system ·
                  admin · autosave — informational, and the fallback.

   The drawer is untouched and still works; this is an addition, not a
   replacement, so no existing behaviour is lost. */
const N_CATS = [
  ["all",       "All",         null,                                          "All"],
  ["emergency", "Emergencies", ["emergency", "security", "continuity"],       "Emergency"],
  ["approval",  "Approvals",   ["approval", "correction", "submit"],          "Approval"],
  ["alert",     "Alerts",      ["delay", "delays", "ai", "sync", "machine"],  "Alert"],
  ["updates",   "Updates",     ["production", "heat", "shift", "report", "daily",
                                "system", "admin", "autosave"],               "Update"],
];
/* [key, plural label for the tab, kinds it owns, SINGULAR label for the badge] */
/* Priority outranks kind: a critical anything is an emergency. */
function catOf(n) {
  if (n.priority === "critical") return "emergency";
  for (const [key, , kinds] of N_CATS) {
    if (kinds && kinds.includes(n.kind)) {
      if (key === "updates" && n.priority === "high") return "alert";
      return key;
    }
  }
  return n.priority === "high" ? "alert" : "updates";
}

const NP = {q: "", cat: "all", offset: 0, items: [], has_more: false, busy: false, unreadOnly: false};

async function viewNotifications() {
  setTitle("Notification Centre");
  $("#view").innerHTML = `
    <h2 class="pg">Notification Centre <small>every alert, approval and emergency in one place</small></h2>
    <div class="filter-bar card glass">
      <label class="fld wide"><span>Search</span>
        <input id="np-q" placeholder="heat number, title, message…" value="${esc(NP.q)}"></label>
      <label class="fld"><span>Show</span>
        <select id="np-unread">
          <option value="">all notifications</option>
          <option value="1" ${NP.unreadOnly ? "selected" : ""}>unread only</option>
        </select></label>
      <button class="btn" id="np-readall">✓ Mark all read</button>
      <button class="btn" id="np-prefs">🔔 Preferences</button>
    </div>
    <div class="nc-cats" id="np-cats"></div>
    <div id="np-list" class="sec"><div class="empty">Loading notifications…</div></div>
    <div id="np-more" class="sec"></div>`;

  let qt = null;
  $("#np-q").addEventListener("input", () => {
    clearTimeout(qt);
    qt = setTimeout(() => { NP.q = $("#np-q").value.trim(); loadNotifPage(true); }, 300);
  });
  $("#np-unread").onchange = e => { NP.unreadOnly = !!e.target.value; renderNotifPage(); };
  $("#np-prefs").onclick = () => showNotifPrefs(false);
  $("#np-readall").onclick = async () => {
    try {
      const r = await POST("/api/notifications/read-all");
      toast(`${r.marked} notification(s) marked read`, "ok", 2200);
      loadNotifPage(true); pollNotifications();
    } catch (e) { toast(e.message, "err"); }
  };
  await loadNotifPage(true);
}

async function loadNotifPage(reset) {
  if (NP.busy) return;
  NP.busy = true;
  try {
    if (reset) { NP.offset = 0; NP.items = []; }
    const p = new URLSearchParams({limit: 80, offset: NP.offset});
    if (NP.q) p.set("q", NP.q);
    const r = await GET("/api/notifications?" + p.toString());
    NP.items = NP.offset ? NP.items.concat(r.items) : r.items;
    NP.has_more = r.has_more;
    renderNotifPage();
  } catch (e) {
    const l = $("#np-list");
    if (l) l.innerHTML = `<div class="card glass empty">Could not load notifications — ${esc(e.message)}</div>`;
  }
  NP.busy = false;
}

function renderNotifPage() {
  const cats = $("#np-cats"), list = $("#np-list"), more = $("#np-more");
  if (!cats || !list) return;

  const counts = {};
  NP.items.forEach(n => {
    const c = catOf(n);
    counts[c] = counts[c] || {t: 0, u: 0};
    counts[c].t++; if (!n.read) counts[c].u++;
  });

  cats.innerHTML = N_CATS.map(([key, label]) => {
    const c = key === "all"
      ? {t: NP.items.length, u: NP.items.filter(n => !n.read).length}
      : (counts[key] || {t: 0, u: 0});
    return `<button class="nc-cat ${key} ${NP.cat === key ? "on" : ""}" data-c="${key}"
        aria-pressed="${NP.cat === key}">
        <span class="nc-label">${label}</span>
        <span class="nc-count">${c.t}</span>
        ${c.u ? `<span class="nc-unread">${c.u} new</span>` : ""}
      </button>`;
  }).join("");
  cats.onclick = e => {
    const c = e.target.closest("[data-c]")?.dataset.c;
    if (c) { NP.cat = c; renderNotifPage(); }
  };

  let rows = NP.items.filter(n => NP.cat === "all" || catOf(n) === NP.cat);
  if (NP.unreadOnly) rows = rows.filter(n => !n.read);

  list.innerHTML = rows.length ? rows.map(n => {
    const cat = catOf(n);
    return `
    <article class="nc-item ${cat} ${n.read ? "" : "unread"}" data-id="${n.id}">
      <span class="nc-rail" aria-hidden="true"></span>
      <div class="nc-body">
        <div class="nc-top">
          <b class="nc-title">${esc(n.title)}</b>
          <span class="nc-badge ${cat}">${N_CATS.find(c => c[0] === cat)[3]}</span>
          ${n.priority && n.priority !== "normal" ? `<span class="tag ${n.priority === "critical" ? "error" : "warn"}">${esc(n.priority)}</span>` : ""}
          ${n.read ? "" : `<span class="nc-dot" title="Unread"></span>`}
        </div>
        ${n.body ? `<p class="nc-text">${esc(n.body)}</p>` : ""}
        <div class="nc-meta">
          <time>${esc(n.at.replace("T", " ").slice(0, 16))}</time>
          <span class="nc-kind">${esc(n.kind)}</span>
          ${n.heat_no ? `<a class="link mono" href="#/heat/${esc(n.heat_no)}">${esc(n.heat_no)}</a>` : ""}
        </div>
      </div>
      ${n.read ? "" : `<button class="btn small nc-read" data-read="${n.id}">Mark read</button>`}
    </article>`;
  }).join("") : `<div class="card glass empty">${
      NP.q ? "No notifications match this search."
           : NP.unreadOnly ? "Nothing unread in this category."
           : "No notifications in this category."}</div>`;

  list.onclick = async e => {
    const id = e.target.closest("[data-read]")?.dataset.read;
    if (!id) return;
    try {
      await POST(`/api/notifications/${id}/read`);
      const item = NP.items.find(x => String(x.id) === String(id));
      if (item) item.read = true;
      renderNotifPage(); pollNotifications();
    } catch (err) { toast(err.message, "err"); }
  };

  more.innerHTML = NP.has_more
    ? `<button class="btn wide" id="np-load">Load older notifications ↓</button>` : "";
  const lm = $("#np-load");
  if (lm) lm.onclick = () => { NP.offset = NP.items.length; loadNotifPage(false); };
}

/* ═══ NOTIFICATION CENTER v2 (The Notification Law) ═══
   Scales to 1000+ · scrollable everywhere · grouped · searchable ·
   mark-all · live while open · preferences one tap away. */
const ND = {q: "", group: "all", offset: 0, items: [], has_more: false, busy: false};
const N_GROUPS = [
  ["all", "All", null],
  ["production", "Production", ["production", "delay", "shift", "autosave", "heat"]],
  ["approval", "Approvals", ["approval"]],
  ["ai", "AI", ["ai"]],
  ["conflict", "Conflict", ["sync"]],
  ["report", "Reports", ["report"]],
  ["system", "System", ["system", "admin", "security", "emergency"]],
  ["continuity", "Continuity", ["continuity"]],
];
const groupOf = kind => (N_GROUPS.find(g => g[2] && g[2].includes(kind)) || N_GROUPS[0])[0];

function openDrawer() {
  const d = $("#drawer"); d.classList.remove("hidden");
  $("#drawer-body").innerHTML = `
    <div class="nd-tools">
      <input id="nd-q" placeholder="Search notifications…" value="${esc(ND.q)}">
      <button class="btn small" id="nd-readall" title="Mark all as read">✓ All read</button>
      <button class="btn small" onclick="showNotifPrefs(false)" title="Notification preferences">🔔</button>
    </div>
    <div class="nd-chips" id="nd-chips"></div>
    <div class="nd-list" id="nd-list"><div class="empty">Loading…</div></div>
    <div id="nd-more"></div>`;
  let qt = null;
  $("#nd-q").addEventListener("input", () => {
    clearTimeout(qt); qt = setTimeout(() => { ND.q = $("#nd-q").value.trim(); loadNotifs(true); }, 300);
  });
  $("#nd-readall").onclick = async () => {
    try {
      const r = await POST("/api/notifications/read-all");
      toast(`${r.marked} notification(s) marked read`, "ok", 2200);
      loadNotifs(true); pollNotifications();
    } catch (e) { toast(e.message, "err"); }
  };
  loadNotifs(true);
}

async function loadNotifs(reset) {
  if (ND.busy) return; ND.busy = true;
  try {
    if (reset) { ND.offset = 0; ND.items = []; }
    const p = new URLSearchParams({limit: 80, offset: ND.offset});
    if (ND.q) p.set("q", ND.q);
    const r = await GET("/api/notifications?" + p.toString());
    ND.items = ND.offset ? ND.items.concat(r.items) : r.items;
    ND.has_more = r.has_more;
    renderNotifs();
  } catch (e) { toast(e.message, "err"); }
  ND.busy = false;
}

function renderNotifs() {
  const chips = $("#nd-chips"), list = $("#nd-list");
  if (!chips || !list) return;
  const counts = {};
  ND.items.forEach(n => {
    const g = groupOf(n.kind);
    counts[g] = counts[g] || {t: 0, u: 0};
    counts[g].t++; if (!n.read) counts[g].u++;
  });
  chips.innerHTML = N_GROUPS.map(([k, label]) => {
    const c = k === "all"
      ? {t: ND.items.length, u: ND.items.filter(n => !n.read).length}
      : (counts[k] || {t: 0, u: 0});
    if (k !== "all" && !c.t) return "";
    return `<span class="sg-chip ${ND.group === k ? "on" : ""}" data-g="${k}">
      ${label}${c.u ? ` <b>${c.u}</b>` : ""}</span>`;
  }).join("");
  chips.onclick = e => {
    const g = e.target.closest("[data-g]")?.dataset.g;
    if (g) { ND.group = g; renderNotifs(); }
  };
  const rows = ND.items.filter(n => ND.group === "all" || groupOf(n.kind) === ND.group);
  const scrollKeep = list.scrollTop;
  list.innerHTML = rows.length ? rows.map(n => `
    <div class="notif ${n.read ? "" : "unread"} ${n.priority}" data-id="${n.id}">
      <div class="row spread"><b>${esc(n.title)}</b>
        ${n.heat_no ? `<a class="link mono" href="#/heat/${n.heat_no}">${n.heat_no}</a>` : ""}</div>
      ${n.body ? `<div class="small">${esc(n.body)}</div>` : ""}
      <div class="n-meta">${n.at.slice(5, 16)} · ${groupOf(n.kind)}${n.priority !== "normal" ? " · " + n.priority : ""}${n.read ? "" : " · tap to mark read"}</div>
    </div>`).join("") : `<div class="empty">${ND.q ? "No notifications match the search." : "No notifications."}</div>`;
  list.scrollTop = scrollKeep;
  $("#nd-more").innerHTML = ND.has_more
    ? `<button class="btn small" style="width:100%" onclick="ND.offset+=80;loadNotifs(false)">Load older notifications ↓</button>` : "";
  list.onclick = async e => {
    const el = e.target.closest(".notif");
    if (el && el.classList.contains("unread")) {
      await POST(`/api/notifications/${el.dataset.id}/read`).catch(() => {});
      el.classList.remove("unread");
      const it = ND.items.find(x => x.id === +el.dataset.id);
      if (it) it.read = true;
      pollNotifications(); renderNotifs();
    }
  };
}

/* ───────────────────────── router ───────────────────────── */
const routes = {
  dashboard: viewDashboard, entry: viewEntry, heats: viewHeats,
  reports: viewReports, search: viewSearch, calculator: viewCalculator,
  knowledge: viewKnowledge, admin: viewAdmin, heat: viewHeatWorkspace,
  ai: viewAI, notifications: viewNotifications,
};
async function route() {
  const parts = location.hash.replace(/^#\//, "").split("/");
  const name = parts[0] || "dashboard";
  document.body.dataset.view = name;
  $$(".rail a").forEach(a => a.classList.toggle("active", a.dataset.nav === name || (name === "heat" && a.dataset.nav === "entry")));
  $("#training-banner").classList.add("hidden");
  const fn = routes[name] || viewDashboard;
  $("#view").innerHTML = `<div class="empty">Loading…</div>`;
  try { await fn(...parts.slice(1)); }
  catch (e) { $("#view").innerHTML = `<div class="card glass"><h3>Could not load this view</h3><div>${esc(e.message)}</div></div>`; }
}
function setTitle(t) { $("#sb-title").textContent = t; }

/* ensure stage definitions + masters are cached */
async function ensureDefs() {
  if (!S.stageDefs) {
    const d = await GET("/api/stage-defs");
    S.stageDefs = d.stages; S.sequence = d.sequence;
  }
  if (!S.masters) S.masters = await GET("/api/admin/masters");
}

/* ───────────────────────── HEAT RAIL (signature) ───────────────────────── */
function heatRail(h) {
  const nodes = h.steps.map((s, i) => `
    ${i ? `<div class="hr-seg ${h.steps[i - 1].state === "done" ? "done" : ""}"></div>` : ""}
    <div class="hr-node ${s.state}" title="${esc(s.title)}"></div>`).join("");
  const labels = h.steps.map(s =>
    `<span class="${s.state === "running" || s.state === "open" ? "on" : ""}">${esc(s.title.split(" ")[0])}</span>`).join("");
  return `
  <div class="heat-rail glass" onclick="location.hash='#/heat/${h.heat_no}'">
    <div class="hr-top">
      <span class="hr-no">${h.heat_no}</span>
      <span class="tag ${h.status}">${h.status.replace("_", " ")}</span>
      ${h.is_training ? `<span class="tag train">training</span>` : ""}
      <span class="hr-meta">${esc(h.grade || "")} · Shift ${h.shift} · Route ${h.route || "—"} · SE ${esc(h.shift_engineer || "—")}</span>
    </div>
    <div class="hr-track">${nodes}</div>
    <div class="hr-labels">${labels}</div>
  </div>`;
}

/* ═══════════════ DIGITAL COMMAND CENTER — built around the ═══════════════
   ═══ SMART SWITCHABLE INDUSTRIAL GANTT CHART v2 (Constitution S.4) ═══════ */
const SG = {mode: "heat", span: "day", range: "today", day: null, days: 1,
            zoom: 1, hidden: new Set(), timer: null};

async function viewDashboard() {
  setTitle("Digital Command Center");
  const [d, corrections] = await Promise.all([
    GET("/api/dashboard"),
    GET("/api/corrections?status=pending").catch(() => []),
  ]);
  S.plant = d.plant;
  const k = d.kpis, at = d.attention;

  const attRows = [];
  at.pending_approvals.forEach(p => attRows.push(`
    <tr><td><a class="link mono" href="#/heat/${p.heat_no}">${p.heat_no}</a></td>
      <td>Awaiting <b>${esc(p.awaiting)}</b> approval (L${p.level})</td>
      <td>${p.mine ? `<button class="btn small primary" onclick="quickApprove('${p.heat_no}',${p.level})">Approve</button>` : ""}</td></tr>`));
  corrections.forEach(c => attRows.push(`
    <tr><td><a class="link mono" href="#/heat/${c.heat_no}">${c.heat_no}</a></td>
      <td><span class="tag warn">correction pending</span> ${esc(c.stage_title)}: ${Object.keys(c.changes).map(esc).join(", ")} — ${esc(c.reason)}</td>
      <td>${c.can_decide ? `<button class="btn small primary" onclick="decideCorrection(${c.id})">Review</button>` : ""}</td></tr>`));
  at.open_delays.forEach(x => attRows.push(`
    <tr><td><a class="link mono" href="#/heat/${x.heat_no}">${x.heat_no}</a></td>
      <td><span class="tag warn">open delay</span> ${esc(x.stage)} — ${esc(x.reason || x.remarks || "")} since ${x.since.slice(11, 16)}</td><td></td></tr>`));
  at.route_pending.forEach(hn => attRows.push(`
    <tr><td><a class="link mono" href="#/heat/${hn}">${hn}</a></td>
      <td><span class="tag open">route pending</span> Shift Engineer decision required at LRF</td><td></td></tr>`));

  $("#view").innerHTML = `
    <!-- ═══ THE HEART: SMART SWITCHABLE INDUSTRIAL GANTT CHART ═══ -->
    <div class="sg-hero glass" id="sg-hero">
      <div class="sg-head">
        <span class="sg-title">Smart Industrial Gantt</span>
        <span class="sg-live">Live</span>
        <div class="sg-switch" id="sg-mode">
          <button data-v="heat" class="on">Heat</button>
          <button data-v="machine">Machine</button>
          <button data-v="shift">Shift</button>
          <button data-v="delays">Delays</button>
          <button data-v="production">Production</button>
        </div>
        <div class="sg-range">
          <select id="sg-range">
            <option value="shift">This shift</option>
            <option value="today" selected>Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="custom">Custom…</option>
          </select>
          <input type="date" id="sg-date" class="hidden">
          <input type="number" id="sg-days" class="hidden" min="1" max="31" value="1" style="width:64px" title="days">
        </div>
        <div class="sg-zoom" title="Zoom timeline">
          <button id="sg-zo">−</button><button id="sg-zr">⤢</button><button id="sg-zi">＋</button>
        </div>
        <div class="sg-sum" id="sg-sum"></div>
      </div>
      <div id="sg-chips" class="sg-chips hidden"></div>
      <div class="sg-body grab" id="sg-scroll"><div class="sg-grid" id="sg-grid"><div class="sg-empty">Fusing plant data…</div></div></div>
      <!-- Swatch colours come from the stage tokens in app.css, so the legend
           can never drift from the bars it describes. -->
      <div class="sg-legend">
        <span><i data-sw="eaf"></i>EAF</span>
        <span><i data-sw="zpf"></i>ZPF</span>
        <span><i data-sw="lrf"></i>LRF</span>
        <span><i data-sw="vd"></i>VD</span>
        <span><i data-sw="caster"></i>Caster / LTC</span>
        <span><i data-sw="quality"></i>Quality</span>
        <span><i data-sw="delay"></i>Delay</span>
        <span><i data-sw="shutdown"></i>Shutdown ≥60m</span>
        <span><i data-sw="approval"></i>Approval / Lock</span>
        <span><i data-sw="route"></i>Route</span>
        <span><i data-sw="validation"></i>Validation / Correction</span>
      </div>
    </div>

    <!-- ═══ DIGITAL TWIN: LIVE MACHINE INTELLIGENCE (S.5) ═══ -->
    <div class="sec">
      <h3 class="eyebrow">Machine Status Board — the plant, live</h3>
      <div class="mb-grid" id="mb-grid"><div class="card glass empty">Reading machines…</div></div>
    </div>

    <div class="grid cols-4 sec" id="kpi-grid">
      <div class="kpi glass hot"><b data-k="active_heats">${k.active_heats}</b><span>Active heats</span></div>
      <div class="kpi glass"><b data-k="completed_today">${k.completed_today}</b><span>Completed today</span></div>
      <div class="kpi glass"><b data-k="billet_t">${fmtN(k.billet_t)}</b><span>Billet tonnes</span></div>
      <div class="kpi glass"><b data-k="billets">${k.billets}</b><span>Billets</span></div>
      <div class="kpi glass"><b data-k="energy_kwh">${fmtN(k.energy_kwh, 0)}</b><span>Energy kWh</span></div>
      <div class="kpi glass"><b data-k="o2_nm3">${fmtN(k.o2_nm3, 0)}</b><span>Oxygen Nm³</span></div>
      <div class="kpi glass"><b data-k="delay_min">${fmtN(k.delay_min, 0)}</b><span>Delay minutes</span></div>
      <div class="kpi glass"><b data-k="shift_heats">${k.shift_heats}</b><span>Heats this shift</span></div>
    </div>

    <div class="sec card glass">
      <h3>Needs attention</h3>
      <div id="att-region">${attRows.length ? `<table class="t"><tbody>${attRows.join("")}</tbody></table>`
        : `<div class="empty">Nothing pending — production is flowing.</div>`}</div>
    </div>

    <div class="sec">
      <div class="row spread"><h3 class="eyebrow tight">Live heats — Digital DNA</h3>
        <button class="btn small" onclick="route()">Refresh</button></div>
      <div class="grid" style="margin-top:10px">
        ${d.active_heats.length ? d.active_heats.map(heatRail).join("")
          : `<div class="card glass empty">No active heats. Start one from Manual Entry.</div>`}
      </div>
    </div>

    <div class="grid cols-2 sec">
      <div class="card glass">
        <h3>Recent activity</h3>
        <div class="tline">${d.recent.slice(0, 12).map(e => `
          <div class="tl-ev k-${e.kind}"><time>${e.at.slice(5, 16)}</time>
            ${e.heat_no ? `<a class="link mono" href="#/heat/${e.heat_no}">${e.heat_no}</a> ` : ""}
            ${esc(e.detail)} <span class="who">— ${esc(e.actor)}</span></div>`).join("") || `<div class="empty">No events yet.</div>`}
        </div>
      </div>
      <div class="card glass">
        <h3>Shift handover intelligence</h3>
        <div id="handover"><button class="btn" onclick="loadHandover()">Generate handover summary</button></div>
      </div>
    </div>`;

  // controls
  $("#sg-mode").onclick = e => { const v = e.target.dataset?.v; if (v) { SG.mode = v; refreshGantt(); } };
  $("#sg-range").value = SG.range;
  $("#sg-range").onchange = () => {
    SG.range = $("#sg-range").value;
    const custom = SG.range === "custom";
    $("#sg-date").classList.toggle("hidden", !custom);
    $("#sg-days").classList.toggle("hidden", !custom);
    if (!custom) refreshGantt();
  };
  $("#sg-date").onchange = $("#sg-days").onchange = () => refreshGantt();
  $("#sg-zi").onclick = () => { SG.zoom = Math.min(SG.zoom * 1.5, 6); applyZoom(); };
  $("#sg-zo").onclick = () => { SG.zoom = Math.max(SG.zoom / 1.5, 1); applyZoom(); };
  $("#sg-zr").onclick = () => { SG.zoom = 1; applyZoom(); };
  // drag-to-pan
  const sc = $("#sg-scroll");
  let drag = null;
  sc.addEventListener("mousedown", e => {
    if (e.target.closest(".sg-bar,.sg-ev,.sg-delay,a,button")) return;
    drag = {x: e.clientX, left: sc.scrollLeft};
    sc.classList.add("grabbing");
  });
  addEventListener("mousemove", e => { if (drag) sc.scrollLeft = drag.left - (e.clientX - drag.x); });
  addEventListener("mouseup", () => { drag = null; sc.classList.remove("grabbing"); });
  // MOBILE: pinch-to-zoom + smooth touch panning on the Smart Gantt
  let pinch = null;
  const dist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  sc.addEventListener("touchstart", e => {
    if (e.touches.length === 2) pinch = {d: dist(e.touches), zoom: SG.zoom};
  }, {passive: true});
  sc.addEventListener("touchmove", e => {
    if (pinch && e.touches.length === 2) {
      SG.zoom = Math.min(6, Math.max(1, pinch.zoom * dist(e.touches) / pinch.d));
      applyZoom();
    }
  }, {passive: true});
  sc.addEventListener("touchend", () => { pinch = null; }, {passive: true});

  await Promise.all([refreshGantt(), loadMachines()]);
  clearInterval(SG.timer);
  SG.timer = setInterval(() => {
    // Safety net only — the 2-second pulse refreshes these the moment
    // something actually changes, so this rarely does any work.
    if (location.hash.startsWith("#/dashboard") && $("#sg-grid")) { refreshGantt(true); loadMachines(true); }
    else clearInterval(SG.timer);
  }, 120000);
}

function applyZoom() {
  const g = $("#sg-grid");
  if (g) g.style.minWidth = (760 * SG.zoom) + "px";
}

function _rangeParams() {
  const today = S.plant?.plant_day || new Date().toISOString().slice(0, 10);
  const shift = (d, n) => { const x = new Date(d + "T00:00:00"); x.setDate(x.getDate() - n); return x.toISOString().slice(0, 10); };
  switch (SG.range) {
    case "shift": return {span: "shift", plant_day: today, days: 1};
    case "yesterday": return {span: "day", plant_day: shift(today, 1), days: 1};
    case "7d": return {span: "day", plant_day: today, days: 7};
    case "30d": return {span: "day", plant_day: today, days: 30};
    case "custom": return {span: "day", plant_day: $("#sg-date")?.value || today,
                           days: +($("#sg-days")?.value || 1)};
    default: return {span: "day", plant_day: today, days: 1};
  }
}

async function refreshGantt(silent) {
  $$("#sg-mode button").forEach(b => b.classList.toggle("on", b.dataset.v === SG.mode));
  const p = _rangeParams();
  try {
    const g = await GET(`/api/gantt?mode=${SG.mode}&span=${p.span}&plant_day=${p.plant_day}&days=${p.days}`);
    renderSmartGantt(g);
    applyZoom();
  } catch (e) { if (!silent) toast(e.message, "err"); }
}

function renderSmartGantt(g) {
  const grid = $("#sg-grid");
  if (!grid) return;
  const t0 = new Date(g.window[0]).getTime();
  const t1 = new Date(g.window[1]).getTime();
  const span = Math.max(t1 - t0, 60000);
  const pct = iso => Math.min(100, Math.max(0, (new Date(iso).getTime() - t0) / span * 100));

  $("#sg-sum").innerHTML = `
    <span class="tag open">${g.summary.rows} ${({heat:"heats",machine:"machines",shift:"shifts",delays:"categories",production:"casters"})[g.mode]}</span>
    <span class="tag ${g.summary.live ? "running" : "pending"}">${g.summary.live} live</span>
    <span class="tag ${g.summary.delay_min ? "warn" : "done"}">${fmtN(g.summary.delay_min, 0)} delay min</span>
    ${g.summary.tonnes ? `<span class="tag done">${fmtN(g.summary.tonnes)} t cast</span>` : ""}
    ${g.summary.shutdowns ? `<span class="tag error">${g.summary.shutdowns} shutdown(s)</span>` : ""}`;

  // machine lane filter chips (machine mode only)
  const chips = $("#sg-chips");
  if (g.mode === "machine") {
    chips.classList.remove("hidden");
    chips.innerHTML = g.machines.map(m =>
      `<span class="sg-chip ${SG.hidden.has(m) ? "" : "on"}" data-m="${m}">${m}</span>`).join("");
    chips.onclick = e => {
      const m = e.target.dataset?.m;
      if (!m) return;
      SG.hidden.has(m) ? SG.hidden.delete(m) : SG.hidden.add(m);
      renderSmartGantt(g);
    };
  } else { chips.classList.add("hidden"); }

  let rows = g.rows;
  if (g.mode === "machine") rows = rows.filter(r => !SG.hidden.has(r.key));

  if (!rows.length) {
    grid.innerHTML = `<div class="sg-empty">No plant activity in this window — the Gantt lights up the moment a stage starts.</div>`;
    return;
  }

  // time axis: hourly for ≤1 day, daily for multi-day
  let ticks = "";
  if (g.days > 1) {
    for (let dd = 0; dd < g.days; dd++) {
      const t = t0 + dd * 86400000;
      ticks += `<span style="left:${(t - t0) / span * 100}%">${new Date(t + 7200000).toISOString().slice(5, 10)}</span>`;
    }
  } else {
    const hours = span / 3600000;
    const step = hours > 14 ? 2 : 1;
    for (let hh = 0; hh <= Math.floor(hours); hh += step) {
      const t = new Date(t0 + hh * 3600000);
      ticks += `<span style="left:${(hh * 3600000) / span * 100}%">${String(t.getHours()).padStart(2, "0")}:00</span>`;
    }
  }

  const rowsHTML = rows.map(r => {
    const bars = r.bars.map(b => {
      const l = pct(b.start), w = Math.max(pct(b.end) - l, 0.8);
      const label = g.mode === "heat" ? b.title : (b.title && g.mode === "production" ? b.title : b.heat_no);
      return `<div class="sg-bar ${b.live ? "live" : ""}" data-stage="${b.kind || b.stage}"
        style="left:${l}%;width:${w}%"
        data-tip="<b>${esc(b.heat_no)} — ${esc(b.title)}</b>${esc(b.grade)}${b.route ? " · " + b.route : ""}${b.machine ? " · " + b.machine : ""}<br>${b.start.slice(5, 16)} → ${b.live ? "running" : b.end.slice(11, 16)}${b.done ? " · submitted ✓" : b.live ? " · LIVE" : ""}${b.billet_t ? "<br>" + b.billet_t + " t billets" : ""}"
        onclick="location.hash='#/heat/${b.heat_no}/${b.stage}'">${esc(label)}</div>`;
    }).join("");
    const delays = r.delays.map(dl => {
      const l = pct(dl.start), w = Math.max(pct(dl.end) - l, 0.6);
      return `<div class="sg-delay ${dl.shutdown ? "shutdown" : ""}" style="left:${l}%;width:${w}%"
        data-tip="<b>${dl.shutdown ? "⛔ SHUTDOWN" : "⏱ Delay"} — ${esc(dl.heat_no)}</b>${esc(dl.category)}: ${esc(dl.reason || "")}<br>${fmtN(dl.min, 0)} min${dl.open ? " · STILL OPEN" : ""}${dl.machine ? " · " + dl.machine : ""}"></div>`;
    }).join("");
    const events = r.events.map(ev => `
      <div class="sg-ev k-${ev.kind === "correction" ? "validation" : ev.kind}" style="left:${pct(ev.at)}%"
        data-tip="<b>${esc(ev.heat_no)} — ${ev.kind.replace("_", " ")}</b>${esc(ev.detail)}<br>${ev.at.slice(5, 16)}"></div>`).join("");
    const nowLine = (Date.now() >= t0 && Date.now() <= t1)
      ? `<div class="sg-now" style="left:${(Date.now() - t0) / span * 100}%"></div>` : "";
    return `<div class="sg-row">
      <div class="sg-lab"><b>${r.kind === "heat" ? `<a href="#/heat/${r.key}">${esc(r.label)}</a>` : esc(r.label)}</b>
        <small>${esc(r.sub)}${r.utilization_min !== undefined ? ` · ${fmtN(r.utilization_min, 0)} min util` : ""}</small></div>
      <div class="sg-track">${bars}${delays}${events}${nowLine}</div>
    </div>`;
  }).join("");

  grid.innerHTML = `<div class="sg-axis">${ticks}</div>${rowsHTML}`;

  let tip = $("#sg-tip");
  if (!tip) { tip = document.createElement("div"); tip.id = "sg-tip"; tip.className = "sg-tip glass hidden"; document.body.appendChild(tip); }
  grid.onmousemove = e => {
    const el = e.target.closest("[data-tip]");
    if (!el) { tip.classList.add("hidden"); return; }
    tip.innerHTML = el.dataset.tip;
    tip.classList.remove("hidden");
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 330) + "px";
    tip.style.top = (e.clientY + 16) + "px";
  };
  grid.onmouseleave = () => tip.classList.add("hidden");
}

async function loadMachines(silent) {
  try {
    const mb = await GET("/api/machines");
    const grid = $("#mb-grid");
    if (!grid) return;
    grid.innerHTML = mb.machines.map(x => `
      <div class="mb-card glass">
        <b class="code">${x.code}</b>
        <span class="mb-state tag ${x.state}">${x.state}</span>
        <div class="small muted" style="margin-top:2px">${esc(x.label)}</div>
        <div class="mb-line"><span>Current heat</span>
          <b>${x.current_heat ? `<a class="link" href="#/heat/${x.current_heat}">${x.current_heat}</a>` : "—"}</b></div>
        ${x.current_stage ? `<div class="mb-line"><span>${esc(x.current_stage)}</span><b>${fmtN(x.running_min, 0)} min</b></div>` : ""}
        ${x.open_delay ? `<div class="mb-line"><span style="color:var(--warn)">⏱ ${esc(x.open_delay)}</span></div>` : ""}
        <div class="mb-line"><span>Delays today</span><b>${x.delay_count} · ${fmtN(x.delay_min, 0)} min</b></div>
        ${x.production_t !== null && x.production_t !== undefined ? `<div class="mb-line"><span>Production</span><b>${fmtN(x.production_t)} t</b></div>` : ""}
        <div class="mb-line"><span>Next heat</span><b>${x.next_heat ? `<a class="link" href="#/heat/${x.next_heat}">${x.next_heat}</a>` : "—"}</b></div>
        <div class="mb-line"><span>Utilization</span><b>${x.utilization_pct}%</b></div>
        <div class="mb-util"><i style="width:${x.utilization_pct}%"></i></div>
      </div>`).join("");
  } catch (e) { if (!silent) toast(e.message, "err"); }
}

async function loadHandover() {
  const h = await GET("/api/handover");
  $("#handover").innerHTML = `
    <div class="small muted">Outgoing Shift ${h.outgoing_shift} → Incoming Shift ${h.incoming_shift} · ${h.window[0].slice(5, 16)} → ${h.window[1].slice(5, 16)}</div>
    <div class="row" style="margin:8px 0">
      <span class="tag done">${h.stages_submitted} stages submitted</span>
      <span class="tag locked">${h.heats_locked.length} heats locked</span>
      <span class="tag open">${h.pending_heats.length} heats pending</span>
    </div>
    ${h.pending_heats.length ? `<table class="t"><thead><tr><th>Heat</th><th>Status</th><th>Route</th></tr></thead><tbody>
      ${h.pending_heats.map(p => `<tr><td class="mono"><a class="link" href="#/heat/${p.heat_no}">${p.heat_no}</a></td>
        <td><span class="tag ${p.status}">${p.status}</span></td><td>${p.route || "—"}</td></tr>`).join("")}
    </tbody></table>` : ""}
    <div class="tline scroll-panel sm">${h.events.map(e => `
      <div class="tl-ev k-${e.kind}"><time>${e.at.slice(11, 16)}</time>
        ${e.heat_no ? `<span class="mono">${e.heat_no}</span> ` : ""}${esc(e.detail)}</div>`).join("") || `<div class="empty">No events in the outgoing shift window.</div>`}
    </div>`;
}

async function quickApprove(heatNo, level) {
  const rem = await promptModal(`Approve ${heatNo} — Level ${level}`,
    "Your digital signature will be recorded permanently on this heat.", "Remarks (optional)");
  if (rem === null) return;
  try {
    await POST(`/api/heats/${heatNo}/approve`, {level, remarks: rem || ""});
    toast(`Heat ${heatNo}: level ${level} approved`, "ok");
    route();
  } catch (e) { toast(e.message, "err", 5000); }
}

/* ───────────────────────── MANUAL ENTRY (heat list + create) ───────────────────────── */
async function viewEntry() {
  setTitle("Production Entry");
  await ensureDefs();
  const heats = await GET("/api/heats?active=true&limit=60");
  const training = heats.filter(h => h.is_training);
  const prod = heats.filter(h => !h.is_training);
  const railify = h => ({...h, steps: railSteps(h)});
  $("#view").innerHTML = `
    <h2 class="pg">Production Entry
      <small>select a live heat, or start a new one</small></h2>
    <div class="row">
      ${can("heats.create") ? `<button class="btn primary" onclick="openCreateHeat(false)">＋ New production heat</button>
      <button class="btn" onclick="openCreateHeat(true)">🎓 New training heat</button>` : `<span class="muted small">Your role can enter data on existing heats; heat creation belongs to the Shift Engineer.</span>`}
    </div>
    <div class="grid sec">
      ${prod.map(h => heatRail(railify(h))).join("") || `<div class="card glass empty">No active production heats.</div>`}
    </div>
    ${training.length ? `<h2 class="pg">Training heats <small>excluded from reports & knowledge</small></h2>
      <div class="grid">${training.map(h => heatRail(railify(h))).join("")}</div>` : ""}`;
}

function railSteps(h) {
  // derive rail steps from gates for list payloads
  const order = S.sequence.filter(st => (h.gates[st] && !["not_in_route"].includes(h.gates[st].state)) &&
    !(st === "vd" && ["awaiting_route"].includes(h.gates[st].state) && h.route));
  return order.filter(st => h.gates[st].state !== "awaiting_route" || !h.route).map(st => ({
    stage: st, title: S.stageDefs[st].title,
    state: h.gates[st].state === "done" ? "done"
      : h.gates[st].state === "open" ? (h.status === st ? "running" : "open")
      : "pending",
  }));
}

async function openCreateHeat(isTraining) {
  await ensureDefs();
  const grades = S.masters.grades;
  UI.modal(`
    <h3>${isTraining ? "New training heat" : "New production heat"}</h3>
    <div class="sub">${isTraining
      ? "Training heats behave exactly like production but are excluded from reports, analytics and organizational knowledge."
      : "The heat number is issued from the active series unless you enter one manually."}</div>
    <div class="fgrid">
      <label class="fld"><span>Steel grade * <span class="unit">type any grade — new grades register automatically</span></span>
        <input id="nh-grade" list="nh-grades" placeholder="e.g. Fe500D, IS2062, E250 BR" autocomplete="off">
        <datalist id="nh-grades">${grades.map(g => `<option value="${esc(g.code)}">`).join("")}</datalist></label>
      <label class="fld"><span>Melting furnace * <span class="unit">decides the number series and the route</span></span>
        <select id="nh-entry">
          <option value="eaf">EAF — Electric Arc Furnace (E series)</option>
          <option value="zpf">ZPF (F series)</option>
        </select></label>
      <label class="fld"><span>Heat number <span class="unit">(blank = auto from series)</span></span>
        <input id="nh-no" placeholder="auto" class="mono" style="text-transform:uppercase"></label>
      <label class="fld"><span>Shift Engineer</span>
        <input id="nh-se" value="${esc(S.user.role === "Shift Engineer" ? S.user.name : "")}"></label>
    </div>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="nh-go">Create heat</button>
    </div>`, m => {
    const syncGo = () => { $("#nh-go", m).disabled = !$("#nh-grade", m).value.trim(); };
    syncGo();
    let gt = null;
    $("#nh-grade", m).addEventListener("input", () => {
      syncGo();
      clearTimeout(gt);
      gt = setTimeout(async () => {
        try {
          const gs = await GET("/api/grades/search?q=" + encodeURIComponent($("#nh-grade", m).value.trim()));
          $("#nh-grades", m).innerHTML = gs.map(g =>
            `<option value="${esc(g.code)}">${g.heats} heat(s)${g.has_specs ? "" : " · no specs yet"}</option>`).join("");
        } catch {}
      }, 250);
    });
    $("#nh-go", m).onclick = async () => {
      try {
        const gcode = $("#nh-grade", m).value.trim();
        if (!gcode) { toast("Enter a steel grade — any grade is accepted", "err"); return; }
        const h = await POST("/api/heats", {
          grade_code: gcode,
          heat_no: $("#nh-no", m).value.trim() || null,
          shift_engineer: $("#nh-se", m).value.trim(),
          is_training: !!isTraining,
          entry_stage: $("#nh-entry", m).value,
        });
        UI.closeModal();
        toast(`Heat ${h.heat_no} created`, "ok");
        location.hash = `#/heat/${h.heat_no}`;
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
}

/* ───────────────────────── HEAT WORKSPACE ───────────────────────── */
async function viewHeatWorkspace(heatNo, stageArg) {
  await ensureDefs();
  const h = await GET(`/api/heats/${heatNo}`);
  S.heat = h;                       // live pulse compares against this (2837)
  setTitle(`Heat ${h.heat_no}`);
  if (h.is_training) {
    const tb = $("#training-banner");
    tb.textContent = "🎓 TRAINING MODE — this heat is excluded from production reports and organizational knowledge.";
    tb.classList.remove("hidden");
  }
  const stages = S.sequence.filter(st => h.gates[st]);
  const current = stageArg && h.gates[stageArg] ? stageArg
    : (h.required_stages.find(st => h.gates[st]?.state === "open") || "review");

  // THE PROCESS ROUTE LAW — the post-EAF decision gets a dedicated control.
  // Not every heat goes through ZPF: LRF-5 / LRF-6 bypass it entirely. The
  // decision blocks ZPF *and* LRF, so it must be answerable, never a dead end.
  const canRoute = ["Shift Engineer", "Shift Incharge", "Administrator",
                    "Super Administrator", "Owner"].includes(S.user.role);
  const eafDone = h.gates?.eaf?.state === "done";
  const routeOpen = eafDone && !h.aborted && h.status !== "locked";
  const routePending = routeOpen && !h.route_refining;

  const tabs = stages.map(st => {
    const g = h.gates[st];
    const cls = g.state === "done" ? "done" : (st === current ? "active"
      : ["locked", "awaiting_route", "not_in_route", "aborted"].includes(g.state) ? "gated" : "");
    return `<button class="stage-tab ${cls}" data-st="${st}"
      title="${esc(g.reason || S.stageDefs[st].title)}">${S.stageDefs[st].icon} ${esc(S.stageDefs[st].title)}</button>`;
  }).join("");

  $("#view").innerHTML = `
    <div class="row spread" style="margin-top:14px">
      <div class="row">
        <span class="hr-no" style="font-size:24px">${h.heat_no}</span>
        <span class="tag ${h.status}">${h.status.replace("_", " ")}</span>
        ${h.entry_stage === "zpf"
          ? `<span class="tag open" title="Melting furnace">melted in ZPF</span>` : ""}
        ${h.route_refining
          ? `<span class="tag ${h.route_refining === "ZPF" ? "open" : "done"}" title="Process route after the melting furnace">${
              h.route_refining === "ZPF" ? "via ZPF"
              /* A ZPF-melted heat did not "bypass" the ZPF — it started there. */
              : h.entry_stage === "zpf" ? esc(h.route_refining)
              : `${esc(h.route_refining)} · ZPF bypassed`}</span>`
          : eafDone ? `<span class="tag error">next step not chosen</span>` : ""}
        ${h.route ? `<span class="tag open">Route ${h.route}</span>` : `<span class="tag pending">route pending</span>`}
        <span class="muted small">Grade ${esc(h.grade)} · Plant Day ${h.plant_day} · Shift ${h.shift} · SE ${esc(h.shift_engineer || "—")}</span>
      </div>
      <div class="row">
        ${h.status === "locked" && isAdmin() ? `<button class="btn danger small" onclick="unlockHeat('${h.heat_no}')">Unlock (audited)</button>` : ""}
        ${h.status !== "locked" && !h.aborted && can("heats.abort") ? `<button class="btn danger small" onclick="abortHeat('${h.heat_no}')">Abort / divert</button>` : ""}
        ${!h.aborted && can("production.entry") ? `<button class="btn small" onclick="requestCorrection('${h.heat_no}')">Request correction</button>` : ""}
        <button class="btn small" onclick="showVersions('${h.heat_no}')">Versions</button>
        ${routeOpen && canRoute ? `<button class="btn ${routePending ? "primary route-cta" : "small"}" id="route-cta"
          title="${routePending ? "This heat cannot continue until its next step is chosen" : "Re-selection is audited on the permanent timeline"}"
          >➡ ${routePending ? "Choose next step" : "Change process route"}</button>` : ""}
        <button class="btn small" onclick="showTimeline('${h.heat_no}')">Timeline</button>
        ${can("reports.export") ? `<button class="btn small" onclick="download('/api/reports/heat/${h.heat_no}/xlsx','heat_${h.heat_no}.xlsx')">Excel report</button>` : ""}
      </div>
    </div>
    ${routePending ? `
      <div class="route-gate glass sec">
        <div class="rg-head">
          <span class="rg-icon">➡</span>
          <div>
            <b>Where shall this heat go next?</b>
            <small>EAF is submitted. This heat is waiting on its refining route — ZPF is optional, and choosing an LRF sends it straight there.</small>
          </div>
        </div>
        ${canRoute ? `<div class="rg-opts">
          <button class="rg-opt" data-rg="ZPF"><b>ZPF</b><span>De-phosphorisation first, then ladle refining</span></button>
          <button class="rg-opt" data-rg="LRF-5"><b>LRF-5</b><span>Direct to LRF-5 · bypasses ZPF</span></button>
          <button class="rg-opt" data-rg="LRF-6"><b>LRF-6</b><span>Direct to LRF-6 · bypasses ZPF</span></button>
        </div>`
        : `<div class="finding warning"><b>note</b><div>Your role (${esc(S.user.role)}) cannot select the route — the Shift Engineer decides (5.8).</div></div>`}
      </div>` : ""}
    ${heatRail({...h, steps: railSteps(h)})}
    <div class="stage-tabs">${tabs}</div>
    <div id="stage-body"></div>`;

  // One-click straight from the banner into the audited confirm dialog.
  $$(".rg-opt").forEach(b => b.onclick = () => refiningRouteTile(h, false, b.dataset.rg));
  const rcta = $("#route-cta");
  if (rcta) rcta.onclick = () => refiningRouteTile(h);

  $$(".stage-tab").forEach(b => b.onclick = () => {
    const st = b.dataset.st, g = h.gates[st];
    if (["locked", "awaiting_route", "not_in_route"].includes(g.state) && h.status !== "locked") {
      // A pending route decision is answerable right here — never a dead end.
      if (routePending && canRoute && ["zpf", "lrf"].includes(st)) return refiningRouteTile(h);
      toast(g.reason || "This module is not yet available for this heat.", "err", 4200);
      return;
    }
    location.hash = `#/heat/${h.heat_no}/${st}`;
  });

  await renderStage(h, current);
}

async function renderStage(h, stage) {
  const box = $("#stage-body");
  if (stage === "review") { return renderReview(h, box); }
  const sd = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
  const def = S.stageDefs[stage];
  const readonly = h.status === "locked" || h.aborted;

  const tiles = def.tiles.map(t => {
    let state = "todo", subtitle = "";
    if (t.fields) {
      const req = t.fields.filter(f => f.req && !f.auto);
      const filled = req.filter(f => sd.data[f.key] !== undefined && sd.data[f.key] !== "" && sd.data[f.key] !== null);
      const any = t.fields.some(f => sd.data[f.key] !== undefined && sd.data[f.key] !== "" && sd.data[f.key] !== null);
      state = req.length && filled.length === req.length ? "done" : any ? "part" : "todo";
      subtitle = req.length ? `${filled.length}/${req.length} required` : `${t.fields.length} fields`;
    }
    if (t.chemistry) {
      const reqF = (t.fields || []).filter(f => f.req && !f.auto);
      const fOk = reqF.every(f => sd.data[f.key] !== undefined && sd.data[f.key] !== "" && sd.data[f.key] !== null);
      state = sd.chemistry.length && fOk ? "done" : (sd.chemistry.length || (t.fields || []).some(f => sd.data[f.key])) ? "part" : "todo";
      subtitle = `${sd.chemistry.length} sample(s)` + (reqF.length ? (fOk ? " · readings ✓" : ` · ${reqF.map(f => f.label).join(", ")} required`) : "");
    }
    if (t.materials) {
      const rows = sd.materials.filter(m => matchCat(m, t.materials.category, t.materials.point || ""));
      state = rows.length ? "done" : "todo"; subtitle = `${rows.length} addition(s)`;
    }
    if (t.delays) { state = sd.delays.length ? (sd.delays.some(d => !d.end) ? "part" : "done") : "todo"; subtitle = `${sd.delays.length} delay(s)`; }
    if (t.route) { state = h.route ? "done" : "err"; subtitle = h.route ? `Route ${h.route}` : "decision required"; }
    if (t.route_refining) {
      state = h.route_refining ? "done" : "err";
      subtitle = h.route_refining
        ? `\u2192 ${h.route_refining}` +
          (h.route_refining === "ZPF" || h.entry_stage === "zpf" ? "" : " (bypasses ZPF)")
        : "where next? decision required";
    }
    if (t.calculator) { state = "todo"; subtitle = "open calculator"; }
    return `<div class="tile glass" data-tile="${t.key}">
      <span class="st ${state}"></span>
      <span class="ti">${t.icon}</span><b>${esc(t.label)}</b><small>${esc(subtitle)}</small></div>`;
  }).join("");

  const errs = sd.findings.filter(f => f.level === "error").length;
  const warns = sd.findings.filter(f => f.level === "warning").length;

  box.innerHTML = `
    <div class="row spread">
      <div class="row">
        <span class="tag ${sd.status}">${sd.status.replace("_", " ")}</span>
        ${errs ? `<span class="tag error">${errs} blocking</span>` : `<span class="tag done">validation clear</span>`}
        ${warns ? `<span class="tag warn">${warns} warning(s)</span>` : ""}
        <span class="muted small">Entry authorized: ${def.authorized.join(", ")}</span>
      </div>
      ${!readonly && sd.status !== "submitted" ? `
        <button class="btn primary" id="submit-stage" ${errs ? "disabled" : ""}
          title="${errs ? "The system is guiding you — complete the checklist below, the button unlocks automatically" : "All mandatory requirements satisfied"}">
          ${errs ? `Complete ${errs} required item${errs > 1 ? "s" : ""} to submit` : `Submit ${esc(def.title)} →`}</button>` :
        sd.status === "submitted" ? `<span class="tag done">submitted ✓</span>` : ""}
    </div>
    ${errs && !readonly && sd.status !== "submitted" ? `
      <div class="guide glass sec"><b>Please complete the following:</b>
        <ul>${sd.findings.filter(f => f.level === "error").map(f =>
          `<li><b>${esc(f.tile)}</b> — ${esc(f.message)}</li>`).join("")}</ul>
        <div class="small muted" style="margin-top:8px">Your entries auto-save as you type — nothing is lost while you complete the record. The submit button unlocks the moment every requirement is satisfied.</div>
      </div>` : ""}
    <div class="tiles sec">${tiles}</div>
    <div id="stage-findings" class="sec">${findingsHTML(sd.findings.filter(f => f.level !== "error"))}</div>`;

  $$("#stage-body .tile").forEach(el => el.onclick = () => {
    const t = def.tiles.find(x => x.key === el.dataset.tile);
    openTile(h, stage, t, sd, readonly);
  });
  const sb = $("#submit-stage");
  if (sb) sb.onclick = async () => {
    sb.disabled = true;
    try {
      const r = await POST(`/api/heats/${h.heat_no}/stages/${stage}/submit`);
      if (r.ok) {
        toast(`${def.title} submitted`, "ok");
        localStorage.removeItem(`sms2_draft_${h.heat_no}_${stage}`);
        location.hash = `#/heat/${h.heat_no}`;
        route();
      } else {
        $("#stage-findings").innerHTML = findingsHTML(r.findings);
        toast("Validation blocked submission — see findings below", "err", 4200);
        $("#stage-findings").scrollIntoView({behavior: "smooth"});
      }
    } catch (e) { toast(e.message, "err", 5000); }
    sb.disabled = false;
  };
}

/* Does an addition belong in this tile?
   A tile claims one category or several (Tapping Additions takes both the
   ferroalloys and the tapping lime), and optionally one addition POINT.
   Two tiles on the same stage can share a category — the furnace's flux tile
   and its tapping tile both hold flux — so the point is what separates them:
     tile WITH a point    → only additions made at that point
     tile WITHOUT a point → everything except tapping (which has its own tile) */
const matchCat = (m, cat, point) => {
  const c = m.category || (S.masters.materials.find(x => x.id === m.material_id) || {}).category;
  const cats = Array.isArray(cat) ? cat : [cat];
  if (!cats.includes(c)) return false;
  const p = m.point || "process";
  return point ? p === point : p !== "tapping";
};

function findingsHTML(findings) {
  if (!findings?.length) return "";
  return findings.map(f => `
    <div class="finding ${f.level}"><b>${f.level}</b>
      <div><b style="letter-spacing:0;text-transform:none;font-size:12.5px">${esc(f.tile)}:</b> ${esc(f.message)}</div></div>`).join("");
}

/* ── tile editors ── */
function openTile(h, stage, tile, sd, readonly) {
  if (tile.route_refining) return refiningRouteTile(h, readonly);
  if (tile.route) return routeTile(h, readonly);
  if (tile.calculator) return calculatorTile(h);
  if (tile.chemistry) return chemistryTile(h, stage, tile, sd, readonly);
  if (tile.materials) return materialsTile(h, stage, tile, sd, readonly);
  if (tile.delays) return delaysTile(h, stage, sd, readonly);
  return fieldsTile(h, stage, tile, sd, readonly);
}

function fieldInput(f, val) {
  const v = val === undefined || val === null ? "" : val;
  const unit = f.unit ? `<span class="unit">${esc(f.unit)}</span>` : "";
  const req = f.req ? " *" : "";
  if (f.type === "select")
    return `<label class="fld"><span>${esc(f.label)}${req}</span>
      <select data-k="${f.key}" ${f.auto ? "disabled" : ""}>
        <option value=""></option>
        ${f.options.map(o => `<option ${String(v) === String(o) ? "selected" : ""}>${esc(o)}</option>`).join("")}
      </select></label>`;
  if (f.type === "textarea")
    return `<label class="fld" style="grid-column:1/-1"><span>${esc(f.label)}</span>
      <textarea data-k="${f.key}">${esc(v)}</textarea></label>`;
  if (f.type === "check")
    return `<label class="fld"><span>${esc(f.label)}</span>
      <select data-k="${f.key}"><option value=""></option>
        <option value="Yes" ${v === "Yes" ? "selected" : ""}>Yes</option>
        <option value="No" ${v === "No" ? "selected" : ""}>No</option></select></label>`;
  const type = f.type === "time" ? "datetime-local" : f.type === "number" ? "number" : "text";
  return `<label class="fld ${f.auto ? "auto" : ""}"><span>${esc(f.label)}${req}${unit}${f.auto ? ` <span class="unit">auto</span>` : ""}</span>
    <input type="${type}" step="${f.step || "any"}" data-k="${f.key}" value="${esc(v)}" ${f.auto ? "readonly" : ""}></label>`;
}

function fieldsTile(h, stage, tile, sd, readonly) {
  const draftKey = `sms2_draft_${h.heat_no}_${stage}`;
  const draft = JSON.parse(localStorage.getItem(draftKey) || "{}");
  const hadDraft = Object.keys(draft).some(k => draft[k] !== "" && draft[k] !== (sd.data || {})[k]);
  const data = {...sd.data, ...draft};
  if (hadDraft) setTimeout(() => toast("Auto Save Engine: unsaved draft restored from this workstation", "ok", 3500), 250);
  UI.modal(`
    <div class="row spread"><h3>${tile.icon} ${esc(tile.label)} — ${h.heat_no}</h3>
      <span class="autosave" id="asv">${readonly ? "read-only (locked)" : "auto-save on"}</span></div>
    <div class="sub">${esc(S.stageDefs[stage].title)} · fields marked * are mandatory before submission · dashed fields are auto-calculated</div>
    <div class="fgrid">${tile.fields.map(f => fieldInput(f, data[f.key])).join("")}</div>
    ${tile.chemistry ? `<div class="sub" style="margin-top:8px">This tile also carries a chemistry block — use the Chemistry tile to record samples.</div>` : ""}
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Close</button>
      ${!readonly ? `<button class="btn primary" id="tile-save">Save now</button>` : ""}
    </div>`, m => {
    if (readonly) { $$("input,select,textarea", m).forEach(i => i.disabled = true); return; }
    const markReq = () => $$("[data-k]", m).forEach(i => {
      const f = (tile.fields || []).find(x => x.key === i.dataset.k);
      const fl = i.closest(".fld");
      if (f && f.req && !f.auto && fl) fl.classList.toggle("miss", i.value === "");
    });
    markReq();
    m.addEventListener("input", markReq);
    let timer = null, version = sd.version;
    const collect = () => {
      const out = {};
      $$("[data-k]", m).forEach(i => { if (!i.readOnly) out[i.dataset.k] = i.value; });
      return out;
    };
    const save = async () => {
      const body = collect();
      localStorage.setItem(draftKey, JSON.stringify(body));
      $("#asv", m).textContent = "saving…"; $("#asv", m).classList.remove("saved");
      try {
        const r = await PUT(`/api/heats/${h.heat_no}/stages/${stage}`, {data: body, version});
        version = r.version; sd.version = r.version; sd.data = r.data;
        localStorage.removeItem(draftKey);
        // reflect auto fields
        $$("[data-k]", m).forEach(i => { if (i.readOnly && r.data[i.dataset.k] !== undefined) i.value = r.data[i.dataset.k]; });
        const _t = new Date().toLocaleTimeString("en-IN", {hour12: false});
        S.lastSave = {label: `${h.heat_no} \u00b7 ${tile.label}`, time: _t};
        $("#asv", m).textContent = "saved " + _t;
        $("#asv", m).classList.add("saved");
        refreshStage(h, stage);   // live guidance: submit gate + tile states update as you type
      } catch (e) {
        $("#asv", m).textContent = "not saved";
        const det = e.data && e.data.detail;
        if (e.status === 409 && det && det.server_data) {
          syncAlert(h, stage, tile, collect(), det, v => { version = v; sd.version = v; });
        } else toast(e.message, "err", 4500);
      }
    };
    m.addEventListener("input", () => {
      $("#asv", m).textContent = "editing…";
      localStorage.setItem(draftKey, JSON.stringify(collect()));   // instant local draft — no data ever lost
      clearTimeout(timer); timer = setTimeout(save, 900);
    });
    const flush = () => { try { localStorage.setItem(draftKey, JSON.stringify(collect())); } catch {} };
    addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", flush);
    $("#tile-save", m).onclick = async () => { clearTimeout(timer); await save(); toast("Saved", "ok", 1400); refreshStage(h, stage); };
    m.addEventListener("mousedown", e => { if (e.target === m) { clearTimeout(timer); save(); } });
  });
}

function refreshStage(h, stage) {
  // re-render the tile grid behind the modal without navigation
  renderStage(h, stage).catch(() => {});
}

function chemistryTile(h, stage, tile, sd, readonly) {
  const cfg = tile.chemistry;
  const specs = sd.grade_specs || {};
  const cells = cfg.elements.map(el => {
    const s = specs[el];
    return `<div class="chem-cell" data-el="${el}">
      <label class="fld"><span>${el} %</span>
        <input type="number" step="0.001" data-el-in="${el}"></label>
      ${s ? `<div class="spec">${s.min}–${s.max}${cfg.show_target ? ` aim ${s.aim}` : ""}</div>` : `<div class="spec">&nbsp;</div>`}
    </div>`;
  }).join("");
  const hist = () => sd.chemistry.map(c => `
    <tr><td>${esc(c.label || cfg.label)}</td><td class="mono">${fmtT(c.time)}</td>
      ${cfg.elements.map(el => {
        const v = c.values[el]; const s = specs[el];
        const bad = s && v !== undefined && v !== null && (v < s.min || v > s.max);
        return `<td class="mono" style="${bad ? "color:var(--err);font-weight:700" : ""}">${v ?? "—"}</td>`;
      }).join("")}
      <td>${!readonly ? `<button class="btn small" data-del="${c.id}" title="Supersede Record — preserved forever in audit history, never deleted">⊘ Supersede</button>` : ""}</td></tr>`).join("");

  const hasFields = (tile.fields || []).length > 0;
  const draftKey = `sms2_draft_${h.heat_no}_${stage}`;
  const fdraft = hasFields ? JSON.parse(localStorage.getItem(draftKey) || "{}") : {};
  const fdata = {...sd.data, ...fdraft};
  UI.modal(`
    <h3>🧪 ${esc(tile.label)} — ${h.heat_no}</h3>
    <div class="sub">${esc(cfg.label)} sample · out-of-grade values highlight red · superseding keeps the original in the audit trail (no-delete policy)</div>
    ${hasFields ? `
    <div class="row spread" style="margin-top:2px"><h3 style="margin:0">Process readings</h3>
      <span class="autosave" id="ch-asv">${readonly ? "read-only (locked)" : "auto-save on"}</span></div>
    <div class="fgrid">${tile.fields.map(f => fieldInput(f, fdata[f.key])).join("")}</div>` : ""}
    ${!readonly ? `
    <div class="row">
      <label class="fld" style="flex:1"><span>Sample label</span><input id="ch-label" value="${esc(cfg.label)}"></label>
      <label class="fld" style="flex:1"><span>Sample time</span><input id="ch-time" type="datetime-local" value="${nowLocalISO()}"></label>
    </div>
    <div class="chem-grid">${cells}</div>
    <div class="modal-foot" style="position:static;justify-content:flex-start">
      <button class="btn primary" id="ch-add">＋ Record sample</button>
    </div>` : ""}
    <h3 style="margin-top:16px">Recorded samples</h3>
    <div style="overflow:auto"><table class="t"><thead><tr><th>Label</th><th>Time</th>
      ${cfg.elements.map(e => `<th>${e}</th>`).join("")}<th></th></tr></thead>
      <tbody id="ch-hist">${hist() || ""}</tbody></table>
      ${!sd.chemistry.length ? `<div class="empty">No samples recorded yet.</div>` : ""}</div>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`, m => {
    // ── process readings (e.g. Bath Temperature °C): full Auto Save Engine ──
    let fVersion = sd.version, fTimer = null;
    const collectF = () => {
      const out = {...sd.data};
      $$("[data-k]", m).forEach(i => { if (!i.readOnly) out[i.dataset.k] = i.value; });
      return out;
    };
    const reqFieldsOk = () => (tile.fields || []).every(f => {
      if (!f.req || f.auto) return true;
      const i = $(`[data-k="${f.key}"]`, m);
      const v = i ? i.value : "";
      return v !== "" && (f.type !== "number" || !isNaN(+v));
    });
    const missingReqLabels = () => (tile.fields || [])
      .filter(f => f.req && !f.auto)
      .filter(f => { const i = $(`[data-k="${f.key}"]`, m); return !i || i.value === ""; })
      .map(f => f.label + (f.unit ? ` (${f.unit})` : ""));
    if (hasFields && !readonly) {
      const markReqC = () => $$("[data-k]", m).forEach(i => {
        const f = tile.fields.find(x => x.key === i.dataset.k);
        const fl = i.closest(".fld");
        if (f && f.req && !f.auto && fl) fl.classList.toggle("miss", i.value === "");
      });
      markReqC();
      const saveF = async () => {
        const body = collectF();
        localStorage.setItem(draftKey, JSON.stringify(body));
        const asv = $("#ch-asv", m);
        if (asv) { asv.textContent = "saving…"; asv.classList.remove("saved"); }
        try {
          const r = await PUT(`/api/heats/${h.heat_no}/stages/${stage}`, {data: body, version: fVersion});
          fVersion = r.version; sd.version = r.version; sd.data = r.data;
          localStorage.removeItem(draftKey);
          const _t2 = new Date().toLocaleTimeString("en-IN", {hour12: false});
          S.lastSave = {label: `${h.heat_no} \u00b7 ${tile.label}`, time: _t2};
          if (asv) { asv.textContent = "saved " + _t2; asv.classList.add("saved"); }
          refreshStage(h, stage);
        } catch (e) {
          if (asv) asv.textContent = "not saved";
          const det = e.data && e.data.detail;
          if (e.status === 409 && det && det.server_data)
            syncAlert(h, stage, tile, collectF(), det, v => { fVersion = v; sd.version = v; });
          else toast(e.message, "err", 4500);
        }
      };
      $$("[data-k]", m).forEach(i => i.addEventListener("input", () => {
        markReqC(); chGate();
        localStorage.setItem(draftKey, JSON.stringify(collectF()));
        const asv = $("#ch-asv", m); if (asv) asv.textContent = "editing…";
        clearTimeout(fTimer); fTimer = setTimeout(saveF, 900);
      }));
    } else if (hasFields) {
      $$("[data-k]", m).forEach(i => i.disabled = true);
    }
    // live spec coloring + validate-before-save (no click-then-error)
    function chGate() {
      const btn = $("#ch-add", m);
      if (!btn) return;
      const any = $$("[data-el-in]", m).some(x => x.value !== "");
      const fOk = reqFieldsOk();
      btn.disabled = !any || !fOk;
      btn.title = !fOk
        ? "The system is guiding you: complete " + missingReqLabels().join(", ") + " first"
        : any ? "Ready — sample will be recorded permanently"
              : "The system is guiding you: enter at least one element value first";
    }
    chGate();
    $$("[data-el-in]", m).forEach(i => i.addEventListener("input", () => {
      const el = i.dataset.elIn, s = specs[el], cell = i.closest(".chem-cell");
      cell.classList.remove("in", "out");
      if (s && i.value !== "") cell.classList.add((+i.value < s.min || +i.value > s.max) ? "out" : "in");
      chGate();
    }));
    const addBtn = $("#ch-add", m);
    if (addBtn) addBtn.onclick = async () => {
      const values = {};
      $$("[data-el-in]", m).forEach(i => { if (i.value !== "") values[i.dataset.elIn] = +i.value; });
      try {
        await POST(`/api/heats/${h.heat_no}/stages/${stage}/chemistry`,
          {label: $("#ch-label", m).value, sample_time: $("#ch-time", m).value || null, values});
        toast("Chemistry sample recorded", "ok");
        UI.closeModal();
        const fresh = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
        Object.assign(sd, fresh);
        refreshStage(h, stage);
        chemistryTile(h, stage, tile, sd, readonly);
      } catch (e) { toast(e.message, "err", 5000); }
    };
    m.addEventListener("click", async e => {
      const id = e.target.dataset?.del;
      if (!id) return;
      if (!(await confirmModal("Supersede sample?", "The sample stays in the permanent audit trail but is excluded from calculations and reports.", "Supersede", true))) return;
      try {
        await DEL(`/api/heats/${h.heat_no}/chemistry/${id}`);
        toast("Sample superseded", "ok");
        UI.closeModal();
        const fresh = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
        Object.assign(sd, fresh); refreshStage(h, stage);
        chemistryTile(h, stage, tile, sd, readonly);
      } catch (er) { toast(er.message, "err"); }
    });
  });
}

function materialsTile(h, stage, tile, sd, readonly) {
  const cat = tile.materials.category;
  const point = tile.materials.point || "";
  const cats = Array.isArray(cat) ? cat : [cat];
  const mats = S.masters.materials.filter(x => cats.includes(x.category));
  const rows = () => sd.materials.filter(x => matchCat(x, cat, point)).map(x => `
    <tr><td>${esc(x.name)}</td><td class="mono">${fmtN(x.qty, 2)} ${esc(x.unit)}</td>
      <td class="mono">${fmtT(x.time)}</td><td>${esc(x.remarks || "")}</td>
      <td>${!readonly ? `<button class="btn small" data-del="${x.id}" title="Supersede Record — preserved forever in audit history">⊘ Supersede</button>` : ""}</td></tr>`).join("");
  const total = sd.materials.filter(x => matchCat(x, cat, point)).reduce((a, x) => a + x.qty, 0);

  UI.modal(`
    <h3>${tile.icon} ${esc(tile.materials.label)} — ${h.heat_no}</h3>
    <div class="sub">${esc(S.stageDefs[stage].title)} · additions come from the Material Master · removals supersede, never delete</div>
    ${!readonly ? `
    <div class="fgrid">
      <label class="fld"><span>Material</span>
        <select id="ma-mat">${mats.map(x => `<option value="${x.id}">${esc(x.code)} — ${esc(x.name)}</option>`).join("")}</select></label>
      <label class="fld"><span>Quantity <span class="unit">${esc(tile.materials.unit || mats[0]?.unit || "kg")}</span></span>
        <input id="ma-qty" type="number" step="any"></label>
      <label class="fld"><span>Addition time</span><input id="ma-time" type="datetime-local" value="${nowLocalISO()}"></label>
      <label class="fld"><span>Remarks</span><input id="ma-rem"></label>
    </div>
    <div class="modal-foot" style="position:static;justify-content:flex-start">
      <button class="btn primary" id="ma-add">＋ Add</button>
    </div>` : ""}
    <h3 style="margin-top:14px">Recorded additions <span class="muted plain">· total ${fmtN(total, 2)}</span></h3>
    <table class="t"><thead><tr><th>Material</th><th>Qty</th><th>Time</th><th>Remarks</th><th></th></tr></thead>
      <tbody>${rows()}</tbody></table>
    ${!sd.materials.filter(x => matchCat(x, cat, point)).length ? `<div class="empty">Nothing recorded yet.</div>` : ""}
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`, m => {
    const add = $("#ma-add", m);
    if (add) {
      const maGate = () => {
        add.disabled = !(+$("#ma-qty", m).value > 0);
        add.title = add.disabled ? "The system is guiding you: enter the quantity first" : "Ready";
      };
      maGate();
      $("#ma-qty", m).addEventListener("input", maGate);
    }
    if (add) add.onclick = async () => {
      const qty = +$("#ma-qty", m).value;
      try {
        await POST(`/api/heats/${h.heat_no}/stages/${stage}/materials`, {
          material_id: +$("#ma-mat", m).value, quantity: qty,
          addition_time: $("#ma-time", m).value || null, remarks: $("#ma-rem", m).value,
          point: point || "process",
        });
        toast("Addition recorded", "ok");
        UI.closeModal();
        const fresh = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
        Object.assign(sd, fresh); refreshStage(h, stage);
        materialsTile(h, stage, tile, sd, readonly);
      } catch (e) { toast(e.message, "err", 5000); }
    };
    m.addEventListener("click", async e => {
      const id = e.target.dataset?.del;
      if (!id) return;
      if (!(await confirmModal("Supersede addition?", "The record remains in the audit trail but is excluded from totals.", "Supersede", true))) return;
      try {
        await DEL(`/api/heats/${h.heat_no}/materials/${id}`);
        UI.closeModal();
        const fresh = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
        Object.assign(sd, fresh); refreshStage(h, stage);
        materialsTile(h, stage, tile, sd, readonly);
      } catch (er) { toast(er.message, "err"); }
    });
  });
}

function delaysTile(h, stage, sd, readonly) {
  // Company delay taxonomy: stage → AGENCY → reason. Offer only the reasons
  // that belong to THIS stage (plus any global reason with no stage set), then
  // let the operator pick the agency first and the reason second.
  const stageReasons = S.masters.delay_reasons.filter(r => !r.stage || r.stage === stage);
  const agencies = [...new Set(stageReasons.map(r => r.category))].sort((a, b) => a.localeCompare(b));
  const rows = sd.delays.map(d => `
    <tr><td class="mono">${fmtT(d.start)}</td><td class="mono">${d.end ? fmtT(d.end) : `<span class="tag warn">open</span>`}</td>
      <td class="mono">${d.min ?? "—"}</td><td>${esc(d.reason)}</td><td>${esc(d.remarks || "")}</td>
      <td>${!readonly ? `<button class="btn small" data-del="${d.id}" title="Supersede Record — preserved forever in audit history">⊘ Supersede</button>` : ""}</td></tr>`).join("");
  UI.modal(`
    <h3>⏱ Delay Tracking — ${h.heat_no}</h3>
    <div class="sub">${esc(S.stageDefs[stage].title)} · duration auto-calculates · delays ≥30 min raise a high-priority notification</div>
    ${!readonly ? `
    <div class="fgrid">
      <label class="fld"><span>Start time</span><input id="dl-start" type="datetime-local" value="${nowLocalISO()}"></label>
      <label class="fld"><span>End time <span class="unit">(leave blank if ongoing)</span></span><input id="dl-end" type="datetime-local"></label>
      <label class="fld" style="grid-column:1/-1"><span>Agency</span>
        <select id="dl-agency">
          <option value="" disabled selected>Select agency…</option>
          ${agencies.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join("")}
        </select></label>
      <label class="fld" style="grid-column:1/-1"><span>Reason</span>
        <select id="dl-reason" disabled>
          <option value="" disabled selected>Select an agency first…</option>
        </select></label>
      <label class="fld" style="grid-column:1/-1"><span>Remarks</span><input id="dl-rem"></label>
    </div>
    <div class="modal-foot" style="position:static;justify-content:flex-start">
      <button class="btn primary" id="dl-add">＋ Record delay</button>
    </div>` : ""}
    <h3 style="margin-top:14px">Recorded delays</h3>
    <table class="t"><thead><tr><th>Start</th><th>End</th><th>Min</th><th>Reason</th><th>Remarks</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
    ${!sd.delays.length ? `<div class="empty">No delays recorded — good shift.</div>` : ""}
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`, m => {
    // Cascade: choosing an agency fills the reason dropdown with just that
    // agency's reasons for this stage. The reason stays locked until then.
    const agencySel = $("#dl-agency", m), reasonSel = $("#dl-reason", m);
    if (agencySel && reasonSel) agencySel.onchange = () => {
      const list = stageReasons.filter(r => r.category === agencySel.value);
      reasonSel.innerHTML = `<option value="" disabled selected>Select reason…</option>` +
        list.map(r => `<option value="${r.id}">${esc(r.reason)}</option>`).join("");
      reasonSel.disabled = !list.length;
    };
    const add = $("#dl-add", m);
    if (add) add.onclick = async () => {
      const rid = +($("#dl-reason", m).value);
      if (!rid) { toast("Select an agency and a reason first.", "warn"); return; }
      try {
        await POST(`/api/heats/${h.heat_no}/stages/${stage}/delays`, {
          start_time: $("#dl-start", m).value, end_time: $("#dl-end", m).value || null,
          reason_id: rid, remarks: $("#dl-rem", m).value,
        });
        toast("Delay recorded", "ok");
        UI.closeModal();
        const fresh = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
        Object.assign(sd, fresh); refreshStage(h, stage);
        delaysTile(h, stage, sd, readonly);
      } catch (e) { toast(e.message, "err", 5000); }
    };
    m.addEventListener("click", async e => {
      const id = e.target.dataset?.del;
      if (!id) return;
      if (!(await confirmModal("Supersede delay?", "The record remains in the audit trail.", "Supersede", true))) return;
      await DEL(`/api/heats/${h.heat_no}/delays/${id}`).catch(er => toast(er.message, "err"));
      UI.closeModal();
      const fresh = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
      Object.assign(sd, fresh); refreshStage(h, stage);
      delaysTile(h, stage, sd, readonly);
    });
  });
}

function routeTile(h, readonly) {
  const canRoute = ["Shift Engineer", "Shift Incharge", "Administrator"].includes(S.user.role);
  const opt = r => `
    <div class="tile glass ${h.route === r ? "" : ""}" data-r="${r}" style="text-align:center;${h.route === r ? "border-color:var(--accent)" : ""}">
      <span class="ti">${r.startsWith("VD") ? "🌀" : "🏭"}</span><b>${r}</b>
      <small>${r.startsWith("VD") ? "Vacuum degassing → caster" : "Direct to billet caster"}</small>
      ${h.route === r ? `<div class="tag open" style="margin-top:6px">selected</div>` : ""}
    </div>`;
  UI.modal(`
    <h3>➡ Process Route Decision — ${h.heat_no}</h3>
    <div class="sub">The route decision belongs to the Shift Engineer. LRF chemistry must be recorded first.
      ${h.route ? `Current route: <b>${h.route}</b>. Re-selection is audited on the timeline.` : "No route selected yet."}</div>
    <div class="tiles">${["VD-2", "VD-3", "BC-2", "BC-3"].map(opt).join("")}</div>
    ${!canRoute ? `<div class="finding warning" style="margin-top:12px"><b>note</b><div>Your role (${esc(S.user.role)}) cannot select the route — the Shift Engineer decides (5.8).</div></div>` : ""}
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`, m => {
    if (readonly || !canRoute) return;
    $$("[data-r]", m).forEach(el => el.onclick = async () => {
      const r = el.dataset.r;
      if (!(await confirmModal(`Select route ${r}?`,
        `Heat ${h.heat_no} will follow <b>${r}</b>. ${r.startsWith("VD") ? "The Vacuum Degassing module will unlock." : "The heat will bypass VD and go directly to the caster."} This decision is recorded on the permanent timeline.`, "Select route"))) return;
      try {
        await POST(`/api/heats/${h.heat_no}/route`, {route: r});
        toast(`Route ${r} selected`, "ok");
        UI.closeModal();
        location.hash = `#/heat/${h.heat_no}/lrf`; route();
      } catch (e) { toast(e.message, "err", 5500); }
    });
  });
}

function calculatorTile(h) {
  UI.modal(`
    <h3>🧮 Metallurgical Calculator — ${h.heat_no}</h3>
    <div class="sub">Auto-populated from this heat: tap weight, latest chemistry, grade aims. All outputs are CALCULATED values — review before acceptance (Human Authority, 3.8).</div>
    <div id="calc-out" class="empty">Computing from heat data…</div>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Close</button>
      <button class="btn primary hidden" id="calc-accept">Accept → record additions</button>
    </div>`, async m => {
    try {
      const r = await POST("/api/calculator/alloys", {heat_no: h.heat_no});
      const out = $("#calc-out", m);
      if (!r.ok) {
        out.innerHTML = `<div class="finding warning"><b>inputs</b><div>${esc(r.message)}<br>${r.missing.map(esc).join("<br>")}</div></div>`;
        return;
      }
      if (!r.lines.length) {
        out.innerHTML = `<div class="finding info"><b>info</b><div>${esc(r.message)}</div></div>`;
        return;
      }
      out.innerHTML = `
        <div class="small muted">Heat weight ${fmtN(r.inputs.heat_weight_t, 1)} t</div>
        <table class="t"><thead><tr><th>Element</th><th>Alloy</th><th>Quantity</th><th>Basis</th></tr></thead><tbody>
          ${r.lines.map(l => `<tr><td><b>${l.element}</b></td><td>${l.alloy ? esc(l.alloy) : "—"}</td>
            <td class="mono">${l.qty_kg !== null ? fmtN(l.qty_kg, 1) + " kg" : "—"}</td><td class="small muted">${esc(l.note)}</td></tr>`).join("")}
        </tbody></table>
        <h3 style="margin-top:12px">Predicted chemistry after additions</h3>
        <div class="row">${Object.entries(r.predicted).map(([el, v]) =>
          `<span class="tag">${el} ${v}</span>`).join(" ")}</div>`;
      const okLines = r.lines.filter(l => l.qty_kg);
      if (okLines.length && h.status !== "locked") {
        const btn = $("#calc-accept", m);
        btn.classList.remove("hidden");
        btn.onclick = async () => {
          if (!(await confirmModal("Record calculated additions?",
            okLines.map(l => `${l.alloy}: <b>${fmtN(l.qty_kg, 1)} kg</b>`).join("<br>") +
            "<br><br>These will be added to the LRF Ferro Alloy Addition log with remark “from calculator”.", "Record additions"))) return;
          try {
            for (const l of okLines) {
              const mat = S.masters.materials.find(x => x.code === l.alloy);
              if (mat) await POST(`/api/heats/${h.heat_no}/stages/lrf/materials`,
                {material_id: mat.id, quantity: l.qty_kg, remarks: `from calculator (${l.element})`});
            }
            toast("Calculated additions recorded", "ok");
            UI.closeModal(); route();
          } catch (e) { toast(e.message, "err", 5000); }
        };
      }
    } catch (e) { $("#calc-out", m).innerHTML = `<div class="finding error"><b>error</b><div>${esc(e.message)}</div></div>`; }
  });
}

/* ── review & approvals ── */
async function renderReview(h, box) {
  const r = await GET(`/api/heats/${h.heat_no}/review`);
  const errs = r.validation.filter(f => f.level === "error");
  const approved = Object.fromEntries((r.heat.approvals || []).filter(a => a.decision === "approved").map(a => [a.level, a]));
  const chain = [[1, "Operator"], [2, "Shift Engineer"], [3, "Shift Incharge"]];
  const nextLevel = chain.find(([l]) => !approved[l])?.[0];

  box.innerHTML = `
    <div class="grid cols-2">
      <div class="card glass">
        <h3>Final validation (whole heat)</h3>
        ${r.validation.length ? findingsHTML(r.validation)
          : `<div class="finding ok"><b>clear</b><div>All stages submitted, quality accepted, route recorded. Ready for the approval chain.</div></div>`}
      </div>
      <div class="card glass">
        <h3>Approval chain — digital signatures</h3>
        ${chain.map(([l, role]) => {
          const a = approved[l];
          if (a) return `<div class="finding ok"><b>L${l} ✓</b>
            <div><b>${esc(role)}</b> — ${esc(a.user)} · ${a.at}<br><span class="mono small muted">signed</span></div></div>`;
          const canDo = (S.user.role === role || isAdmin()) && l === nextLevel && !errs.length && h.status !== "locked";
          return `<div class="finding ${l === nextLevel ? "warning" : "info"}"><b>L${l}</b>
            <div style="flex:1"><b>${esc(role)}</b> — ${l === nextLevel ? "awaiting approval" : "waits for previous level"}</div>
            ${canDo ? `<button class="btn small primary" onclick="quickApprove('${h.heat_no}',${l})">Approve as ${esc(role)}</button>` : ""}</div>`;
        }).join("")}
        ${h.status === "locked" ? `<div class="finding ok"><b>locked</b><div>Heat locked ${r.report.locked_at || ""}. Read-only permanent record.</div></div>` : ""}
      </div>
    </div>
    <div class="sec card glass">
      <h3>Complete production record — Digital DNA</h3>
      ${r.report.stages.map(s => `
        <details style="margin:8px 0"><summary style="cursor:pointer"><b>${esc(s.title)}</b>
          <span class="tag ${s.status}">${s.status}</span>
          <span class="muted small">${s.submitted_at ? "submitted " + s.submitted_at : ""}</span></summary>
          <div style="padding:8px 4px">
            ${Object.keys(s.data).length ? `<table class="t"><tbody>${Object.entries(s.data)
              .filter(([, v]) => v !== "" && v !== null)
              .map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`).join("")}</tbody></table>` : ""}
            ${s.chemistry.length ? `<h3 style="margin-top:8px">Chemistry</h3><table class="t"><tbody>
              ${s.chemistry.map(c => `<tr><td>${esc(c.label)}</td><td class="mono small">${Object.entries(c.values).map(([e2, v]) => `${e2} ${v}`).join(" · ")}</td></tr>`).join("")}</tbody></table>` : ""}
            ${s.materials.length ? `<h3 style="margin-top:8px">Materials</h3><table class="t"><tbody>
              ${s.materials.map(x => `<tr><td>${esc(x.material)}</td><td class="mono">${fmtN(x.qty, 2)} ${esc(x.unit)}</td><td class="small muted">${esc(x.remarks || "")}</td></tr>`).join("")}</tbody></table>` : ""}
            ${s.delays.length ? `<h3 style="margin-top:8px">Delays</h3><table class="t"><tbody>
              ${s.delays.map(x => `<tr><td class="mono">${x.start.slice(5, 16)} → ${x.end ? x.end.slice(11, 16) : "open"}</td><td>${x.min ?? "—"} min</td><td>${esc(x.reason)}</td></tr>`).join("")}</tbody></table>` : ""}
          </div>
        </details>`).join("")}
    </div>`;
}

async function abortHeat(heatNo) {
  const reason = await promptModal(`Abort / divert heat ${heatNo}`,
    "Aborting is a first-class audited exception — the heat and its history are preserved permanently. Reason is mandatory.");
  if (reason === null) return;
  try {
    await POST(`/api/heats/${heatNo}/abort`, {reason});
    toast(`Heat ${heatNo} aborted`, "ok"); route();
  } catch (e) { toast(e.message, "err", 5000); }
}

async function unlockHeat(heatNo) {
  const reason = await promptModal(`Administrator unlock — ${heatNo}`,
    "Unlocking supersedes existing approvals and reopens the heat for correction. The unlock and reason enter the permanent audit log.");
  if (reason === null) return;
  try {
    await POST(`/api/heats/${heatNo}/unlock`, {reason});
    toast(`Heat ${heatNo} unlocked — approvals must be re-signed`, "ok"); route();
  } catch (e) { toast(e.message, "err", 5000); }
}

async function showTimeline(heatNo) {
  const t = await GET(`/api/heats/${heatNo}/timeline`);
  UI.modal(`
    <h3>⛓ Production Timeline — ${heatNo}</h3>
    <div class="sub">Every event, every actor, every timestamp. Nothing exists without a timeline.</div>
    <div class="tline scroll-panel lg">
      ${t.events.map(e => `
        <div class="tl-ev k-${e.kind}"><time>${e.at.slice(0, 16)}</time>
          <span class="tag ${e.kind === "locked" ? "locked" : e.kind === "aborted" || e.kind === "unlocked" ? "error" : e.kind === "stage_submitted" ? "open" : "pending"}">${e.kind.replace("_", " ")}</span>
          ${esc(e.detail)} <span class="who">— ${esc(e.actor)} · ${e.plant_day} ${e.shift}</span></div>`).join("")}
    </div>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`);
}

/* ───────────────────────── HEAT TRACKING ───────────────────────── */
async function viewHeats() {
  setTitle("Heat Tracking & Timelines");
  await ensureDefs();
  const heats = await GET("/api/heats?limit=200");
  const filters = `
    <div class="filter-bar card glass" style="margin-bottom:var(--sp-3)">
      <label class="fld"><span>Status</span>
        <select id="hf-status"><option value="">all</option>
          ${["eaf","zpf","lrf","vd","caster","quality","review","locked","aborted"]
            .map(s=>`<option>${s}</option>`).join("")}</select></label>
      <label class="fld"><span>Shift</span>
        <select id="hf-shift"><option value="">all</option><option>A</option><option>B</option><option>C</option></select></label>
      <label class="fld"><span>Type</span>
        <select id="hf-type"><option value="">production + training</option><option value="prod">production only</option><option value="train">training only</option></select></label>
      <label class="fld wide"><span>Filter text (heat no / grade / SE)</span>
        <input id="hf-q" placeholder="e.g. E2601, Fe500D"></label>
    </div>`;
  const render = () => {
    const st = $("#hf-status").value, sh = $("#hf-shift").value,
          ty = $("#hf-type").value, q = $("#hf-q").value.trim().toLowerCase();
    const rows = heats.filter(h =>
      (!st || h.status === st) && (!sh || h.shift === sh) &&
      (ty !== "prod" || !h.is_training) && (ty !== "train" || h.is_training) &&
      (!q || [h.heat_no, h.grade, h.shift_engineer].join(" ").toLowerCase().includes(q)))
      .map(h => `
      <tr>
        <td class="mono"><a class="link" href="#/heat/${h.heat_no}">${h.heat_no}</a></td>
        <td>${esc(h.grade || "")}</td>
        <td><span class="tag ${h.status}">${h.status.replace("_"," ")}</span>
            ${h.is_training ? `<span class="tag train">T</span>` : ""}</td>
        <td>${h.route || "—"}</td><td>${h.plant_day}</td><td>${h.shift}</td>
        <td>${esc(h.shift_engineer || "")}</td>
        <td><button class="btn small" onclick="showTimeline('${h.heat_no}')">Timeline</button></td>
      </tr>`).join("");
    $("#heat-table-body").innerHTML = rows || `<tr><td colspan="8" class="empty">No heats match the filters.</td></tr>`;
  };
  $("#view").innerHTML = `
    <h2 class="pg">Heat Tracking <small>${heats.length} heats in the register — every heat traceable forever</small></h2>
    ${filters}
    <div class="card glass" style="overflow:auto">
      <table class="t"><thead><tr>
        <th>Heat No</th><th>Grade</th><th>Status</th><th>Route</th><th>Plant Day</th><th>Shift</th><th>Shift Engineer</th><th></th>
      </tr></thead><tbody id="heat-table-body"></tbody></table>
    </div>`;
  ["hf-status","hf-shift","hf-type","hf-q"].forEach(id => $("#"+id).addEventListener("input", render));
  render();
}

/* ───────────────────────── REPORTS & ANALYTICS ───────────────────────── */
async function viewReports() {
  setTitle("Reports & Analytics Center");
  if (!can("reports.view")) { $("#view").innerHTML = `<div class="card glass empty">Your role does not include report access.</div>`; return; }
  const today = S.plant?.plant_day || new Date().toISOString().slice(0,10);
  $("#view").innerHTML = `
    <h2 class="pg">Reports & Analytics <small>plant day runs 22:00 → 22:00</small></h2>
    <div class="filter-bar card glass">
      <label class="fld"><span>Plant day</span><input type="date" id="rp-day" value="${today}"></label>
      <button class="btn primary" id="rp-load">Load daily report</button>
      ${can("reports.export") ? `<button class="btn" id="rp-xlsx">⬇ Daily Excel</button>` : ""}
      <span class="fb-gap"></span>
      <label class="fld"><span>Heat report</span><input id="rp-heat" class="mono" placeholder="E260151" style="text-transform:uppercase"></label>
      <button class="btn" id="rp-heat-go">Open</button>
      ${can("reports.export") ? `<button class="btn" id="rp-heat-xlsx">⬇ Heat Excel</button>` : ""}
      <button class="btn primary" id="rp-audit" title="Download a presentation-ready audit and performance dashboard">◫ Audit report</button>
    </div>
    <div id="rp-out" class="sec"></div>
    <div class="sec card glass"><h3>Analytics — last 14 plant days</h3><div id="rp-analytics" class="empty">Loading…</div></div>
    <div class="sec card glass"><h3>Report library — immutable archive</h3><div id="rp-lib" class="empty">Loading…</div></div>`;

  const loadDaily = async () => {
    const day = $("#rp-day").value;
    const d = await GET(`/api/reports/daily?plant_day=${day}`);
    const t = d.totals;
    $("#rp-out").innerHTML = `
      <div class="card glass">
        <h3>Daily production — Plant Day ${d.plant_day} <span class="muted plain">(${d.window[0].slice(5,16)} → ${d.window[1].slice(5,16)})</span></h3>
        <div class="grid cols-4" style="margin-bottom:12px">
          <div class="kpi glass hot"><b>${t.heats}</b><span>Heats</span></div>
          <div class="kpi glass"><b>${fmtN(t.billet_t)}</b><span>Billet t</span></div>
          <div class="kpi glass"><b>${fmtN(t.energy_kwh,0)}</b><span>kWh</span></div>
          <div class="kpi glass"><b>${fmtN(t.delay_min,0)}</b><span>Delay min</span></div>
        </div>
        <div style="overflow:auto"><table class="t"><thead><tr>
          <th>Heat</th><th>Grade</th><th>Shift</th><th>Route</th><th>Status</th><th>Tap t</th><th>kWh</th><th>O₂</th><th>Billets</th><th>Billet t</th><th>Yield %</th>
        </tr></thead><tbody>
          ${d.heats.map(h=>`<tr><td class="mono"><a class="link" href="#/heat/${h.heat_no}">${h.heat_no}</a></td>
            <td>${esc(h.grade)}</td><td>${h.shift}</td><td>${h.route}</td>
            <td><span class="tag ${h.status}">${h.status}</span></td>
            <td class="mono">${fmtN(h.tap_weight)}</td><td class="mono">${fmtN(h.energy_kwh,0)}</td>
            <td class="mono">${fmtN(h.o2,0)}</td><td class="mono">${h.billets ?? "—"}</td>
            <td class="mono">${fmtN(h.billet_t)}</td><td class="mono">${h.yield_pct ?? "—"}</td></tr>`).join("")
          || `<tr><td colspan="11" class="empty">No production heats on this plant day.</td></tr>`}
        </tbody></table></div>
        <h3 style="margin-top:14px">Shift summary</h3>
        <table class="t"><thead><tr><th>Shift</th><th>Heats</th><th>Billets</th><th>Billet t</th></tr></thead><tbody>
          ${Object.entries(d.per_shift).map(([s,v])=>`<tr><td><b>${s}</b></td><td>${v.heats}</td><td>${v.billets}</td><td class="mono">${fmtN(v.tonnes)}</td></tr>`).join("")}
        </tbody></table>
        ${d.delays.length ? `<h3 style="margin-top:14px">Delays (${t.delay_count} · ${fmtN(t.delay_min,0)} min)</h3>
        <table class="t"><thead><tr><th>Heat</th><th>Stage</th><th>Category</th><th>Reason</th><th>Min</th><th>Remarks</th></tr></thead><tbody>
          ${d.delays.map(x=>`<tr><td class="mono">${x.heat_no}</td><td>${esc(x.stage)}</td><td>${esc(x.category)}</td>
            <td>${esc(x.reason)}</td><td class="mono">${x.duration_min ?? "—"}</td><td>${esc(x.remarks||"")}</td></tr>`).join("")}
        </tbody></table>` : ""}
      </div>`;
  };
  $("#rp-load").onclick = () => loadDaily().catch(e=>toast(e.message,"err"));
  const dx = $("#rp-xlsx"); if (dx) dx.onclick = () => download(`/api/reports/daily/xlsx?plant_day=${$("#rp-day").value}`, "daily.xlsx");
  $("#rp-heat-go").onclick = () => { const v = $("#rp-heat").value.trim(); if (v) location.hash = `#/heat/${v.toUpperCase()}/review`; };
  const ra = $("#rp-audit"); if (ra) ra.onclick = () => downloadAuditReport(14);
  const hx = $("#rp-heat-xlsx"); if (hx) hx.onclick = () => { const v = $("#rp-heat").value.trim().toUpperCase(); if (v) download(`/api/reports/heat/${v}/xlsx`, `heat_${v}.xlsx`); };
  loadDaily().catch(()=>{ $("#rp-out").innerHTML = `<div class="card glass empty">Could not load today's report.</div>`; });

  // analytics
  GET("/api/analytics?days=14").then(an => {
    const trend = an.trend;
    $("#rp-analytics").innerHTML = `
      <div class="grid cols-2">
        <div><h3 style="margin-top:0">Heats & billet tonnes per plant day</h3>${svgBars(trend.map(t=>({label:t.plant_day.slice(5), v:t.billet_t, v2:t.heats})), "t", "heats")}</div>
        <div><h3 style="margin-top:0">Delay minutes per plant day</h3>${svgBars(trend.map(t=>({label:t.plant_day.slice(5), v:t.delay_min})), "min")}</div>
      </div>
      <div class="grid cols-3 sec">
        <div><h3 style="margin-top:0">Delay Pareto by category</h3>
          ${an.delay_pareto.length ? svgHBars(an.delay_pareto.map(p=>({label:p.category, v:p.minutes})), "min") : `<div class="empty">No delays in window.</div>`}</div>
        <div><h3 style="margin-top:0">Shift comparison</h3>
          <table class="t"><thead><tr><th>Shift</th><th>Heats</th><th>Locked</th></tr></thead><tbody>
          ${Object.entries(an.shift_comparison).map(([s,v])=>`<tr><td><b>${s}</b></td><td>${v.heats}</td><td>${v.locked}</td></tr>`).join("") || `<tr><td colspan=3 class="empty">—</td></tr>`}
          </tbody></table></div>
        <div><h3 style="margin-top:0">Route usage</h3>
          ${svgHBars(Object.entries(an.route_usage).map(([r,c])=>({label:r, v:c})), "heats")}</div>
      </div>`;
  }).catch(e => $("#rp-analytics").innerHTML = `<div class="empty">${esc(e.message)}</div>`);

  // library
  GET("/api/reports/archive").then(lib => {
    $("#rp-lib").innerHTML = lib.length ? `
      <div class="scroll-panel md"><table class="t"><thead><tr>
        <th>Generated</th><th>Kind</th><th>Reference</th><th>By</th><th></th></tr></thead><tbody>
        ${lib.map(r=>`<tr><td class="mono">${r.at.slice(0,16)}</td><td><span class="tag">${r.kind}</span></td>
          <td class="mono">${esc(r.ref)}</td><td>${esc(r.generated_by)}</td>
          <td><button class="btn small" onclick="download('/api/reports/archive/${r.id}/download','${esc(r.filename)}')">⬇</button></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No reports generated yet — every export lands here permanently.</div>`;
  }).catch(()=>{});
}

/* tiny dependency-free SVG charts */
function svgBars(rows, unit, unit2) {
  if (!rows.length) return `<div class="empty">No data.</div>`;
  const W=560,H=190,P=28,bw=(W-P*2)/rows.length;
  const max=Math.max(...rows.map(r=>r.v),1), max2=rows[0].v2!==undefined?Math.max(...rows.map(r=>r.v2),1):0;
  return `<svg class="svgchart" viewBox="0 0 ${W} ${H}" role="img">
    ${rows.map((r,i)=>{
      const h=(r.v/max)*(H-P*2), x=P+i*bw;
      return `<rect x="${x+3}" y="${H-P-h}" width="${bw-6}" height="${Math.max(h,1)}" rx="3" fill="var(--accent)" opacity=".85">
        <title>${r.label}: ${fmtN(r.v)} ${unit}${r.v2!==undefined?` · ${r.v2} ${unit2}`:""}</title></rect>
      ${max2?`<circle cx="${x+bw/2}" cy="${H-P-(r.v2/max2)*(H-P*2)}" r="3.5" fill="var(--info)"><title>${r.label}: ${r.v2} ${unit2}</title></circle>`:""}
      ${rows.length<=16||i%2===0?`<text x="${x+bw/2}" y="${H-8}" font-size="8.5" fill="var(--tx2)" text-anchor="middle">${r.label}</text>`:""}`;
    }).join("")}
    <text x="${P}" y="14" font-size="9" fill="var(--tx2)">${unit}${max2?` (bars) · ${unit2} (dots)`:""} · max ${fmtN(max)}</text>
  </svg>`;
}
function svgHBars(rows, unit) {
  if (!rows.length) return `<div class="empty">No data.</div>`;
  const W=420, rh=26, H=rows.length*rh+8, max=Math.max(...rows.map(r=>r.v),1);
  return `<svg class="svgchart" viewBox="0 0 ${W} ${H}">
    ${rows.map((r,i)=>{
      const w=(r.v/max)*(W-150);
      return `<text x="0" y="${i*rh+16}" font-size="10.5" fill="var(--tx)">${esc(r.label)}</text>
        <rect x="110" y="${i*rh+6}" width="${Math.max(w,2)}" height="13" rx="3" fill="var(--accent)" opacity=".85"></rect>
        <text x="${116+w}" y="${i*rh+16}" font-size="10" fill="var(--tx2)">${fmtN(r.v,0)} ${unit}</text>`;
    }).join("")}
  </svg>`;
}

/* ───────────────────────── UNIVERSAL SEARCH ───────────────────────── */
async function viewSearch(qInit) {
  setTitle("Universal Search");
  await ensureDefs();
  $("#view").innerHTML = `
    <h2 class="pg">Universal Search <small>heat numbers, grades, remarks, delay reasons, timeline events, personnel</small></h2>
    <div class="filter-bar card glass">
      <label class="fld wide"><span>Search</span>
        <input id="se-q" placeholder="e.g. E260151, crane, Fe500D, breakout…" value="${esc(qInit||"")}"></label>
      <label class="fld"><span>Status</span><select id="se-status"><option value="">any</option>
        ${["eaf","zpf","lrf","vd","caster","quality","review","locked","aborted"].map(s=>`<option>${s}</option>`).join("")}</select></label>
      <label class="fld"><span>Shift</span><select id="se-shift"><option value="">any</option><option>A</option><option>B</option><option>C</option></select></label>
      <label class="fld"><span>Route</span><select id="se-route"><option value="">any</option><option>VD-2</option><option>VD-3</option><option>BC-2</option><option>BC-3</option></select></label>
      <label class="fld"><span>Plant day</span><input type="date" id="se-day"></label>
      <button class="btn primary" id="se-go">Search</button>
    </div>
    <div id="se-out" class="sec"></div>`;
  const go = async () => {
    const p = new URLSearchParams();
    const q = $("#se-q").value.trim(); if (q) p.set("q", q);
    if ($("#se-status").value) p.set("status", $("#se-status").value);
    if ($("#se-shift").value) p.set("shift", $("#se-shift").value);
    if ($("#se-route").value) p.set("route", $("#se-route").value);
    if ($("#se-day").value) p.set("plant_day", $("#se-day").value);
    $("#se-out").innerHTML = `<div class="empty">Searching…</div>`;
    const r = await GET("/api/search?" + p.toString());
    $("#se-out").innerHTML = `
      <div class="card glass">
        <h3>${r.count} result(s)${r.query ? ` for “${esc(r.query)}”` : ""}</h3>
        <table class="t"><thead><tr><th>Heat</th><th>Grade</th><th>Status</th><th>Route</th><th>Plant day / shift</th><th>Matched in</th></tr></thead><tbody>
        ${r.results.map(x=>`<tr>
          <td class="mono"><a class="link" href="#/heat/${x.heat_no}">${x.heat_no}</a>${x.is_training?` <span class="tag train">T</span>`:""}</td>
          <td>${esc(x.grade)}</td><td><span class="tag ${x.status}">${x.status.replace("_"," ")}</span></td>
          <td>${x.route||"—"}</td><td class="mono">${x.plant_day} / ${x.shift}</td>
          <td><span class="tag">${x.matched_in}</span> <span class="small muted">${esc(x.detail||"")}</span></td></tr>`).join("")
          || `<tr><td colspan="6" class="empty">Nothing found — try fewer filters.</td></tr>`}
        </tbody></table>
      </div>`;
  };
  $("#se-go").onclick = () => go().catch(e=>toast(e.message,"err"));
  $("#se-q").addEventListener("keydown", e=>{ if(e.key==="Enter") go().catch(er=>toast(er.message,"err")); });
  if (qInit) go().catch(()=>{});
}

/* ───────────────────────── STANDALONE CALCULATOR ───────────────────────── */
async function viewCalculator() {
  setTitle("Metallurgical Calculation Engine");
  await ensureDefs();
  const grades = S.masters.grades;
  const els = ["C","Si","Mn","S","P","Cr","Ni","Al","Cu","Ti"];
  $("#view").innerHTML = `
    <h2 class="pg">Metallurgical Calculator <small>alloys · flux · cored wire — deterministic, never guesses (7.10)</small></h2>
    <div class="grid cols-2">
      <div class="card glass">
        <h3>Ferro alloy requirement</h3>
        <div class="row">
          <label class="fld" style="flex:1"><span>Auto-fill from heat</span><input id="ca-heat" class="mono" placeholder="E260151" style="text-transform:uppercase"></label>
          <button class="btn small" id="ca-fill">Fetch heat data</button>
        </div>
        <div class="row">
          <label class="fld" style="flex:1"><span>Heat weight <span class="unit">t</span></span><input id="ca-wt" type="number" step="any"></label>
          <label class="fld" style="flex:1"><span>Target from grade</span>
            <select id="ca-grade"><option value="">manual targets</option>${grades.map(g=>`<option value="${g.code}">${g.code} (aims)</option>`).join("")}</select></label>
        </div>
        <table class="t"><thead><tr><th>Element</th><th>Current %</th><th>Target %</th></tr></thead><tbody>
          ${els.map(e=>`<tr><td><b>${e}</b></td>
            <td><input class="mono num-input" type="number" step="0.001" data-cur="${e}"></td>
            <td><input class="mono num-input" type="number" step="0.001" data-tgt="${e}"></td></tr>`).join("")}
        </tbody></table>
        <div class="modal-foot" style="position:static;justify-content:flex-start">
          <button class="btn primary" id="ca-go">Calculate additions</button>
        </div>
        <div id="ca-out"></div>
      </div>
      <div>
        <div class="card glass">
          <h3>Flux recommendation</h3>
          <div class="row">
            <label class="fld" style="flex:1"><span>Heat weight <span class="unit">t</span></span><input id="fx-wt" type="number" step="any"></label>
            <label class="fld" style="flex:1"><span>Sulphur <span class="unit">%</span></span><input id="fx-s" type="number" step="0.001"></label>
            <button class="btn primary" id="fx-go">Calculate</button>
          </div>
          <div id="fx-out"></div>
        </div>
        <div class="card glass sec">
          <h3>CaSi wire feeding</h3>
          <div class="fgrid">
            <label class="fld"><span>Heat weight <span class="unit">t</span></span><input id="wr-wt" type="number" step="any"></label>
            <label class="fld"><span>Target Ca <span class="unit">ppm</span></span><input id="wr-ppm" type="number" value="25"></label>
            <label class="fld"><span>Wire Ca content <span class="unit">g/m</span></span><input id="wr-gpm" type="number" value="220"></label>
            <label class="fld"><span>Recovery <span class="unit">%</span></span><input id="wr-rec" type="number" value="12"></label>
          </div>
          <button class="btn primary" id="wr-go">Calculate wire length</button>
          <div id="wr-out"></div>
        </div>
      </div>
    </div>`;

  $("#ca-grade").addEventListener("change", () => {
    const g = grades.find(x => x.code === $("#ca-grade").value);
    els.forEach(e => { const i = $(`[data-tgt="${e}"]`); i.value = g && g.specs[e] ? g.specs[e].aim : ""; });
  });
  $("#ca-fill").onclick = async () => {
    const hn = $("#ca-heat").value.trim().toUpperCase();
    if (!hn) return;
    try {
      const r = await POST("/api/calculator/alloys", {heat_no: hn});
      const inp = r.inputs || {};
      $("#ca-wt").value = inp.heat_weight_t ?? "";
      els.forEach(e => {
        $(`[data-cur="${e}"]`).value = inp.current?.[e] ?? "";
        $(`[data-tgt="${e}"]`).value = inp.target?.[e] ?? "";
      });
      toast("Heat data loaded — tap weight, latest chemistry, grade aims", "ok");
    } catch (e) { toast(e.message, "err", 4500); }
  };
  $("#ca-go").onclick = async () => {
    const current = {}, target = {};
    els.forEach(e => {
      const c = $(`[data-cur="${e}"]`).value, t = $(`[data-tgt="${e}"]`).value;
      if (c !== "") current[e] = +c;
      if (t !== "") target[e] = +t;
    });
    try {
      const r = await POST("/api/calculator/alloys",
        {heat_weight_t: +$("#ca-wt").value || null, current, target});
      $("#ca-out").innerHTML = !r.ok
        ? `<div class="finding warning"><b>inputs</b><div>${esc(r.message)}<br>${(r.missing||[]).map(esc).join("<br>")}</div></div>`
        : !r.lines.length
          ? `<div class="finding info"><b>info</b><div>${esc(r.message)}</div></div>`
          : `<table class="t"><thead><tr><th>El</th><th>Alloy</th><th>Qty</th><th>Basis</th></tr></thead><tbody>
              ${r.lines.map(l=>`<tr><td><b>${l.element}</b></td><td>${l.alloy?esc(l.alloy):"—"}</td>
                <td class="mono">${l.qty_kg!==null?fmtN(l.qty_kg,1)+" kg":"—"}</td><td class="small muted">${esc(l.note)}</td></tr>`).join("")}
             </tbody></table>
             <div class="row" style="margin-top:8px"><span class="muted small">Predicted:</span>
               ${Object.entries(r.predicted).map(([e,v])=>`<span class="tag">${e} ${v}</span>`).join(" ")}</div>
             <div class="small muted" style="margin-top:6px">${esc(r.message)}</div>`;
    } catch (e) { toast(e.message, "err", 4500); }
  };
  $("#fx-go").onclick = async () => {
    const r = await POST("/api/calculator/flux",
      {heat_weight_t: +$("#fx-wt").value || null, sulphur_pct: $("#fx-s").value === "" ? null : +$("#fx-s").value});
    $("#fx-out").innerHTML = !r.ok
      ? `<div class="finding warning"><b>inputs</b><div>${esc(r.message)}<br>${(r.missing||[]).map(esc).join("<br>")}</div></div>`
      : `<div class="row" style="margin-top:8px"><span class="tag open">Lime ${fmtN(r.lime_kg,0)} kg</span>
          <span class="tag open">Dolomite ${fmtN(r.dolomite_kg,0)} kg</span></div>
         <div class="small muted" style="margin-top:6px">${esc(r.note)}</div>`;
  };
  $("#wr-go").onclick = async () => {
    const r = await POST("/api/calculator/wire", {
      heat_weight_t: +$("#wr-wt").value || null, target_ca_ppm: +$("#wr-ppm").value,
      wire_gpm: +$("#wr-gpm").value, recovery_pct: +$("#wr-rec").value});
    $("#wr-out").innerHTML = !r.ok
      ? `<div class="finding warning"><b>inputs</b><div>${esc(r.message)}<br>${(r.missing||[]).map(esc).join("<br>")}</div></div>`
      : `<div class="row" style="margin-top:8px"><span class="tag open">${fmtN(r.metres,0)} metres CaSi</span></div>
         <div class="small muted" style="margin-top:6px">${esc(r.note)}</div>`;
  };
}

/* ───────────────────────── ORGANIZATIONAL MEMORY ───────────────────────── */
async function viewKnowledge() {
  setTitle("Organizational Memory");
  await ensureDefs();
  const grades = S.masters.grades;
  $("#view").innerHTML = `
    <h2 class="pg">Organizational Memory <small>the WHY behind every decision — knowledge that never retires</small></h2>
    <div class="filter-bar card glass">
      <label class="fld wide"><span>Search notes</span><input id="kn-q" placeholder="e.g. desulphurisation, breakout, VD practice…"></label>
      <label class="fld"><span>Grade</span>
        <select id="kn-grade"><option value="">all grades</option>${grades.map(g=>`<option>${g.code}</option>`).join("")}</select></label>
      <button class="btn primary" id="kn-go">Search</button>
      ${can("knowledge.write") ? `<button class="btn" id="kn-add">＋ Record knowledge</button>` : ""}
    </div>
    <div id="kn-practice" class="sec"></div>
    <div id="kn-out" class="sec grid"></div>`;
  const load = async () => {
    const p = new URLSearchParams();
    if ($("#kn-q").value.trim()) p.set("q", $("#kn-q").value.trim());
    if ($("#kn-grade").value) p.set("grade", $("#kn-grade").value);
    const r = await GET("/api/knowledge?" + p.toString());
    $("#kn-practice").innerHTML = r.practice.length ? `
      <div class="card glass"><h3>Practice intelligence — derived from approved heats only</h3>
        ${r.practice.map(f=>`<div class="finding info"><b>fact</b><div>${esc(f.fact)}</div></div>`).join("")}</div>` : "";
    $("#kn-out").innerHTML = r.notes.map(n => `
      <div class="card glass">
        <div class="row spread"><b>${esc(n.topic || "Note")}</b>
          <span class="muted small mono">${n.at.slice(0,16)}</span></div>
        <div class="small" style="margin:8px 0;white-space:pre-wrap">${esc(n.body)}</div>
        <div class="row">
          ${n.heat_no ? `<a class="link mono small" href="#/heat/${n.heat_no}">${n.heat_no}</a>` : ""}
          ${n.grade_code ? `<span class="tag">${esc(n.grade_code)}</span>` : ""}
          ${n.tags ? n.tags.split(",").map(t=>`<span class="tag pending">${esc(t.trim())}</span>`).join("") : ""}
          <span class="muted small" style="margin-left:auto">— ${esc(n.author)}</span>
        </div>
      </div>`).join("") || `<div class="card glass empty">No knowledge notes yet. When an engineer explains a decision here, it outlives every transfer and retirement.</div>`;
  };
  $("#kn-go").onclick = () => load().catch(e=>toast(e.message,"err"));
  $("#kn-q").addEventListener("keydown", e=>{ if(e.key==="Enter") load(); });
  const ab = $("#kn-add");
  if (ab) ab.onclick = () => UI.modal(`
    <h3>✦ Record engineering knowledge</h3>
    <div class="sub">Capture the reasoning: why this route, why this practice, what was learned. This becomes searchable organizational memory forever.</div>
    <div class="fgrid">
      <label class="fld"><span>Topic</span><input id="kn-n-topic" placeholder="e.g. Why VD-2 for Fe500D high-S heats"></label>
      <label class="fld"><span>Heat (optional)</span><input id="kn-n-heat" class="mono" placeholder="E260151" style="text-transform:uppercase"></label>
      <label class="fld"><span>Grade (optional)</span>
        <select id="kn-n-grade"><option value=""></option>${grades.map(g=>`<option>${g.code}</option>`).join("")}</select></label>
      <label class="fld"><span>Tags <span class="unit">comma separated</span></span><input id="kn-n-tags"></label>
    </div>
    <label class="fld"><span>Knowledge *</span><textarea id="kn-n-body" style="min-height:120px"></textarea></label>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="kn-n-save">Preserve knowledge</button>
    </div>`, m => {
    const knGate = () => { $("#kn-n-save", m).disabled = !$("#kn-n-body", m).value.trim(); };
    knGate();
    m.addEventListener("input", knGate);
    $("#kn-n-save", m).onclick = async () => {
      const body = $("#kn-n-body", m).value.trim();
      try {
        await POST("/api/knowledge", {
          topic: $("#kn-n-topic", m).value, heat_no: $("#kn-n-heat", m).value.trim().toUpperCase() || null,
          grade_code: $("#kn-n-grade", m).value || null, tags: $("#kn-n-tags", m).value, body});
        toast("Knowledge preserved", "ok");
        UI.closeModal(); load();
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
  load().catch(()=>{});
}

/* ───────────────────────── ADMINISTRATION ───────────────────────── */
async function viewAdmin() {
  setTitle("Administration & Governance");
  if (!isAdmin()) { $("#view").innerHTML = `<div class="card glass empty">Administration is restricted to the Administrator role.</div>`; return; }
  await ensureDefs();
  $("#view").innerHTML = `
    <h2 class="pg">Administration <small>every critical action requires password re-authentication and is audited</small></h2>
    <div class="stage-tabs">
      ${["Users","Roles","Masters","Corrections","Approvals","Audit log","Settings","Backup"].map((t,i)=>`<button class="stage-tab ${i===0?"active":""}" data-ad="${t}">${t}</button>`).join("")}
    </div>
    <div id="ad-body"></div>`;
  const body = $("#ad-body");
  const tabs = {
    "Users": adminUsers, "Roles": adminRoles, "Masters": adminMasters,
    "Corrections": adminCorrections, "Approvals": adminApprovals,
    "Audit log": adminAudit, "Settings": adminSettings, "Backup": adminBackup,
  };
  $$("[data-ad]").forEach(b => b.onclick = () => {
    $$("[data-ad]").forEach(x=>x.classList.toggle("active", x===b));
    tabs[b.dataset.ad](body).catch(e=>body.innerHTML=`<div class="empty">${esc(e.message)}</div>`);
  });
  adminUsers(body);
}

async function adminUsers(body) {
  const [users, roles] = await Promise.all([GET("/api/admin/users"), GET("/api/admin/roles")]);
  body.innerHTML = `
    <div class="card glass">
      <div class="row spread"><h3 style="margin:0">User accounts — archived, never deleted</h3>
        <button class="btn primary small" id="au-new">＋ Create user</button></div>
      <table class="t" style="margin-top:10px"><thead><tr>
        <th>Employee ID</th><th>Name</th><th>Designation</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
        ${users.map(u=>`<tr>
          <td class="mono"><b>${esc(u.employee_id)}</b></td><td>${esc(u.name)}</td><td>${esc(u.designation)}</td>
          <td>${esc(u.role)}</td>
          <td>${u.archived?`<span class="tag error">archived</span>`:u.active?`<span class="tag done">active</span>`:`<span class="tag warn">disabled</span>`}</td>
          <td><button class="btn small" data-edit="${u.id}">Manage</button></td></tr>`).join("")}
      </tbody></table>
    </div>`;
  $("#au-new").onclick = () => UI.modal(`
    <h3>Create user</h3>
    <div class="sub">Critical operation — your password is required to confirm (11.7).</div>
    <div class="fgrid">
      <label class="fld"><span>Employee ID *</span><input id="nu-emp" style="text-transform:uppercase"></label>
      <label class="fld"><span>Full name *</span><input id="nu-name"></label>
      <label class="fld"><span>Designation</span><input id="nu-desig"></label>
      <label class="fld"><span>Department</span><input id="nu-dept"></label>
      <label class="fld"><span>Role *</span><select id="nu-role">${roles.map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join("")}</select></label>
      <label class="fld"><span>Initial password * <span class="unit">min 8, letters+digits</span></span><input id="nu-pw" type="password"></label>
      <label class="fld" style="grid-column:1/-1"><span>YOUR password (re-authentication) *</span><input id="nu-reauth" type="password"></label>
    </div>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="nu-go">Create user</button></div>`, m => {
    $("#nu-go", m).onclick = async () => {
      try {
        await POST("/api/admin/users", {
          employee_id: $("#nu-emp",m).value, name: $("#nu-name",m).value,
          designation: $("#nu-desig",m).value, department: $("#nu-dept",m).value,
          role_id: +$("#nu-role",m).value, password: $("#nu-pw",m).value,
          reauth_password: $("#nu-reauth",m).value});
        toast("User created", "ok"); UI.closeModal(); adminUsers(body);
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
  body.addEventListener("click", e => {
    const id = e.target.dataset?.edit;
    if (!id) return;
    const u = users.find(x=>x.id===+id);
    UI.modal(`
      <h3>Manage ${esc(u.name)} <span class="mono muted">(${esc(u.employee_id)})</span></h3>
      <div class="sub">Changes require your password. Users are archived, never deleted (10.13).</div>
      <div class="fgrid">
        <label class="fld"><span>Role</span><select id="eu-role">${roles.map(r=>`<option value="${r.id}" ${r.id===u.role_id?"selected":""}>${esc(r.name)}</option>`).join("")}</select></label>
        <label class="fld"><span>Account state</span>
          <select id="eu-state">
            <option value="active" ${u.active&&!u.archived?"selected":""}>Active</option>
            <option value="disabled" ${!u.active&&!u.archived?"selected":""}>Disabled</option>
            <option value="archived" ${u.archived?"selected":""}>Archived</option>
          </select></label>
        <label class="fld"><span>Reset password <span class="unit">(optional)</span></span><input id="eu-pw" type="password"></label>
        <label class="fld"><span>YOUR password *</span><input id="eu-reauth" type="password"></label>
      </div>
      <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Cancel</button>
        <button class="btn primary" id="eu-go">Apply changes</button></div>`, m => {
      $("#eu-go", m).onclick = async () => {
        const state = $("#eu-state",m).value;
        try {
          await api("PATCH", `/api/admin/users/${u.id}`, {
            role_id: +$("#eu-role",m).value,
            active: state === "active", archived: state === "archived",
            reset_password: $("#eu-pw",m).value || null,
            reauth_password: $("#eu-reauth",m).value});
          toast("User updated", "ok"); UI.closeModal(); adminUsers(body);
        } catch (er) { toast(er.message, "err", 5000); }
      };
    });
  }, {once: true});
}

async function adminRoles(body) {
  const roles = await GET("/api/admin/roles");
  const ALLPERMS = ["production.entry","production.approve.l1","production.approve.l2","production.approve.l3",
                    "reports.view","reports.export","search","knowledge.write","heats.create","heats.abort","admin"];
  body.innerHTML = `
    <div class="card glass">
      <div class="row spread"><h3 style="margin:0">Roles & permissions — configuration, not code (10.4)</h3>
        <button class="btn primary small" id="ar-new">＋ Create role</button></div>
      <table class="t" style="margin-top:10px"><thead><tr><th>Role</th><th>Type</th><th>Permissions</th></tr></thead><tbody>
        ${roles.map(r=>`<tr><td><b>${esc(r.name)}</b></td>
          <td>${r.is_system?`<span class="tag">system</span>`:`<span class="tag open">custom</span>`}</td>
          <td class="small">${(r.permissions||[]).map(p=>`<span class="tag pending">${p}</span>`).join(" ")}</td></tr>`).join("")}
      </tbody></table>
    </div>`;
  $("#ar-new").onclick = () => UI.modal(`
    <h3>Create role</h3>
    <div class="sub">New designations join without source-code changes. Requires your password.</div>
    <label class="fld"><span>Role name *</span><input id="nr-name" placeholder="e.g. Caster Supervisor"></label>
    <label class="fld"><span>Description</span><input id="nr-desc"></label>
    <div class="fld"><span>Permissions</span>
      <div class="row">${ALLPERMS.map(p=>`<label class="small" style="display:flex;gap:5px;align-items:center">
        <input type="checkbox" value="${p}" class="nr-p">${p}</label>`).join("")}</div></div>
    <label class="fld"><span>YOUR password *</span><input id="nr-reauth" type="password"></label>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="nr-go">Create role</button></div>`, m => {
    $("#nr-go", m).onclick = async () => {
      try {
        await POST("/api/admin/roles", {
          name: $("#nr-name",m).value, description: $("#nr-desc",m).value,
          permissions: $$(".nr-p:checked",m).map(i=>i.value),
          reauth_password: $("#nr-reauth",m).value});
        toast("Role created", "ok"); UI.closeModal(); adminRoles(body);
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
}

async function adminMasters(body) {
  S.masters = await GET("/api/admin/masters");
  const mm = S.masters;
  const series = mm.heat_series.find(s=>s.active) || mm.heat_series[0] || {prefix:"E",next_number:260151,pad:6};
  body.innerHTML = `
    <div class="grid cols-2">
      <div class="card glass">
        <div class="row spread"><h3 style="margin:0">Steel grades (${mm.grades.length})</h3>
          <button class="btn small primary" id="am-grade">＋ Grade</button></div>
        <div class="scroll-panel md"><table class="t"><tbody>
          ${mm.grades.map(g=>`<tr><td><b>${esc(g.code)}</b></td>
            <td class="small mono muted">${Object.entries(g.specs).map(([e,s])=>`${e} ${s.min}-${s.max}`).join(" · ")}</td></tr>`).join("")}
        </tbody></table></div>
      </div>
      <div class="card glass">
        <div class="row spread"><h3 style="margin:0">Materials (${mm.materials.length})</h3>
          <button class="btn small primary" id="am-mat">＋ Material</button></div>
        <div class="scroll-panel md"><table class="t"><tbody>
          ${mm.materials.map(x=>`<tr><td class="mono"><b>${esc(x.code)}</b></td><td>${esc(x.name)}</td>
            <td><span class="tag">${x.category}</span></td>
            <td class="small mono muted">${Object.entries(x.composition||{}).map(([e,v])=>`${e} ${v}%`).join(" ")}</td></tr>`).join("")}
        </tbody></table></div>
      </div>
      <div class="card glass">
        <div class="row spread"><h3 style="margin:0">Delay reasons (${mm.delay_reasons.length})</h3>
          <button class="btn small primary" id="am-delay">＋ Reason</button></div>
        <div class="scroll-panel sm"><table class="t"><tbody>
          ${mm.delay_reasons.map(d=>`<tr><td><span class="tag">${esc(d.category)}</span></td><td>${esc(d.reason)}</td><td class="muted small">${esc(d.department)}</td></tr>`).join("")}
        </tbody></table></div>
      </div>
      <div class="card glass">
        <h3>Heat number series</h3>
        <div class="row">
          <label class="fld"><span>Prefix</span><input id="hs-prefix" value="${esc(series.prefix)}" style="width:80px"></label>
          <label class="fld"><span>Next number</span><input id="hs-next" type="number" value="${series.next_number}"></label>
          <label class="fld"><span>Padding</span><input id="hs-pad" type="number" value="${series.pad}" style="width:80px"></label>
          <button class="btn primary" id="hs-save">Set active series</button>
        </div>
        <div class="small muted">Next heat will be <b class="mono">${esc(series.prefix)}${String(series.next_number).padStart(series.pad,"0")}</b>. Equipment master: ${mm.equipment.length} units (${mm.equipment.filter(e=>e.kind==="ladle").length} ladles, VD-2/3, BC-2/3, BF-1/2).</div>
      </div>
    </div>`;
  $("#hs-save").onclick = async () => {
    try {
      await POST("/api/admin/heat-series", {prefix: $("#hs-prefix").value, next_number: +$("#hs-next").value, pad: +$("#hs-pad").value});
      toast("Heat series updated", "ok"); adminMasters(body);
    } catch (e) { toast(e.message, "err"); }
  };
  $("#am-grade").onclick = () => {
    const els = ["C","Si","Mn","S","P","Ti","Cr","Ni","Al","Cu"];
    UI.modal(`
      <h3>Add steel grade</h3>
      <label class="fld"><span>Grade code *</span><input id="ng-code" placeholder="e.g. Fe600"></label>
      <table class="t"><thead><tr><th>El</th><th>Min %</th><th>Aim %</th><th>Max %</th></tr></thead><tbody>
        ${els.map(e=>`<tr><td><b>${e}</b></td>
          ${["min","aim","max"].map(k=>`<td><input type="number" step="0.001" data-g="${e}.${k}"
            class="num-input"></td>`).join("")}</tr>`).join("")}
      </tbody></table>
      <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Cancel</button>
        <button class="btn primary" id="ng-go">Add grade</button></div>`, m => {
      $("#ng-go",m).onclick = async () => {
        const specs = {};
        els.forEach(e => {
          const g = {};
          ["min","aim","max"].forEach(k => { const v = $(`[data-g="${e}.${k}"]`,m).value; if (v!=="") g[k]=+v; });
          if (Object.keys(g).length) specs[e] = {min:g.min??0, aim:g.aim??0, max:g.max??0};
        });
        try {
          await POST("/api/admin/grades", {code: $("#ng-code",m).value.trim(), specs});
          toast("Grade added", "ok"); UI.closeModal(); S.masters=null; adminMasters(body);
        } catch (e) { toast(e.message, "err", 5000); }
      };
    });
  };
  $("#am-mat").onclick = () => UI.modal(`
    <h3>Add material</h3>
    <div class="fgrid">
      <label class="fld"><span>Code *</span><input id="nm-code" style="text-transform:uppercase"></label>
      <label class="fld"><span>Name *</span><input id="nm-name"></label>
      <label class="fld"><span>Category *</span><select id="nm-cat">
        <option>scrap</option><option>flux</option><option selected>alloy</option><option>wire</option><option>additive</option></select></label>
      <label class="fld"><span>Unit</span><select id="nm-unit"><option>kg</option><option>t</option><option>m</option></select></label>
      <label class="fld" style="grid-column:1/-1"><span>Composition <span class="unit">e.g. Si:75, C:0.1, recovery:92</span></span>
        <input id="nm-comp" placeholder="Element:percent pairs, comma separated"></label>
    </div>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="nm-go">Add material</button></div>`, m => {
    $("#nm-go",m).onclick = async () => {
      const comp = {};
      $("#nm-comp",m).value.split(",").map(s=>s.trim()).filter(Boolean).forEach(pair => {
        const [k,v] = pair.split(":").map(x=>x.trim());
        if (k && v!==undefined && !isNaN(+v)) comp[k]=+v;
      });
      try {
        await POST("/api/admin/materials", {code:$("#nm-code",m).value.trim().toUpperCase(),
          name:$("#nm-name",m).value, category:$("#nm-cat",m).value, unit:$("#nm-unit",m).value, composition:comp});
        toast("Material added", "ok"); UI.closeModal(); S.masters=null; adminMasters(body);
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
  $("#am-delay").onclick = () => UI.modal(`
    <h3>Add delay reason</h3>
    <div class="fgrid">
      <label class="fld"><span>Category *</span><select id="nd-cat">
        ${["Mechanical","Electrical","Process","Crane","Material","Utility","Quality","Caster","Other"].map(c=>`<option>${c}</option>`).join("")}</select></label>
      <label class="fld"><span>Responsible department</span><input id="nd-dept"></label>
      <label class="fld" style="grid-column:1/-1"><span>Reason *</span><input id="nd-reason"></label>
    </div>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="nd-go">Add reason</button></div>`, m => {
    $("#nd-go",m).onclick = async () => {
      try {
        await POST("/api/admin/delay-reasons", {category:$("#nd-cat",m).value, reason:$("#nd-reason",m).value, department:$("#nd-dept",m).value});
        toast("Delay reason added", "ok"); UI.closeModal(); S.masters=null; adminMasters(body);
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
}

async function adminAudit(body) {
  const load = async () => {
    const p = new URLSearchParams();
    if ($("#aud-cat").value) p.set("category", $("#aud-cat").value);
    if ($("#aud-heat").value.trim()) p.set("heat_no", $("#aud-heat").value.trim().toUpperCase());
    const rows = await GET("/api/admin/audit?" + p.toString());
    $("#aud-rows").innerHTML = rows.map(a=>`<tr>
      <td class="mono">${a.at.slice(0,19)}</td><td>${esc(a.user)}</td>
      <td><span class="tag">${a.category}</span></td><td>${esc(a.action)}</td>
      <td class="mono">${a.heat_no ? `<a class="link" href="#/heat/${a.heat_no}">${a.heat_no}</a>` : "—"}</td>
      <td class="small muted">${esc(a.detail||"")}</td></tr>`).join("")
      || `<tr><td colspan="6" class="empty">No entries.</td></tr>`;
  };
  body.innerHTML = `
    <div class="card glass">
      <h3>Immutable audit log — append-only, never edited, never deleted</h3>
      <div class="row">
        <label class="fld"><span>Category</span><select id="aud-cat"><option value="">all</option>
          ${["auth","production","approval","admin","report","system"].map(c=>`<option>${c}</option>`).join("")}</select></label>
        <label class="fld"><span>Heat no</span><input id="aud-heat" class="mono" style="text-transform:uppercase"></label>
        <button class="btn primary" id="aud-go">Filter</button>
      </div>
      <div class="scroll-panel lg" style="margin-top:var(--sp-3)"><table class="t"><thead><tr>
        <th>Time</th><th>User</th><th>Category</th><th>Action</th><th>Heat</th><th>Detail</th></tr></thead>
        <tbody id="aud-rows"></tbody></table></div>
    </div>`;
  $("#aud-go").onclick = () => load().catch(e=>toast(e.message,"err"));
  load();
}

async function adminSettings(body) {
  const st = await GET("/api/admin/settings");
  body.innerHTML = `
    <div class="card glass">
      <h3>System settings — changes require your password and are audited</h3>
      <table class="t"><thead><tr><th>Key</th><th>Value</th><th></th></tr></thead><tbody>
        ${Object.entries(st).map(([k,v])=>`<tr><td class="mono">${esc(k)}</td>
          <td><input data-set="${esc(k)}" value="${esc(v)}" class="num-input" ${k==="plant_day_start"?'title="Constitutional — informational; the Plant Date Engine is the source of truth"':""}></td>
          <td><button class="btn small" data-save="${esc(k)}">Save</button></td></tr>`).join("")}
      </tbody></table>
      <div class="small muted" style="margin-top:8px">Note: <code>plant_day_start</code> is constitutionally frozen at 22:00 — the setting is displayed for documentation; the Plant Date Engine is the single source of truth. <code>maintenance_mode: on</code> shows a banner to users (production entry remains available to avoid data loss).</div>
    </div>`;
  body.addEventListener("click", async e => {
    const k = e.target.dataset?.save;
    if (!k) return;
    const pw = await promptModal("Confirm setting change", `Changing “${k}”. Enter YOUR password to re-authenticate.`, "Your password");
    if (pw === null) return;
    try {
      await POST("/api/admin/settings", {key: k, value: $(`[data-set="${k}"]`).value, reauth_password: pw});
      toast("Setting saved", "ok");
    } catch (er) { toast(er.message, "err", 5000); }
  });
}

async function adminBackup(body) {
  const render = async () => {
    const [list, st] = await Promise.all([
      GET("/api/admin/backup/list"),
      GET("/api/admin/backup/status").catch(() => null),
    ]);

    // ── Failsafe banner ──────────────────────────────────────────────────
    // Shown when the database is empty but snapshots exist on the server.
    // It only OFFERS: restoring automatically would overwrite a legitimately
    // new plant on its first day and hide whatever caused the loss.
    const rec = st?.recovery;
    $("#bk-failsafe").innerHTML = rec?.recoverable ? `
      <div class="finding error bk-alert">
        <b>recovery</b>
        <div><b>This database is empty, but ${rec.snapshots} backup(s) are on the server.</b>
        That usually means the data was lost — a wiped disk, or a fresh deploy on a
        host that does not keep files. The newest is
        <span class="mono">${esc(rec.latest.filename)}</span> from ${esc(rec.latest.at)}.
        Restore it below, or ignore this if the plant genuinely is new.</div>
      </div>` : "";

    // ── 7-day off-server reminder ────────────────────────────────────────
    const rem = st?.reminder;
    $("#bk-remind").innerHTML = rem?.due ? `
      <div class="finding warning bk-alert">
        <b>reminder</b>
        <div><b>Save a copy off this server.</b>
        ${rem.never ? "No backup has ever been downloaded."
                    : `The last copy was taken ${rem.days_since} days ago.`}
        Backups live on the same disk as the database — if that disk dies, both
        go with it. Download the newest below and keep it somewhere else.
        SMS-2 asks every ${rem.every_days} days.</div>
      </div>` : "";

    $("#bk-list").innerHTML = list.length ? `
      <table class="t"><thead><tr><th>Backup file</th><th>Taken</th><th class="num">Size</th><th></th><th></th></tr></thead><tbody>
      ${list.map(b => `<tr>
        <td class="mono">${esc(b.filename)}${b.encrypted ? ` <span class="tag done">encrypted</span>` : ""}</td>
        <td class="mono">${esc(b.at || "—")}</td>
        <td class="num mono">${fmtN(b.size_kb, 0)} KB</td>
        <td><button class="btn small" onclick="download('/api/admin/backup/${esc(b.filename)}/download','${esc(b.filename)}')">⬇ Save to PC</button></td>
        <td><button class="btn small" data-restore="${esc(b.filename)}">Restore</button></td>
      </tr>`).join("")}
      </tbody></table>` : `<div class="empty">No backups yet.</div>`;

    $$("#bk-list [data-restore]").forEach(b =>
      b.onclick = () => restoreDialog({serverFile: b.dataset.restore}));
  };

  body.innerHTML = `
    <div id="bk-failsafe"></div>
    <div id="bk-remind"></div>

    <div class="card glass">
      <h3>Business continuity — backups</h3>
      <div class="small muted" style="margin-bottom:var(--sp-3)">
        A snapshot is taken automatically every 3 days and whenever you ask.
        Set <code>SMS2_BACKUP_PASSPHRASE</code> on the server and every backup is
        encrypted (AES-256). Keep at least one copy off this machine — and test a
        restore now and then, because an untested backup is only a hope.
      </div>
      <div class="row">
        <button class="btn primary" id="bk-go">Create backup now</button>
        <button class="btn" id="bk-restore-file">⭯ Restore from a file…</button>
      </div>
      <div id="bk-list" class="sec"></div>
    </div>

    <div class="card glass sec">
      <h3>Restore from a file on this computer</h3>
      <div class="small muted" style="margin-bottom:var(--sp-3)">
        Drag a backup here — including one you saved on your PC. Encrypted
        <span class="mono">.enc</span> files are accepted; you will be asked for the
        passphrase. Nothing is changed until you confirm.
      </div>
      <div id="bk-drop" class="bk-drop" tabindex="0" role="button"
           aria-label="Drop a backup file here, or click to choose one">
        <span class="bk-drop-icon" aria-hidden="true">⭳</span>
        <b>Drop a backup file here</b>
        <span>or click to choose · .db or .db.enc</span>
      </div>
      <input type="file" id="bk-file" accept=".db,.enc,.sqlite,.sqlite3" class="hidden">
    </div>`;

  $("#bk-go").onclick = async () => {
    const b = $("#bk-go"); b.disabled = true;
    try { const r = await POST("/api/admin/backup");
      toast(`Backup created: ${r.filename}${r.encrypted ? " (encrypted)" : ""}`, "ok", 3200);
      render();
    } catch (e) { toast(e.message, "err", 5000); }
    b.disabled = false;
  };

  const drop = $("#bk-drop"), input = $("#bk-file");
  const pick = () => input.click();
  $("#bk-restore-file").onclick = pick;
  drop.onclick = pick;
  drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
  input.onchange = () => { if (input.files[0]) restoreDialog({file: input.files[0]}); input.value = ""; };
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => {
    const f = e.dataTransfer?.files?.[0];
    if (f) restoreDialog({file: f});
  });

  window.__bkRender = render;
  render();
}

/* Restore confirmation — the most destructive operation in SMS-2, so it states
   plainly what will be lost and requires the administrator's own password. */
function restoreDialog(opts) {
  const name = opts.file ? opts.file.name : opts.serverFile;
  const enc = /\.enc$/i.test(name);
  UI.modal(`
    <h3>Restore database</h3>
    <div class="sub">Every heat, approval and audit entry recorded <b>since this
      backup was taken will be lost</b>. The current database is snapshotted
      first, so this can be undone.</div>
    <div class="ca-row"><span>File</span><b class="mono">${esc(name)}</b></div>
    ${enc ? `<label class="fld"><span>Backup passphrase</span>
      <input type="password" id="rs-pass" placeholder="the passphrase this backup was encrypted with"></label>` : ""}
    <label class="fld"><span>Your password *</span>
      <input type="password" id="rs-auth" placeholder="re-authenticate to confirm"></label>
    <div class="finding warning"><b>note</b><div>On a plant server prefer
      <span class="mono">deploy/restore.sh</span> with the service stopped. This
      in-app path exists for hosted deployments where there is no console.</div></div>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn danger" id="rs-go">Restore — overwrite database</button>
    </div>`, m => {
    $("#rs-go", m).onclick = async () => {
      const auth = $("#rs-auth", m).value;
      if (!auth) { toast("Enter your password to confirm", "err"); return; }
      const btn = $("#rs-go", m); btn.disabled = true; btn.textContent = "Restoring…";
      try {
        let blob = opts.file;
        if (!blob) {           // restoring one that already lives on the server
          const r = await fetch(`/api/admin/backup/${encodeURIComponent(opts.serverFile)}/download`,
                                {headers: {Authorization: "Bearer " + S.token}});
          if (!r.ok) throw new Error("Could not read that backup from the server");
          blob = new File([await r.blob()], opts.serverFile);
        }
        const fd = new FormData();
        fd.append("file", blob, name);
        fd.append("reauth_password", auth);
        fd.append("passphrase", enc ? ($("#rs-pass", m)?.value || "") : "");
        const res = await fetch("/api/admin/backup/restore",
          {method: "POST", headers: {Authorization: "Bearer " + S.token}, body: fd});
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || "Restore failed");
        UI.closeModal();
        UI.modal(`
          <h3>Database restored</h3>
          <div class="sub">${out.heats} heats and ${out.users} users are back.</div>
          <div class="ca-row"><span>Safety copy of the previous database</span>
            <b class="mono">${esc(out.safety_copy || "—")}</b></div>
          <div class="finding info"><b>next</b><div>${esc(out.note || "")}</div></div>
          <div class="modal-foot">
            <button class="btn primary" onclick="UI.closeModal(); doLogout(true)">Sign out</button>
          </div>`);
      } catch (e) {
        toast(e.message, "err", 6000);
        btn.disabled = false; btn.textContent = "Restore — overwrite database";
      }
    };
  });
}

/* expose handlers used from inline HTML */
/* (debug expose relocated to end of file — see FAIL SAFE LAW) */


/* ═══════ CORRECTION SYSTEM UI (Constitution S.3) ═══════ */
async function requestCorrection(heatNo) {
  await ensureDefs();
  const h = await GET(`/api/heats/${heatNo}`);
  const stages = h.required_stages.filter(s => s !== "review");
  UI.modal(`
    <h3>✏ Request correction — ${heatNo}</h3>
    <div class="sub">Old value → preserved → correction request → approval → new value → version → audit.
      The original is never deleted. Approval by Shift Incharge or Administrator-class.</div>
    <div class="fgrid">
      <label class="fld"><span>Stage</span>
        <select id="cr-stage">${stages.map(s => `<option value="${s}">${esc(S.stageDefs[s].title)}</option>`).join("")}</select></label>
      <label class="fld"><span>Field</span><select id="cr-field"></select></label>
      <label class="fld"><span>Current value</span><input id="cr-old" readonly class="mono"></label>
      <label class="fld"><span>New value *</span><input id="cr-new"></label>
    </div>
    <label class="fld"><span>Reason * (mandatory)</span><textarea id="cr-reason"></textarea></label>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="cr-go">Submit correction request</button>
    </div>`, m => {
    let stageData = {};
    const loadFields = async () => {
      const st = $("#cr-stage", m).value;
      const sd = await GET(`/api/heats/${heatNo}/stages/${st}`);
      stageData = sd.data || {};
      const fields = S.stageDefs[st].tiles.flatMap(t => t.fields || []).filter(f => !f.auto);
      $("#cr-field", m).innerHTML = fields.map(f =>
        `<option value="${f.key}">${esc(f.label)}</option>`).join("");
      showOld();
    };
    const showOld = () => {
      const k = $("#cr-field", m).value;
      $("#cr-old", m).value = stageData[k] ?? "(empty)";
    };
    $("#cr-stage", m).onchange = loadFields;
    $("#cr-field", m).onchange = showOld;
    loadFields();
    const crGate = () => {
      const ok = $("#cr-new", m).value.trim() !== "" && $("#cr-reason", m).value.trim() !== "";
      $("#cr-go", m).disabled = !ok;
      $("#cr-go", m).title = ok ? "Ready for approval routing"
        : "The system is guiding you: enter the new value and the mandatory reason";
    };
    crGate();
    m.addEventListener("input", crGate);
    $("#cr-go", m).onclick = async () => {
      const reason = $("#cr-reason", m).value.trim();
      try {
        await POST(`/api/heats/${heatNo}/corrections`, {
          stage: $("#cr-stage", m).value,
          changes: {[$("#cr-field", m).value]: $("#cr-new", m).value},
          reason});
        toast("Correction request submitted for approval", "ok");
        UI.closeModal();
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
}

async function decideCorrection(cid) {
  const list = await GET("/api/corrections?status=pending");
  const c = list.find(x => x.id === cid);
  if (!c) { toast("Request no longer pending", "err"); route(); return; }
  UI.modal(`
    <h3>Correction review — ${c.heat_no} · ${esc(c.stage_title)}</h3>
    <div class="sub">Requested by ${esc(c.requested_by)} · ${c.requested_at}</div>
    <table class="t"><thead><tr><th>Field</th><th>Old value (preserved)</th><th>New value</th></tr></thead><tbody>
      ${Object.entries(c.changes).map(([k, ch]) => `<tr><td>${esc(k)}</td>
        <td class="mono">${esc(ch.old ?? "(empty)")}</td>
        <td class="mono cell-new">${esc(ch.new)}</td></tr>`).join("")}
    </tbody></table>
    <div class="finding info" style="margin-top:10px"><b>reason</b><div>${esc(c.reason)}</div></div>
    <label class="fld"><span>Decision remarks</span><input id="cd-rem"></label>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Later</button>
      <button class="btn danger" id="cd-rej">Reject</button>
      <button class="btn primary" id="cd-app">Approve &amp; apply as new version</button>
    </div>`, m => {
    const decide = async approve => {
      try {
        const r = await POST(`/api/corrections/${cid}/decide`,
          {approve, remarks: $("#cd-rem", m).value});
        toast(approve ? `Applied as version ${r.version}` : "Correction rejected", "ok");
        UI.closeModal(); route();
      } catch (e) { toast(e.message, "err", 5000); }
    };
    $("#cd-app", m).onclick = () => decide(true);
    $("#cd-rej", m).onclick = () => decide(false);
  });
}

async function showVersions(heatNo) {
  const vers = await GET(`/api/heats/${heatNo}/versions`);
  UI.modal(`
    <h3>🕰 Version history — ${heatNo}</h3>
    <div class="sub">Digital Time Machine: every submission and approved correction, immutable, forever.</div>
    ${vers.length ? `<div class="scroll-panel lg"><table class="t"><thead><tr>
      <th>Stage</th><th>Version</th><th>Kind</th><th>When</th><th>By</th><th>Changes / reason</th></tr></thead><tbody>
      ${vers.map(v => `<tr><td>${esc(v.stage_title)}</td><td class="mono"><b>v${v.version_no}</b></td>
        <td><span class="tag ${v.kind === "correction" ? "warn" : "done"}">${v.kind}</span></td>
        <td class="mono">${v.at.slice(0, 16)}</td><td>${esc(v.by)}</td>
        <td class="small">${v.kind === "correction" && v.changes
          ? Object.entries(v.changes).map(([k, ch]) => `${esc(k)}: <s class="muted">${esc(ch.old ?? "∅")}</s> → <b>${esc(ch.new)}</b>`).join("<br>")
          : esc(v.reason)}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No versions yet — the first snapshot is written on stage submission.</div>`}
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`);
}

async function adminCorrections(body) {
  const [pending, decided] = await Promise.all([
    GET("/api/corrections?status=pending"),
    GET("/api/corrections?status=approved"),
  ]);
  body.innerHTML = `
    <div class="card glass">
      <h3>Pending correction requests</h3>
      ${pending.length ? `<table class="t"><thead><tr><th>Heat</th><th>Stage</th><th>Changes</th><th>Reason</th><th>By</th><th></th></tr></thead><tbody>
        ${pending.map(c => `<tr><td class="mono"><a class="link" href="#/heat/${c.heat_no}">${c.heat_no}</a></td>
          <td>${esc(c.stage_title)}</td><td class="small">${Object.keys(c.changes).map(esc).join(", ")}</td>
          <td class="small">${esc(c.reason)}</td><td>${esc(c.requested_by)}</td>
          <td>${c.can_decide ? `<button class="btn small primary" onclick="decideCorrection(${c.id})">Review</button>` : ""}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No pending corrections.</div>`}
    </div>
    <div class="card glass sec">
      <h3>Applied corrections (version history)</h3>
      ${decided.length ? `<table class="t"><thead><tr><th>Heat</th><th>Stage</th><th>Version</th><th>Approved by</th><th>When</th></tr></thead><tbody>
        ${decided.map(c => `<tr><td class="mono">${c.heat_no}</td><td>${esc(c.stage_title)}</td>
          <td class="mono">v${c.applied_version}</td><td>${esc(c.decided_by)}</td>
          <td class="mono">${c.decided_at?.slice(0, 16) || ""}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">None yet.</div>`}
    </div>`;
}

/* ═══════ THE AI PRODUCTION ENGINEER (Constitution S.6) ═══════ */
const AI_STATE = {mode: "production_engineer"};

async function viewAI() {
  setTitle("AI Production Engineer");
  const data = await GET("/api/ai/insights");
  const modes = data.modes;
  const samples = [
    "Why did production drop?", "Why are delays increasing?",
    "Which shift is the best?", "Which machine is underutilized?",
    "Which machine should be maintained?", "How can I save power?",
    "What are today's problems?", "Explain LRF", "Explain VD", "Explain EAF",
  ];
  $("#view").innerHTML = `
    <h2 class="pg">AI Production Engineer <small>read-only intelligence — analyzes, explains, recommends, predicts, teaches. Never modifies data.</small></h2>
    <div class="filter-bar card glass">
      <div class="ai-orb"></div>
      <div style="flex:1">
        <div class="stage-tabs" style="margin:0" id="ai-modes">
          ${Object.entries(modes).map(([k, v]) =>
            `<button class="stage-tab ${k === AI_STATE.mode ? "active" : ""}" data-m="${k}">${esc(v)}</button>`).join("")}
        </div>
      </div>
    </div>
    <div class="row card glass sec">
      <label class="fld"><span>Ask the plant</span>
        <input id="ai-q" placeholder="e.g. Why did production drop last week?"></label>
      <button class="btn primary" id="ai-go">Ask</button>
    </div>
    <div class="row" style="margin-top:8px">
      ${samples.map(s => `<span class="tag pending ai-chip" data-q="${esc(s)}">${esc(s)}</span>`).join(" ")}
    </div>
    <div id="ai-answer" class="sec"></div>
    <h3 class="eyebrow spaced">Live plant intelligence — computed from your permanent records</h3>
    <div class="grid cols-2" id="ai-insights">
      ${data.insights.map(i => `
        <div class="ai-card glass ${i.severity}">
          <span class="ai-k">${i.kind}${i.subject ? " · " + esc(i.subject) : ""}</span>
          <div style="margin-top:5px">${esc(i.text)}</div>
        </div>`).join("")}
    </div>
    <div class="small muted sec">The AI Production Engineer is deterministic in v1 — every statement above is computed
      directly from recorded heats, delays and timings, so it cannot hallucinate and keeps working with zero
      internet or AI-service dependency (Constitution S.7). The more data SMS-2 collects, the more intelligent it becomes.</div>`;

  $("#ai-modes").onclick = e => {
    const k = e.target.dataset?.m;
    if (!k) return;
    AI_STATE.mode = k;
    $$("#ai-modes .stage-tab").forEach(b => b.classList.toggle("active", b.dataset.m === k));
  };
  const ask = async q => {
    $("#ai-answer").innerHTML = `<div class="card glass empty">Analyzing plant records…</div>`;
    try {
      const a = await POST("/api/ai/ask", {question: q, mode: AI_STATE.mode});
      $("#ai-answer").innerHTML = `
        <div class="card glass accent">
          <div class="row spread"><h3 style="margin:0">${esc(a.persona)}</h3>
            <span class="tag">${esc(q.slice(0, 60))}</span></div>
          <div class="ai-ans" style="margin-top:8px">${esc(a.answer)}</div>
          <div class="small muted" style="margin-top:10px">${a.facts.map(esc).join(" · ")}</div>
        </div>`;
    } catch (e) { toast(e.message, "err"); }
  };
  $("#ai-go").onclick = () => { const q = $("#ai-q").value.trim(); if (q) ask(q); };
  $("#ai-q").addEventListener("keydown", e => { if (e.key === "Enter") $("#ai-go").click(); });
  $("#view").addEventListener("click", e => {
    const q = e.target.dataset?.q;
    if (q) { $("#ai-q").value = q; ask(q); }
  });
}


/* ═══ PRODUCTION SYNCHRONIZATION ENGINE (never silent, never "reload page") ═══ */
function syncAlert(h, stage, tile, mine, det, applyVersion) {
  const server = det.server_data || {};
  const changed = Object.keys(mine).filter(k =>
    (mine[k] ?? "") !== "" && String(mine[k]) !== String(server[k] ?? ""));
  const rows = changed.map(k => `
    <tr><td>${esc(k)}</td>
      <td class="mono">${esc(server[k] ?? "(empty)")}</td>
      <td class="mono cell-new">${esc(mine[k])}</td></tr>`).join("");
  UI.modal(`
    <h3>⚡ Production Synchronization Alert</h3>
    <div class="sub">This heat has been updated from another authorized workstation
      (<b>${esc(det.updated_by)}</b>). Your current changes have been safely preserved
      in this workstation's draft — nothing has been lost, and nothing will be
      overwritten silently.</div>
    ${changed.length ? `<table class="t"><thead><tr><th>Field</th><th>Their saved value</th><th>Your draft</th></tr></thead>
      <tbody>${rows}</tbody></table>`
      : `<div class="empty">Your draft matches the latest saved data — you can simply continue.</div>`}
    <div class="modal-foot" style="flex-wrap:wrap">
      <button class="btn" data-a="keep">Keep current draft</button>
      <button class="btn" data-a="theirs">View their version</button>
      ${changed.length ? `<button class="btn" data-a="approval">Request approval</button>
      <button class="btn primary" data-a="merge">Merge changes</button>` : ""}
    </div>`, m => {
    m.addEventListener("click", async e => {
      const a = e.target.dataset?.a;
      if (!a) return;
      if (a === "keep") {
        applyVersion(det.server_version);           // future save merges over latest
        toast("Draft kept — it stays on this workstation until you save or submit", "ok", 3800);
        UI.closeModal();
      }
      if (a === "theirs") {
        localStorage.removeItem(`sms2_draft_${h.heat_no}_${stage}`);
        applyVersion(det.server_version);
        UI.closeModal(); UI.closeModal();           // close alert + tile
        toast(`Showing the version saved by ${det.updated_by}`, "ok");
        refreshStage(h, stage);
        const sd = await GET(`/api/heats/${h.heat_no}/stages/${stage}`);
        fieldsTile(h, stage, tile, sd, false);
      }
      if (a === "merge") {
        try {
          const merged = {...server};
          changed.forEach(k => merged[k] = mine[k]);
          const r = await PUT(`/api/heats/${h.heat_no}/stages/${stage}`,
            {data: merged, version: det.server_version});
          applyVersion(r.version);
          localStorage.removeItem(`sms2_draft_${h.heat_no}_${stage}`);
          toast(`Merged — your fields applied over their version (v${r.version})`, "ok", 3800);
          UI.closeModal();
        } catch (er) { toast(er.message, "err", 5000); }
      }
      if (a === "approval") {
        const reason = await promptModal("Escalate to approval",
          "Your conflicting values will be routed through the Correction System (approved by Shift Incharge / higher authority). State the reason.");
        if (reason === null) return;
        try {
          const ch = {}; changed.forEach(k => ch[k] = mine[k]);
          await POST(`/api/heats/${h.heat_no}/corrections`,
            {stage, changes: ch, reason: `[SYNC ESCALATION] ${reason}`});
          toast("Sent to higher authority — correction request created", "ok", 4200);
          UI.closeModal();
        } catch (er) { toast(er.message, "err", 5000); }
      }
    });
  });
}

/* ══════════════════════════════════════════════════════════════════
   THE SOUND NOTIFICATION LAW
   The approval tone is reserved for events that need a human decision:
   approval requests, critical administrative alerts, important production
   alerts. It is NEVER played for auto-save, live data updates, the 2-second
   refresh cycle, report refreshes or synchronisation. It plays ONCE.
   ══════════════════════════════════════════════════════════════════ */
const SFX = {
  el: null,
  prefs() { return (S.user && S.user.notification_prefs) || {}; },
  mode() { return this.prefs().sound_mode || "important"; },   // important|approval|critical|silent
  volume() { const v = this.prefs().sound_volume; return v === undefined ? 0.7 : +v; },
  IMPORTANT_KINDS: ["delay", "emergency", "security", "system", "continuity", "admin"],

  qualifies(n) {
    // The ONLY three categories permitted to make a sound.
    const approval = n.kind === "approval";
    const criticalAdmin = n.priority === "critical";
    const importantProduction = n.priority === "high" && this.IMPORTANT_KINDS.includes(n.kind);
    const mode = this.mode();
    if (mode === "silent") return false;
    if (mode === "approval") return approval;
    if (mode === "critical") return criticalAdmin;
    return approval || criticalAdmin || importantProduction;      // "important" (default)
  },

  play() {                    // PLAYS ONCE — never loops, never queues
    try {
      if (!this.el) {
        this.el = new Audio("/static/assets/approval-notification.mp3");
        this.el.loop = false;
        this.el.preload = "auto";
      }
      this.el.volume = Math.max(0, Math.min(1, this.volume()));
      this.el.pause(); this.el.currentTime = 0;
      const p = this.el.play();
      if (p && p.catch) p.catch(() => {});   // browsers block audio before first interaction
    } catch {}
  },

  test() { const m = this.mode(); if (m === "silent") { toast("Silent mode is active", "err", 2000); return; } this.play(); }
};

/* New-notification handler — the ONLY place sound may originate.
   Called from the notification poll with the freshly fetched list. */
function onNewNotifications(list) {
  const last = +(localStorage.getItem("sms2_last_notif") || 0);
  let maxId = last;
  list.forEach(n => { if (n.id > maxId) maxId = n.id; });
  if (!last) { localStorage.setItem("sms2_last_notif", String(maxId)); return; }  // first load: silent
  const fresh = list.filter(n => n.id > last && !n.read);
  if (!fresh.length) return;
  localStorage.setItem("sms2_last_notif", String(maxId));

  const p = S.user.notification_prefs || {};
  // browser pop-ups (opt-in, high/critical only)
  if (p.browser && "Notification" in window && Notification.permission === "granted") {
    fresh.filter(n => n.priority === "high" || n.priority === "critical").slice(0, 3)
      .forEach(n => { try { new Notification("SMS-2 \u00b7 " + n.title, {body: n.body || "", tag: "sms2-" + n.id}); } catch {} });
  }
  // sound: at most ONE tone per batch, only for qualifying events
  try {                     // FAIL SAFE LAW: a broken sound system only mutes
    if (fresh.some(n => SFX.qualifies(n))) SFX.play();
  } catch (e) { console.error("SMS-2 sound disabled (non-fatal):", e); }
  // approval events open the decision dialog and refresh the Approval Centre
  const appr = fresh.find(n => n.kind === "approval");
  if (appr && !document.querySelector(".modal-back")) approvalAlert(appr).catch(() => {});
}

/* ══ THE APPROVAL LAW ══
   Premium alert dialog, approval sound (played by the caller), visual
   notification and real-time refresh. APPROVE signs the heat at the
   caller's level; REJECT returns it for correction through the audited
   Correction System — SMS-2 has no silent rejection path. */
async function approvalAlert(n) {
  const isPw = /password/i.test(n.title || "");
  let pending = null;
  if (!isPw && n.heat_no) {
    try {
      const d = await GET("/api/dashboard");
      pending = (d.attention?.pending_approvals || [])
        .find(p => p.heat_no === n.heat_no) || null;
    } catch {}
  }
  const canAct = pending && pending.mine;
  UI.modal(`
    <div class="ap-wrap">
      <div class="ap-badge">New approval request</div>
      ${n.heat_no ? `<div class="ap-heat"><span>Heat</span><b class="mono">${esc(n.heat_no)}</b></div>` : ""}
      <div class="ap-role">${pending
        ? `${esc(pending.awaiting)} approval \u00b7 Level ${pending.level}`
        : esc(n.title)}</div>
      ${n.body ? `<div class="ap-body">${esc(n.body)}</div>` : ""}
      ${!isPw && pending && !canAct ? `
        <div class="ap-note">This heat awaits <b>${esc(pending.awaiting)}</b>.
          Your role cannot sign at this level \u2014 you can still open the heat to review it.</div>` : ""}
      ${!isPw && !pending && n.heat_no ? `
        <div class="ap-note">This approval is no longer pending \u2014 it may already have been signed.</div>` : ""}
    </div>
    <div class="modal-foot ap-foot">
      <button class="btn" data-ap="cancel">Cancel</button>
      ${isPw ? `<button class="btn primary" data-ap="centre">Open Approval Centre</button>`
        : canAct ? `<button class="btn danger" data-ap="reject">Reject</button>
                    <button class="btn primary" data-ap="approve">Approve</button>`
        : n.heat_no ? `<button class="btn primary" data-ap="open">Open heat</button>` : ""}
    </div>`, m => {
    m.addEventListener("click", async e => {
      const a = e.target.closest("[data-ap]")?.dataset.ap;
      if (!a) return;
      if (a !== "cancel") { try { await POST(`/api/notifications/${n.id}/read`); } catch {} }
      if (a === "cancel") { UI.closeModal(); return; }
      if (a === "centre") {
        UI.closeModal(); location.hash = "#/admin";
        setTimeout(() => document.querySelector('[data-ad="Approvals"]')?.click(), 240);
        return;
      }
      if (a === "open") { UI.closeModal(); location.hash = `#/heat/${n.heat_no}`; return; }
      if (a === "approve") {
        try {
          await POST(`/api/heats/${n.heat_no}/approve`, {level: pending.level, remarks: ""});
          toast(`Heat ${n.heat_no}: level ${pending.level} approved \u2014 signature recorded`, "ok", 3600);
          UI.closeModal(); pulseNow();
        } catch (er) { toast(er.message, "err", 6000); }
        return;
      }
      if (a === "reject") {
        UI.closeModal();
        const reason = await promptModal(`Reject Heat ${n.heat_no}`,
          "Rejection returns this heat for correction through the audited Correction System. State the reason \u2014 it is recorded permanently and sent to the submitting engineer.");
        if (reason === null) return;
        if (!reason.trim()) { toast("A rejection reason is mandatory", "err", 4000); return; }
        try {
          await POST(`/api/heats/${n.heat_no}/corrections`,
            {stage: "review", changes: {}, reason: `[APPROVAL REJECTED \u2014 L${pending.level} ${S.user.role}] ${reason.trim()}`});
          toast(`Heat ${n.heat_no} returned for correction`, "ok", 4000);
          pulseNow();
        } catch (er) { toast(er.message, "err", 6000); }
      }
    });
  });
}


function pulseNow() { PULSE.last = {}; PULSE.tick(); }

/* ══════════════════════════════════════════════════════════════════════
   THE REAL-TIME LAW · 2-SECOND PULSE
   Every 2s SMS-2 asks the server for a ~450-byte set of counters and
   signatures. Only the domains whose signature CHANGED are refreshed, and
   each refresh patches the smallest possible region — never the page.

   Guarantees enforced here:
     · no page reload, ever
     · no flicker: a region is skipped while it holds keyboard focus,
       while a dialog is open over it, or while the tab is hidden
     · auto-save is never interrupted: the pulse only reads, and it never
       touches an open tile workspace
   ══════════════════════════════════════════════════════════════════════ */
const PULSE = {
  timer: null, last: {}, handlers: {}, busy: false, fails: 0,

  on(domain, fn) { (this.handlers[domain] = this.handlers[domain] || []).push(fn); },

  start() {
    clearInterval(this.timer);
    this.timer = setInterval(() => this.tick(), 2000);
    this.tick();
  },
  stop() { clearInterval(this.timer); this.timer = null; this.last = {}; },

  async tick() {
    if (this.busy || document.hidden || !S.token) return;
    this.busy = true;
    try {
      const p = await GET("/api/pulse");
      this.fails = 0;
      if (p.plant_day) S.plant = {...(S.plant || {}), plant_day: p.plant_day, shift: p.shift};
      for (const key of Object.keys(p)) {
        if (key === "t" || key === "plant_day" || key === "shift") continue;
        const sig = JSON.stringify(p[key]);
        const seen = this.last[key];
        if (seen === sig) continue;
        this.last[key] = sig;
        if (seen === undefined) continue;          // first observation = baseline only
        for (const fn of (this.handlers[key] || [])) {
          try { await fn(p[key]); } catch {}
        }
      }
    } catch (e) {
      // A transient network blip must never disturb the operator.
      if (++this.fails === 5) toast("Live updates paused — reconnecting…", "err", 2500);
    }
    this.busy = false;
  }
};

/* A region may be patched only when nobody is working inside it. */
function patchable(el) {
  if (!el || !el.isConnected) return false;
  if (document.querySelector(".modal-back")) return false;       // a dialog is open
  const a = document.activeElement;
  if (a && a !== document.body && el.contains(a)) return false;  // user is typing here
  return true;
}

function swapRegion(el, html) {
  if (!patchable(el)) return false;
  if (el.innerHTML === html) return false;                       // nothing changed: no repaint
  el.innerHTML = html;
  el.classList.remove("live-pop"); void el.offsetWidth; el.classList.add("live-pop");
  return true;
}

function setLiveText(el, text) {
  if (!el || el.textContent === String(text)) return;
  el.textContent = text;
  el.classList.remove("live-pop"); void el.offsetWidth; el.classList.add("live-pop");
}

/* Narrow dashboard patcher: KPI numbers and the attention list only. */
async function patchDashboardLive() {
  const grid = document.getElementById("kpi-grid");
  const att = document.getElementById("att-region");
  if (!grid && !att) return;                       // not on the dashboard — nothing to do
  const [d, corrections] = await Promise.all([
    GET("/api/dashboard"),
    GET("/api/corrections?status=pending").catch(() => []),
  ]);
  if (grid && patchable(grid)) {
    const k = d.kpis;
    const fmt = {billet_t: v => fmtN(v), energy_kwh: v => fmtN(v, 0),
                 o2_nm3: v => fmtN(v, 0), delay_min: v => fmtN(v, 0)};
    grid.querySelectorAll("[data-k]").forEach(b => {
      const key = b.dataset.k;
      if (k[key] === undefined) return;
      setLiveText(b, (fmt[key] || (v => v))(k[key]));
    });
  }
  if (att) {
    const at = d.attention, rows = [];
    at.pending_approvals.forEach(p => rows.push(`
      <tr><td><a class="link mono" href="#/heat/${p.heat_no}">${p.heat_no}</a></td>
        <td>Awaiting <b>${esc(p.awaiting)}</b> approval (L${p.level})</td>
        <td>${p.mine ? `<button class="btn small primary" onclick="quickApprove('${p.heat_no}',${p.level})">Approve</button>` : ""}</td></tr>`));
    corrections.forEach(c => rows.push(`
      <tr><td><a class="link mono" href="#/heat/${c.heat_no}">${c.heat_no}</a></td>
        <td>Correction pending — ${esc(c.stage)}</td><td></td></tr>`));
    at.open_delays.forEach(x => rows.push(`
      <tr><td><a class="link mono" href="#/heat/${x.heat_no}">${x.heat_no}</a></td>
        <td>Open delay — ${esc(x.reason || "running")}</td><td></td></tr>`));
    at.route_pending.forEach(hn => rows.push(`
      <tr><td><a class="link mono" href="#/heat/${hn}">${hn}</a></td>
        <td>Process route decision pending</td><td></td></tr>`));
    swapRegion(att, rows.length
      ? `<table class="t"><tbody>${rows.join("")}</tbody></table>`
      : `<div class="empty">Nothing pending — production is flowing.</div>`);
  }
}

/* Registration — each domain refreshes ONLY its own component. */
PULSE.on("notifications", () => pollNotifications());
PULSE.on("approvals", () => patchDashboardLive());
PULSE.on("production", () => patchDashboardLive());
PULSE.on("heats", async () => {
  await patchDashboardLive();
  if (document.getElementById("sg-grid")) refreshGantt(true);     // only the Gantt redraws
  const hv = location.hash.match(/^#\/heat\/([^/]+)/);
  if (hv && S.heat && patchable(document.getElementById("view"))) {
    try {
      const h = await GET(`/api/heats/${hv[1]}`);
      if (JSON.stringify(h.gates) !== JSON.stringify(S.heat.gates) ||
          h.status !== S.heat.status) { S.heat = h; renderHeat(h); }
    } catch {}
  }
});
PULSE.on("machines", () => { if (document.querySelector(".mb-grid")) loadMachines(true); });
PULSE.on("quality", () => patchDashboardLive());
PULSE.on("reports", () => { if (location.hash.startsWith("#/reports")) route(); });
PULSE.on("users", () => {
  const t = document.querySelector('[data-ad="Users"].active');
  if (t && !document.querySelector(".modal-back")) t.click();
});

/* ══ THE PROCESS ROUTE LAW — post-EAF decision ══ */
/* Re-render the open heat workspace — used by the live pulse (2837) and after
   a route decision. Previously called but never defined (ReferenceError). */
function renderHeat(h) { return viewHeatWorkspace(h.heat_no); }

/* Single audited path for recording the post-EAF route, shared by the banner
   buttons, the dedicated header button and the locked-tab shortcut. */
async function applyRefiningRoute(h, code) {
  const isZ = code === "ZPF";
  if (!(await confirmModal(`Send Heat ${h.heat_no} to ${code}?`,
    isZ ? "The <b>ZPF</b> module will unlock for this heat, followed by the Ladle Refining Furnace."
        : `This heat will go <b>directly to ${code}</b>, bypassing ZPF entirely. ZPF will never be shown as a required stage for this heat, and the LRF unit is pre-filled as ${code}.`,
    "Confirm route"))) return;
  try {
    const r = await POST(`/api/heats/${h.heat_no}/refining-route`, {route: code});
    toast(`Process route ${code} recorded`, "ok", 3000);
    UI.closeModal();
    S.heat = r; renderHeat(r);
  } catch (er) { toast(er.message, "err", 6000); }
}

function refiningRouteTile(h, readonly, preselect) {
  if (preselect && !readonly) return applyRefiningRoute(h, preselect);
  const OPTS = [
    ["ZPF", "ZPF \u2014 Zero Phosphorus Furnace", "De-phosphorisation before ladle refining. The ZPF module unlocks for this heat."],
    ["LRF-5", "LRF-5 \u2014 Ladle Refining Furnace 5", "This heat goes directly to LRF-5. ZPF is not part of this heat and is never shown as required."],
    ["LRF-6", "LRF-6 \u2014 Ladle Refining Furnace 6", "This heat goes directly to LRF-6. ZPF is not part of this heat and is never shown as required."],
  ];
  UI.modal(`
    <h3>\u27a1 Where shall this heat go next?</h3>
    <div class="sub">Heat ${esc(h.heat_no)} \u00b7 every heat follows its own route \u2014 two heats of the same grade may take different paths.
      ${h.route_refining ? `Current: <b>${esc(h.route_refining)}</b>. Re-selection is audited on the permanent timeline.` : "This decision is recorded on the permanent timeline."}</div>
    <div class="route-opts">
      ${OPTS.map(([code, title, desc]) => `
        <button class="route-opt ${h.route_refining === code ? "on" : ""}" data-rr="${code}" ${readonly ? "disabled" : ""}>
          <b>${esc(title)}</b><span>${esc(desc)}</span></button>`).join("")}
    </div>
    <div class="modal-foot"><button class="btn" onclick="UI.closeModal()">Close</button></div>`, m => {
    m.addEventListener("click", e => {
      const code = e.target.closest("[data-rr]")?.dataset.rr;
      if (!code || readonly) return;
      applyRefiningRoute(h, code);
    });
  });
}

/* ══ THE LOGOUT SAFETY LAW ══ */
function pendingDrafts() {
  const out = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith("sms2_draft_")) {
      try { if (Object.keys(JSON.parse(localStorage.getItem(k) || "{}")).length) out.push(k.replace("sms2_draft_", "").replace("_", " \u00b7 ")); }
      catch {}
    }
  }
  return out;
}

function confirmLogout() {
  const ls = S.lastSave;
  const drafts = pendingDrafts();
  UI.modal(`
    <div class="lo-wrap">
      <div class="lo-mark"><div class="melt-dot"></div></div>
      <h3 class="lo-title">Confirm logout?</h3>
      <div class="lo-sub">You will be logged out of <b>SMS-2 IDOS</b></div>

      <div class="logout-status">
        <div class="row spread"><span>Signed in as</span>
          <b>${esc(S.user.name)} \u00b7 ${esc(S.user.role)}</b></div>
        <div class="row spread"><span>Auto-save</span>
          <b class="${ls ? "ok-tx" : ""}">${ls
            ? `completed \u2713 \u00b7 ${esc(ls.label)} \u00b7 ${esc(ls.time)}`
            : "nothing edited this session"}</b></div>
      </div>

      ${drafts.length ? `
        <div class="guide glass lo-warn">
          <b>Unsaved changes detected</b>
          <ul>${drafts.map(d => `<li>${esc(d)}</li>`).join("")}</ul>
          <div class="small muted" style="margin-top:8px">These drafts remain on this
            workstation and are restored when you sign back in \u2014 nothing is lost.
            You may also cancel and submit them first.</div>
        </div>`
        : `<div class="lo-clear">All entries are saved to the plant database.</div>`}
    </div>
    <div class="modal-foot lo-foot">
      <button class="btn" data-lo="cancel">Cancel</button>
      <button class="btn danger" data-lo="go">Log out</button>
    </div>`, m => {
    m.addEventListener("click", e => {
      const a = e.target.closest("[data-lo]")?.dataset.lo;
      if (a === "cancel") UI.closeModal();
      if (a === "go") { UI.closeModal(); finishLogout(false); }
    });
  });
}

/* ══ PASSWORD MANAGEMENT LAW — available to every user ══ */
function changePasswordDialog() {
  UI.modal(`
    <h3>\ud83d\udd11 Change password</h3>
    <div class="sub">Your new password is private \u2014 SMS-2 stores it only as a cryptographic hash and never writes it to the credentials ledger.</div>
    <div class="fgrid">
      <label class="fld"><span>Current password *</span><input type="password" id="cp-cur" autocomplete="current-password"></label>
      <label class="fld"><span>New password *</span><input type="password" id="cp-new" autocomplete="new-password"></label>
      <label class="fld"><span>Confirm new password *</span><input type="password" id="cp-cnf" autocomplete="new-password"></label>
    </div>
    <div id="cp-msg" class="small muted">At least 8 characters, including a letter and a number.</div>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="cp-go" disabled>Change password</button>
    </div>`, m => {
    const gate = () => {
      const cur = $("#cp-cur", m).value, nw = $("#cp-new", m).value, cf = $("#cp-cnf", m).value;
      const okLen = nw.length >= 8 && /[A-Za-z]/.test(nw) && /[0-9]/.test(nw);
      const match = nw !== "" && nw === cf;
      $("#cp-go", m).disabled = !(cur && okLen && match);
      $("#cp-msg", m).textContent = !nw ? "At least 8 characters, including a letter and a number."
        : !okLen ? "New password needs at least 8 characters, including a letter and a number."
        : !match ? "The confirmation does not match the new password."
        : "Ready \u2014 your new password meets the plant policy.";
      $("#cp-msg", m).className = "small " + (okLen && match ? "ok-tx" : "muted");
    };
    gate(); m.addEventListener("input", gate);
    $("#cp-go", m).onclick = async () => {
      try {
        await POST("/api/auth/password", {current: $("#cp-cur", m).value, new: $("#cp-new", m).value});
        toast("Password changed \u2014 use it at your next sign-in", "ok", 4000);
        UI.closeModal();
      } catch (e) { toast(e.message, "err", 5500); }
    };
  });
}

/* ══ FORGOT PASSWORD LAW — request, never an automatic reset ══ */
function forgotPasswordDialog() {
  UI.modal(`
    <h3>\ud83d\udd12 Password recovery request</h3>
    <div class="sub">SMS-2 never resets a password automatically. Your request goes to the plant administrator for approval, and new credentials are issued to you in person.</div>
    <div class="fgrid">
      <label class="fld"><span>Employee ID *</span><input id="fp-id" placeholder="e.g. OP001" autocomplete="username"></label>
      <label class="fld"><span>Remarks (optional)</span><input id="fp-rm" placeholder="e.g. locked out at start of C shift"></label>
    </div>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="fp-go" disabled>Send request to administrator</button>
    </div>`, m => {
    const gate = () => { $("#fp-go", m).disabled = !$("#fp-id", m).value.trim(); };
    gate(); m.addEventListener("input", gate);
    $("#fp-go", m).onclick = async () => {
      try {
        const r = await POST("/api/auth/forgot-password",
          {employee_id: $("#fp-id", m).value.trim(), remarks: $("#fp-rm", m).value.trim()});
        UI.closeModal();
        UI.modal(`<h3>\u2713 Request sent</h3><div class="sub">${esc(r.message)}</div>
          <div class="modal-foot"><button class="btn primary" onclick="UI.closeModal()">Close</button></div>`);
      } catch (e) { toast(e.message, "err", 5500); }
    };
  });
}

/* ══ ADMIN APPROVAL LAW — password recovery centre ══ */
async function adminApprovals(body) {
  body.innerHTML = `<div class="row" style="gap:8px;margin-bottom:10px">
      ${["pending", "approved", "rejected"].map((s, i) => `<span class="sg-chip ${i === 0 ? "on" : ""}" data-pq="${s}">${s}</span>`).join("")}
      <button class="btn small" id="ap-cred" style="margin-left:auto">\ud83d\udd10 Credentials ledger</button>
    </div><div id="ap-list"><div class="empty">Loading\u2026</div></div>`;
  const load = async status => {
    const rows = await GET(`/api/admin/password-requests?status=${status}`);
    $("#ap-list", body).innerHTML = rows.length ? `<table class="t"><thead><tr>
        <th>Requested</th><th>Employee</th><th>Role</th><th>Account</th><th>Remarks</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td class="mono">${esc(r.requested_at.slice(5, 16))}</td>
        <td><b>${esc(r.employee_id)}</b><div class="small muted">${esc(r.user_name)}</div></td>
        <td>${esc(r.role)}</td><td><span class="tag ${r.account_status}">${esc(r.account_status)}</span></td>
        <td class="small">${esc(r.remarks || "\u2014")}</td>
        <td>${r.status === "pending" ? `<span class="tag warn">pending</span>`
            : `<span class="tag ${r.status === "approved" ? "done" : ""}">${esc(r.action || r.status)}</span>
               <div class="small muted">${esc(r.decided_by || "")}</div>`}</td>
        <td>${r.status === "pending" ? `<button class="btn small primary" data-pr="${r.id}" data-emp="${esc(r.employee_id)}">Decide</button>` : ""}</td>
      </tr>`).join("")}</tbody></table>`
      : `<div class="empty">No ${status} recovery requests.</div>`;
    $("#ap-list", body).onclick = e => {
      const b = e.target.closest("[data-pr]");
      if (b) decidePasswordRequest(+b.dataset.pr, b.dataset.emp, () => load(status));
    };
  };
  body.querySelectorAll("[data-pq]").forEach(c => c.onclick = () => {
    body.querySelectorAll("[data-pq]").forEach(x => x.classList.remove("on"));
    c.classList.add("on"); load(c.dataset.pq);
  });
  $("#ap-cred", body).onclick = credentialsLedgerDialog;
  load("pending");
}

function decidePasswordRequest(id, emp, done) {
  UI.modal(`
    <h3>Password recovery \u2014 ${esc(emp)}</h3>
    <div class="sub">Verify the requester's identity in person before issuing credentials (Industrial Security Law). Every outcome is audited.</div>
    <div class="fgrid">
      <label class="fld"><span>Decision *</span>
        <select id="pd-act">
          <option value="approve_reset">Approve \u2014 issue a new password</option>
          <option value="reject">Reject the request</option>
          <option value="disable_account">Disable this account</option>
        </select></label>
      <label class="fld"><span>Temporary password (blank = system-generated)</span>
        <input id="pd-pw" placeholder="leave blank for a secure random password"></label>
      <label class="fld"><span>Remarks</span><input id="pd-rm" placeholder="e.g. identity confirmed at main gate"></label>
      <label class="fld"><span>Your password (re-authentication) *</span><input type="password" id="pd-auth"></label>
    </div>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="pd-go" disabled>Record decision</button>
    </div>`, m => {
    const gate = () => { $("#pd-go", m).disabled = !$("#pd-auth", m).value; };
    gate(); m.addEventListener("input", gate);
    $("#pd-act", m).onchange = () => {
      $("#pd-pw", m).closest(".fld").style.display =
        $("#pd-act", m).value === "approve_reset" ? "" : "none";
    };
    $("#pd-go", m).onclick = async () => {
      try {
        const r = await POST(`/api/admin/password-requests/${id}/decide`, {
          action: $("#pd-act", m).value, remarks: $("#pd-rm", m).value.trim(),
          temp_password: $("#pd-pw", m).value.trim() || null,
          reauth_password: $("#pd-auth", m).value});
        UI.closeModal();
        if (r.temporary_password) {
          UI.modal(`<h3>\u2713 New credentials issued</h3>
            <div class="sub">Hand these to <b>${esc(emp)}</b> in person and ask them to change the password after signing in. This password is also recorded in the credentials ledger.</div>
            <div class="cred-box mono">${esc(r.temporary_password)}</div>
            <div class="modal-foot">
              <button class="btn" id="cp-copy">Copy</button>
              <button class="btn primary" onclick="UI.closeModal()">Done</button></div>`, mm => {
            $("#cp-copy", mm).onclick = () => {
              navigator.clipboard?.writeText(r.temporary_password);
              toast("Copied to clipboard", "ok", 1800);
            };
          });
        } else toast(`Request ${r.status}`, "ok", 3000);
        done && done(); pollNotifications();
      } catch (e) { toast(e.message, "err", 6000); }
    };
  });
}

function credentialsLedgerDialog() {
  UI.modal(`
    <h3>\ud83d\udd10 Credentials ledger</h3>
    <div class="sub">Administrator-only. Re-authenticate to view <code>credentials.txt</code>. Only issued credentials appear \u2014 user-chosen passwords are never written to disk.</div>
    <label class="fld"><span>Your password *</span><input type="password" id="cl-auth"></label>
    <div class="modal-foot">
      <button class="btn" onclick="UI.closeModal()">Cancel</button>
      <button class="btn primary" id="cl-go">View ledger</button>
    </div>`, m => {
    $("#cl-go", m).onclick = async () => {
      try {
        const r = await POST("/api/admin/credentials-file", {reauth_password: $("#cl-auth", m).value});
        UI.closeModal();
        UI.modal(`<h3>\ud83d\udd10 credentials.txt</h3>
          <div class="sub mono small">${esc(r.path)}</div>
          <pre class="cred-file">${esc(r.content)}</pre>
          <div class="modal-foot"><button class="btn primary" onclick="UI.closeModal()">Close</button></div>`);
      } catch (e) { toast(e.message, "err", 5500); }
    };
  });
}

/* ═══ NOTIFICATION PREFERENCE ENGINE ═══ */
const OPTIONAL_KINDS = [["production", "Production"], ["approval", "Approvals"],
  ["ai", "AI intelligence"], ["report", "Reports"], ["admin", "Administrative"],
  ["shift", "Shift"], ["sync", "Synchronization"], ["autosave", "Auto-save"]];

function showNotifPrefs(firstLogin) {
  const p = S.user.notification_prefs || {};
  UI.modal(`
    <h3>🔔 Notification preferences${firstLogin ? " — welcome!" : ""}</h3>
    <div class="sub">${firstLogin
      ? "Choose which optional notifications you'd like. You can change this anytime from the bell panel."
      : "Optional notifications honour your choices below."}
      <b>Emergency, security, system and production-delay alerts can never be disabled</b> — plant safety first.</div>
    <div class="fgrid">
      ${OPTIONAL_KINDS.map(([k, label]) => `
        <label class="fld" style="flex-direction:row;align-items:center;gap:9px">
          <input type="checkbox" class="np-k" value="${k}" ${p[k] === false ? "" : "checked"}>
          <span style="text-transform:none;letter-spacing:0;font-size:13px">${label} notifications</span></label>`).join("")}
    </div>
    <h3 style="margin-top:12px">Channels</h3>
    <div class="fgrid">
      <label class="fld" style="flex-direction:row;align-items:center;gap:9px">
        <input type="checkbox" id="np-browser" ${p.browser ? "checked" : ""}>
        <span style="text-transform:none;letter-spacing:0;font-size:13px">Browser / desktop / mobile pop-ups (high-priority only)</span></label>
      <label class="fld" style="flex-direction:row;align-items:center;gap:9px">
        <input type="checkbox" id="np-email" ${p.email ? "checked" : ""}>
        <span style="text-transform:none;letter-spacing:0;font-size:13px">Email for critical alerts (requires plant SMTP in Admin → Settings)</span></label>
      <label class="fld"><span>Your email address</span>
        <input id="np-addr" type="email" value="${esc(S.user.email || "")}" placeholder="name@plant.local"></label>
    </div>
    <h3 style="margin-top:12px">Notification sound</h3>
    <div class="sub" style="margin-bottom:8px">The approval tone is reserved for events needing a human decision. It never plays for auto-save, live updates, the refresh cycle or report generation.</div>
    <div class="fgrid">
      <label class="fld"><span>When to play</span>
        <select id="np-sound">
          ${[["important", "Approvals + critical + important production alerts"],
             ["approval", "Approval requests only"],
             ["critical", "Critical alerts only"],
             ["silent", "Silent mode \u2014 never play a sound"]].map(([v, t]) =>
            `<option value="${v}" ${(p.sound_mode || "important") === v ? "selected" : ""}>${t}</option>`).join("")}
        </select></label>
      <label class="fld"><span>Volume <span class="unit" id="np-volv">${Math.round((p.sound_volume === undefined ? 0.7 : p.sound_volume) * 100)}%</span></span>
        <input type="range" id="np-vol" min="0" max="100" value="${Math.round((p.sound_volume === undefined ? 0.7 : p.sound_volume) * 100)}"></label>
    </div>
    <div class="row" style="gap:8px;margin:2px 0 4px">
      <button class="btn small" id="np-test">\u25b6 Test sound</button>
      <button class="btn small" onclick="UI.closeModal();changePasswordDialog()">\ud83d\udd11 Change password</button>
    </div>
    <div class="fgrid" style="margin-top:6px">
      ${["Emergency", "Security", "System / database", "Production delays & heat failures"].map(x => `
        <label class="fld" style="flex-direction:row;align-items:center;gap:9px;opacity:.65">
          <input type="checkbox" checked disabled><span style="text-transform:none;letter-spacing:0;font-size:13px">${x} — always on</span></label>`).join("")}
    </div>
    <div class="modal-foot">
      ${firstLogin ? "" : `<button class="btn" onclick="UI.closeModal()">Cancel</button>`}
      <button class="btn primary" id="np-save">Save preferences</button>
    </div>`, m => {
    $("#np-vol", m).addEventListener("input", () => {
      $("#np-volv", m).textContent = $("#np-vol", m).value + "%";
      S.user.notification_prefs = {...(S.user.notification_prefs || {}),
        sound_volume: (+$("#np-vol", m).value) / 100};
    });
    $("#np-test", m).onclick = () => {
      S.user.notification_prefs = {...(S.user.notification_prefs || {}),
        sound_mode: $("#np-sound", m).value, sound_volume: (+$("#np-vol", m).value) / 100};
      try { SFX.test(); } catch (e) { toast("Sound system unavailable", "err", 2500); }
    };
    $("#np-save", m).onclick = async () => {
      const prefs = {};
      $$(".np-k", m).forEach(i => prefs[i.value] = i.checked);
      prefs.browser = $("#np-browser", m).checked;
      prefs.email = $("#np-email", m).checked;
      prefs.email_address = $("#np-addr", m).value.trim();
      prefs.sound_mode = $("#np-sound", m).value;
      prefs.sound_volume = (+$("#np-vol", m).value) / 100;
      try {
        const r = await POST("/api/auth/notification-prefs", {prefs});
        S.user.notification_prefs = r.prefs;
        S.user.email = prefs.email_address;
        if (prefs.browser && "Notification" in window && Notification.permission === "default") {
          Notification.requestPermission();
        }
        toast("Notification preferences saved", "ok");
        UI.closeModal();
      } catch (e) { toast(e.message, "err", 5000); }
    };
  });
}

function browserNotify(list) {
  const p = S.user.notification_prefs || {};
  if (!p.browser || !("Notification" in window) || Notification.permission !== "granted") return;
  const last = +(localStorage.getItem("sms2_last_notif") || 0);
  let maxId = last;
  list.filter(n => !n.read && n.id > last && (n.priority === "high" || n.priority === "critical"))
      .slice(0, 3)
      .forEach(n => {
        try { new Notification("SMS-2 · " + n.title, {body: n.body || "", tag: "sms2-" + n.id}); } catch {}
        maxId = Math.max(maxId, n.id);
      });
  list.forEach(n => maxId = Math.max(maxId, n.id));
  localStorage.setItem("sms2_last_notif", String(maxId));
}

/* Debug expose — last, after every declaration, and guarded: its failure may
   cost the console shortcuts, never the plant. */
try { Object.assign(window, {UI, quickApprove, loadHandover, openCreateHeat, abortHeat,
  unlockHeat, showTimeline, download, route, requestCorrection, showVersions,
  decideCorrection, loadMachines, showNotifPrefs, syncAlert, loadNotifs, ND,
  SFX, changePasswordDialog, forgotPasswordDialog, credentialsLedgerDialog,
  refiningRouteTile, confirmLogout, finishLogout, PULSE, pulseNow,
  patchDashboardLive, approvalAlert}); } catch (e) { console.error("SMS-2 expose failed (non-fatal):", e); }

boot();
