"""
SMS-2 IDOS — VALIDATION ENGINE (deterministic)
===============================================
The BLOCKING validation required by 5.12 / 6H.8 / 6H.13 is implemented as
deterministic rules — always available, offline, and constitutionally
incapable of hallucination (7.10). Production is therefore never blocked
by an optional AI service failure (8.14, 11.16).

The AI Advisor (Section 7) is a separate, NON-blocking layer that consumes
this engine's structured findings to phrase explanations and suggestions.
Every finding carries: level (error|warning|info), tile, message, and —
per 7.10 — when information is missing the engine states exactly what is
required instead of guessing.
"""
from __future__ import annotations
from datetime import datetime
from .stage_config import STAGE_DEFS, stage_def_for
from ..core.plant_time import duration_minutes

TEMP_RANGE = (1400.0, 1800.0)        # plausible liquid steel window, °C
HM_TEMP_RANGE = (1150.0, 1550.0)     # hot metal arrival window, °C
MAX_HEAT_WEIGHT_T = 250.0
MAX_ENERGY_KWH = 120_000.0
MAX_O2_NM3 = 8_000.0


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def finding(level, tile, message):
    return {"level": level, "tile": tile, "message": message}


def validate_stage(stage: str, data: dict, chemistry: list[dict],
                   materials: list[dict], delays: list[dict],
                   grade_specs: dict | None = None,
                   entry_stage: str = "eaf") -> list[dict]:
    """Validate one stage record. Returns structured findings.
    `grade_specs`: {element: {min,aim,max}} for the heat's grade.
    `entry_stage`: which furnace melted the heat — the ZPF only shows (and so
    only requires) its Charge Mix for heats actually melted there."""
    cfg = stage_def_for(entry_stage, stage)
    out: list[dict] = []

    # 1 — Mandatory fields (5.12: Missing Fields)
    for tile in cfg["tiles"]:
        for f in tile.get("fields", []):
            if f["req"] and not f["auto"]:
                v = data.get(f["key"])
                if v in (None, "", []):
                    out.append(finding("error", tile["label"],
                                       f"Missing required field: {f['label']}."))
        # 2 — Chemistry presence (5.12: Missing Chemistry)
        chem = tile.get("chemistry")
        if chem and chem.get("required"):
            live = [c for c in chemistry if not c.get("superseded")]
            if not live:
                out.append(finding("error", tile["label"],
                                   f"Missing {chem['label']} chemistry sample. "
                                   f"Required elements: {', '.join(chem['elements'])}."))
            else:
                latest = live[-1]["values"]
                missing = [e for e in chem["elements"][:6] if latest.get(e) in (None, "")]
                if missing:
                    out.append(finding("warning", tile["label"],
                                       f"Chemistry sample incomplete — no value for: {', '.join(missing)}."))
                # 3 — Grade compliance (out-of-range highlighting, 6D.7 / 6G.6)
                if grade_specs:
                    for el, val in latest.items():
                        spec = grade_specs.get(el)
                        if spec and val not in (None, ""):
                            v = float(val)
                            if v < spec["min"] or v > spec["max"]:
                                out.append(finding(
                                    "warning", tile["label"],
                                    f"{el} = {v}% is outside grade limits "
                                    f"({spec['min']}–{spec['max']}%)."))

    # 4 — Chronology (6C.14: timings follow chronological order)
    time_pairs = {
        # The charge is now recorded as hot-metal charging + scrap rows, so
        # the furnace's own chronology runs charging -> power on -> tapping.
        "eaf": [("hm_charging", "power_on", "Power cannot be on before the charge goes in"),
                ("power_on", "power_off", "Power ON/OFF reversed"),
                ("power_on", "tap_start", "Tapping cannot begin before power on"),
                ("tap_start", "tap_end", "Tap start/end reversed"),
                ("o2_start", "o2_end", "Oxygen blowing start/end reversed")],
        "zpf": [("zpf_start", "zpf_end", "ZPF start/end reversed"),
                ("blow_on_time", "tap_time", "Blow-ON must precede tapping"),
                ("o2_start", "o2_end", "Oxygen start/end reversed")],
        "lrf": [("lrf_start", "lrf_end", "LRF start/end reversed"),
                ("power_on", "power_off", "Power ON/OFF reversed"),
                ("ar_start", "ar_end", "Argon start/end reversed")],
        "vd": [("vd_start", "vd_end", "VD start/end reversed"),
               ("vd_start", "vac_achieved", "Vacuum achieved before VD start"),
               ("ar_start", "ar_end", "Argon start/end reversed")],
        "caster": [("cast_start", "cast_end", "Casting start/end reversed"),
                   ("cut_start", "cut_end", "Cutting start/end reversed")],
        "quality": [], "review": [],
    }
    for a, b, msg in time_pairs.get(stage, []):
        da, db = _parse_dt(data.get(a)), _parse_dt(data.get(b))
        if da and db and db < da:
            out.append(finding("error", "Timings", f"{msg}: {a} → {b}."))

    # 5 — Plausibility (5.12: Invalid Temperatures / Invalid Weights)
    def rng(key, lo, hi, label, unit):
        v = data.get(key)
        if v not in (None, ""):
            try:
                v = float(v)
                if not (lo <= v <= hi):
                    out.append(finding("warning", "Plausibility",
                                       f"{label} = {v} {unit} is outside the expected "
                                       f"range {lo}–{hi} {unit}. Please verify."))
            except (TypeError, ValueError):
                out.append(finding("error", "Plausibility", f"{label} is not a number."))

    # Charge plausibility belongs to whichever furnace melted the heat.
    if stage in ("eaf", "zpf"):
        rng("hm_temp", *HM_TEMP_RANGE, "Hot metal temperature", "°C")
        rng("hm_weight", 1, MAX_HEAT_WEIGHT_T, "Hot metal weight", "t")
    if stage == "eaf":
        rng("bath_temp", *TEMP_RANGE, "Bath temperature", "°C")
        rng("tap_temp", *TEMP_RANGE, "Tap temperature", "°C")
        rng("tap_weight", 1, MAX_HEAT_WEIGHT_T, "Tap weight", "t")
        rng("energy_kwh", 1, MAX_ENERGY_KWH, "Energy consumption", "kWh")
        rng("o2_consumption", 0, MAX_O2_NM3, "Oxygen consumption", "Nm³")
    if stage in ("zpf", "lrf", "vd"):
        rng("temp_initial", *TEMP_RANGE, "Initial temperature", "°C")
        rng("temp_final", *TEMP_RANGE, "Final temperature", "°C")
    if stage == "caster":
        rng("tundish_temp", *TEMP_RANGE, "Tundish temperature", "°C")
        rng("billet_weight", 0.1, MAX_HEAT_WEIGHT_T, "Total billet weight", "t")

    # 6 — Delay completeness
    for d in delays:
        if d.get("superseded"):
            continue
        if not d.get("end_time"):
            out.append(finding("warning", "Delay Tracking",
                               "A delay record has no end time — close it before submission."))
        if not d.get("reason_id"):
            out.append(finding("error", "Delay Tracking",
                               "A delay record has no delay reason selected."))

    # 7 — Stage-specific business rules
    if stage == "quality":
        dec = data.get("decision")
        if dec == "Rejected" and not (data.get("decision_remarks") or "").strip():
            out.append(finding("error", "Quality Decision",
                               "Rejected heats require mandatory remarks (6G.13)."))
        if dec in ("Rejected", "Hold") and not data.get("defect_category"):
            out.append(finding("warning", "Non-Conformance",
                               "Consider recording non-conformance details for this decision."))
    if stage == entry_stage:   # the furnace that actually melted this heat
        live_scrap = [m for m in materials
                      if not m.get("superseded") and m.get("category") == "scrap"]
        if not live_scrap:
            out.append(finding("warning", "Scrap & Charge Materials",
                               "No scrap materials recorded. Confirm this heat is 100% hot metal."))
    return out


