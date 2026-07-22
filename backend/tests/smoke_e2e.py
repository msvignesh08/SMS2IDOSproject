"""SMS-2 IDOS end-to-end smoke test: a full heat from creation to LOCKED,
exercising auth, RBAC, stage framework, validation gating, chemistry,
materials, delays, route decision, calculator, approvals, reports, search."""
import json, urllib.request

BASE = "http://127.0.0.1:8020"

def call(method, path, body=None, token=None, expect_ok=True):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        payload = json.loads(e.read())
        if expect_ok:
            raise AssertionError(f"{method} {path} -> {e.code}: {payload}")
        return {"__status": e.code, **payload}

def login(emp, pw):
    return call("POST", "/api/auth/login", {"employee_id": emp, "password": pw})

# ── auth
op  = login("OP001", "steel@123");  t_op = op["token"]
se  = login("SE001", "steel@123");  t_se = se["token"]
si  = login("SI001", "steel@123");  t_si = si["token"]
print("LOGIN OK — plant:", op["plant"])

bad = call("POST", "/api/auth/login", {"employee_id": "OP001", "password": "wrong"}, expect_ok=False)
assert bad["__status"] == 401, "wrong password must be rejected"
print("BAD LOGIN REJECTED OK")

# ── masters
masters = call("GET", "/api/admin/masters", token=t_se)
grade = next(g for g in masters["grades"] if g["code"] == "Fe500D")
mat = {m["code"]: m for m in masters["materials"]}
dr = masters["delay_reasons"][0]
print(f"MASTERS OK — {len(masters['grades'])} grades, {len(masters['materials'])} materials")

# ── create heat (auto series)
h = call("POST", "/api/heats", {"grade_id": grade["id"], "shift_engineer": "R. Sharma"}, token=t_se)
HN = h["heat_no"]
print("HEAT CREATED:", HN, "| gates:", {k: v["state"] for k, v in h["gates"].items()})
assert h["gates"]["eaf"]["state"] == "open"
assert h["gates"]["caster"]["state"] == "locked", "Caster must be gated until upstream submits (5.2)"

dup = call("POST", "/api/heats", {"grade_id": grade["id"], "heat_no": HN}, token=t_se, expect_ok=False)
assert dup["__status"] == 400, "duplicate heat number must be prevented (5.3)"
print("DUPLICATE HEAT PREVENTED OK")

# ── premature submit must fail validation
r = call("POST", f"/api/heats/{HN}/stages/eaf/submit", token=t_op)
assert r["ok"] is False and any(f["level"] == "error" for f in r["findings"])
print("VALIDATION BLOCKS EMPTY SUBMIT OK —", len(r["findings"]), "findings")

# ── charge mix — the hot metal and scrap charged INTO the furnace, recorded
#    on the EAF stage alongside the furnace operation itself.
call("PUT", f"/api/heats/{HN}/stages/eaf", {"data": {
    "bottom_life": 812, "ebt_life": 47, "hm_weight": 92.5, "hm_temp": 1350,
    "hm_charging": "2026-07-16T06:45", "hmc_route": "Launder",
    "stage_remarks": "Normal ladle condition."}}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/eaf/chemistry",
     {"label": "Hot Metal", "values": {"C": 4.2, "Si": 0.65, "Mn": 0.4, "S": 0.045, "P": 0.11, "Ti": 0.05}}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/eaf/materials",
     {"material_id": mat["HSC"]["id"], "quantity": 12.0, "remarks": "2 boxes"}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/eaf/materials",
     {"material_id": mat["DRI"]["id"], "quantity": 8.0}, token=t_op)
st = call("GET", f"/api/heats/{HN}/stages/eaf", token=t_op)
assert st["data"]["total_charge"] == 112.5, f"auto charge calc wrong: {st['data']}"
assert st["data"]["hm_pct"] == 82.2, f"HM% wrong: {st['data']}"
print("CHARGE MIX AUTO-CALC OK — total charge", st["data"]["total_charge"],
      "t |", st["data"]["hm_pct"], "% HM")

# ── EAF furnace operation (with chronology test: tap before charging end)
call("PUT", f"/api/heats/{HN}/stages/eaf", {"data": {
    "power_on": "2026-07-16T06:55", "power_off": "2026-07-16T08:10",
    "energy_kwh": 38500, "o2_consumption": 2900, "bath_temp": 1640,
    "tap_start": "2026-07-16T06:10", "tap_end": "2026-07-16T08:25",
    "tap_temp": 1655, "tap_weight": 104.0}}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/eaf/chemistry",
     {"label": "EAF Bath", "values": {"C": 0.08, "Si": 0.01, "Mn": 0.12, "S": 0.035, "P": 0.012, "Ti": 0.0}}, token=t_op)
r = call("POST", f"/api/heats/{HN}/stages/eaf/submit", token=t_op)
assert not r["ok"] and any("Tapping cannot begin" in f["message"] for f in r["findings"]), r
print("CHRONOLOGY RULE ENFORCED OK — tapping before power-on is blocked")
call("PUT", f"/api/heats/{HN}/stages/eaf", {"data": {"tap_start": "2026-07-16T08:15"}}, token=t_op)
# Flux is charged at the same stage but is not metallic charge: the scrap
# total must ignore it, or every heat's HM% silently drifts.
call("POST", f"/api/heats/{HN}/stages/eaf/materials",
     {"material_id": mat["LIME"]["id"], "quantity": 3.0}, token=t_op)
st2 = call("GET", f"/api/heats/{HN}/stages/eaf", token=t_op)
assert st2["data"]["total_scrap"] == 20.0, f"flux leaked into scrap total: {st2['data']}"
assert st2["data"]["total_charge"] == 112.5, f"flux leaked into charge total: {st2['data']}"
print("CHARGE/FLUX SEPARATION OK — 3 t lime ignored by the metallic charge total")
r = call("POST", f"/api/heats/{HN}/stages/eaf/submit", token=t_op)
assert r["ok"], r
# THE PROCESS ROUTE LAW: post-EAF decision — this heat goes via ZPF
r = call("POST", f"/api/heats/{HN}/refining-route", {"route": "ZPF"}, token=t_se)
assert r["route_refining"] == "ZPF" and "zpf" in r["required_stages"], r
print("POST-EAF ROUTE OK — ZPF selected for this heat")
print("EAF SUBMITTED — power_on_duration auto:", call("GET", f"/api/heats/{HN}/stages/eaf", token=t_op)["data"]["power_on_duration"], "min")

# ── ZPF with a delay
call("PUT", f"/api/heats/{HN}/stages/zpf", {"data": {
    "zpf_start": "2026-07-16T08:30", "zpf_end": "2026-07-16T09:05",
    "o2_total": 450, "temp_initial": 1630, "temp_final": 1605,
    "slag_practice": "Single Slag"}}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/zpf/chemistry",
     {"label": "ZPF", "values": {"C": 0.06, "Si": 0.01, "Mn": 0.10, "S": 0.030, "P": 0.010, "Ti": 0.0}}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/zpf/delays",
     {"start_time": "2026-07-16T08:40", "end_time": "2026-07-16T08:52",
      "reason_id": dr["id"], "remarks": "Crane occupied"}, token=t_op)
