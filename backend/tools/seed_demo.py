"""SMS-2 IDOS — comprehensive demo production data generator.

Builds a realistic 14-day plant history that exercises EVERY path the system
supports, so the dashboard, Gantt, analytics and audit report all have
something meaningful to show.

Coverage
--------
  grades          Fe500D · Fe550D · IS2062 · E250BR · SAE1018 (incl. grades
                  registered at entry, exercising the Grade Entry System)
  refining route  ZPF  AND  direct-to-LRF-5 / LRF-6 (the ZPF bypass)
  post-LRF route  VD-2 · VD-3 · BC-2 · BC-3
  shifts          A · B · C
  states          locked (fully approved) · in-progress at every stage ·
                  aborted · training (excluded from reports by design)
  quality         Accepted · Accepted with Remarks · Rejected
  delays          across several categories, including a long one

How it works
------------
Everything is driven through the REAL HTTP API, exactly as an operator would:
validation, stage gating, the route laws, the three-level approval chain and
the audit trail are all genuinely exercised. Nothing is written straight to the
tables, so the result cannot contain a state the application itself would
refuse to create.

The ONE exception is the plant day. `plant_day` and `shift` are stamped from
the constitutional clock at creation time (`lifecycle.create_heat`), and the
API has no backdating parameter — correctly, because production data should
never claim to have happened on a day it did not. So historical heats are
created normally and then their day/shift stamps are shifted afterwards,
directly in SQLite, purely so the trend charts have a history to draw. That
step is clearly isolated in `backdate()` below and touches nothing else.

Usage
-----
    python tools/seed_demo.py                                   # local
    python tools/seed_demo.py --days 21                         # longer history
    python tools/seed_demo.py --url https://your.onrender.com   # a deployment

Run it on a FRESH database (delete data/sms2_idos.db*, restart the server).
The smoke test rotates OP001's password, so a database it has touched will
fail here — that is expected, not a bug.

Against a REMOTE target it: asks before writing anything, never assumes the
published demo passwords (env var SMS2_SEED_PASSWORD, else a getpass prompt),
and SKIPS backdating — `backdate()` edits the local SQLite file, so running it
against a remote server would rewrite the wrong database. Every heat therefore
lands on today; multi-day history is a local-only feature.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = "http://127.0.0.1:8020"
DB = Path(__file__).resolve().parent.parent / "data" / "sms2_idos.db"
random.seed(20260720)                      # reproducible demo data


# ─────────────────────────── HTTP helpers ───────────────────────────
def call(method: str, path: str, body=None, token=None, quiet=False):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        payload = e.read().decode(errors="replace")
        if quiet:
            return {"__error": e.code, "detail": payload}
        raise SystemExit(f"\n{method} {path} -> HTTP {e.code}\n{payload}\n")
    except urllib.error.URLError as e:
        raise SystemExit(f"\nCannot reach {BASE} — is the server running?  ({e.reason})\n")


def login(emp: str, pw: str):
    r = call("POST", "/api/auth/login", {"employee_id": emp, "password": pw}, quiet=True)
    if "__error" in r:
        raise SystemExit(
            f"\nLogin failed for {emp}. Run this against a FRESH database:\n"
            f"  stop the server, delete backend/data/sms2_idos.db*, restart, then re-run.\n"
            f"(smoke_e2e.py permanently rotates OP001's password.)\n")
    return r["token"]


def t(h: int, m: int = 0) -> str:
    """A datetime-local string on a fixed base date; the plant day is set later.

    Minutes roll into hours: callers do arithmetic like `t(h, 20 + delay_min)`,
    and a raw f-string there yields minute 85, which the API rejects with a
    500 (`minute must be in 0..59`).
    """
    h, m = h + m // 60, m % 60
    return f"2026-07-20T{h % 24:02d}:{m:02d}"


def submit(hn: str, stage: str, token) -> tuple[bool, str]:
    """Submit a stage and CHECK THE RESULT.

    The endpoint answers HTTP 200 with `{ok: false, findings: [...]}` when the
    Validation Engine blocks a submission — a silent no-op if the caller only
    looks at the status code. Ignoring that produced demo heats that appeared
    to advance but had not, and the failure only surfaced two stages later.
    """
    r = call("POST", f"/api/heats/{hn}/stages/{stage}/submit", token=token)
    if r.get("ok"):
        return True, ""
    blockers = "; ".join(f"{f.get('tile')}: {f.get('message')}"
                         for f in r.get("findings", []) if f.get("level") == "error")
    return False, blockers or "blocked by validation"


# ─────────────────────────── stage payloads ───────────────────────────
def hot_metal_data(base_h: int, weight: float, temp: int):
    return {"blast_furnace": random.choice(["BF-1", "BF-2"]),
            "ladle_no": str(random.randint(1, 15)),
            "hm_weight": weight, "hm_temp": temp,
            "hm_arrival": t(base_h, 10), "hm_charging": t(base_h, 35),
            "stage_remarks": random.choice(
                ["Normal ladle condition.", "Slight skull on ladle lip.",
                 "Ladle preheated, good thermal state.", "Torpedo delayed at BF."])}


def eaf_data(base_h: int, energy: int, tap_w: float, tap_t: int):
    return {"charging_start": t(base_h, 40), "charging_end": t(base_h + 1, 10),
            "power_on": t(base_h, 45), "power_off": t(base_h + 1, 55),
            "energy_kwh": energy, "o2_consumption": random.randint(2500, 3400),
            "n2_consumption": random.randint(120, 300),
            "ar_consumption": random.randint(40, 110),
            "electrode_kg": random.randint(90, 160),
            "bath_temp": random.randint(1600, 1650),
            "tap_start": t(base_h + 2, 0), "tap_end": t(base_h + 2, 12),
            "tap_temp": tap_t, "tap_weight": tap_w,
            "slag_condition": random.choice(["Good", "Foamy", "Heavy", "Dry"])}


CHEM = {
    "hot_metal": lambda: {"C": round(random.uniform(4.0, 4.5), 2), "Si": round(random.uniform(0.4, 0.9), 2),
                          "Mn": round(random.uniform(0.2, 0.6), 2), "S": round(random.uniform(0.02, 0.06), 3),
                          "P": round(random.uniform(0.08, 0.14), 3), "Ti": round(random.uniform(0.02, 0.08), 3)},
    "bath":      lambda: {"C": round(random.uniform(0.05, 0.12), 3), "Si": round(random.uniform(0.005, 0.02), 3),
                          "Mn": round(random.uniform(0.08, 0.18), 3), "S": round(random.uniform(0.02, 0.045), 3),
                          "P": round(random.uniform(0.008, 0.02), 3), "Ti": 0.0},
    "final":     lambda: {"C": round(random.uniform(0.18, 0.26), 3), "Si": round(random.uniform(0.15, 0.30), 3),
                          "Mn": round(random.uniform(0.75, 1.05), 3), "S": round(random.uniform(0.015, 0.035), 3),
                          "P": round(random.uniform(0.015, 0.03), 3), "Ti": round(random.uniform(0.0, 0.02), 3)},
}


# ─────────────────────────── heat driver ───────────────────────────
def drive_heat(tok, spec, masters, log):
    """Create one heat and take it as far along its route as `spec` asks."""
    op, se, si, qe = tok["op"], tok["se"], tok["si"], tok["qe"]
    mat = {m["code"]: m for m in masters["materials"]}
    reasons = masters["delay_reasons"]

    h = call("POST", "/api/heats",
             {"grade_code": spec["grade"], "shift_engineer": spec["engineer"],
              "is_training": spec.get("training", False)}, token=se)
    hn = h["heat_no"]
    stop = spec["stop_at"]
    base = spec["base_hour"]

    # ── Hot Metal ──
    call("PUT", f"/api/heats/{hn}/stages/hot_metal",
         {"data": hot_metal_data(base, spec["hm_weight"], spec["hm_temp"])}, token=op)
    call("POST", f"/api/heats/{hn}/stages/hot_metal/chemistry",
         {"label": "Hot Metal", "values": CHEM["hot_metal"]()}, token=op)
    for code, qty in (("HSC", round(random.uniform(8, 16), 1)), ("DRI", round(random.uniform(5, 12), 1))):
        if code in mat:
            call("POST", f"/api/heats/{hn}/stages/hot_metal/materials",
                 {"material_id": mat[code]["id"], "quantity": qty}, token=op)
    if stop == "hot_metal":
        return hn, "in progress · Hot Metal"
    ok, why = submit(hn, "hot_metal", op)
    if not ok: return hn, f"blocked at Hot Metal ({why})"

    # ── EAF ──
    call("PUT", f"/api/heats/{hn}/stages/eaf",
         {"data": eaf_data(base, spec["energy"], spec["tap_weight"], spec["tap_temp"])}, token=op)
    call("POST", f"/api/heats/{hn}/stages/eaf/chemistry",
         {"label": "EAF Bath", "values": CHEM["bath"]()}, token=op)
    for d in spec.get("delays", []):
        r = next((x for x in reasons if x["category"] == d["cat"]), reasons[0])
        # DelayIn takes start_time/end_time (not start/end).
        call("POST", f"/api/heats/{hn}/stages/eaf/delays",
             {"reason_id": r["id"], "start_time": t(base + 1, 20),
              "end_time": t(base + 1, 20 + d["min"]), "remarks": d["note"]}, token=op)
    if stop == "eaf":
        return hn, "in progress · EAF"
    ok, why = submit(hn, "eaf", op)
    if not ok: return hn, f"blocked at EAF ({why})"

    # ── THE PROCESS ROUTE LAW: ZPF, or straight to a ladle furnace ──
    call("POST", f"/api/heats/{hn}/refining-route", {"route": spec["refining"]}, token=se)
    if stop == "route":
        return hn, f"awaiting {spec['refining']}"

    if spec["refining"] == "ZPF":
        call("PUT", f"/api/heats/{hn}/stages/zpf", {"data": {
            "zpf_start": t(base + 2, 20), "zpf_end": t(base + 2, 50),
            "o2_total": random.randint(700, 1200),
            "temp_initial": spec["tap_temp"] - 20, "temp_final": spec["tap_temp"] - 45,
            "lance_used": "Yes", "slag_practice": random.choice(["Single Slag", "Double Slag"])}}, token=op)
        call("POST", f"/api/heats/{hn}/stages/zpf/chemistry",
             {"label": "ZPF", "values": CHEM["bath"]()}, token=op)
        if stop == "zpf":
            return hn, "in progress · ZPF"
        ok, why = submit(hn, "zpf", op)
        if not ok: return hn, f"blocked at ZPF ({why})"

    # ── LRF ──
    lrf_unit = "LRF-5" if spec["refining"] in ("ZPF", "LRF-5") else "LRF-6"
    call("PUT", f"/api/heats/{hn}/stages/lrf", {"data": {
        "lrf_unit": lrf_unit, "lrf_start": t(base + 3, 0), "lrf_end": t(base + 3, 40),
        "temp_initial": spec["tap_temp"] - 60, "temp_final": spec["tap_temp"] - 15,
        "power_on_min": random.randint(18, 34), "argon_flow": random.randint(8, 20)}}, token=op)
    call("POST", f"/api/heats/{hn}/stages/lrf/chemistry",
         {"label": "LRF", "values": CHEM["final"]()}, token=op)
    for code in ("FeMn", "FeSi", "SiMn"):
        if code in mat:
            call("POST", f"/api/heats/{hn}/stages/lrf/materials",
                 {"material_id": mat[code]["id"], "quantity": round(random.uniform(60, 320), 1)}, token=op)
    if stop == "lrf":
        return hn, "in progress · LRF"

    call("POST", f"/api/heats/{hn}/route", {"route": spec["route"]}, token=se)
    ok, why = submit(hn, "lrf", op)
    if not ok: return hn, f"blocked at LRF ({why})"

    # ── VD (only on a VD route) ──
    if spec["route"].startswith("VD"):
        call("PUT", f"/api/heats/{hn}/stages/vd", {"data": {
            "vd_start": t(base + 4, 0), "vd_end": t(base + 4, 22),
            "temp_initial": spec["tap_temp"] - 30, "temp_final": spec["tap_temp"] - 55,
            "vacuum_min": random.randint(12, 20)}}, token=op)
        call("POST", f"/api/heats/{hn}/stages/vd/chemistry",
             {"label": "VD", "values": CHEM["final"]()}, token=op)
        if stop == "vd":
            return hn, "in progress · VD"
        ok, why = submit(hn, "vd", op)
        if not ok: return hn, f"blocked at VD ({why})"

    # ── Caster ──
    billets = random.randint(48, 74)
    call("PUT", f"/api/heats/{hn}/stages/caster", {"data": {
        "cast_start": t(base + 4, 40), "cast_end": t(base + 5, 45),
        "tundish_temp": random.randint(1520, 1560),
        "strand_count": random.choice([4, 5, 6]),
        "billet_size": random.choice(["100×100", "125×125", "150×150"]),
        "billet_length": random.choice([6.0, 9.0, 12.0]),
        "billet_count": billets,
        "billet_weight": round(billets * random.uniform(1.55, 1.75), 1)}}, token=op)
    if stop == "caster":
        return hn, "in progress · Caster"
    ok, why = submit(hn, "caster", op)
    if not ok: return hn, f"blocked at Caster ({why})"

    # ── Quality ──
    call("PUT", f"/api/heats/{hn}/stages/quality", {"data": {
        "decision": spec["quality"], "decision_time": t(base + 6, 10),
        "decision_remarks": spec.get("q_note", "Chemistry within grade window.")}}, token=qe)
    call("POST", f"/api/heats/{hn}/stages/quality/chemistry",
         {"label": "Final Product", "values": CHEM["final"]()}, token=qe)
    if stop == "quality":
        return hn, "in progress · Quality"
    ok, why = submit(hn, "quality", qe)
    if not ok: return hn, f"quality held ({why})"
    # Review & Submit is restricted to Shift Engineer and above.
    ok, why = submit(hn, "review", se)
    if not ok: return hn, f"review blocked ({why})"

    # ── Three-level approval chain, in order ──
    if stop == "review":
        return hn, "awaiting approval"
    # A rejected heat CANNOT be approved — the Review validation blocks it
    # (6H.8). That is correct behaviour, so such a heat is left standing at
    # review, which is exactly where a real rejected heat would sit.
    if spec["quality"] == "Rejected":
        return hn, "REJECTED · held at review"
    # Order is enforced by the constitution: Operator → Engineer → Incharge.
    for tk, lvl in ((op, 1), (se, 2), (si, 3)):
        call("POST", f"/api/heats/{hn}/approve",
             {"level": lvl, "remarks": f"L{lvl} verified"}, token=tk)
    return hn, f"LOCKED · {spec['route']}"


# ─────────────────── plant-day backdating (see module docstring) ───────────────────
def backdate(rows):
    """Shift day/shift stamps so the demo set spans a real history.

    The API deliberately offers no backdating, so this reaches into SQLite —
    the single place this script does so. It rewrites only the day/shift
    stamps on the heat and its stage records; no production values, no audit
    entries and no approvals are altered.
    """
    if not DB.exists():
        print(f"  ! database not found at {DB} — skipping backdating")
        return 0
    con = sqlite3.connect(DB)
    cur = con.cursor()
    n = 0
    for heat_no, day, shift in rows:
        cur.execute("UPDATE heats SET plant_day=?, shift=? WHERE heat_no=?",
                    (day.isoformat(), shift, heat_no))
        cur.execute("""UPDATE stage_records SET plant_day=?, shift=?
                       WHERE heat_id=(SELECT id FROM heats WHERE heat_no=?)""",
                    (day.isoformat(), shift, heat_no))
        n += cur.rowcount and 1 or 1
    con.commit()
    con.close()
    return n


# ─────────────────────────── the plan ───────────────────────────
def build_plan(days: int):
    """A spread of heats covering every route, shift, grade and end-state."""
    grades = ["Fe500D", "Fe500D", "Fe550D", "IS2062", "E250BR", "SAE1018", "Fe500D", "IS2062"]
    engineers = ["R. Sharma", "K. Patnaik", "S. Mishra", "A. Behera", "M. Nayak"]
    refinings = ["ZPF", "LRF-5", "LRF-6"]
    routes = ["VD-2", "VD-3", "BC-2", "BC-3"]
    shifts = ["A", "B", "C"]
    delay_cats = ["Mechanical", "Electrical", "Process", "Operational", "Raw Material"]
    notes = ["Crane unavailable", "Electrode change", "Ladle not ready",
             "Tundish preparation", "Power dip", "Scrap bucket delayed"]

    plan = []
    today = date.today()
    # ── History: completed, locked heats spread across the window ──
    for i in range(days - 1, 0, -1):
        day = today - timedelta(days=i)
        for j in range(random.randint(1, 3)):
            k = len(plan)
            dl = []
            if random.random() < 0.55:
                dl.append({"cat": random.choice(delay_cats),
                           "min": random.choice([6, 9, 12, 18, 25, 40, 65]),
                           "note": random.choice(notes)})
            plan.append({
                "grade": grades[k % len(grades)],
                "engineer": engineers[k % len(engineers)],
                "refining": refinings[k % len(refinings)],
                "route": routes[k % len(routes)],
                "quality": "Rejected" if k % 17 == 5 else
                           ("Accepted with Remarks" if k % 7 == 3 else "Accepted"),
                "q_note": "Rim segregation observed." if k % 17 == 5 else "Chemistry within grade window.",
                "stop_at": "done", "day": day, "shift": shifts[j % 3],
                "base_hour": 6 + j * 5,
                "hm_weight": round(random.uniform(88, 98), 1),
                "hm_temp": random.randint(1320, 1380),
                "energy": random.randint(34000, 42000),
                "tap_weight": round(random.uniform(98, 108), 1),
                "tap_temp": random.randint(1630, 1670),
                "delays": dl,
            })
    # ── Today: live heats parked at every stage, so the Gantt has movement ──
    live = [("hot_metal", "ZPF", "VD-2"), ("eaf", "LRF-5", "BC-2"), ("route", "LRF-6", "BC-3"),
            ("zpf", "ZPF", "VD-3"), ("lrf", "LRF-5", "VD-2"), ("caster", "ZPF", "BC-2"),
            ("quality", "LRF-6", "VD-3"), ("review", "ZPF", "BC-3")]
    for n, (stop, refining, route) in enumerate(live):
        plan.append({
            "grade": grades[n % len(grades)], "engineer": engineers[n % len(engineers)],
            "refining": refining, "route": route, "quality": "Accepted",
            "stop_at": stop, "day": today, "shift": shifts[n % 3],
            "base_hour": 6 + (n % 3) * 5,
            "hm_weight": round(random.uniform(88, 98), 1), "hm_temp": random.randint(1320, 1380),
            "energy": random.randint(34000, 42000), "tap_weight": round(random.uniform(98, 108), 1),
            "tap_temp": random.randint(1630, 1670),
            "delays": [{"cat": "Process", "min": 14, "note": "Temperature correction"}] if n == 1 else [],
        })
    # ── Two training heats (excluded from reports by design) ──
    for n in range(2):
        plan.append({
            "grade": "Fe500D", "engineer": "Training Cell", "refining": "ZPF",
            "route": "BC-2", "quality": "Accepted", "stop_at": "lrf", "training": True,
            "day": today, "shift": "A", "base_hour": 7,
            "hm_weight": 92.0, "hm_temp": 1350, "energy": 37000,
            "tap_weight": 103.0, "tap_temp": 1650, "delays": [],
        })
    return plan


def main():
    global BASE
    ap = argparse.ArgumentParser(description="Generate SMS-2 demo production data.")
    ap.add_argument("--days", type=int, default=14, help="history window (default 14)")
    ap.add_argument("--url", default=BASE,
                    help="target server (default %(default)s). Point this at a "
                         "deployment to populate it, e.g. https://your-app.onrender.com")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt for a remote target")
    args = ap.parse_args()

    BASE = args.url.rstrip("/")
    remote = not any(h in BASE for h in ("127.0.0.1", "localhost", "0.0.0.0"))

    print(f"SMS-2 IDOS — demo data generator  ({BASE})\n")

    # ── Credentials ────────────────────────────────────────────────────────
    # A deployment should NOT be running the published default passwords, so
    # never assume them for a remote target. Read the environment first, and
    # otherwise ask — via getpass, so nothing is echoed to the screen or left
    # in shell history.
    admin_pw = (os.environ.get("SMS2_ADMIN_PASSWORD") or "").strip()
    user_pw = (os.environ.get("SMS2_SEED_PASSWORD") or "").strip()
    if remote:
        print("  target is REMOTE — this will create real records on that server.")
        if not user_pw:
            user_pw = getpass.getpass("  password for SE001/SI001/OP001/QE001: ").strip()
        if not args.yes:
            print(f"\n  About to create ~38 heats spanning {args.days} plant days on:")
            print(f"      {BASE}")
            if input("  Type 'yes' to continue: ").strip().lower() != "yes":
                raise SystemExit("  Cancelled — nothing was written.\n")
        print()
    else:
        user_pw = user_pw or "steel@123"
        admin_pw = admin_pw or "admin@123"

    tok = {"op": login("OP001", user_pw), "se": login("SE001", user_pw),
           "si": login("SI001", user_pw), "qe": login("QE001", user_pw)}
    masters = call("GET", "/api/admin/masters", token=tok["se"])
    print(f"  masters: {len(masters['grades'])} grades, {len(masters['materials'])} materials, "
          f"{len(masters['delay_reasons'])} delay reasons")

    plan = build_plan(args.days)
    print(f"  plan   : {len(plan)} heats across {args.days} plant days\n")

    made, hist = [], []
    for i, spec in enumerate(plan, 1):
        hn, state = drive_heat(tok, spec, masters, print)
        made.append((hn, state))
        if spec["day"] != date.today():
            hist.append((hn, spec["day"], spec["shift"]))
        print(f"  [{i:>3}/{len(plan)}] {hn:<10} {spec['grade']:<8} "
              f"{spec['refining']:<6} → {spec['route']:<5} {state}")

    # One heat aborted, to exercise the diverted state. It must be an
    # IN-PROGRESS heat: a locked heat is read-only and the API rightly
    # refuses to abort it (6H.11).
    live = [hn for hn, state in made if not state.startswith(("LOCKED", "REJECTED"))]
    if live:
        target = live[-1]
        r = call("POST", f"/api/heats/{target}/abort",
                 {"reason": "Ladle skull detected — heat diverted to the scrap yard."},
                 token=tok["si"], quiet=True)
        if "__error" in r:
            print(f"\n  ! abort of {target} refused: {r.get('detail','')[:90]}")
        else:
            print(f"\n  aborted {target} (diverted heat)")

    if hist:
        if remote:
            # backdate() edits the LOCAL SQLite file directly. Against a remote
            # target that would silently rewrite the wrong database — the one on
            # this machine — while the server's data stayed untouched. Skip it.
            print(f"\n  ! {len(hist)} heats could not be backdated: the plant day is")
            print("    stamped by the server's clock and there is no API to change")
            print("    it, so every heat lands on TODAY on a remote target.")
            print("    History spanning several days only works locally.")
        else:
            n = backdate(hist)
            print(f"  backdated {n} historical heats across the window")

    print(f"\nDone — {len(made)} heats created.")
    print("Reload the dashboard; the trend, Pareto and audit report now have history.")
    print("NOTE: restart the server if the Gantt looks stale (SQLite WAL).")


if __name__ == "__main__":
    main()
