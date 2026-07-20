"""
SMS-2 IDOS — THE AI PRODUCTION ENGINEER (Constitution S.6)
===========================================================
Not a chatbot. A read-only production engineer whose job is to IMPROVE THE
STEEL PLANT: it analyzes, explains, recommends, predicts and teaches —
and NEVER modifies a single record (AI Restrictions, S.3/S.6).

v1 intelligence is fully deterministic — every statement is computed from
the plant's own permanent records, so it is constitutionally incapable of
hallucination and keeps working with zero internet/AI-service dependency
(S.7 Business Continuity). The SMS2_AI_PROVIDER seam remains for a future
local-LLM phrasing layer on top of these same computed facts.

Modes (S.7): production_engineer · consultant · trainer · management · analytical
"""
from __future__ import annotations
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .. import models as M
from ..core import plant_time

MODES = {
    "production_engineer": "AI Production Engineer",
    "consultant": "Digital Consultant",
    "trainer": "Digital Trainer",
    "management": "Management Consultant",
    "analytical": "Analytical Mode",
}


# ─────────────────────── data assembly ───────────────────────

def _window_stats(db: Session, d0, d1):
    """Aggregate production facts for [d0, d1) plant-day range."""
    heats = (db.query(M.Heat)
             .filter(M.Heat.plant_day >= d0, M.Heat.plant_day < d1,
                     M.Heat.is_training == False,       # noqa: E712
                     M.Heat.archived == False).all())   # noqa: E712
    st = {"heats": len(heats), "locked": 0, "tonnes": 0.0, "energy": 0.0,
          "billets": 0, "delay_min": 0.0, "vd_heats": 0,
          "per_shift": {"C": {"heats": 0, "tonnes": 0.0},
                        "A": {"heats": 0, "tonnes": 0.0},
                        "B": {"heats": 0, "tonnes": 0.0}},
          "machine_min": {}, "machine_delay": {}, "delay_cat": {},
          "delay_reason": {}, "mech_by_machine": {}}
    from ..routers.dashboard import _machine_for
    for h in heats:
        if h.status == "locked":
            st["locked"] += 1
        if (h.route or "").startswith("VD"):
            st["vd_heats"] += 1
        t = 0.0
        for r in db.query(M.StageRecord).filter_by(heat_id=h.id):
            d = r.data or {}
            if r.stage == "caster":
                t = float(d.get("billet_weight") or 0)
                st["tonnes"] += t
                st["billets"] += int(float(d.get("billet_count") or 0))
            if r.stage == "eaf":
                st["energy"] += float(d.get("energy_kwh") or 0)
            if r.started_at and r.submitted_at:
                mc = _machine_for(r.stage, h, d)
                if mc:
                    mins = (r.submitted_at - r.started_at).total_seconds() / 60.0
                    st["machine_min"][mc] = st["machine_min"].get(mc, 0.0) + mins
        if h.shift in st["per_shift"]:
            st["per_shift"][h.shift]["heats"] += 1
            st["per_shift"][h.shift]["tonnes"] += t
        for dl in db.query(M.Delay).filter_by(heat_id=h.id, superseded=False):
            mins = dl.duration_min or 0
            st["delay_min"] += mins
            cat = dl.reason.category if dl.reason else "Other"
            st["delay_cat"][cat] = st["delay_cat"].get(cat, 0.0) + mins
            reason = dl.reason.reason if dl.reason else (dl.remarks or "unspecified")
            st["delay_reason"][reason] = st["delay_reason"].get(reason, 0.0) + mins
            rec = db.query(M.StageRecord).filter_by(heat_id=h.id, stage=dl.stage).first()
            mc = _machine_for(dl.stage, h, (rec.data or {}) if rec else {})
            if mc:
                st["machine_delay"][mc] = st["machine_delay"].get(mc, 0.0) + mins
                if cat == "Mechanical":
                    st["mech_by_machine"][mc] = st["mech_by_machine"].get(mc, 0) + 1
    st["kwh_per_t"] = round(st["energy"] / st["tonnes"], 1) if st["tonnes"] else None
    return st