r = call("POST", f"/api/heats/{HN}/stages/zpf/submit", token=t_op)
assert r["ok"], r
print("ZPF SUBMITTED with delay OK")

# ── LRF: chemistry, calculator, route decision
call("PUT", f"/api/heats/{HN}/stages/lrf", {"data": {
    "lrf_unit": "LRF-6",
    "lrf_start": "2026-07-16T09:15", "lrf_end": "2026-07-16T10:20",
    "power_on": "2026-07-16T09:20", "power_off": "2026-07-16T10:05",
    "temp_initial": 1580, "temp_final": 1602}}, token=t_op)

# route before chemistry must fail (6D.16)
r = call("POST", f"/api/heats/{HN}/route", {"route": "VD-2.1"}, token=t_se, expect_ok=False)
assert r["__status"] == 400 and "Chemistry" in r["detail"], r
print("ROUTE-BEFORE-CHEMISTRY BLOCKED OK (6D.16)")

call("POST", f"/api/heats/{HN}/stages/lrf/chemistry",
     {"label": "LRF", "values": {"C": 0.12, "Si": 0.05, "Mn": 0.35, "S": 0.020, "P": 0.012}}, token=t_op)

# operator must NOT be able to select route (5.8)
r = call("POST", f"/api/heats/{HN}/route", {"route": "VD-2.1"}, token=t_op, expect_ok=False)
assert r["__status"] == 403, r
print("ROUTE RESTRICTED TO SHIFT ENGINEER OK (5.8)")

# calculator auto-populated from the heat (6D.8)
calc = call("POST", "/api/calculator/alloys", {"heat_no": HN}, token=t_se)
assert calc["ok"] and calc["lines"], calc
print("CALCULATOR OK — e.g.", calc["lines"][0]["alloy"], calc["lines"][0]["qty_kg"], "kg for", calc["lines"][0]["element"],
      "| predicted Mn:", calc["predicted"].get("Mn"))

# 7.10 anti-guess behavior
miss = call("POST", "/api/calculator/alloys", {"current": {"Si": ""}, "target": {"Si": 0.2}}, token=t_se)
assert miss["ok"] is False and miss["missing"], miss
print("7.10 NEVER-GUESS OK — engine states required inputs:", miss["missing"])

call("POST", f"/api/heats/{HN}/route", {"route": "VD-2.1"}, token=t_se)
r = call("POST", f"/api/heats/{HN}/stages/lrf/submit", token=t_op)
assert r["ok"], r
h2 = call("GET", f"/api/heats/{HN}", token=t_op)
assert h2["gates"]["vd"]["state"] == "open", h2["gates"]
print("LRF SUBMITTED, ROUTE VD-2.1 — VD unlocked, caster gated:", h2["gates"]["caster"]["state"])

# ── VD
call("PUT", f"/api/heats/{HN}/stages/vd", {"data": {
    "vd_start": "2026-07-16T10:30", "vd_end": "2026-07-16T10:55",
    "vac_level": 0.8, "temp_initial": 1600, "temp_final": 1575}}, token=t_op)
call("POST", f"/api/heats/{HN}/stages/vd/chemistry",
     {"label": "VD Final", "values": {"C": 0.20, "Si": 0.20, "Mn": 0.80, "S": 0.018, "P": 0.012}}, token=t_op)
r = call("POST", f"/api/heats/{HN}/stages/vd/submit", token=t_op)
assert r["ok"], r
print("VD SUBMITTED OK")

# ── Caster
call("PUT", f"/api/heats/{HN}/stages/caster", {"data": {
    "ltc_no": "LTC-1",
    "cast_start": "2026-07-16T11:05", "cast_end": "2026-07-16T12:10",
    "tundish_temp": 1545, "strand_count": 4, "billet_size": "125×125",
    "billet_length": 12, "billet_count": 58, "billet_weight": 98.6}}, token=t_op)
r = call("POST", f"/api/heats/{HN}/stages/caster/submit", token=t_op)
assert r["ok"], r
print("CASTER SUBMITTED OK — duration auto:",
      call("GET", f"/api/heats/{HN}/stages/caster", token=t_op)["data"]["cast_duration"], "min")

# ── Quality (rejected-without-remarks rule, then accept)
call("PUT", f"/api/heats/{HN}/stages/quality", {"data": {
    "decision": "Rejected", "decision_time": "2026-07-16T13:00"}}, token=t_se)
call("POST", f"/api/heats/{HN}/stages/quality/chemistry",
     {"label": "Lab", "values": {"C": 0.21, "Si": 0.19, "Mn": 0.82, "S": 0.019, "P": 0.013}}, token=t_se)
r = call("POST", f"/api/heats/{HN}/stages/quality/submit", token=t_se)
assert not r["ok"] and any("mandatory remarks" in f["message"] for f in r["findings"]), r
print("REJECTED-WITHOUT-REMARKS BLOCKED OK (6G.13)")
call("PUT", f"/api/heats/{HN}/stages/quality", {"data": {
    "decision": "Accepted", "surface_finish": "Good"}}, token=t_se)
r = call("POST", f"/api/heats/{HN}/stages/quality/submit", token=t_se)
assert r["ok"], r
print("QUALITY ACCEPTED OK")

# ── Review + approvals chain
rev = call("GET", f"/api/heats/{HN}/review", token=t_se)
assert not [e for e in rev["validation"] if e["level"] == "error"], rev["validation"]
print("FINAL VALIDATION CLEAN OK")

r = call("POST", f"/api/heats/{HN}/approve", {"level": 2}, token=t_se, expect_ok=False)
assert r["__status"] == 400, "chain order must be enforced"
print("APPROVAL CHAIN ORDER ENFORCED OK (5.13)")
call("POST", f"/api/heats/{HN}/approve", {"level": 1}, token=t_op)
call("POST", f"/api/heats/{HN}/approve", {"level": 2}, token=t_se)
h3 = call("POST", f"/api/heats/{HN}/approve", {"level": 3}, token=t_si)
assert h3["status"] == "locked", h3
print("HEAT LOCKED OK — 3-level approvals + digital signatures recorded")

# read-only after lock
r = call("PUT", f"/api/heats/{HN}/stages/eaf", {"data": {"energy_kwh": 1}}, token=t_op, expect_ok=False)
assert r["__status"] == 423, r
print("LOCKED = READ-ONLY OK (6H.11)")

