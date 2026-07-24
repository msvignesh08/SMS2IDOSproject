"""
SMS-2 IDOS — PRODUCTION ADVISOR
================================
Analyses this plant's own heats and produces findings with the evidence
attached: benchmarks, outliers, trends, correlations, and what to do about
them.

WHY THIS IS NOT A LANGUAGE MODEL
    The obvious way to build "an assistant that analyses data and advises"
    is to hand the numbers to a model and let it talk. For a plant that is
    the wrong trade. A small model — the kind that fits on plant hardware —
    cannot do reliable arithmetic and will state a tap temperature that was
    never recorded, in a confident sentence, to an engineer who may act on
    it. Fine-tuning changes the prose, not the arithmetic.

    So every number here is COMPUTED and every claim carries the heats it
    came from. The advisor cannot invent a figure because it never generates
    one. What it loses is conversational range; what it keeps is the right to
    be believed.

    engines/ai_brain.py remains the place for open-ended questions when a key
    is configured. This is the part that must be right whether or not anything
    else is available.

EVERY FINDING CARRIES
    severity   critical | warning | opportunity | good
    headline   what was found, in one line
    evidence   the numbers, and how many heats they rest on
    action     what to do, or None when the honest answer is "watch it"
    heats      the specific heat numbers, so it can be checked
"""
from __future__ import annotations

import statistics
from datetime import timedelta

from sqlalchemy.orm import Session

from .. import models as M
from ..core import plant_time

# A finding needs enough heats behind it to mean anything. Below this the
# advisor stays quiet rather than reading noise as a trend — one bad heat is
# an anecdote, not a pattern.
MIN_SAMPLE = 8
OUTLIER_SIGMA = 2.0


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _finding(severity, headline, evidence, action=None, heats=None, metric=None):
    return {"severity": severity, "headline": headline, "evidence": evidence,
            "action": action, "heats": (heats or [])[:12], "metric": metric}


def collect(db: Session, days: int = 30) -> dict:
    """Flatten the window into one row per heat, ready to analyse."""
    m = plant_time.now_moment()
    d0 = m.plant_day - timedelta(days=max(1, days) - 1)
    heats = (db.query(M.Heat)
             .filter(M.Heat.archived == False,             # noqa: E712
                     M.Heat.is_training == False,          # noqa: E712
                     M.Heat.plant_day >= d0,
                     M.Heat.plant_day <= m.plant_day).all())
    if not heats:
        return {"window": [d0.isoformat(), m.plant_day.isoformat()], "rows": [],
                "delays": []}
    ids = [h.id for h in heats]
    recs: dict[int, dict] = {i: {} for i in ids}
    for r in db.query(M.StageRecord).filter(M.StageRecord.heat_id.in_(ids)):
        recs[r.heat_id][r.stage] = r.data or {}
    delays = []
    for dl in db.query(M.Delay).filter(M.Delay.heat_id.in_(ids),
                                       M.Delay.superseded == False):   # noqa: E712
        delays.append({"heat_id": dl.heat_id, "stage": dl.stage,
                       "minutes": _f(dl.duration_min) or 0.0,
                       "agency": dl.reason.category if dl.reason else "—",
                       "reason": dl.reason.reason if dl.reason else (dl.remarks or "—")})

    rows = []
    for h in heats:
        d = recs.get(h.id, {})
        e = d.get(h.entry_stage) or d.get("eaf") or {}
        c = d.get("caster") or {}
        tap = _f(e.get("tap_weight"))
        rows.append({
            "heat_no": h.heat_no, "plant_day": h.plant_day, "shift": h.shift,
            "grade": h.grade.code if h.grade else None,
            "entry": h.entry_stage, "status": h.status,
            "tap_t": tap, "billet_t": _f(c.get("billet_weight")),
            "energy": _f(e.get("energy_kwh")), "o2": _f(e.get("o2_consumption")),
            "ttt": _f(e.get("ttt")), "tap_temp": _f(e.get("tap_temp")),
            "hm_pct": _f(e.get("hm_pct")),
            "kwh_t": (_f(e.get("energy_kwh")) / tap) if (tap and _f(e.get("energy_kwh"))) else None,
            "o2_t": (_f(e.get("o2_consumption")) / tap) if (tap and _f(e.get("o2_consumption"))) else None,
        })
    return {"window": [d0.isoformat(), m.plant_day.isoformat()],
            "rows": rows, "delays": delays,
            "heat_by_id": {h.id: h.heat_no for h in heats}}