def _pct(new, old):
    if not old:
        return None
    return round((new - old) / old * 100.0, 1)


# ─────────────────────── insight computation ───────────────────────

def compute_insights(db: Session) -> list[dict]:
    """Deterministic alerts, recommendations, predictions and facts —
    phrased exactly the way the constitution's examples demand."""
    today = plant_time.now_moment().plant_day
    cur = _window_stats(db, today - timedelta(days=6), today + timedelta(days=1))
    prev = _window_stats(db, today - timedelta(days=13), today - timedelta(days=6))
    m30 = _window_stats(db, today - timedelta(days=29), today + timedelta(days=1))
    out: list[dict] = []

    def add(kind, severity, text, subject=""):
        out.append({"kind": kind, "severity": severity, "subject": subject, "text": text})

    # production trend
    dp = _pct(cur["tonnes"], prev["tonnes"])
    if dp is not None and dp <= -8:
        top = max(cur["delay_cat"], key=cur["delay_cat"].get) if cur["delay_cat"] else None
        add("alert", "high",
            f"Production has dropped {abs(dp)}% versus the previous 7 days "
            f"({cur['tonnes']:g} t vs {prev['tonnes']:g} t)."
            + (f" The largest delay contributor is {top} "
               f"({cur['delay_cat'][top]:g} min)." if top else ""), "Production")
    elif dp is not None and dp >= 8:
        add("fact", "normal",
            f"Production has increased {dp}% over the last 7 days "
            f"({cur['tonnes']:g} t vs {prev['tonnes']:g} t).", "Production")

    # per-machine utilization drops (the constitution's LRF-6 example)
    for mc, mins in cur["machine_min"].items():
        pm = prev["machine_min"].get(mc)
        ch = _pct(mins, pm)
        if ch is not None and ch <= -10:
            add("alert", "high",
                f"{mc} has experienced a {abs(ch)}% utilization drop over the "
                f"last 7 days ({mins:.0f} min vs {pm:.0f} min).", mc)
        elif ch is not None and ch >= 10:
            add("fact", "normal",
                f"{mc} productivity has increased by {ch}% over the last 7 days.", mc)

    # delay trend + worst machine (the VD-3 example)
    dd = _pct(cur["delay_min"], prev["delay_min"])
    if dd is not None and dd >= 15:
        add("alert", "high",
            f"Delays are increasing: {cur['delay_min']:.0f} min this week vs "
            f"{prev['delay_min']:.0f} min last week ({dd:+.0f}%).", "Delays")
    if m30["machine_delay"]:
        worst = max(m30["machine_delay"], key=m30["machine_delay"].get)
        add("fact", "normal",
            f"{worst} has experienced the highest delay rate this month "
            f"({m30['machine_delay'][worst]:.0f} min).", worst)

    # best shift (the Shift-C example)
    if m30["heats"]:
        best = max(m30["per_shift"], key=lambda s: m30["per_shift"][s]["tonnes"])
        b = m30["per_shift"][best]
        if b["heats"]:
            add("fact", "normal",
                f"Shift {best} has shown the best productivity over the last 30 days "
                f"({b['tonnes']:g} t across {b['heats']} heats).", f"Shift {best}")

    # bottleneck predictions: open delays + queue pressure
    now_at = plant_time.now_moment().at
    open_dl = (db.query(M.Delay).join(M.Heat, M.Delay.heat_id == M.Heat.id)
               .filter(M.Delay.end_time.is_(None), M.Delay.superseded == False,   # noqa
                       M.Heat.status.notin_(["locked", "aborted"])).all())
    from ..routers.dashboard import _machine_for
    for dl in open_dl:
        rec = db.query(M.StageRecord).filter_by(heat_id=dl.heat_id, stage=dl.stage).first()
        h = db.get(M.Heat, dl.heat_id)
        mc = _machine_for(dl.stage, h, (rec.data or {}) if rec else {})
        mins = (now_at - dl.start_time).total_seconds() / 60.0
        if mc and mins >= 15:
            add("prediction", "critical" if mins >= 45 else "high",
                f"{mc} may cause a bottleneck — an open "
                f"{dl.reason.category if dl.reason else 'delay'} has been running "
                f"{mins:.0f} minutes on Heat {h.heat_no}.", mc)

    # maintenance predictions: repeated mechanical delays on one machine
    for mc, cnt in m30["mech_by_machine"].items():
        if cnt >= 3:
            add("prediction", "high",
                f"{mc} may require maintenance — {cnt} mechanical delays recorded "
                f"in the last 30 days.", mc)

    # energy intelligence
    if cur["kwh_per_t"] and prev["kwh_per_t"]:
        de = _pct(cur["kwh_per_t"], prev["kwh_per_t"])
        if de is not None and de >= 5:
            add("recommendation", "high",
                f"Specific energy has risen {de}% to {cur['kwh_per_t']} kWh/t. "
                f"Review EAF power-on discipline, slag foaming practice and "
                f"charge mix.", "Energy")
        elif cur["kwh_per_t"]:
            add("fact", "normal",
                f"Specific energy this week: {cur['kwh_per_t']} kWh/t billet.", "Energy")

    # top recurring delay reason → recommendation
    if m30["delay_reason"]:
        r = max(m30["delay_reason"], key=m30["delay_reason"].get)
        add("recommendation", "normal",
            f"The most recurring delay this month is \u201c{r}\u201d "
            f"({m30['delay_reason'][r]:.0f} min). A focused countermeasure here "
            f"gives the largest single delay reduction.", "Delays")

    if not out:
        add("fact", "normal",
            "Insufficient production history in the window — the AI Production "
            "Engineer grows more intelligent with every recorded heat "
            "(Future Directive, S.6).", "Plant")
    return out