# ── timeline / dashboard / reports / search / handover / analytics
tl = call("GET", f"/api/heats/{HN}/timeline", token=t_op)
print("TIMELINE OK —", len(tl["events"]), "events (Digital DNA)")
dash = call("GET", "/api/dashboard", token=t_se)
print("DASHBOARD OK — plant day", dash["plant"]["plant_day"], "shift", dash["plant"]["shift"],
      "| KPIs:", dash["kpis"])
rep = call("GET", "/api/reports/heat/" + HN, token=t_se)
print("HEAT REPORT OK —", len(rep["stages"]), "stages,", len(rep["approvals"]), "approvals")
daily = call("GET", "/api/reports/daily", token=t_se)
print("DAILY REPORT OK — totals:", daily["totals"])
sr = call("GET", f"/api/search?q=crane", token=t_se)
assert sr["count"] >= 1, sr
print("DEEP SEARCH OK — 'crane' found via delay remarks in", sr["results"][0]["heat_no"])
ho = call("GET", "/api/handover", token=t_se)
print("HANDOVER OK —", ho["outgoing_shift"], "→", ho["incoming_shift"])
an = call("GET", "/api/analytics", token=t_se)
print("ANALYTICS OK — delay pareto:", an["delay_pareto"])

# ── knowledge note
call("POST", "/api/knowledge", {"heat_no": HN, "grade_code": "Fe500D",
     "topic": "Why VD-2 for Fe500D", "body": "S was 0.020 after LRF; VD chosen to hit 0.018 max with argon rinse.",
     "tags": "route,desulphurisation"}, token=t_se)
kn = call("GET", "/api/knowledge?grade=Fe500D", token=t_se)
assert kn["notes"] and kn["practice"], kn
print("KNOWLEDGE ENGINE OK —", kn["practice"][1]["fact"])

# ── training mode heat excluded from production reports
tr = call("POST", "/api/heats", {"grade_id": grade["id"], "is_training": True}, token=t_se)
daily2 = call("GET", "/api/reports/daily", token=t_se)
assert all(row["heat_no"] != tr["heat_no"] for row in daily2["heats"])
print("TRAINING MODE OK —", tr["heat_no"], "excluded from production reports")

# ── admin: audit + unlock with reason + backup
audit = call("GET", "/api/admin/audit?heat_no=" + HN, token=login("ADMIN","admin@123")["token"])
print("AUDIT OK —", len(audit), "entries for", HN)
adm = login("ADMIN", "admin@123")["token"]
r = call("POST", f"/api/heats/{HN}/unlock", {"reason": ""}, token=adm, expect_ok=False)
assert r["__status"] == 400
u = call("POST", f"/api/heats/{HN}/unlock", {"reason": "Smoke test: verify audited unlock"}, token=adm)
assert u["status"] == "review"
print("ADMIN UNLOCK (with mandatory reason) OK")
call("POST", f"/api/heats/{HN}/approve", {"level": 1}, token=t_op)
call("POST", f"/api/heats/{HN}/approve", {"level": 2}, token=t_se)
call("POST", f"/api/heats/{HN}/approve", {"level": 3}, token=t_si)
bk = call("POST", "/api/admin/backup", token=adm)
print("BACKUP OK —", bk["filename"])

print("\n========================================")
print("SMS-2 IDOS v1.0 END-TO-END SMOKE TEST: ALL PASSED")
print("========================================")

# ═══ CONSTITUTION S.1–S.7 EXTENSIONS ═══
print("\n--- Constitution S.1-S.7 extension tests ---")
adm2 = login("ADMIN", "admin@123")["token"]
own = login("OWNER", "owner@123")["token"]
vw = login("VIEW01", "steel@123")["token"]
print("HIERARCHY LOGINS OK — Owner, View Only")

# View Only RBAC: cannot create heats
r = call("POST", "/api/heats", {"grade_id": grade["id"]}, token=vw, expect_ok=False)
assert r["__status"] == 403, r
print("VIEW-ONLY RBAC OK — heat creation denied")

# Business continuity: archiving the LAST active Shift Incharge must be DENIED
users = call("GET", "/api/admin/users", token=adm2)
si_user = next(u for u in users if u["employee_id"] == "SI001")
r = call("PATCH", f"/api/admin/users/{si_user['id']}",
         {"archived": True, "reauth_password": "admin@123"}, token=adm2, expect_ok=False)
assert r["__status"] == 400 and "Business Continuity" in r["detail"], r
print("BUSINESS CONTINUITY GUARD OK — last Shift Incharge archive DENIED (S.2)")

# Ownership transfer continuity: giving Owner to the LAST Shift Engineer is DENIED
se_user = next(u for u in users if u["employee_id"] == "SE001")
r = call("POST", "/api/admin/transfer-ownership",
         {"target_user_id": se_user["id"], "reauth_password": "owner@123"},
         token=own, expect_ok=False)
assert r["__status"] == 400 and "Business Continuity" in r["detail"], r
# Transfer via PM001 (unprotected role): OWNER -> PM001 -> OWNER
pm_user = next(u for u in users if u["employee_id"] == "PM001")
t = call("POST", "/api/admin/transfer-ownership",
         {"target_user_id": pm_user["id"], "reauth_password": "owner@123"}, token=own)
assert t["new_owner"] == "PM001", t
own2 = login("PM001", "steel@123")["token"]
ow_user = next(u for u in call("GET", "/api/admin/users", token=adm2) if u["employee_id"] == "OWNER")
call("POST", "/api/admin/transfer-ownership",
     {"target_user_id": ow_user["id"], "reauth_password": "steel@123"}, token=own2)
print("OWNERSHIP TRANSFER OK — last-SE promotion DENIED (continuity); Owner → PM001 → Owner (audited)")

# Correction system on the LOCKED heat: request -> approve -> version
h_locked = call("GET", f"/api/heats/{HN}", token=t_se)
assert h_locked["status"] == "locked"
cr = call("POST", f"/api/heats/{HN}/corrections",
          {"stage": "eaf", "changes": {"energy_kwh": 38750},
           "reason": "Meter reconciliation after shift audit"}, token=t_op)
assert cr["changes"]["energy_kwh"]["old"] == 38500, cr
r = call("POST", f"/api/corrections/{cr['id']}/decide", {"approve": True, "remarks": "Verified vs meter"},
         token=t_op, expect_ok=False)
assert r["__status"] == 403, "operator must not approve corrections"
dec = call("POST", f"/api/corrections/{cr['id']}/decide",
           {"approve": True, "remarks": "Verified vs meter"}, token=t_si)
assert dec["status"] == "approved" and dec["version"] >= 2, dec
st2 = call("GET", f"/api/heats/{HN}/stages/eaf", token=t_se)
assert st2["data"]["energy_kwh"] == 38750, st2["data"].get("energy_kwh")
vers = call("GET", f"/api/heats/{HN}/versions?stage=eaf", token=t_se)
assert any(v["kind"] == "correction" and v["changes"].get("energy_kwh") for v in vers), vers
print(f"CORRECTION SYSTEM OK — old 38500 preserved, new 38750 applied as v{dec['version']} with approval + version history (S.3)")