def _stats(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    return {"n": len(vals), "mean": statistics.fmean(vals),
            "sd": statistics.pstdev(vals),
            "median": statistics.median(vals),
            "lo": min(vals), "hi": max(vals)}


def _outliers(rows, key, direction="high"):
    """Heats more than OUTLIER_SIGMA from the mean on one metric."""
    s = _stats([r[key] for r in rows])
    if not s or s["n"] < MIN_SAMPLE or s["sd"] == 0:
        return [], s
    lim = s["mean"] + OUTLIER_SIGMA * s["sd"] if direction == "high" \
        else s["mean"] - OUTLIER_SIGMA * s["sd"]
    hit = [r for r in rows if r[key] is not None
           and (r[key] > lim if direction == "high" else r[key] < lim)]
    hit.sort(key=lambda r: -r[key] if direction == "high" else r[key])
    return hit, s


# ═══════════════════════════════════════════════════════════════════════════
# THE ANALYSES
# ═══════════════════════════════════════════════════════════════════════════

def _energy(rows):
    out = []
    hit, s = _outliers(rows, "kwh_t", "high")
    if s and hit:
        excess = sum(r["kwh_t"] - s["mean"] for r in hit)
        tonnes = sum(r["tap_t"] or 0 for r in hit)
        out.append(_finding(
            "warning",
            f"{len(hit)} heats used markedly more power per tonne than the rest",
            f"Mean {s['mean']:.0f} kWh/t across {s['n']} heats (sd {s['sd']:.0f}). "
            f"These sat above {s['mean'] + OUTLIER_SIGMA * s['sd']:.0f} kWh/t, "
            f"peaking at {hit[0]['kwh_t']:.0f}. Roughly "
            f"{excess * (tonnes / max(len(hit), 1)):.0f} kWh above the norm in total.",
            "Check scrap mix, hot-metal ratio and power-on time on these heats. "
            "A cold or scrap-heavy charge is the usual cause.",
            [r["heat_no"] for r in hit], "kwh_t"))
    return out


def _hot_metal_correlation(rows):
    """More hot metal should mean less electricity. Check it holds here."""
    pairs = [(r["hm_pct"], r["kwh_t"]) for r in rows
             if r["hm_pct"] is not None and r["kwh_t"] is not None]
    if len(pairs) < MIN_SAMPLE:
        return []
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    try:
        r = statistics.correlation(xs, ys)
    except (statistics.StatisticsError, ValueError):
        return []
    hi = [y for x, y in pairs if x >= statistics.median(xs)]
    lo = [y for x, y in pairs if x < statistics.median(xs)]
    if not hi or not lo:
        return []
    gap = statistics.fmean(lo) - statistics.fmean(hi)
    if r < -0.3 and gap > 0:
        return [_finding(
            "good",
            "Hot metal is doing its job — more of it means less electricity",
            f"Across {len(pairs)} heats the correlation between HM% and kWh/t is "
            f"{r:.2f}. Heats above the median hot-metal ratio averaged "
            f"{statistics.fmean(hi):.0f} kWh/t against {statistics.fmean(lo):.0f} "
            f"for those below — {gap:.0f} kWh/t cheaper.",
            None, [], "hm_pct")]
    if r > -0.1:
        return [_finding(
            "warning",
            "Higher hot metal is NOT reducing power the way it should",
            f"Correlation between HM% and kWh/t is {r:.2f} across {len(pairs)} "
            f"heats; it should be clearly negative. Either the hot-metal "
            f"temperature is low, or the charge weights are not being recorded "
            f"accurately enough to see the effect.",
            "Check hot-metal arrival temperature and confirm charge weights are "
            "entered per heat rather than estimated.",
            [], "hm_pct")]
    return []


def _cycle_time(rows):
    out = []
    s = _stats([r["ttt"] for r in rows])
    if not s or s["n"] < MIN_SAMPLE:
        return out
    hit, _ = _outliers(rows, "ttt", "high")
    if hit:
        lost = sum(r["ttt"] - s["median"] for r in hit)
        out.append(_finding(
            "opportunity",
            f"{len(hit)} long cycles cost about {lost:.0f} minutes against the median",
            f"Median tap-to-tap {s['median']:.0f} min across {s['n']} heats "
            f"(range {s['lo']:.0f}–{s['hi']:.0f}). Recovering these to median "
            f"would free roughly {lost / 60:.1f} furnace-hours in this window.",
            "Look at the delay records on these specific heats — a long cycle "
            "with no logged delay usually means the delay was never entered.",
            [r["heat_no"] for r in hit], "ttt"))
    return out


def _shifts(rows):
    by: dict[str, list] = {}
    for r in rows:
        if r["shift"]:
            by.setdefault(r["shift"], []).append(r)
    if len(by) < 2:
        return []
    scored = []
    for sh, rs in by.items():
        s = _stats([r["kwh_t"] for r in rs])
        if s and s["n"] >= MIN_SAMPLE:
            scored.append((sh, s["mean"], s["n"], len(rs)))
    if len(scored) < 2:
        return []
    scored.sort(key=lambda x: x[1])
    best, worst = scored[0], scored[-1]
    gap = worst[1] - best[1]
    if gap / max(best[1], 1) < 0.05:
        return [_finding(
            "good", "The three shifts are running within 5% of each other",
            f"Best {best[0]} at {best[1]:.0f} kWh/t, highest {worst[0]} at "
            f"{worst[1]:.0f}. A spread this small means practice is consistent "
            f"across crews.", None, [], "kwh_t")]
    return [_finding(
        "opportunity",
        f"Shift {worst[0]} uses {gap:.0f} kWh/t more power than shift {best[0]}",
        f"{best[0]}: {best[1]:.0f} kWh/t over {best[2]} heats. "
        f"{worst[0]}: {worst[1]:.0f} kWh/t over {worst[2]} heats. "
        f"A {gap / best[1] * 100:.0f}% difference between crews on the same plant.",
        f"Sit with {best[0]}'s crew on charge sequence and power profile, then "
        f"walk it through with {worst[0]}. Differences this size are usually "
        f"practice, not equipment.",
        [], "kwh_t")]


def _delays(data):
    delays = data["delays"]
    if not delays:
        return []
    names = data.get("heat_by_id", {})
    by_agency: dict[str, list] = {}
    by_reason: dict[str, list] = {}
    for d in delays:
        by_agency.setdefault(d["agency"], []).append(d)
        by_reason.setdefault(f"{d['agency']} — {d['reason']}", []).append(d)
    total = sum(d["minutes"] for d in delays)
    if total <= 0:
        return []
    out = []
    top = sorted(by_agency.items(), key=lambda x: -sum(d["minutes"] for d in x[1]))[0]
    mins = sum(d["minutes"] for d in top[1])
    out.append(_finding(
        "warning" if mins / total > 0.35 else "opportunity",
        f"{top[0]} accounts for {mins / total * 100:.0f}% of all delay minutes",
        f"{mins:.0f} of {total:.0f} minutes across {len(top[1])} stoppages "
        f"(average {mins / len(top[1]):.0f} min each).",
        f"Raise {top[0]} at the shift review with these numbers.",
        [names.get(d["heat_id"], "") for d in top[1]], "delay"))

    # The reason that recurs most often is usually the one worth fixing, even
    # when a single long shutdown outweighs it on total minutes.
    freq = sorted(by_reason.items(), key=lambda x: -len(x[1]))[0]
    if len(freq[1]) >= 5:
        fm = sum(d["minutes"] for d in freq[1])
        out.append(_finding(
            "opportunity",
            f"“{freq[0]}” happened {len(freq[1])} times — the most repeated stoppage",
            f"{fm:.0f} minutes total, averaging {fm / len(freq[1]):.0f} min each. "
            f"Repeated short stops are usually more recoverable than one long "
            f"shutdown of the same size.",
            "A recurring stoppage has a root cause that a single fix removes. "
            "Worth a maintenance investigation before the next campaign.",
            [names.get(d["heat_id"], "") for d in freq[1]], "delay"))
    return out


def _yield_check(rows):
    pairs = [(r["tap_t"], r["billet_t"], r["heat_no"]) for r in rows
             if r["tap_t"] and r["billet_t"]]
    if len(pairs) < MIN_SAMPLE:
        # Say so rather than staying silent: a missing metric IS a finding.
        recorded = sum(1 for r in rows if r["billet_t"])
        if rows and recorded < len(rows) * 0.5:
            return [_finding(
                "warning",
                "Yield cannot be judged — billet weight is missing on most heats",
                f"Only {recorded} of {len(rows)} heats in this window carry a "
                f"billet weight, so metallic yield cannot be computed honestly.",
                "Make billet weight a completed field at the caster. Without it "
                "the plant cannot see its own yield.",
                [], "yield")]
        return []
    ys = [(b / t) * 100 for t, b, _ in pairs]
    s = _stats(ys)
    low = [p[2] for p, y in zip(pairs, ys) if y < s["mean"] - OUTLIER_SIGMA * s["sd"]]
    if low:
        return [_finding(
            "warning", f"{len(low)} heats came in well below the usual yield",
            f"Mean yield {s['mean']:.1f}% across {s['n']} heats (sd {s['sd']:.1f}).",
            "Check for slag carryover, over-oxidation at tapping, or a "
            "mis-recorded tap weight on these heats.",
            low, "yield")]
    return [_finding("good", f"Yield is steady at {s['mean']:.1f}%",
                     f"Across {s['n']} heats, spread {s['lo']:.1f}–{s['hi']:.1f}%.",
                     None, [], "yield")]


def advise(db: Session, days: int = 30) -> dict:
    """The full advisory pass."""
    data = collect(db, days)
    rows = data["rows"]
    if not rows:
        return {"window": data["window"], "heats": 0, "findings": [_finding(
            "good", "No heats recorded in this window",
            "Nothing to analyse. Widen the window or check the plant day.",
            None, [], None)]}

    findings = []
    findings += _energy(rows)
    findings += _hot_metal_correlation(rows)
    findings += _cycle_time(rows)
    findings += _shifts(rows)
    findings += _delays(data)
    findings += _yield_check(rows)

    rank = {"critical": 0, "warning": 1, "opportunity": 2, "good": 3}
    findings.sort(key=lambda f: rank.get(f["severity"], 9))

    def st(key):
        s = _stats([r[key] for r in rows])
        return {k: round(v, 1) for k, v in s.items()} if s else None

    return {"window": data["window"], "heats": len(rows),
            "findings": findings,
            "benchmarks": {"kwh_per_t": st("kwh_t"), "o2_per_t": st("o2_t"),
                           "ttt_min": st("ttt"), "tap_temp": st("tap_temp"),
                           "hm_pct": st("hm_pct")},
            "sample_floor": MIN_SAMPLE}
