"""
SMS-2 IDOS — AI PRODUCTION ENGINEER (reasoning layer)
=====================================================
Adds genuine reasoning on top of engines/insights.py, which stays exactly as
it is. The two are a floor and a ceiling, not alternatives:

    insights.py   deterministic, offline, always available. Window statistics,
                  trend detection, alerting, keyword-matched answers. This is
                  what a plant with no internet gets, and it never regresses.

    ai_brain.py   this file. When an API key is configured, questions go to
                  Claude with READ-ONLY tools over the plant's own database,
                  so it answers from THIS plant's heats rather than from
                  general steelmaking knowledge alone.

WHY A KEY AND NOT A BUNDLED MODEL
    SMS-2 is built for an offline intranet. A reasoning model of this class
    cannot run on plant hardware, so the honest options were "no reasoning"
    or "reasoning when the site allows an outbound call". This is the second,
    switched off by default: with no key the app behaves exactly as before.

WHAT LEAVES THE PLANT WHEN IT IS ON
    The question, and whatever the tools below return for it — heat numbers,
    process values, chemistry, delays. No credentials, no user passwords, no
    audit trail. Nothing is written back: every tool is a SELECT. The
    Constitution's rule that the AI Production Engineer never writes
    production data is enforced here by there being no writing tool at all.

CONFIGURED BY ENVIRONMENT, NEVER BY THE DATABASE OR THE REPOSITORY:
    ANTHROPIC_API_KEY   the key                     <- never logged or shown
    SMS2_AI_MODEL       default claude-opus-4-8
    SMS2_AI_EFFORT      low|medium|high|xhigh|max   default high
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .. import models as M
from ..core import plant_time
from .stage_config import STAGE_DEFS, stage_def_for

MODEL = (os.environ.get("SMS2_AI_MODEL") or "claude-opus-4-8").strip()
EFFORT = (os.environ.get("SMS2_AI_EFFORT") or "high").strip()
MAX_TOKENS = 8000
MAX_TURNS = 12          # tool round-trips before we stop and answer with what we have


def config() -> dict:
    """What is configured — WITHOUT the key.

    Safe to hand to the admin screen: reports whether the key is present,
    never what it is.
    """
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    try:
        import anthropic  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return {"enabled": bool(key) and installed,
            "key_set": bool(key),          # never the key itself
            "sdk_installed": installed,
            "model": MODEL,
            "effort": EFFORT}


# ═══════════════════════════════════════════════════════════════════════════
# READ-ONLY TOOLS OVER THE PLANT'S OWN DATA
#
# Every one of these is a SELECT. There is deliberately no tool that writes,
# approves, deletes or unlocks — the model cannot alter production data
# because nothing here is capable of altering production data.
# ═══════════════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "get_heat",
        "description": (
            "Everything recorded for ONE heat: its route, every stage's process "
            "values, chemistry samples against the grade's aim, material additions "
            "and delays. Call this whenever the question names a heat number, or "
            "when diagnosing why a specific heat went wrong."),
        "input_schema": {
            "type": "object",
            "properties": {
                "heat_no": {"type": "string",
                            "description": "e.g. E263126 (EAF) or F263581 (ZPF)"}},
            "required": ["heat_no"],
        },
    },
    {
        "name": "find_heats",
        "description": (
            "Search heats and get a compact summary line for each. Use to answer "
            "'which heats…', to pick examples, or to gather a set before "
            "comparing. Returns at most 60 rows, newest plant day first."),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer",
                              "description": "Plant days back from today. Default 7."},
                "plant_day": {"type": "string", "description": "One exact day, YYYY-MM-DD."},
                "grade": {"type": "string", "description": "Grade code, e.g. Fe500D."},
                "shift": {"type": "string", "enum": ["A", "B", "C"]},
                "entry_stage": {"type": "string", "enum": ["eaf", "zpf"],
                                "description": "Which furnace the heat started in."},
                "route": {"type": "string",
                          "description": "VD-2 / VD-3 / BC-2 / BC-3"},
                "status": {"type": "string",
                           "description": "created|eaf|zpf|lrf|vd|caster|review|locked"},
                "with_delays_only": {"type": "boolean",
                                     "description": "Only heats that took a delay."},
            },
        },
    },
    {
        "name": "plant_summary",
        "description": (
            "Aggregate production over a window: heat count, tapped and billet "
            "tonnage, energy and oxygen per tonne, average tap-to-tap, total delay "
            "minutes, and the split by furnace, grade and shift. Use for trends, "
            "benchmarking and 'how did we do' questions."),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer", "description": "Default 7."},
                "group_by": {"type": "string",
                             "enum": ["shift", "grade", "entry_stage", "plant_day"],
                             "description": "Optional breakdown."},
            },
        },
    },
    {
        "name": "delay_analysis",
        "description": (
            "Delay pareto over a window — total minutes and occurrences by agency "
            "(category), by specific reason, and by stage. Use for 'what is costing "
            "us time' and for root-cause questions about lost production."),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer", "description": "Default 30."},
                "stage": {"type": "string",
                          "description": "Limit to one stage: eaf|zpf|lrf|vd|caster"},
            },
        },
    },
    {
        "name": "grade_spec",
        "description": (
            "The plant's target chemistry for a grade — min, aim and max per "
            "element, as set by the administrator. Use before judging whether a "
            "heat's chemistry was in spec; never assume a spec from memory."),
        "input_schema": {
            "type": "object",
            "properties": {"grade": {"type": "string",
                                     "description": "Grade code. Omit to list all grades."}},
        },
    },
    {
        "name": "stage_reference",
        "description": (
            "Which fields this plant actually records at a stage, with their units "
            "and which are mandatory. Use when teaching an operator what to enter, "
            "or to check whether a value the question mentions is even captured "
            "here."),
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string",
                          "description": "eaf|zpf|lrf|vd|caster|quality"},
                "entry_stage": {"type": "string", "enum": ["eaf", "zpf"],
                                "description": "The EAF form differs by entry furnace."},
            },
            "required": ["stage"],
        },
    },
]


def _day_window(days_back: int | None, default: int = 7):
    d1 = plant_time.now_moment().plant_day
    return d1 - timedelta(days=max(1, days_back or default) - 1), d1


def _heat_rows(db: Session, heats):
    out = []
    for h in heats:
        recs = {r.stage: (r.data or {}) for r in
                db.query(M.StageRecord).filter_by(heat_id=h.id)}
        eaf = recs.get(h.entry_stage) or {}
        cas = recs.get("caster") or {}
        out.append({
            "heat_no": h.heat_no,
            "plant_day": h.plant_day.isoformat() if h.plant_day else None,
            "shift": h.shift, "grade": h.grade.code if h.grade else None,
            "entry_stage": h.entry_stage, "status": h.status,
            "route_refining": h.route_refining, "route": h.route,
            "tap_temp": eaf.get("tap_temp"), "tap_weight": eaf.get("tap_weight"),
            "ttt_min": eaf.get("ttt"), "energy_kwh": eaf.get("energy_kwh"),
            "billet_t": cas.get("billet_weight"),
            "historical": bool(h.is_imported),
        })
    return out


def _run_tool(db: Session, name: str, args: dict) -> dict:
    """Dispatch one tool call. Read-only by construction."""
    live = (M.Heat.archived == False, M.Heat.is_training == False)   # noqa: E712

    if name == "get_heat":
        hn = str(args.get("heat_no", "")).strip().upper()
        h = db.query(M.Heat).filter(M.Heat.heat_no == hn).first()
        if not h:
            return {"error": f"No heat {hn} in this plant's records."}
        aim = {}
        if h.grade:
            aim = {s.element: {"min": s.min_pct, "aim": s.aim_pct, "max": s.max_pct}
                   for s in (h.grade.specs or [])}
        stages = {}
        for r in db.query(M.StageRecord).filter_by(heat_id=h.id):
            stages[r.stage] = {"status": r.status, "data": r.data or {},
                               "started_at": str(r.started_at or ""),
                               "submitted_at": str(r.submitted_at or "")}
        chem = [{"stage": c.stage, "label": c.sample_label, "values": c.values}
                for c in db.query(M.ChemistrySample)
                .filter_by(heat_id=h.id, superseded=False)]
        mats = [{"stage": m.stage, "material": m.material.name if m.material else "",
                 "qty": m.quantity, "unit": m.unit, "point": m.addition_point}
                for m in db.query(M.MaterialAddition)
                .filter_by(heat_id=h.id, superseded=False)]
        dels = []
        for d in db.query(M.Delay).filter_by(heat_id=h.id, superseded=False):
            dels.append({"stage": d.stage, "minutes": d.duration_min,
                         "agency": d.reason.category if d.reason else "",
                         "reason": d.reason.reason if d.reason else "",
                         "remarks": d.remarks})
        return {"heat_no": h.heat_no,
                "plant_day": h.plant_day.isoformat() if h.plant_day else None,
                "shift": h.shift, "grade": h.grade.code if h.grade else None,
                "grade_aim_chemistry": aim,
                "entry_stage": h.entry_stage, "status": h.status,
                "route_refining": h.route_refining, "route": h.route,
                # An imported heat predates this app's entry rules. Say so, so
                # the model does not fault an operator for a blank field that
                # was never on the paper log.
                "historical_import": bool(h.is_imported),
                "stages": stages, "chemistry_samples": chem,
                "material_additions": mats, "delays": dels}

    if name == "find_heats":
        q = db.query(M.Heat).filter(*live)
        if args.get("plant_day"):
            q = q.filter(M.Heat.plant_day == date.fromisoformat(args["plant_day"]))
        else:
            d0, d1 = _day_window(args.get("days_back"))
            q = q.filter(M.Heat.plant_day >= d0, M.Heat.plant_day <= d1)
        if args.get("shift"):
            q = q.filter(M.Heat.shift == args["shift"])
        if args.get("entry_stage"):
            q = q.filter(M.Heat.entry_stage == args["entry_stage"])
        if args.get("route"):
            q = q.filter(M.Heat.route == args["route"])
        if args.get("status"):
            q = q.filter(M.Heat.status == args["status"])
        if args.get("grade"):
            g = db.query(M.SteelGrade).filter(
                M.SteelGrade.code == args["grade"]).first()
            if not g:
                return {"error": f"No grade {args['grade']}. Call grade_spec with no "
                                 f"argument to list the grades this plant runs."}
            q = q.filter(M.Heat.grade_id == g.id)
        if args.get("with_delays_only"):
            # Filter BEFORE the limit. Applied after, this would mean "of the
            # 60 newest heats, the ones with delays" — which silently answers
            # a different question than the one asked.
            q = q.filter(M.Heat.id.in_(
                db.query(M.Delay.heat_id).filter(M.Delay.superseded == False)))  # noqa: E712
        heats = q.order_by(M.Heat.plant_day.desc(),
                           M.Heat.heat_no.desc()).limit(60).all()
        return {"count": len(heats), "heats": _heat_rows(db, heats)}

    if name == "plant_summary":
        d0, d1 = _day_window(args.get("days_back"))
        heats = db.query(M.Heat).filter(*live).filter(
            M.Heat.plant_day >= d0, M.Heat.plant_day <= d1).all()
        ids = {h.id for h in heats}
        agg = {"heats": len(heats), "tap_t": 0.0, "billet_t": 0.0,
               "energy_kwh": 0.0, "o2_nm3": 0.0, "ttt_sum": 0.0, "ttt_n": 0,
               "billet_n": 0}
        groups: dict[str, dict] = {}
        key_of = args.get("group_by")
        for h in heats:
            recs = {r.stage: (r.data or {}) for r in
                    db.query(M.StageRecord).filter_by(heat_id=h.id)}
            e = recs.get(h.entry_stage) or {}
            c = recs.get("caster") or {}
            tap = float(e.get("tap_weight") or 0)
            bil = float(c.get("billet_weight") or 0)
            agg["tap_t"] += tap
            agg["billet_t"] += bil
            if bil:
                agg["billet_n"] += 1
            agg["energy_kwh"] += float(e.get("energy_kwh") or 0)
            agg["o2_nm3"] += float(e.get("o2_consumption") or 0)
            if e.get("ttt"):
                agg["ttt_sum"] += float(e["ttt"]); agg["ttt_n"] += 1
            if key_of:
                k = {"shift": h.shift, "grade": h.grade.code if h.grade else "—",
                     "entry_stage": h.entry_stage,
                     "plant_day": h.plant_day.isoformat() if h.plant_day else "—"}[key_of]
                g = groups.setdefault(str(k), {"heats": 0, "tap_t": 0.0, "billet_t": 0.0})
                g["heats"] += 1; g["tap_t"] += tap; g["billet_t"] += bil
        delay_min = sum(float(d.duration_min or 0) for d in db.query(M.Delay)
                        .filter(M.Delay.superseded == False,          # noqa: E712
                                M.Delay.heat_id.in_(ids or {-1})))
        out = {"window": [d0.isoformat(), d1.isoformat()],
               "heats": agg["heats"],
               "tapped_t": round(agg["tap_t"], 1),
               "billet_t": round(agg["billet_t"], 1),
               # Yield is only meaningful when most heats actually recorded a
               # billet weight. The imported history rarely did, so computing
               # it regardless would report a ~0.5% yield with total
               # confidence. Below 60% coverage we return the coverage instead
               # of the number, and say why.
               "yield_pct": (round(100 * agg["billet_t"] / agg["tap_t"], 2)
                             if agg["tap_t"] and agg["heats"]
                             and agg["billet_n"] >= 0.6 * agg["heats"] else None),
               "billet_weight_recorded_on": f"{agg['billet_n']} of {agg['heats']} heats",
               "yield_note": (None if agg["billet_n"] >= 0.6 * agg["heats"]
                              else "Yield withheld: too few heats in this window "
                                   "recorded a billet weight to compute it honestly."),
               "energy_kwh": round(agg["energy_kwh"]),
               "kwh_per_t": round(agg["energy_kwh"] / agg["tap_t"], 1)
                            if agg["tap_t"] else None,
               "o2_nm3": round(agg["o2_nm3"]),
               "avg_ttt_min": round(agg["ttt_sum"] / agg["ttt_n"], 1)
                              if agg["ttt_n"] else None,
               "delay_min": round(delay_min, 1)}
        if key_of:
            out["by_" + key_of] = {k: {**v, "tap_t": round(v["tap_t"], 1),
                                       "billet_t": round(v["billet_t"], 1)}
                                   for k, v in sorted(groups.items())}
        return out

    if name == "delay_analysis":
        d0, d1 = _day_window(args.get("days_back"), default=30)
        ids = {h.id for h in db.query(M.Heat).filter(*live).filter(
            M.Heat.plant_day >= d0, M.Heat.plant_day <= d1)}
        by_agency: dict[str, list] = {}
        by_reason: dict[str, list] = {}
        by_stage: dict[str, list] = {}
        total = 0.0
        for d in db.query(M.Delay).filter(M.Delay.superseded == False,   # noqa: E712
                                          M.Delay.heat_id.in_(ids or {-1})):
            if args.get("stage") and d.stage != args["stage"]:
                continue
            mins = float(d.duration_min or 0)
            total += mins
            ag = d.reason.category if d.reason else "—"
            rs = f"{ag} — {d.reason.reason}" if d.reason else "—"
            for bucket, k in ((by_agency, ag), (by_reason, rs), (by_stage, d.stage)):
                cur = bucket.setdefault(k, [0.0, 0])
                cur[0] += mins; cur[1] += 1
        rank = lambda b: [{"key": k, "minutes": round(v[0], 1), "count": v[1]}   # noqa: E731
                          for k, v in sorted(b.items(), key=lambda x: -x[1][0])][:15]
        return {"window": [d0.isoformat(), d1.isoformat()],
                "total_delay_min": round(total, 1),
                "by_agency": rank(by_agency), "by_reason": rank(by_reason),
                "by_stage": rank(by_stage)}

    if name == "grade_spec":
        if not args.get("grade"):
            return {"grades": [g.code for g in db.query(M.SteelGrade)
                               .filter(M.SteelGrade.archived == False)]}   # noqa: E712
        g = db.query(M.SteelGrade).filter(
            M.SteelGrade.code == args["grade"]).first()
        if not g:
            return {"error": f"No grade {args['grade']} in this plant's masters."}
        return {"grade": g.code, "name": g.name,
                "spec": {s.element: {"min": s.min_pct, "aim": s.aim_pct,
                                     "max": s.max_pct} for s in (g.specs or [])}}

    if name == "stage_reference":
        st = str(args.get("stage", "")).strip().lower()
        if st not in STAGE_DEFS:
            return {"error": f"No stage {st}. Stages: {', '.join(STAGE_DEFS)}"}
        sd = stage_def_for(args.get("entry_stage") or "eaf", st)
        tiles = []
        for t in sd.get("tiles", []):
            fields = [{"key": f["key"], "label": f["label"],
                       "unit": f.get("unit", ""), "required": bool(f.get("req")),
                       "auto": bool(f.get("auto")), "options": f.get("options")}
                      for f in (t.get("fields") or [])]
            tiles.append({"tile": t.get("label"), "fields": fields,
                          "materials": bool(t.get("materials"))})
        return {"stage": st, "title": sd.get("title"), "tiles": tiles}

    return {"error": f"No such tool: {name}"}


# ═══════════════════════════════════════════════════════════════════════════
# THE PERSONA
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM = """\
You are the AI Production Engineer inside SMS-2 IDOS, the digital MES of a \
steel melting shop. You are talking to the people who run that shop — \
operators, shift engineers, shift incharges and the plant administrator.