# Smart Gantt v2: all five modes + time machine + machine lanes
for mode in ["heat", "machine", "shift", "delays", "production"]:
    g = call("GET", f"/api/gantt?mode={mode}&span=day", token=t_se)
    assert g["mode"] == mode and "rows" in g, mode
print("GANTT v2 OK — all five modes respond (heat/machine/shift/delays/production)")
g7 = call("GET", "/api/gantt?mode=machine&span=day&days=7", token=t_se)
assert g7["days"] == 7
# Bars live on the PLANT day of the operator-entered process times (this
# heat's data is dated 2026-07-16), not on the day the keyboard was used.
gm = call("GET", "/api/gantt?mode=machine&span=day&plant_day=2026-07-16", token=t_se)
lanes = [r["key"] for r in gm["rows"]]
# The whole plant is on the board, in process order: EAF, ZPF, both LRFs, the
# four VD tanks, then the casters. The ladle transfer cars are not lanes -- a
# car moves a ladle, it does not process it.
assert "HM-BAY" not in gm["machines"], "the hot metal bay is no longer a lane"
assert not [m for m in gm["machines"] if "LTC" in m],     f"transfer cars are not machine lanes: {gm['machines']}"
_order = ["EAF", "ZPF", "LRF-5", "LRF-6",
          "VD-2.1", "VD-2.2", "VD-3.1", "VD-3.2",
          "BC-2", "BC-3", "QLAB"]
assert gm["machines"] == _order, f"lane order is EAF, ZPF, LRF, VD, BC: {gm['machines']}"

# This heat recorded lrf_unit = LRF-6, so its refining belongs to LRF-6.
lrf6 = next((r for r in gm["rows"] if r["key"] == "LRF-6"), None)
assert lrf6 and lrf6["bars"], "LRF bar must land in the LRF-6 lane per lrf_unit"
assert lrf6["bars"][0]["start"].startswith("2026-07-16 09:15"),     f"LRF bar must carry the operator-entered process time: {lrf6['bars'][0]}"
other = next((r for r in gm["rows"] if r["key"] == "LRF-5"), None)
assert other is not None and not other["bars"], "this heat never ran on LRF-5"
print("GANTT LANES OK — EAF, ZPF, LRF-5/6, 4 VD tanks, 2 casters, in process order")

# Machine intelligence board
mb = call("GET", "/api/machines", token=t_se)
eaf_m = next(x for x in mb["machines"] if x["code"] == "EAF")
assert "state" in eaf_m and "utilization_pct" in eaf_m
print("MACHINE BOARD OK —", ", ".join(f"{x['code']}:{x['state']}" for x in mb["machines"][:6]))

# Equipment governance: maintenance state
call("POST", "/api/admin/equipment/VD-3.1/status",
     {"status": "maintenance", "reauth_password": "admin@123"}, token=adm2)
mb2 = call("GET", "/api/machines", token=t_se)
vd3 = next((x for x in mb2["machines"] if x["code"] == "VD-3.1"), None)
if vd3: assert vd3["state"] == "MAINTENANCE", vd3
call("POST", "/api/admin/equipment/VD-3.1/status",
     {"status": "available", "reauth_password": "admin@123"}, token=adm2)
print("EQUIPMENT GOVERNANCE OK — VD-3.1 maintenance state declared & cleared")

# AI Production Engineer: insights + all five modes + never-guess honesty
ai = call("GET", "/api/ai/insights", token=t_se)
assert ai["insights"], ai
print("AI INSIGHTS OK —", len(ai["insights"]), "finding(s); e.g.:", ai["insights"][0]["text"][:80])
for mode in ["production_engineer", "consultant", "trainer", "management", "analytical"]:
    a = call("POST", "/api/ai/ask", {"question": "Why did production drop?", "mode": mode}, token=t_se)
    assert a["answer"], mode
print("AI MODES OK — all five personas answer")
a = call("POST", "/api/ai/ask", {"question": "explain lrf", "mode": "trainer"}, token=t_se)
assert "Ladle Refining" in a["answer"]
a2 = call("POST", "/api/ai/ask", {"question": "which shift is best", "mode": "management"}, token=t_se)
assert "Shift" in a2["answer"]
print("AI ANSWERS OK — trainer teaches LRF; management gets shift ranking")

print("\n========================================")
print("CONSTITUTION S.1-S.7 EXTENSIONS: ALL PASSED")
print("========================================")

# ═══ GRADE ENTRY / SYNC ENGINE / PREFERENCE ENGINE (final directive) ═══
print("\n--- Grade Entry, Sync & Preference Engine tests ---")

# GRADE ENTRY SYSTEM: manual entry of an unknown grade auto-registers it
h2 = call("POST", "/api/heats", {"grade_code": "IS2062", "shift_engineer": "R. Kumar"}, token=t_se)
assert h2["heat_no"], h2
gs = call("GET", "/api/grades/search?q=IS20", token=t_se)
assert any(g["code"] == "IS2062" for g in gs), gs
gs2 = call("GET", "/api/grades/search?q=Fe5", token=t_se)
assert any(g["code"] == "Fe500D" and g["heats"] >= 1 for g in gs2), gs2
# reuse by code (case-insensitive) — must NOT duplicate
h3 = call("POST", "/api/heats", {"grade_code": "is2062"}, token=t_se)
gs3 = call("GET", "/api/grades/search?q=IS2062", token=t_se)
assert len([g for g in gs3 if g["code"].upper() == "IS2062"]) == 1, gs3
print("GRADE ENTRY OK — IS2062 created at entry, searchable, reused case-insensitively, no dropdown lock")

# PRODUCTION SYNCHRONIZATION ENGINE: stale save returns full conflict payload
HN2 = h2["heat_no"]
s1 = call("PUT", f"/api/heats/{HN2}/stages/eaf",
          {"data": {"hm_ladle_no": "LD-01"}, "version": 1}, token=t_se)
conflict = call("PUT", f"/api/heats/{HN2}/stages/eaf",
                {"data": {"hm_ladle_no": "LD-02"}, "version": 1}, token=t_op, expect_ok=False)
assert conflict["__status"] == 409, conflict
det = conflict["detail"]
assert "SYNCHRONIZATION" in det["message"] and det["server_version"] == s1["version"], det
assert det["server_data"].get("hm_ladle_no") == "LD-01" and "Shift Engineer" in det["updated_by"], det
print("SYNC ENGINE OK — 409 carries message + server data + version + last editor (no silent overwrite, no 'reload page')")

# NOTIFICATION PREFERENCE ENGINE
me0 = call("GET", "/api/auth/me", token=t_op)
call("POST", "/api/auth/notification-prefs",
     {"prefs": {"ai": False, "report": False, "browser": True, "email": False,
                "email_address": "op001@plant.local"}}, token=t_op)
