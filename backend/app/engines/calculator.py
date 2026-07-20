"""
SMS-2 IDOS — METALLURGICAL CALCULATION ENGINE (4.15, 6D.8)
===========================================================
Pure, deterministic, UI/DB-independent. Replaces the Excel calculation
worksheets. Embedded in the LRF module AND exposed standalone for
training / planning / engineering analysis.

Physics:
  addition_kg = heat_weight_t * 1000 * (target% - current%) / 100
                ÷ (alloy_element% /100) ÷ (recovery% /100)

Cross-contribution: every alloy may carry several elements (e.g. FeSi
brings Si AND some C). After computing the primary requirement per
deficit element (greedy, largest deficit first), the engine credits the
side-contributions of already-selected additions before sizing the next,
mirroring the proven Heat_Calc/Trim_Calc two-stage worksheet logic.

Constitutional rule 7.10 — never guess: if any required input is missing,
the engine returns exactly which inputs are required instead of a number.
"""
from __future__ import annotations


def alloy_requirements(heat_weight_t, current: dict, target: dict,
                       alloys: list[dict]) -> dict:
    """
    current/target: {element: pct}
    alloys: [{code, name, composition: {element: pct, 'recovery': pct}, unit}]
    Returns {'ok': bool, 'missing': [...], 'lines': [...], 'predicted': {...}}
    """
    missing = []
    if heat_weight_t in (None, "", 0):
        missing.append("Heat weight (t)")
    deficits = {}
    for el, tgt in (target or {}).items():
        if tgt in (None, ""):
            continue
        if float(tgt) <= 0:
            # Residual-cap element (aim 0, max-only spec): additions are never
            # made toward zero, so its current value is not a required input.
            # Grade-limit policing of residuals belongs to the Validation Engine.
            continue
        cur = (current or {}).get(el)
        if cur in (None, ""):
            missing.append(f"Current {el} (%)")
            continue
        d = float(tgt) - float(cur)
        if d > 0.0005:
            deficits[el] = d
    if missing:
        return {"ok": False,
                "message": "Unable to perform calculations. Required information:",
                "missing": sorted(set(missing)), "lines": [], "predicted": {}}
    if not deficits:
        return {"ok": True, "lines": [], "predicted": dict(current or {}),
                "message": "All elements at or above target — no additions required."}

    hw_kg = float(heat_weight_t) * 1000.0
    # Pick, per element, the alloy with the highest content of that element
    chosen: dict[str, dict] = {}
    for el in deficits:
        best = None
        for a in alloys:
            comp = a.get("composition") or {}
            pct = comp.get(el)
            if pct and (best is None or pct > (best.get("composition") or {}).get(el, 0)):
                best = a
        if best:
            chosen[el] = best

    lines = []
    achieved = {el: float(v) for el, v in (current or {}).items() if v not in (None, "")}
    # Greedy: largest deficit first; credit cross-contributions as we go
    for el in sorted(deficits, key=lambda e: -deficits[e]):
        cur_now = achieved.get(el, float(current.get(el) or 0))
        tgt = float(target[el])
        remaining = tgt - cur_now
        if remaining <= 0.0005:
            continue
        alloy = chosen.get(el)
        if not alloy:
            lines.append({"element": el, "alloy": None, "qty_kg": None,
                          "note": f"No alloy in the Alloy Master carries {el}. "
                                  f"Human verification required."})
            continue
        comp = alloy["composition"]
        content = float(comp[el]) / 100.0
        recovery = float(comp.get("recovery", 95.0)) / 100.0
        qty = hw_kg * (remaining / 100.0) / content / recovery
        qty = round(qty, 1)
        lines.append({"element": el, "alloy": alloy["code"],
                      "alloy_name": alloy.get("name", alloy["code"]),
                      "qty_kg": qty,
                      "note": f"{alloy['code']}: {comp[el]}% {el}, "
                              f"{comp.get('recovery', 95.0)}% recovery"})
        # cross-contribution of this addition on every element it carries
        for other, pct in comp.items():
            if other == "recovery":
                continue
            delta = qty * (float(pct) / 100.0) * recovery / hw_kg * 100.0
            achieved[other] = round(achieved.get(other,
                                    float(current.get(other) or 0)) + delta, 4)

    return {"ok": True, "lines": lines,
            "predicted": {k: round(v, 4) for k, v in achieved.items()},
            "message": "Calculated values — review before acceptance (3.8 Human Authority)."}


def flux_recommendation(heat_weight_t, sulphur_pct=None) -> dict:
    """Simple lime/dolomite practice per tonne — plant-tunable via masters
    in future versions; declared as CALCULATED values (7.8)."""
    if heat_weight_t in (None, "", 0):
        return {"ok": False, "missing": ["Heat weight (t)"],
                "message": "Unable to perform calculations. Required information:"}
    hw = float(heat_weight_t)
    lime = round(hw * 6.0, 0)        # ~6 kg/t baseline
    dolo = round(hw * 2.5, 0)
    note = "Baseline slag practice: 6 kg/t lime, 2.5 kg/t dolomite."
    if sulphur_pct not in (None, "") and float(sulphur_pct) > 0.030:
        lime = round(lime * 1.3, 0)
        note += " Sulphur above 0.030% — lime increased 30% for desulphurisation."
    return {"ok": True, "lime_kg": lime, "dolomite_kg": dolo, "note": note}


def wire_recommendation(heat_weight_t, target_ca_ppm=25, wire_gpm=220.0,
                        recovery_pct=12.0) -> dict:
    """CaSi wire length for calcium treatment. wire_gpm = grams Ca per metre."""
    if heat_weight_t in (None, "", 0):
        return {"ok": False, "missing": ["Heat weight (t)"],
                "message": "Unable to perform calculations. Required information:"}
    hw_kg = float(heat_weight_t) * 1000.0
    ca_needed_g = hw_kg * float(target_ca_ppm) / 1000.0     # ppm → g
    metres = ca_needed_g / (float(wire_gpm) * float(recovery_pct) / 100.0)
    return {"ok": True, "metres": round(metres, 0),
            "note": f"Target {target_ca_ppm} ppm Ca, {wire_gpm} g/m wire, "
                    f"{recovery_pct}% recovery. Calculated value — verify feeding speed."}