def push_ai_alerts(db: Session, insights: list[dict]):
    """High/critical insights become AI notifications (deduped per plant day).
    Read-only principle preserved: notifications are messages, not data changes."""
    pd = plant_time.now_moment().plant_day.isoformat()
    for ins in insights:
        if ins["severity"] not in ("high", "critical"):
            continue
        title = f"AI: {ins['text'][:90]}"
        exists = (db.query(M.Notification)
                  .filter(M.Notification.kind == "ai",
                          M.Notification.title == title,
                          M.Notification.at >= datetime.fromisoformat(pd + " 00:00:00")
                          - timedelta(days=1)).first())
        if not exists:
            db.add(M.Notification(kind="ai", priority=ins["severity"],
                                  title=title, body=ins["text"]))


# ─────────────────────── question answering ───────────────────────

_TRAINING = {
    "eaf": "The Electric Arc Furnace melts the charge (hot metal + scrap/DRI) using "
           "electrical energy through graphite electrodes, assisted by oxygen blowing "
           "for decarburization and chemical energy. Key disciplines: charge sequence, "
           "power profile, slag foaming for arc stability and energy efficiency, bath "
           "temperature and chemistry before tap, and strict tap chronology — tapping "
           "never begins before charging is complete. Records to watch: energy kWh, "
           "O2 Nm3, electrode consumption, tap temperature and weight.",
    "zpf": "The ZPF station conditions the melt after EAF: oxygen lancing, slag "
           "practice (single/double/slag-free), temperature management and an "
           "intermediate chemistry. It protects the LRF from carryover slag and "
           "over-oxidation. Watch: O2 consumption, temperature delta and delays.",
    "lrf": "The Ladle Refining Furnace is the decision-making center: arc reheating, "
           "ferro alloy trimming to grade aims using the cross-contribution "
           "calculator, flux and synthetic slag work, argon rinsing, wire feeding, "
           "and the process route decision (VD or direct casting). Chemistry versus "
           "grade window is the core discipline — every element inside min/max "
           "before release.",
    "vd": "Vacuum Degassing removes hydrogen and supports deep desulphurisation "
          "under vacuum with argon stirring. Chosen for grades with tight S/H "
          "limits. Watch: vacuum level and duration, argon flow, temperature loss, "
          "final chemistry.",
    "ltc": "Ladle Transfer Cars move full ladles between LRF/VD and the casters. "
           "Their availability directly gates caster sequence — an LTC delay "
           "becomes a casting delay within minutes.",
    "bc": "The Billet Caster solidifies steel into billets: tundish temperature "
          "discipline (superheat), casting speed vs section, strand condition, "
          "cutting length and crop control. Yield = billet weight / tap weight — "
          "the single best health number of the whole heat.",
    "caster": None,  # alias resolved below
    "quality": "Quality inspection covers laboratory chemistry against the grade "
               "window, dimensional checks, visual inspection (cracks, inclusions, "
               "scale) and the formal decision. Rejections require remarks and "
               "non-conformance analysis — that reasoning becomes organizational "
               "memory.",
    "reports": "Reports in SMS-2 are permanent knowledge assets: the daily report "
               "covers the 22:00→22:00 plant day per shift; the heat report is the "
               "complete Digital DNA. Every export is archived in the Report "
               "Library forever.",
    "delays": "Delay discipline: record start immediately, close with end time, pick "
              "the master reason, add remarks. Delays ≥60 min are treated as "
              "shutdowns. The Pareto of delay categories drives improvement focus.",
    "utilization": "Utilization = productive minutes on a machine / available window. "
                   "SMS-2 computes it from stage start/submit timestamps per machine "
                   "lane. Chase the largest idle gaps first — they are free capacity.",
}
_TRAINING["caster"] = _TRAINING["bc"]