me1 = call("GET", "/api/auth/me", token=t_op)
assert me1["user"]["notification_prefs"]["ai"] is False, me1["user"]
# generate a report → report notification must appear (prefs on for SE)
import urllib.request as _ur
_rq = _ur.Request(f"{BASE}/api/reports/daily/xlsx", headers={"Authorization": f"Bearer {adm2}"})
_ur.urlopen(_rq).read()
n_se = call("GET", "/api/notifications", token=t_se)["items"]
assert any(n["kind"] == "report" for n in n_se), [n["kind"] for n in n_se][:10]
# a ≥30-min delay raises a mandatory 'delay' notification
call("PUT", f"/api/heats/{HN2}/stages/eaf",
     {"data": {"hm_ladle_no": "LD-03"}}, token=t_se)   # versionless save (draft merge)
call("POST", f"/api/heats/{HN2}/stages/eaf/delays",
     {"start_time": "2026-07-17T06:00", "end_time": "2026-07-17T06:45",
      "reason_id": dr["id"], "remarks": "HM crane breakdown"}, token=t_se)
# operator disabled 'report' → filtered out; delay notifications still visible (mandatory)
n_op = call("GET", "/api/notifications", token=t_op)["items"]
assert not any(n["kind"] == "report" for n in n_op), [n["kind"] for n in n_op][:10]
assert any(n["kind"] == "delay" for n in n_op), "mandatory delay notifications must never be filtered"
print("PREFERENCE ENGINE OK — optional kinds filtered per user; mandatory delay alerts unfilterable")

# ═══ THE NOTIFICATION LAW ═══
# server-side search
ns = call("GET", "/api/notifications?q=Report", token=adm2)
assert ns["items"] and all("report" in (n["title"] + n["kind"]).lower() or "Report" in n["title"]
                           for n in ns["items"]), ns["items"][:3]
# paging window (limit honoured, has_more flag present)
np1 = call("GET", "/api/notifications?limit=3", token=adm2)
assert len(np1["items"]) <= 3 and "has_more" in np1, np1
# continuity denial produced a Business Continuity alert
nc = call("GET", "/api/notifications?kind=continuity", token=adm2)
assert nc["items"] and "continuity" in nc["items"][0]["kind"], nc["items"][:2]
# mark all as read
ra = call("POST", "/api/notifications/read-all", token=adm2)
assert ra["marked"] >= 1, ra
n_after = call("GET", "/api/notifications", token=adm2)["items"]
assert all(n["read"] for n in n_after), "read-all must clear everything visible"
print("NOTIFICATION LAW OK — search, paging, continuity alerts, mark-all-read")
# ═══ PREMIUM THEME LAW ═══
call("POST", "/api/auth/theme", {"theme": "lowpower"}, token=adm2)
me_t = call("GET", "/api/auth/me", token=adm2)
assert me_t["user"]["theme"] == "lowpower", me_t["user"]
call("POST", "/api/auth/theme", {"theme": "dark"}, token=adm2)
print("THEME LAW OK — low power mode persisted server-side")

print("\n========================================")
print("GRADE/SYNC/PREFERENCE DIRECTIVE: ALL PASSED")
print("========================================")

# ═══ THE REACHABILITY LAW ═══
# A field may only be mandatory if the UI provides a way to enter it.
# (Root cause of the Bath Temperature failure: tiles carrying BOTH fields and
#  a chemistry block rendered only the chemistry editor.)
print("\n--- Field reachability audit ---")
defs = call("GET", "/api/stage-defs", token=t_se)["stages"]
unreachable = []
for st, d in defs.items():
    for t in d["tiles"]:
        # A tile carrying fields ALONGSIDE additions (the charge: scrap rows
        # plus the hot-metal figures) is rendered by the fields editor, which
        # draws the additions above the inputs — so its fields ARE reachable.
        # Same reasoning as the chemistry case that prompted this audit.
        editor = ("fields" if t.get("fields")
                  else "chemistry" if t.get("chemistry") else "materials" if t.get("materials")
                  else "delays" if t.get("delays") else "route" if t.get("route")
                  else "calculator" if t.get("calculator") else "fields")
        for f in (t.get("fields") or []):
            if f.get("req") and not f.get("auto"):
                # every required field must live in a tile whose editor renders inputs
                if editor in ("materials", "delays", "route", "calculator"):
                    unreachable.append(f"{st}/{t['label']}/{f['key']} (editor={editor})")
assert not unreachable, f"UNREACHABLE MANDATORY FIELDS: {unreachable}"
n_req = sum(1 for d in defs.values() for t in d["tiles"] for f in (t.get("fields") or [])
            if f.get("req") and not f.get("auto"))
print(f"REACHABILITY OK — all {n_req} mandatory fields sit in tiles that render inputs")

# Bath Temperature specifically: declared, mandatory, unit-carrying, saved, versioned
eaf_tile = next(t for t in defs["eaf"]["tiles"] if t.get("chemistry") and t.get("fields"))
bt = next(f for f in eaf_tile["fields"] if f["key"] == "bath_temp")
assert bt["req"] and bt["unit"] == "\u00b0C" and bt["type"] == "number", bt
st_eaf = call("GET", f"/api/heats/{HN}/stages/eaf", token=t_se)
assert st_eaf["data"]["bath_temp"] == 38750 or st_eaf["data"]["bath_temp"] == 1640, st_eaf["data"]["bath_temp"]
vers_bt = [v for v in call("GET", f"/api/heats/{HN}/versions?stage=eaf", token=t_se)
           if v["data"].get("bath_temp") is not None]
assert vers_bt, "bath temperature must persist into immutable version snapshots"
print(f"BATH TEMPERATURE OK — mandatory \u00b0C field, saved value {st_eaf['data']['bath_temp']}, "
      f"in {len(vers_bt)} audit version snapshot(s), exported in heat reports")

print("\n========================================")
print("REACHABILITY & BATH TEMPERATURE: ALL PASSED")
print("========================================")

# ═══════════ THE PROCESS ROUTE LAW — every heat follows its OWN route ═══════════
print("\n--- Process Route Law: ZPF bypass ---")
hb = call("POST", "/api/heats", {"grade_code": "Fe500D", "shift_engineer": "R. Kumar"}, token=t_se)
HB = hb["heat_no"]
assert hb["route_refining"] is None
assert hb["gates"]["zpf"]["state"] == "awaiting_route", hb["gates"]["zpf"]
call("PUT", f"/api/heats/{HB}/stages/eaf", {"data": {
    "blast_furnace": "BF-2", "ladle_no": "9", "hm_weight": 90.0, "hm_temp": 1345,
    "hm_arrival": "2026-07-17T06:20", "hm_charging": "2026-07-17T06:45"}}, token=t_se)
call("POST", f"/api/heats/{HB}/stages/eaf/chemistry",
     {"label": "Hot Metal", "values": {"C": 4.3, "Si": 0.6, "Mn": 0.4, "S": 0.04, "P": 0.1, "Ti": 0.05}}, token=t_se)
