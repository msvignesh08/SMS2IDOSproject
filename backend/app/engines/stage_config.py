"""
SMS-2 IDOS — STAGE FRAMEWORK CONFIGURATION
===========================================
All eight production modules (Sections 6A–6H) expressed as configuration
consumed by ONE engine (Configuration over Coding — LAW-5; Consistent
Design — 3.10). Adding a field is a config edit, not a schema change.

Field types: text | number | time (datetime-local) | select | textarea | check
`req`: mandatory before submission (deterministic Validation Engine).
`auto`: computed by the engine — never typed by the operator.
Tiles group fields exactly as the paper log sheets group them.
"""

CHEM_BASE = ["C", "Si", "Mn", "S", "P", "Ti"]
CHEM_LRF = CHEM_BASE + ["Cr", "Ni", "Al", "Cu"]

def F(key, label, type="number", req=False, auto=False, options=None, unit="", step="any"):
    return {"key": key, "label": label, "type": type, "req": req,
            "auto": auto, "options": options or [], "unit": unit, "step": step}

STAGE_DEFS = {
    # ── MODULE 1 — HOT METAL & SCRAP (6A) ─────────────────────────────
    "hot_metal": {
        "title": "Hot Metal & Scrap",
        "icon": "🔥",
        "authorized": ["Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            {"key": "general", "label": "General Information", "icon": "🟦", "fields": [
                F("blast_furnace", "Blast Furnace", "select", req=True, options=["BF-1", "BF-2"]),
                F("ladle_no", "Ladle Number", "select", req=True,
                  options=[str(i) for i in range(1, 16)]),
            ]},
            {"key": "hot_metal", "label": "Hot Metal", "icon": "🫕", "fields": [
                F("hm_weight", "Hot Metal Weight", req=True, unit="t"),
                F("hm_temp", "Hot Metal Temperature", req=True, unit="°C"),
                F("hm_arrival", "Arrival Time", "time"),
                F("hm_charging", "Charging Time", "time", req=True),
                F("hm_remarks", "Remarks", "textarea"),
            ]},
            {"key": "chemistry", "label": "Chemistry", "icon": "🧪",
             "chemistry": {"label": "Hot Metal", "elements": CHEM_BASE, "required": True}},
            {"key": "scrap", "label": "Scrap Details", "icon": "🧲",
             "materials": {"category": "scrap", "label": "Scrap Charging"}},
            {"key": "summary", "label": "Charge Summary", "icon": "⚖", "fields": [
                F("total_scrap", "Total Scrap Weight", auto=True, unit="t"),
                F("total_charge", "Total Charge Weight", auto=True, unit="t"),
            ]},
            {"key": "remarks", "label": "Remarks", "icon": "📝", "fields": [
                F("stage_remarks", "Stage Remarks", "textarea"),
            ]},
        ],
    },

    # ── MODULE 2 — ELECTRIC ARC FURNACE (6B) ──────────────────────────
    "eaf": {
        "title": "Electric Arc Furnace",
        "icon": "⚡",
        "authorized": ["Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            {"key": "charging", "label": "Charging Information", "icon": "🟦", "fields": [
                F("charging_start", "Charging Start Time", "time", req=True),
                F("charging_end", "Charging End Time", "time", req=True),
                F("charging_remarks", "Charging Remarks", "textarea"),
            ]},
            {"key": "power", "label": "Power & Energy", "icon": "⚡", "fields": [
                F("power_on", "Power ON Time", "time", req=True),
                F("power_off", "Power OFF Time", "time", req=True),
                F("power_on_duration", "Total Power ON Duration", auto=True, unit="min"),
                F("energy_kwh", "Total Energy Consumption", req=True, unit="kWh"),
                F("power_remarks", "Furnace Power Remarks", "textarea"),
            ]},
            {"key": "oxygen", "label": "Oxygen & Utilities", "icon": "💨", "fields": [
                F("o2_consumption", "Oxygen Consumption", req=True, unit="Nm³"),
                F("o2_start", "Oxygen Blowing Start", "time"),
                F("o2_end", "Oxygen Blowing End", "time"),
                F("n2_consumption", "Nitrogen Consumption", unit="Nm³"),
                F("ar_consumption", "Argon Consumption", unit="Nm³"),
            ]},
            {"key": "electrode", "label": "Electrode Consumption", "icon": "🕯", "fields": [
                F("electrode_kg", "Electrode Consumption", unit="kg"),
                F("electrode_joints", "Electrode Joint Count", step="1"),
                F("electrode_change", "Electrode Change", "check"),
                F("electrode_remarks", "Electrode Remarks", "textarea"),
            ]},
            {"key": "flux", "label": "Flux & Additions", "icon": "🧱",
             "materials": {"category": "flux", "label": "Flux & Carbon Additions"}},
            {"key": "chemistry", "label": "Temperature & Chemistry", "icon": "🧪",
             "chemistry": {"label": "EAF Bath", "elements": CHEM_BASE, "required": True},
             "fields": [
                F("bath_temp", "Bath Temperature", req=True, unit="°C"),
                F("sampling_time", "Sampling Time", "time"),
             ]},
            {"key": "tapping", "label": "Tapping Information", "icon": "🚿", "fields": [
                F("tap_start", "Tap Start Time", "time", req=True),
                F("tap_end", "Tap End Time", "time", req=True),
                F("tap_temp", "Tap Temperature", req=True, unit="°C"),
                F("tap_weight", "Tap Weight", req=True, unit="t"),
                F("slag_condition", "Slag Condition", "select",
                  options=["Good", "Foamy", "Heavy", "Wet", "Dry"]),
                F("tap_remarks", "Tapping Remarks", "textarea"),
            ]},
            {"key": "delays", "label": "Delay Tracking", "icon": "⏱", "delays": True},
        ],
    },

    # ── MODULE 3 — ZPF (6C) ───────────────────────────────────────────
    "zpf": {
        "title": "ZPF",
        "icon": "🌪",
        "authorized": ["ZPF Operator", "Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            {"key": "process", "label": "Process Operation", "icon": "🔥", "fields": [
                F("zpf_start", "ZPF Start Time", "time", req=True),
                F("zpf_end", "ZPF End Time", "time", req=True),
                F("zpf_duration", "Process Duration", auto=True, unit="min"),
                F("lance_used", "Oxygen Lance Used", "check"),
                F("slag_practice", "Slag Practice", "select",
                  options=["Single Slag", "Double Slag", "Slag Free"]),
                F("process_remarks", "Process Remarks", "textarea"),
            ]},
            {"key": "oxygen", "label": "Oxygen Blowing", "icon": "💨", "fields": [
                F("o2_start", "Oxygen Start Time", "time"),
                F("o2_end", "Oxygen End Time", "time"),
                F("o2_total", "Total Oxygen Consumption", req=True, unit="Nm³"),
            ]},
            {"key": "temperature", "label": "Temperature", "icon": "🌡", "fields": [
                F("temp_initial", "Initial Temperature", req=True, unit="°C"),
                F("temp_intermediate", "Intermediate Temperature", unit="°C"),
                F("temp_final", "Final Temperature", req=True, unit="°C"),
                F("temp_time", "Temperature Measurement Time", "time"),
            ]},
            {"key": "chemistry", "label": "Chemistry", "icon": "🧪",
             "chemistry": {"label": "ZPF", "elements": CHEM_BASE, "required": True}},
            {"key": "flux", "label": "Flux & Additions", "icon": "🧱",
             "materials": {"category": "flux", "label": "Flux Additions"}},
            {"key": "delays", "label": "Delay Tracking", "icon": "⏱", "delays": True},
        ],
    },

    # ── MODULE 4 — LRF (6D): the decision-making center ──────────────
    "lrf": {
        "title": "Ladle Refining Furnace",
        "icon": "🧪",
        "authorized": ["LRF Operator", "Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            {"key": "operation", "label": "Furnace Operation", "icon": "⚡", "fields": [
                F("lrf_unit", "LRF Unit", "select", req=True, options=["LRF-5", "LRF-6"]),
                F("lrf_start", "LRF Start Time", "time", req=True),
                F("lrf_end", "LRF End Time", "time", req=True),
                F("power_on", "Power ON", "time"),
                F("power_off", "Power OFF", "time"),
                F("power_on_time", "Total Power ON Time", auto=True, unit="min"),
                F("energy_kwh", "Energy Consumption", unit="kWh"),
                F("furnace_remarks", "Furnace Remarks", "textarea"),
            ]},
            {"key": "chemistry", "label": "Chemistry (vs Target)", "icon": "🧪",
             "chemistry": {"label": "LRF", "elements": CHEM_LRF, "required": True, "show_target": True}},
            {"key": "calculator", "label": "Metallurgical Calculator", "icon": "🧮", "calculator": True},
            {"key": "alloys", "label": "Ferro Alloy Addition", "icon": "🧱",
             "materials": {"category": "alloy", "label": "Ferro Alloys"}},
            {"key": "flux", "label": "Flux Addition", "icon": "🪨",
             "materials": {"category": "flux", "label": "Flux"}},
            {"key": "wire", "label": "Wire Feeding", "icon": "🧵",
             "materials": {"category": "wire", "label": "Cored Wire / CaSi", "unit": "m"}},
            {"key": "argon", "label": "Argon Purging", "icon": "💨", "fields": [
                F("ar_start", "Argon Start", "time"),
                F("ar_end", "Argon End", "time"),
                F("ar_total", "Total Consumption", unit="Nm³"),
                F("ar_flow", "Flow Rate", unit="Nm³/h"),
            ]},
            {"key": "temperature", "label": "Temperature", "icon": "🌡", "fields": [
                F("temp_initial", "Initial Temperature", req=True, unit="°C"),
                F("temp_intermediate", "Intermediate Temperature", unit="°C"),
                F("temp_final", "Final Temperature", req=True, unit="°C"),
            ]},
            {"key": "route", "label": "Process Route Decision", "icon": "➡", "route": True},
            {"key": "delays", "label": "Delay Tracking", "icon": "⏱", "delays": True},
        ],
    },

    # ── MODULE 5 — VACUUM DEGASSING (6E) ─────────────────────────────
    "vd": {
        "title": "Vacuum Degassing",
        "icon": "🌀",
        "authorized": ["VD Operator", "Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "optional": True,   # only on VD routes
        "tiles": [
            {"key": "vacuum", "label": "Vacuum Operation", "icon": "🌀", "fields": [
                F("vd_start", "VD Start Time", "time", req=True),
                F("vac_achieved", "Vacuum Achieved Time", "time"),
                F("vd_end", "VD End Time", "time", req=True),
                F("vac_duration", "Total Vacuum Duration", auto=True, unit="min"),
                F("vac_level", "Vacuum Level", unit="mbar"),
                F("pump_status", "Pump Status", "select", options=["Normal", "Degraded", "Fault"]),
                F("process_remarks", "Process Remarks", "textarea"),
            ]},
            {"key": "argon", "label": "Argon Purging", "icon": "💨", "fields": [
                F("ar_start", "Argon Start Time", "time"),
                F("ar_end", "Argon End Time", "time"),
                F("ar_total", "Total Argon Consumption", unit="Nm³"),
                F("ar_flow", "Flow Rate", unit="Nm³/h"),
            ]},
            {"key": "wire", "label": "Wire Feeding", "icon": "🧵",
             "materials": {"category": "wire", "label": "Wire Feeding", "unit": "m"}},
            {"key": "temperature", "label": "Temperature", "icon": "🌡", "fields": [
                F("temp_initial", "Initial Temperature", req=True, unit="°C"),
                F("temp_intermediate", "Intermediate Temperature", unit="°C"),
                F("temp_final", "Final Temperature", req=True, unit="°C"),
            ]},
            {"key": "chemistry", "label": "Chemistry", "icon": "🧪",
             "chemistry": {"label": "VD", "elements": CHEM_LRF, "required": True, "show_target": True}},
            {"key": "delays", "label": "Delay Tracking", "icon": "⏱", "delays": True},
        ],
    },

    # ── MODULE 6 — BILLET CASTER (6F) — serves BC-2 and BC-3 ─────────
    "caster": {
        "title": "Billet Caster",
        "icon": "🏭",
        "authorized": ["Billet Caster Operator", "Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            {"key": "process", "label": "Casting Process", "icon": "🔥", "fields": [
                F("ltc_no", "Ladle Transfer Car", "select", options=["LTC-1", "LTC-2"]),
                F("cast_start", "Casting Start Time", "time", req=True),
                F("cast_end", "Casting End Time", "time", req=True),
                F("cast_duration", "Total Casting Time", auto=True, unit="min"),
                F("cast_speed", "Casting Speed", unit="m/min"),
                F("cast_remarks", "Casting Remarks", "textarea"),
            ]},
            {"key": "tundish", "label": "Tundish", "icon": "🫙", "fields": [
                F("tundish_no", "Tundish Number", "text"),
                F("tundish_temp", "Tundish Temperature", req=True, unit="°C"),
                F("tundish_weight", "Tundish Weight", unit="t"),
                F("tundish_change", "Tundish Change", "check"),
            ]},
            {"key": "strand", "label": "Strand Information", "icon": "⚙", "fields": [
                F("strand_count", "Active Strand Count", req=True, step="1"),
                F("strand_status", "Strand Status", "select",
                  options=["All Running", "Partial", "Restarted", "Breakout"]),
                F("strand_remarks", "Strand Remarks", "textarea"),
            ]},
            {"key": "production", "label": "Billet Production", "icon": "🧱", "fields": [
                F("billet_size", "Billet Size", "select", req=True,
                  options=["100×100", "125×125", "150×150", "160×160"]),
                F("billet_length", "Billet Length", req=True, unit="m"),
                F("billet_count", "Number of Billets", req=True, step="1"),
                F("billet_weight", "Total Billet Weight", req=True, unit="t"),
                F("production_remarks", "Production Remarks", "textarea"),
            ]},
            {"key": "cutting", "label": "Cutting Details", "icon": "✂", "fields": [
                F("cut_start", "Cutting Start Time", "time"),
                F("cut_end", "Cutting End Time", "time"),
                F("length_setting", "Billet Length Setting", unit="m"),
                F("crop_end", "Crop End Length", unit="mm"),
            ]},
            {"key": "delays", "label": "Delay Tracking", "icon": "⏱", "delays": True},
        ],
    },

    # ── MODULE 7 — QUALITY (6G) ───────────────────────────────────────
    "quality": {
        "title": "Quality Inspection",
        "icon": "🔬",
        "authorized": ["Laboratory Technician", "Quality Engineer", "Shift Engineer",
                        "Shift Incharge", "Administrator"],
        "tiles": [
            {"key": "lab", "label": "Laboratory Chemistry", "icon": "🧪",
             "chemistry": {"label": "Lab", "elements": CHEM_LRF, "required": True, "show_target": True}},
            {"key": "dimensional", "label": "Dimensional Inspection", "icon": "📏", "fields": [
                F("billet_size", "Billet Size", "text"),
                F("billet_length", "Billet Length", unit="m"),
                F("surface_dimension", "Surface Dimension", "select",
                  options=["Within Tolerance", "Marginal", "Out of Tolerance"]),
                F("weight_verified", "Weight Verification", "check"),
                F("dim_remarks", "Dimensional Remarks", "textarea"),
            ]},
            {"key": "visual", "label": "Visual Inspection", "icon": "👀", "fields": [
                F("surface_crack", "Surface Crack", "check"),
                F("corner_crack", "Corner Crack", "check"),
                F("inclusion", "Inclusion", "check"),
                F("scale", "Scale", "check"),
                F("surface_finish", "Surface Finish", "select",
                  options=["Good", "Acceptable", "Poor"]),
                F("inspection_remarks", "Inspection Remarks", "textarea"),
            ]},
            {"key": "decision", "label": "Quality Decision", "icon": "📋", "fields": [
                F("decision", "Decision", "select", req=True,
                  options=["Accepted", "Accepted with Remarks", "Hold", "Rejected"]),
                F("decision_time", "Decision Time", "time", req=True),
                F("decision_remarks", "Remarks", "textarea"),
            ]},
            {"key": "ncr", "label": "Non-Conformance", "icon": "⚠", "fields": [
                F("ncr_no", "NCR Number", "text"),
                F("defect_category", "Defect Category", "select",
                  options=["", "Chemistry", "Surface", "Internal", "Dimensional", "Other"]),
                F("defect_description", "Defect Description", "textarea"),
                F("root_cause", "Root Cause", "textarea"),
                F("corrective_action", "Corrective Action", "textarea"),
                F("preventive_action", "Preventive Action", "textarea"),
                F("responsible_dept", "Responsible Department", "text"),
            ]},
        ],
    },

    # ── MODULE 8 — REVIEW & SUBMIT (6H) ──────────────────────────────
    "review": {
        "title": "Review & Submit",
        "icon": "✅",
        "authorized": ["Shift Engineer", "Shift Incharge", "Production Manager", "Administrator"],
        "review": True,
        "tiles": [],
    },
}

# Ordered mandatory route; VD inserted only when route requires it (5.8)
def required_stages(route: str | None, refining: str | None = None,
                    has_zpf_data: bool = False) -> list[str]:
    """THE PROCESS ROUTE LAW: every heat follows its OWN route.
    ZPF is included ONLY when the post-EAF decision selected it (or legacy
    data already exists). VD is included ONLY on a VD route. Nothing is
    forced — two heats of the same grade may take different paths."""
    base = ["hot_metal", "eaf"]
    if refining == "ZPF" or has_zpf_data:
        base.append("zpf")
    base.append("lrf")
    if route in ("VD-2", "VD-3"):
        base.append("vd")
    return base + ["caster", "quality", "review"]


REFINING_ROUTES = ("ZPF", "LRF-5", "LRF-6")