def answer(db: Session, question: str, mode: str) -> dict:
    """Deterministic intent-matched answering over real plant data.
    Honest by construction: when the data cannot support an answer, the
    engineer says exactly that instead of guessing (7.10)."""
    q = (question or "").lower()
    today = plant_time.now_moment().plant_day
    cur = _window_stats(db, today - timedelta(days=6), today + timedelta(days=1))
    prev = _window_stats(db, today - timedelta(days=13), today - timedelta(days=6))
    m30 = _window_stats(db, today - timedelta(days=29), today + timedelta(days=1))
    mode = mode if mode in MODES else "production_engineer"
    persona = MODES[mode]
    parts: list[str] = []

    def facts_block():
        return (f"Last 7 plant days: {cur['heats']} heats, {cur['tonnes']:g} t billets, "
                f"{cur['delay_min']:.0f} delay min"
                + (f", {cur['kwh_per_t']} kWh/t." if cur['kwh_per_t'] else "."))

    # trainer mode / teaching intents
    for key, text in _TRAINING.items():
        if text and (f" {key}" in f" {q}" or q.strip() == key):
            if any(w in q for w in ["teach", "explain", "what is", "how does", "train", "learn"]) \
               or mode == "trainer":
                return {"mode": mode, "persona": "Digital Trainer",
                        "answer": text, "facts": [facts_block()]}

    if "production" in q and any(w in q for w in ["drop", "fall", "fell", "down", "decrease", "why"]):
        dp = _pct(cur["tonnes"], prev["tonnes"])
        if dp is None:
            parts.append("There is not enough history in the comparison window to "
                         "attribute a production change — I need at least two weeks "
                         "of recorded heats. I will not guess.")
        else:
            parts.append(f"Billet production is {cur['tonnes']:g} t this week vs "
                         f"{prev['tonnes']:g} t last week ({dp:+.0f}%).")
            if cur["delay_cat"]:
                top = sorted(cur["delay_cat"].items(), key=lambda x: -x[1])[:3]
                parts.append("Largest delay contributors this week: "
                             + "; ".join(f"{c} {v:.0f} min" for c, v in top) + ".")
            if cur["heats"] < prev["heats"]:
                parts.append(f"Heat count also fell: {cur['heats']} vs {prev['heats']}.")
    elif "delay" in q and any(w in q for w in ["increas", "why", "most", "highest", "occur"]):
        if not m30["delay_reason"]:
            parts.append("No delays are recorded in the last 30 days — nothing to analyze.")
        else:
            top = sorted(m30["delay_reason"].items(), key=lambda x: -x[1])[:5]
            parts.append("Top delay reasons over 30 days: "
                         + "; ".join(f"\u201c{r}\u201d {v:.0f} min" for r, v in top) + ".")
            if m30["machine_delay"]:
                wm = max(m30["machine_delay"], key=m30["machine_delay"].get)
                parts.append(f"{wm} carries the highest delay burden "
                             f"({m30['machine_delay'][wm]:.0f} min).")
    elif any(w in q for w in ["best shift", "which shift", "shift is", "productive shift"]):
        if not m30["heats"]:
            parts.append("No production history yet to compare shifts.")
        else:
            ranked = sorted(m30["per_shift"].items(), key=lambda x: -x[1]["tonnes"])
            parts.append("Shift ranking over 30 days: "
                         + "; ".join(f"Shift {s}: {v['tonnes']:g} t / {v['heats']} heats"
                                     for s, v in ranked) + ".")
    elif "underutil" in q or ("which machine" in q and "idle" in q) or "idle" in q:
        if not m30["machine_min"]:
            parts.append("No machine activity recorded yet in the window.")
        else:
            low = sorted(m30["machine_min"].items(), key=lambda x: x[1])[:3]
            parts.append("Lowest recorded productive minutes (30 days): "
                         + "; ".join(f"{mc} {mins:.0f} min" for mc, mins in low)
                         + ". These lanes hold the most spare capacity.")
    elif "maintenance" in q or "maintain" in q:
        cands = [(mc, c) for mc, c in m30["mech_by_machine"].items() if c >= 2]
        if cands:
            parts.append("Maintenance attention suggested for: "
                         + "; ".join(f"{mc} ({c} mechanical delays/30d)" for mc, c in
                                     sorted(cands, key=lambda x: -x[1])) + ".")
        else:
            parts.append("No machine shows a repeated mechanical-delay pattern in "
                         "the last 30 days — no maintenance prediction is justified "
                         "by the data.")
    elif "power" in q or "energy" in q or "kwh" in q:
        if cur["kwh_per_t"]:
            parts.append(f"Specific energy this week is {cur['kwh_per_t']} kWh/t"
                         + (f" (prev {prev['kwh_per_t']} kWh/t)." if prev['kwh_per_t'] else "."))
            parts.append("Levers, in impact order: minimize power-on with charge "
                         "readiness; foamy-slag discipline; hot-metal ratio and "
                         "charge density; avoid re-blows via first-sample accuracy; "
                         "tap-to-cast time to cut reheating at LRF.")
        else:
            parts.append("No cast tonnage in the window yet, so kWh/t cannot be "
                         "computed. I will not estimate it.")
    elif "improve" in q or "problem" in q or "today" in q or "yesterday" in q or "happening" in q:
        parts.append(facts_block())
        ins = compute_insights(db)
        highs = [i for i in ins if i["severity"] in ("high", "critical")][:4]
        if highs:
            parts.append("Current priorities: " + " ".join(i["text"] for i in highs))
        else:
            parts.append("No high-priority findings — production is flowing normally.")
        if "improve" in q and m30["delay_reason"]:
            r = max(m30["delay_reason"], key=m30["delay_reason"].get)
            parts.append(f"Single biggest improvement available: eliminate "
                         f"\u201c{r}\u201d ({m30['delay_reason'][r]:.0f} min/30d).")
    else:
        parts.append("I am the plant's AI Production Engineer — I analyze the "
                     "recorded data only, and I never guess. Ask me: why did "
                     "production drop · why are delays increasing · which shift is "
                     "best · which machine is underutilized · which machine needs "
                     "maintenance · how to save power · what are today's problems · "
                     "or ask me to explain EAF, ZPF, LRF, VD, LTC, BC, quality, "
                     "reports, delays or utilization.")
        parts.append(facts_block())

    prefix = {
        "production_engineer": "",
        "consultant": "Consultant view — ",
        "trainer": "Trainer view — ",
        "management": "Management summary — ",
        "analytical": "Analysis — ",
    }[mode]
    return {"mode": mode, "persona": persona,
            "answer": prefix + " ".join(parts), "facts": [facts_block()]}