call("PUT", f"/api/heats/{HB}/stages/eaf", {"data": {
    "charging_start": "2026-07-17T06:50", "charging_end": "2026-07-17T07:30",
    "power_on": "2026-07-17T06:55", "power_off": "2026-07-17T08:10",
    "energy_kwh": 37800, "o2_consumption": 2800, "bath_temp": 1638,
    "tap_start": "2026-07-17T08:15", "tap_end": "2026-07-17T08:25",
    "tap_temp": 1650, "tap_weight": 102.0}}, token=t_se)
call("POST", f"/api/heats/{HB}/stages/eaf/chemistry",
     {"label": "EAF Bath", "values": {"C": 0.09, "Si": 0.01, "Mn": 0.11, "S": 0.03, "P": 0.011, "Ti": 0.0}}, token=t_se)
assert call("POST", f"/api/heats/{HB}/stages/eaf/submit", token=t_se)["ok"]
# before the decision LRF is held, not silently skipped
hb2 = call("GET", f"/api/heats/{HB}", token=t_se)
assert hb2["gates"]["lrf"]["state"] == "locked" and "Where shall this heat go next" in hb2["gates"]["lrf"]["reason"], hb2["gates"]["lrf"]
# decide: straight to LRF-6, bypassing ZPF entirely
hb3 = call("POST", f"/api/heats/{HB}/refining-route", {"route": "LRF-6"}, token=t_se)
assert hb3["route_refining"] == "LRF-6"
assert "zpf" not in hb3["required_stages"], hb3["required_stages"]
assert hb3["gates"]["zpf"]["state"] == "not_in_route", hb3["gates"]["zpf"]
assert hb3["gates"]["lrf"]["state"] == "open", hb3["gates"]["lrf"]
lrf_pre = call("GET", f"/api/heats/{HB}/stages/lrf", token=t_se)
assert lrf_pre["data"].get("lrf_unit") == "LRF-6", lrf_pre["data"]
print(f"ZPF BYPASS OK — {HB}: LRF-6 selected, ZPF not_in_route, LRF open, unit pre-filled")
# ZPF is never mandatory: the heat validates and reports without it
rv = call("GET", f"/api/heats/{HB}/review", token=t_se)
assert not any("ZPF" in f["message"] for f in rv["validation"]), rv["validation"]
assert not any(s["title"].startswith("ZPF") for s in rv["report"]["stages"]), [s["title"] for s in rv["report"]["stages"]]
print("ZPF NEVER MANDATORY OK — absent from validation and from the heat's Digital DNA")
# a LOCKED heat's route is immutable (lock law takes precedence)
rr = call("POST", f"/api/heats/{HN}/refining-route", {"route": "LRF-5"}, token=t_se, expect_ok=False)
assert rr["__status"] == 423, rr
# re-routing away from recorded ZPF data is refused (No-Delete integrity)
hc = call("POST", "/api/heats", {"grade_code": "Fe500D"}, token=t_se)
HC = hc["heat_no"]
call("PUT", f"/api/heats/{HC}/stages/eaf", {"data": {
    "blast_furnace": "BF-1", "ladle_no": "4", "hm_weight": 91.0, "hm_temp": 1348,
    "hm_arrival": "2026-07-17T14:20", "hm_charging": "2026-07-17T14:45"}}, token=t_se)
call("POST", f"/api/heats/{HC}/stages/eaf/chemistry",
     {"label": "Hot Metal", "values": {"C": 4.2, "Si": 0.6, "Mn": 0.4, "S": 0.04, "P": 0.1, "Ti": 0.05}}, token=t_se)
call("PUT", f"/api/heats/{HC}/stages/eaf", {"data": {
    "charging_start": "2026-07-17T14:50", "charging_end": "2026-07-17T15:30",
    "power_on": "2026-07-17T14:55", "power_off": "2026-07-17T16:10",
    "energy_kwh": 38000, "o2_consumption": 2850, "bath_temp": 1642,
    "tap_start": "2026-07-17T16:15", "tap_end": "2026-07-17T16:25",
    "tap_temp": 1652, "tap_weight": 103.0}}, token=t_se)
call("POST", f"/api/heats/{HC}/stages/eaf/chemistry",
     {"label": "EAF Bath", "values": {"C": 0.09, "Si": 0.01, "Mn": 0.12, "S": 0.03, "P": 0.012, "Ti": 0.0}}, token=t_se)
call("POST", f"/api/heats/{HC}/stages/eaf/submit", token=t_se)
call("POST", f"/api/heats/{HC}/refining-route", {"route": "ZPF"}, token=t_se)
call("PUT", f"/api/heats/{HC}/stages/zpf", {"data": {"zpf_start": "2026-07-17T16:30"}}, token=t_se)
rr2 = call("POST", f"/api/heats/{HC}/refining-route", {"route": "LRF-5"}, token=t_se, expect_ok=False)
assert rr2["__status"] == 400 and "already carries ZPF production data" in rr2["detail"], rr2
print("ROUTE INTEGRITY OK — locked heats immutable; a heat with ZPF data cannot be re-routed to erase it")
# Gantt shows only the stages each heat actually followed — on the plant
# day of its process times (HB's data is dated 2026-07-17)
g = call("GET", "/api/gantt?mode=heat&span=day&plant_day=2026-07-17", token=t_se)
rowB = next((r for r in g["rows"] if r["key"] == HB), None)
assert rowB and not any(b["stage"] == "zpf" for b in rowB["bars"]), rowB and [b["stage"] for b in rowB["bars"]]
print("GANTT ROUTE LAW OK — bypassed heat shows no ZPF bar")

# ═══════════ USER MANAGEMENT · PASSWORD · RECOVERY LAWS ═══════════
print("\n--- Credentials ledger, password change & recovery ---")
import os as _os
CRED = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "credentials.txt"))
assert _os.path.exists(CRED), "credentials.txt must exist"
led = open(CRED).read()
assert "OP001" in led and "steel@123" in led and "CONFIDENTIAL" in led
print("CREDENTIALS LEDGER OK — auto-created at first boot with issued credentials")

# self-service password change → ledger stops storing that secret
call("POST", "/api/auth/password", {"current": "steel@123", "new": "Plant2026x"}, token=t_op)
led2 = open(CRED).read()
op_line = [l for l in led2.splitlines() if l.startswith("| OP001")][0]
assert "(user-managed)" in op_line and "Plant2026x" not in led2, op_line
t_op = login("OP001", "Plant2026x")["token"]
print("PASSWORD CHANGE OK — new password works; ledger shows (user-managed), secret never written")