THIS PLANT, SPECIFICALLY
- Two entry furnaces. A heat starts in EITHER the EAF (heat numbers E…) or \
the ZPF, the Zero Power Furnace (heat numbers F…). Never both.
- After melting: LRF-5 or LRF-6 for ladle refining, then a route decision \
that picks a vacuum degasser (VD-2 or VD-3) or goes direct to a \
caster (BC-2, BC-3). Ladle transfer cars move ladles between them.
- The plant day runs 22:00 to 22:00 and is opened by C shift. Shifts are A, \
B and C. A heat recorded at 23:41 belongs to the NEXT calendar day's plant day.
- Approval is a three-level chain and the audit trail is append-only. Nothing \
is ever deleted.

HOW TO ANSWER
- Use the tools. This plant's real heats are one call away, so answer from \
them rather than from general steelmaking knowledge. If a question names a \
heat, call get_heat before saying anything about it. If it asks about a \
grade's chemistry, call grade_spec — never recite a spec from memory, because \
this plant sets its own aims.
- Lead with the answer. Then the evidence, then the caveats. An engineer \
mid-shift is reading this on a plant terminal.
- Say what the data does not show. If a field is blank, say it is blank — do \
not infer a value and present it as recorded. Heats flagged historical_import \
came from the company's old workbook and were never subject to this app's \
entry rules, so a blank there is a gap in the old records, not an operator \
error.
- Quantities carry units. Temperatures in °C, weights in t, energy in kWh, \
time in minutes.
- You may be asked about steelmaking generally, or about something with \
nothing to do with steel. Answer either. You are not restricted to plant data \
— it is just that when plant data exists, it beats generalities.
- You are READ-ONLY by construction. You can explain what a change would do \
and where to make it, but you cannot record, approve or alter production \
data, and you must not imply otherwise."""

MODES = {
    "production_engineer": "Default. Diagnose, explain, recommend.",
    "metallurgist": "Lean on the chemistry and the physics — de-P, slag "
                    "practice, deoxidation, inclusion control.",
    "analyst": "Lean on the numbers — trends across heats and shifts, "
               "consumption, benchmarking, delay pareto.",
    "trainer": "Teach a new operator. Assume little, define terms, give the "
               "reason behind each step.",
}


def _client():
    import anthropic
    return anthropic.Anthropic()      # reads ANTHROPIC_API_KEY from the environment


def ask(db: Session, question: str, mode: str = "production_engineer",
        history: list | None = None) -> dict:
    """Answer a question with reasoning over this plant's data.

    Never raises. A broken integration must not read as a broken plant, so
    every failure returns a result the caller can fall back from.
    """
    cfg = config()
    if not cfg["enabled"]:
        return {"ok": False, "reason": "not_configured", "config": cfg}

    sys_blocks = [{"type": "text",
                   "text": SYSTEM + "\n\nMODE: " + MODES.get(mode, MODES["production_engineer"]),
                   # The persona and the tool list are byte-identical on every
                   # question, so they cache; only the question varies.
                   "cache_control": {"type": "ephemeral"}}]
    messages = list(history or [])
    messages.append({"role": "user", "content": question})

    tool_calls: list[str] = []
    try:
        client = _client()
        for _ in range(MAX_TURNS):
            r = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS,
                system=sys_blocks, tools=TOOLS, messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
            )
            if r.stop_reason == "refusal":
                return {"ok": False, "reason": "refused",
                        "answer": "That request was declined."}
            messages.append({"role": "assistant", "content": r.content})
            if r.stop_reason == "pause_turn":
                continue
            if r.stop_reason != "tool_use":
                break
            results = []
            for b in r.content:
                if b.type != "tool_use":
                    continue
                tool_calls.append(b.name)
                try:
                    out = _run_tool(db, b.name, dict(b.input or {}))
                    err = False
                except Exception as e:                      # noqa: BLE001
                    # Hand the failure back rather than aborting: the model can
                    # try a different tool, and a half-answer beats a stack trace.
                    out, err = {"error": f"{type(e).__name__}: {e}"}, True
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, default=str)[:60000],
                                "is_error": err})
            messages.append({"role": "user", "content": results})
        text = "\n".join(b.text for b in r.content if b.type == "text").strip()
        return {"ok": True, "answer": text or "(no answer produced)",
                "source": "claude", "model": r.model, "mode": mode,
                "tools_used": tool_calls,
                "usage": {"input": r.usage.input_tokens,
                          "output": r.usage.output_tokens,
                          "cache_read": getattr(r.usage, "cache_read_input_tokens", 0)}}
    except Exception as e:                                   # noqa: BLE001
        # Includes no network, bad key, rate limit, and the SDK being absent.
        # The message names the cause without ever echoing the key.
        return {"ok": False, "reason": "error",
                "detail": f"{type(e).__name__}: {e}"[:400]}