def auto_calculations(stage: str, data: dict, materials: list[dict]) -> dict:
    """Auto-calculated fields (6A.11, 6B.15, 6C.13, 6E.13, 6F.14).
    Deterministic. Returns only computed keys."""
    out = {}
    def dur(a, b, key):
        try:
            m = duration_minutes(_parse_dt(data.get(a)), _parse_dt(data.get(b)))
            if m is not None:
                out[key] = m
        except ValueError:
            pass  # chronology error already reported by validation

    # Charge mix is computed identically at BOTH primary furnaces — a heat is
    # melted in one of them, and the arithmetic of a charge does not change
    # with the unit that holds it. `materials` carries the charge additions
    # alongside flux/tapping ones, so the scrap total filters on category:
    # fluxes and ferroalloys are not part of the metallic charge.
    if stage in ("eaf", "zpf"):
        # Category alone decides this: on a melting furnace every scrap-class
        # material IS the charge, whatever point it was tagged with. Fluxes
        # and the ferroalloys thrown in at tapping are not metallic charge and
        # are excluded by category, so the totals stay honest either way.
        scrap = sum(float(m["quantity"]) for m in materials
                    if not m.get("superseded") and m.get("category") == "scrap")
        out["total_scrap"] = round(scrap, 2)
        hm = float(data.get("hm_weight") or 0)
        # DRI is CHARGED, so it is a material row, not a typed field. It used
        # to be both — a `dri_feed` box beside a DRI addition — which invited
        # the two to disagree about the same tonnes. Read it from the charge.
        dri = sum(float(m["quantity"]) for m in materials
                  if not m.get("superseded") and m.get("category") == "scrap"
                  and str(m.get("code") or "").upper() == "DRI")
        if not dri:                       # heats imported before the change
            dri = float(data.get("dri_feed") or 0)
        total = hm + scrap
        out["total_charge"] = round(total, 2)
        # Charge composition — computed, never typed (Never Guess, 7.10).
        # The logbook records HM% and DRI%; deriving them here means they can
        # never disagree with the weights they are supposed to summarise.
        if total > 0:
            out["hm_pct"] = round(hm / total * 100, 1)
            out["dri_pct"] = round(dri / total * 100, 1)
    if stage == "eaf":
        dur("power_on", "power_off", "power_on_duration")
        dur("tap_start", "tap_end", "tap_duration")
        # Cycle: power-on to tap, and the tap-to-tap time against the
        # PREVIOUS heat's tap — the rhythm the shop is actually measured on.
        dur("power_on", "tap_end", "pon_to_tap")
        dur("pre_heat_tap_time", "tap_end", "ttt")
    if stage == "zpf":
        dur("zpf_start", "zpf_end", "zpf_duration")
        dur("pre_heat_tap_time", "blow_on_time", "tap_to_blow_on")
        dur("blow_on_time", "tap_time", "bon_to_tap")
        dur("pre_heat_tap_time", "tap_time", "ttt")
        ti, tf = data.get("temp_initial"), data.get("temp_final")
        if ti not in (None, "") and tf not in (None, ""):
            out["temp_delta"] = round(float(tf) - float(ti), 1)
    if stage == "lrf":
        dur("power_on", "power_off", "power_on_time")
        dur("lrf_start", "lrf_end", "lrf_duration")
        dur("lrf_start", "lrf_end", "lrf_process_time")
        # Variance vs the standard: negative means the heat ran AHEAD of it.
        # Derived so it can never disagree with the times above.
        std = data.get("std_process_time")
        if std not in (None, "") and out.get("lrf_process_time") is not None:
            try:
                out["process_variance"] = round(out["lrf_process_time"] - float(std), 1)
            except (TypeError, ValueError):
                pass
    if stage == "vd":
        dur("vd_start", "vd_end", "vac_duration")
    if stage == "caster":
        dur("cast_start", "cast_end", "cast_duration")
        tgt = data.get("targeted_cast_time")
        if tgt not in (None, "") and out.get("cast_duration") is not None:
            try:
                out["cast_time_variance"] = round(out["cast_duration"] - float(tgt), 2)
            except (TypeError, ValueError):
                pass
        # Yield accounting: sum every loss stream the logbook tracks, then
        # express billet output as a percentage of the liquid metal it came
        # from. Both computed, so the yield figure is always consistent with
        # the weights beneath it.
        loss_keys = ("cut_reject", "tundish_skull", "sample_wt", "ladle_skull",
                     "slag_pit", "burn_off", "lm_returned", "scale_cut_loss")
        losses = sum(float(data.get(k) or 0) for k in loss_keys)
        if any(data.get(k) not in (None, "") for k in loss_keys):
            out["total_losses"] = round(losses, 2)
        billets = float(data.get("billet_weight") or 0)
        lm = float(data.get("lm_weight") or 0) or (billets + losses)
        if lm > 0 and billets > 0:
            out["yield_pct"] = round(billets / lm * 100, 1)
    return out