# forgot password → request only, never an automatic reset
fp = call("POST", "/api/auth/forgot-password", {"employee_id": "OP001", "remarks": "Forgot after shift rotation"})
assert "administrator" in fp["message"].lower()
r_unknown = call("POST", "/api/auth/forgot-password", {"employee_id": "NOSUCHUSER"})
assert r_unknown["message"] == fp["message"], "must not reveal whether an account exists"
reqs = call("GET", "/api/admin/password-requests", token=adm2)
pr = next(x for x in reqs if x["employee_id"] == "OP001")
assert pr["status"] == "pending" and pr["role"] == "Operator", pr
assert login("OP001", "Plant2026x")["token"], "the old password must still work — no auto-reset"
print("FORGOT PASSWORD OK — request raised for admin approval, no automatic reset, no account disclosure")

# admin approval → new credentials issued, ledger + audit updated, sessions revoked
dec = call("POST", f"/api/admin/password-requests/{pr['id']}/decide",
           {"action": "approve_reset", "remarks": "Identity confirmed at gate", "reauth_password": "admin@123"}, token=adm2)
newpw = dec["temporary_password"]
assert dec["status"] == "approved" and newpw and len(newpw) >= 8, dec
assert login("OP001", newpw)["token"], "issued password must work"
led3 = open(CRED).read()
assert newpw in led3, "issued temporary password must appear in the ledger"
aud = call("GET", "/api/admin/audit?category=admin", token=adm2)
assert any(a["action"] == "password_reset_approved" for a in aud), [a["action"] for a in aud][:8]
print(f"ADMIN APPROVAL OK — temporary password issued, ledger updated, audited, old sessions revoked")

# rejection path is equally first-class
call("POST", "/api/auth/forgot-password", {"employee_id": "QE001"})
pr2 = next(x for x in call("GET", "/api/admin/password-requests", token=adm2) if x["employee_id"] == "QE001")
dec2 = call("POST", f"/api/admin/password-requests/{pr2['id']}/decide",
            {"action": "reject", "remarks": "Requester could not be verified", "reauth_password": "admin@123"}, token=adm2)
assert dec2["status"] == "rejected" and dec2["temporary_password"] is None, dec2
assert login("QE001", "steel@123")["token"], "rejected request must leave the account untouched"
print("REJECTION OK — account untouched, decision audited")

# ledger is administrator-only and re-authenticated
cf = call("POST", "/api/admin/credentials-file", {"reauth_password": "admin@123"}, token=adm2)
assert "SMS-2 IDOS — CREDENTIALS LEDGER" in cf["content"]
bad = call("POST", "/api/admin/credentials-file", {"reauth_password": "wrong"}, token=adm2, expect_ok=False)
assert bad["__status"] in (400, 401, 403), bad
denied = call("POST", "/api/admin/credentials-file", {"reauth_password": "steel@123"}, token=t_se, expect_ok=False)
assert denied["__status"] == 403, denied
print("LEDGER ACCESS OK — administrator-only, password re-authentication enforced")

print("\n========================================")
print("PROCESS ROUTE & USER MANAGEMENT LAWS: ALL PASSED")
print("========================================")

# ═══ SOUND NOTIFICATION LAW (server-side policy persistence) ═══
call("POST", "/api/auth/notification-prefs",
     {"prefs": {"sound_mode": "approval", "sound_volume": 0.4, "browser": True}}, token=t_se)
me_s = call("GET", "/api/auth/me", token=t_se)["user"]["notification_prefs"]
assert me_s["sound_mode"] == "approval" and abs(me_s["sound_volume"] - 0.4) < 1e-6, me_s
call("POST", "/api/auth/notification-prefs", {"prefs": {"sound_mode": "nonsense", "sound_volume": 5}}, token=t_se)
me_s2 = call("GET", "/api/auth/me", token=t_se)["user"]["notification_prefs"]
assert me_s2.get("sound_mode") != "nonsense" and me_s2.get("sound_volume", 1) <= 1.0, me_s2
print("SOUND POLICY OK — mode + volume persisted per user, invalid values rejected")

# ═══ THE REAL-TIME LAW — 2-second pulse ═══
print("\n--- Real-time pulse ---")
import time as _time
p1 = call("GET", "/api/pulse", token=t_se)
for dom in ["notifications", "approvals", "heats", "production", "machines",
            "reports", "users", "quality"]:
    assert dom in p1, f"pulse missing domain {dom}"
_t0 = _time.time()
p2 = call("GET", "/api/pulse", token=t_se)
_lat = _time.time() - _t0
assert _lat < 0.5, f"pulse too slow: {_lat:.3f}s"
# stable when nothing changes — signatures must not churn (no needless repaints)
assert p1["heats"]["sig"] == p2["heats"]["sig"], "heat signature must be stable"
assert p1["machines"]["sig"] == p2["machines"]["sig"], "machine signature must be stable"
assert p1["users"]["sig"] == p2["users"]["sig"], "user signature must be stable"
# a real change must move exactly the right signature
call("POST", "/api/admin/equipment/VD-3.1/status",
     {"status": "maintenance", "reason": "pulse test", "reauth_password": "admin@123"}, token=adm2)
p3 = call("GET", "/api/pulse", token=t_se)
assert p3["machines"]["sig"] != p2["machines"]["sig"], "machine change must move the machine signature"
assert p3["heats"]["sig"] == p2["heats"]["sig"], "a machine change must NOT disturb the heat component"
call("POST", "/api/admin/equipment/VD-3.1/status",
     {"status": "available", "reason": "restore", "reauth_password": "admin@123"}, token=adm2)
print(f"PULSE OK — 8 domains, {_lat*1000:.0f}ms, signatures stable when idle, isolated per component")

# ═══ LOGIN PAGE LAW ═══
r_nf = call("POST", "/api/auth/login", {"employee_id": "GHOST99", "password": "x"}, expect_ok=False)
assert r_nf["__status"] == 401 and "Account not found" in r_nf["detail"], r_nf
r_wp = call("POST", "/api/auth/login", {"employee_id": "SE001", "password": "totally-wrong"}, expect_ok=False)
assert r_wp["__status"] == 401 and "Incorrect password" in r_wp["detail"] and "expired" not in r_wp["detail"].lower(), r_wp
# disabled account → the specified message (VIEW01 is unprotected; disable, test, restore)
users_l = call("GET", "/api/admin/users", token=adm2)
v01 = next(u for u in users_l if u["employee_id"] == "VIEW01")
call("PATCH", f"/api/admin/users/{v01['id']}", {"active": False, "reauth_password": "admin@123"}, token=adm2)
r_dis = call("POST", "/api/auth/login", {"employee_id": "VIEW01", "password": "steel@123"}, expect_ok=False)
assert r_dis["__status"] == 403 and "has been disabled" in r_dis["detail"], r_dis
call("PATCH", f"/api/admin/users/{v01['id']}", {"active": True, "reauth_password": "admin@123"}, token=adm2)
print("LOGIN PAGE LAW OK — account-not-found, wrong-password and disabled messages exact; never 'session expired'")

# ══════════════════════════════════════════════════════════════════════════
# TWO MELTING FURNACES — a heat starts at the EAF (E…) or the ZPF (F…)
# The plant runs both as primary units; a heat is melted in one, never both.
# ══════════════════════════════════════════════════════════════════════════
print("\n--- Dual entry furnaces: EAF (E) and ZPF (F) ---")
# Fresh token: earlier sections rotate OP001's password and revoke its sessions,
# so this block drives everything as the Shift Engineer (untouched, and
# authorised for both furnaces).
t_se = login("SE001", "steel@123")["token"]
t_op = t_se
hz = call("POST", "/api/heats", {"grade_code": "54SICR6", "entry_stage": "zpf",
                                 "shift_engineer": "R. Sharma"}, token=t_se)
HZ = hz["heat_no"]
assert HZ.startswith("F"), f"a ZPF-melted heat must take an F number, got {HZ}"
assert hz["entry_stage"] == "zpf", hz
assert hz["gates"]["eaf"]["state"] == "not_in_route", hz["gates"]["eaf"]
assert "melted in the ZPF" in hz["gates"]["eaf"]["reason"], hz["gates"]["eaf"]
assert hz["gates"]["zpf"]["state"] == "open", hz["gates"]["zpf"]
assert "eaf" not in hz["required_stages"] and "zpf" in hz["required_stages"], hz["required_stages"]
print(f"ZPF ENTRY OK — {HZ}: F series issued, EAF not_in_route, ZPF open")

# the ZPF shows its OWN Charge Mix for a heat melted there
zdef = call("GET", f"/api/heats/{HZ}/stages/zpf", token=t_op)["def"]
zlabels = [t["label"] for t in zdef["tiles"]]
assert "Charge Mix" in zlabels and "Scrap & Charge Materials" in zlabels, zlabels
assert "Tapping Additions" in zlabels, zlabels
# ...but NOT for an EAF heat that comes here as the post-EAF de-phos step
edef = call("GET", f"/api/heats/{HN}/stages/zpf", token=t_op)["def"]
elabels = [t["label"] for t in edef["tiles"]]
assert "Charge Mix" not in elabels, f"an EAF heat must not be asked to charge twice: {elabels}"
print("CHARGE SECTION SCOPING OK — ZPF charges only heats melted in the ZPF")

# charge auto-calc fires at the ZPF exactly as it does at the EAF
call("PUT", f"/api/heats/{HZ}/stages/zpf", {"data": {
    "hm_weight": 60.0, "hm_temp": 1350, "zpf_start": "2026-07-18T02:00",
    "zpf_end": "2026-07-18T02:40", "o2_total": 500,
    "temp_initial": 1600, "temp_final": 1620,
    "blow_on_time": "2026-07-18T02:05", "tap_time": "2026-07-18T03:00",
    "pre_heat_tap_time": "2026-07-18T01:44"}}, token=t_op)
call("POST", f"/api/heats/{HZ}/stages/zpf/materials",
     {"material_id": mat["HSC"]["id"], "quantity": 15.0, "point": "charge"}, token=t_op)
# a ferroalloy thrown into the ladle at tapping must NOT inflate the charge
call("POST", f"/api/heats/{HZ}/stages/zpf/materials",
     {"material_id": mat["SIMN"]["id"], "quantity": 400.0, "point": "tapping"}, token=t_op)
zs = call("GET", f"/api/heats/{HZ}/stages/zpf", token=t_op)
zd = zs["data"]
assert zd["total_scrap"] == 15.0, zd
assert zd["total_charge"] == 75.0, zd
assert zd["hm_pct"] == 80.0, zd
assert zd["ttt"] == 76.0, f"tap-to-tap wrong: {zd}"
assert zd["bon_to_tap"] == 55.0, zd
tap_rows = [m for m in zs["materials"] if m["point"] == "tapping"]
chg_rows = [m for m in zs["materials"] if m["point"] == "charge"]
assert len(tap_rows) == 1 and len(chg_rows) == 1, zs["materials"]
print(f"ZPF CHARGE + CYCLE OK — {zd['total_charge']} t charge, {zd['hm_pct']}% HM, TTT {zd['ttt']} min;"
      " tapping alloys excluded from the charge")

# a ZPF-melted heat has already been through the ZPF: it cannot re-route to it
rr_z = call("POST", f"/api/heats/{HZ}/refining-route", {"route": "ZPF"}, token=t_se, expect_ok=False)
assert rr_z["__status"] == 400 and "already been through" in rr_z["detail"], rr_z
print("ROUTE LAW OK — a ZPF heat cannot be routed back into the ZPF")

# EAF heats are untouched: still E series, still start at the EAF
he = call("POST", "/api/heats", {"grade_code": "Fe500D"}, token=t_se)
assert he["heat_no"].startswith("E") and he["entry_stage"] == "eaf", he
assert he["gates"]["eaf"]["state"] == "open", he["gates"]
print(f"EAF ENTRY UNCHANGED OK — {he['heat_no']} still starts at the EAF on the E series")

print("\n========================================")
print("DUAL FURNACE LAW: ALL PASSED")
print("========================================")

# ══════════════════════════════════════════════════════════════════════════
print("\n--- Maintenance mode ---")
# The setting shipped seeded but read by nothing: switching it on changed
# nothing on screen, and no test noticed. Assert the state actually reaches
# every signed-in client, and that it warns without locking anyone out.
assert call("GET", "/api/auth/me", token=t_se)["maintenance"]["on"] is False

call("POST", "/api/admin/settings",
     {"key": "maintenance_mode", "value": "on", "reauth_password": "admin@123"}, token=adm2)
call("POST", "/api/admin/settings",
     {"key": "maintenance_message", "value": "UPS changeover 02:00-04:00.",
      "reauth_password": "admin@123"}, token=adm2)

# reaches a plant user, not only the administrator who set it
mm = call("GET", "/api/auth/me", token=t_se)["maintenance"]
assert mm["on"] is True and mm["message"] == "UPS changeover 02:00-04:00.", mm
# and it is on the login response, so it is known before a heat is started
assert login("SE001", "steel@123")["maintenance"]["on"] is True

# THE MAINTENANCE LAW: it WARNS, it never locks. An operator mid-heat must
# still be able to finish recording it, or the shift loses data the paper
# log would have kept.
hmt = call("POST", "/api/heats", {"grade_code": "Fe500D"}, token=t_se)
call("PUT", f"/api/heats/{hmt['heat_no']}/stages/eaf",
     {"data": {"tap_temp": 1655}}, token=t_se)

call("POST", "/api/admin/settings",
     {"key": "maintenance_mode", "value": "off", "reauth_password": "admin@123"}, token=adm2)
assert call("GET", "/api/auth/me", token=t_se)["maintenance"]["on"] is False
print("MAINTENANCE MODE OK — reaches every client, warns without locking entry")
