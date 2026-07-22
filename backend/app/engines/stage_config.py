"""
SMS-2 IDOS — STAGE FRAMEWORK CONFIGURATION
===========================================
All production modules (Sections 6A–6H) expressed as configuration
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

# ── VACUUM DEGASSING ──────────────────────────────────────────────────────
# The plant runs FOUR tanks, two per degasser, and the route decision picks the
# tank itself rather than the degasser. Declared once and imported everywhere
# it is tested — the route law, the gates, the Gantt lanes, the equipment
# master and the pickers all read this tuple, so a fifth tank is one edit.
VD_ROUTES = ("VD-2.1", "VD-2.2", "VD-3.1", "VD-3.2")
CASTER_ROUTES = ("BC-2", "BC-3")
ALL_ROUTES = VD_ROUTES + CASTER_ROUTES


def is_vd_route(route: str | None) -> bool:
    """True when a route sends the heat through vacuum degassing.

    Kept as a function, not a `startswith` scattered through the code: the
    legacy values "VD-2"/"VD-3" still sit on heats recorded before the tanks
    were split, and they must keep answering True or those heats lose their
    VD stage.
    """
    return bool(route) and route.startswith("VD")


STAGE_DEFS = {
    # ── MODULE 1 — ELECTRIC ARC FURNACE (6A charge mix + 6B furnace) ──
    # Charging and melting are one operation on one machine, recorded on one
    # sheet — so they are one stage.
    "eaf": {
        "title": "Electric Arc Furnace",
        "icon": "⚡",
        "authorized": ["Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            # ── CHARGE MIX (merged in from the former Hot Metal & Scrap stage)
            # A plant charges hot metal and scrap INTO the furnace: charging is
            # the first operation of the EAF cycle, not a separate module. The
            # shop-floor sheet carries charge and EAF columns on ONE row, so
            # they now live in one stage. Chemistry below takes both samples —
            # record one labelled "Hot Metal" and one "EAF Bath".
            {"key": "general", "label": "General Information", "icon": "🟦", "fields": [
                F("blast_furnace", "Blast Furnace", "select", req=True, options=["BF-1", "BF-2"]),
                F("ladle_no", "Ladle Number", "select", req=True,
                  options=[str(i) for i in range(1, 16)]),
                F("furnace_campaign", "F/C — Furnace Campaign"),
                F("lead_seq", "Lead Sequence"),
            ]},
            {"key": "charge_mix", "label": "Charge Mix", "icon": "⚖", "fields": [
                F("hm_weight", "Hot Metal Weight", req=True, unit="t"),
                F("hm_temp", "Hot Metal Temperature", req=True, unit="°C"),
                F("hm_arrival", "Hot Metal Arrival", "time"),
                F("hm_charging", "Hot Metal Charging", "time", req=True),
                F("dri_feed", "DRI Feed", unit="t"),
                F("hot_heel", "Hot Heel", unit="t"),
                F("hm_remarks", "Charge Remarks", "textarea"),
            ]},
            {"key": "scrap", "label": "Scrap & Charge Materials", "icon": "🧲",
             "materials": {"category": "scrap", "point": "charge",
                           "label": "Charge Mix Additions"}},
            {"key": "charge_summary", "label": "Charge Summary", "icon": "📊", "fields": [
                F("total_scrap", "Total Scrap Weight", auto=True, unit="t"),
                F("total_charge", "Total Charge Weight", auto=True, unit="t"),
                F("hm_pct", "Hot Metal %", auto=True, unit="%"),
                F("dri_pct", "DRI %", auto=True, unit="%"),
                F("yield_pct", "Yield %", unit="%"),
            ]},
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
                F("mix_gas", "Mixed Gas", unit="Nm³"),
                F("tapping_oxygen", "Tapping Oxygen", unit="Nm³"),
            ]},
            {"key": "electrode", "label": "Electrode Consumption", "icon": "🕯", "fields": [
                F("electrode_kg", "Electrode Consumption", unit="kg"),
                # WHICH electrode was added (E1/E2/E3), not a weight — the
                # plant's sheet records the position, so this is text.
                F("electrode_addition", "Electrode Added", "text"),
                F("electrode_supplier", "Electrode Supplier"),
                F("electrode_break", "Electrode Break", "check"),
                F("electrode_joints", "Electrode Joint Count", step="1"),
                F("electrode_change", "Electrode Change", "check"),
                F("electrode_remarks", "Electrode Remarks", "textarea"),
            ]},
            {"key": "flux", "label": "Flux & Additions", "icon": "🧱",
             "materials": {"category": "flux", "label": "Flux & Carbon Additions"}},
            {"key": "chemistry", "label": "Chemistry — Hot Metal & Bath", "icon": "🧪",
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
            # Ferroalloys go into the LADLE as the furnace taps — recorded at
            # the furnace that tapped, not at the LRF downstream.
            {"key": "tapping_add", "label": "Tapping Additions", "icon": "🥄",
             "materials": {"category": ["alloy", "flux"], "point": "tapping",
                           "label": "Added during tapping"}},
            # Power-on/tap cycle — the heat-to-heat rhythm the shop is measured on.
            {"key": "cycle", "label": "Power & Tap Cycle", "icon": "⏲", "fields": [
                F("pre_heat_tap_time", "Previous Heat Tap Time", "time"),
                F("pon_to_tap", "Power-ON to Tap", auto=True, unit="min"),
                F("ttt", "Total Tap-to-Tap Time (TTT)", auto=True, unit="min"),
            ]},
            {"key": "ebt", "label": "Tapping Condition", "icon": "🕳", "fields": [
                F("ebt_free_opening", "EBT Free Opening", "select", options=["Yes", "No"]),
                F("ebt_filling_supplier", "EBT Filling Mass Supplier"),
            ]},
            {"key": "delays", "label": "Delay Tracking", "icon": "⏱", "delays": True},
        ],
    },

    # ── MODULE 3 — ZPF (6C) ───────────────────────────────────────────
    "zpf": {
        "title": "Zero Power Furnace",
        "icon": "🌪",
        "authorized": ["ZPF Operator", "Operator", "Shift Engineer", "Shift Incharge", "Administrator"],
        "tiles": [
            # ── CHARGE MIX — shown ONLY when the heat was MELTED in the ZPF.
            # The ZPF is a primary melting furnace in its own right: heats
            # numbered F… are charged and tapped here and never see the EAF.
            # For an EAF heat that comes here as the optional secondary-refining
            # step, these tiles are hidden — it already charged at the furnace.
            # See stage_def_for(); `charge` marks the tiles it filters.
            {"key": "general", "label": "General Information", "icon": "🟦", "charge": True, "fields": [
                F("blast_furnace", "Blast Furnace", "select", options=["BF-1", "BF-2"]),
                F("ladle_no", "Ladle Number", "select",
                  options=[str(i) for i in range(1, 16)]),
                F("lead_seq", "Lead Sequence"),
            ]},
            {"key": "charge_mix", "label": "Charge Mix", "icon": "⚖", "charge": True, "fields": [
                F("hm_weight", "Hot Metal Weight", req=True, unit="t"),
                F("hm_temp", "Hot Metal Temperature", unit="°C"),
                F("hm_arrival", "Hot Metal Arrival", "time"),
                F("hm_charging", "Hot Metal Charging", "time"),
                F("dri_feed", "DRI Feed", unit="t"),
                F("hot_heel", "Hot Heel", unit="t"),
                F("hm_remarks", "Charge Remarks", "textarea"),
            ]},
            {"key": "scrap", "label": "Scrap & Charge Materials", "icon": "🧲", "charge": True,
             "materials": {"category": "scrap", "point": "charge",
                           "label": "Charge Mix Additions"}},
            {"key": "charge_summary", "label": "Charge Summary", "icon": "📊", "charge": True, "fields": [
                F("total_scrap", "Total Scrap Weight", auto=True, unit="t"),
                F("total_charge", "Total Charge Weight", auto=True, unit="t"),
                F("hm_pct", "Hot Metal %", auto=True, unit="%"),
                F("dri_pct", "DRI %", auto=True, unit="%"),
                F("total_lm_wt", "Total Liquid Metal Weight", unit="t"),
                F("yield_pct", "Yield %", unit="%"),
            ]},
            {"key": "process", "label": "Process Operation", "icon": "🔥", "fields": [
                F("zpf_start", "ZPF Start Time", "time", req=True),
                F("zpf_end", "ZPF End Time", "time", req=True),
                F("zpf_duration", "Process Duration", auto=True, unit="min"),
                F("lance_used", "Oxygen Lance Used", "check"),
                F("slag_practice", "Slag Practice", "select",
                  options=["Single Slag", "Double Slag", "Slag Free"]),
                F("process_remarks", "Process Remarks", "textarea"),
            ]},
            # Blow-on/tap cycle — the heat-to-heat rhythm the shop is measured on.
            {"key": "cycle", "label": "Blow & Tap Cycle", "icon": "⏲", "fields": [
                F("pre_heat_tap_time", "Previous Heat Tap Time", "time"),
                F("blow_on_time", "Blow ON Time", "time"),
                F("tap_time", "Tap Time", "time"),
                F("tap_to_blow_on", "Tap to Blow-ON", auto=True, unit="min"),
                F("bon_to_tap", "Blow-ON to Tap", auto=True, unit="min"),
                F("ttt", "Total Tap-to-Tap Time (TTT)", auto=True, unit="min"),
                F("process_delay", "Process Delay", unit="min"),
            ]},
            {"key": "oxygen", "label": "Oxygen Blowing", "icon": "💨", "fields": [
                F("o2_start", "Oxygen Start Time", "time"),
                F("o2_end", "Oxygen End Time", "time"),
                F("o2_total", "Total Oxygen Consumption", req=True, unit="Nm³"),
                F("tapping_oxygen", "Tapping Oxygen", unit="Nm³"),
            ]},
            {"key": "utilities", "label": "Gases & Utilities", "icon": "🫧", "fields": [
                F("zpf_argon", "ZPF Argon", unit="Nm³"),
                F("lpg_gas", "LPG Gas", unit="kg"),
                F("compressed_air", "Compressed Air", unit="Nm³"),
            ]},
            {"key": "temperature", "label": "Temperature", "icon": "🌡", "fields": [
                F("temp_initial", "Initial Temperature", req=True, unit="°C"),
                F("temp_intermediate", "Intermediate Temperature", unit="°C"),
                F("temp_final", "Final Temperature", req=True, unit="°C"),
                F("temp_time", "Temperature Measurement Time", "time"),
                F("tap_temp", "Tapping Temperature", unit="°C"),
            ]},
            {"key": "chemistry", "label": "Chemistry", "icon": "🧪",
             "chemistry": {"label": "ZPF", "elements": CHEM_BASE, "required": True}},
            {"key": "flux", "label": "Flux & Additions", "icon": "🧱",
             "materials": {"category": "flux", "label": "Flux Additions"}},
            # Ferroalloys go into the LADLE as the furnace taps — recorded at
            # the furnace that tapped, not at the LRF downstream.
            {"key": "tapping_add", "label": "Tapping Additions", "icon": "🥄",
             "materials": {"category": ["alloy", "flux"], "point": "tapping",
                           "label": "Added during tapping"}},
            {"key": "ebt", "label": "Tapping Condition", "icon": "🕳", "fields": [
                F("ebt_free_opening", "EBT Free Opening", "select", options=["Yes", "No"]),
                F("ebt_filling_supplier", "EBT Filling Mass Supplier"),
            ]},
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
                F("lf_power", "LF Power", unit="kWh"),
                F("aux_power", "Auxiliary Power", unit="kWh"),
                F("argon_lrf", "Argon (LRF)", unit="Nm³"),
                F("lf_electrode_addition", "LF Electrode Addition", unit="kg"),
                F("lrf_start", "LRF Start Time", "time", req=True),
                F("lrf_end", "LRF End Time", "time", req=True),
                F("power_on", "Power ON", "time"),
                F("power_off", "Power OFF", "time"),
                F("power_on_time", "Total Power ON Time", auto=True, unit="min"),
                F("energy_kwh", "Energy Consumption", unit="kWh"),
                F("furnace_remarks", "Furnace Remarks", "textarea"),
            ]},
            # How the ladle arrived from the furnace, and how the heat ran
            # against its standard — the numbers the LRF is judged on.
            {"key": "ladle_in", "label": "Ladle Arrival & Condition", "icon": "🪣", "fields": [
                F("ladle_no", "Ladle Number", "select",
                  options=[str(i) for i in range(1, 16)]),
                F("eaf_tap_time", "Furnace Tap Time", "time"),
                F("liquidus", "Liquidus Temperature", unit="°C"),
                F("grade_type", "Grade Type"),
                F("free_board", "Free Board", unit="mm"),
                F("opening_p", "Opening %P", unit="%"),
            ]},
            {"key": "cycle_lrf", "label": "Process Timing", "icon": "⏲", "fields": [
                F("lrf_process_time", "LRF Process Time", auto=True, unit="min"),
                F("std_process_time", "Standard Process Time", unit="min"),
                F("process_variance", "Variance vs Standard", auto=True, unit="min"),
            ]},
            {"key": "crew_lrf", "label": "Crew", "icon": "👷", "fields": [
                F("lrf_engineer", "LRF Engineer"),
                F("shift_incharge", "Shift Incharge"),
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
                F("vd_tank", "VD Tank", "select", options=list(VD_ROUTES)),
                F("vd_start", "VD Start Time", "time", req=True),
                F("argon_vd", "Argon (VD)", unit="Nm³"),
                F("nitrogen_vd", "Nitrogen (VD)", unit="Nm³"),
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
            # Hydrogen is the whole point of degassing — the before/after
            # Hydris readings are the proof the treatment worked.
            {"key": "hydrogen", "label": "Hydrogen & Steam", "icon": "💧", "fields": [
                F("hydris_before", "Hydris Reading — Before", unit="ppm"),
                F("hydris_after", "Hydris Reading — After", unit="ppm"),
                F("vd_steam", "Total Steam Consumption", unit="t"),
            ]},
            {"key": "crew_vd", "label": "Crew", "icon": "👷", "fields": [
                F("vd_engineer", "VD Engineer"),
            ]},
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
            # A caster runs SEQUENCES, not single heats: which sequence this
            # heat belongs to and where it sits in the ladle order.
            {"key": "sequence", "label": "Sequence & Turret", "icon": "🔗", "fields": [
                F("seq_no", "Sequence Number"),
                F("seq_length", "Sequence Position"),
                F("grade_type", "Grade Type"),
                F("ladle_on_turret", "Ladle on Turret Time", "time"),
                F("machine_prep_time", "Machine Preparation Time", unit="min"),
            ]},
            # Actual against target — how this heat cast versus how it should
            # have, which is what the caster's performance is judged on.
            {"key": "cast_perf", "label": "Casting Performance", "icon": "📈", "fields": [
                F("targeted_cast_time", "Targeted Casting Time", unit="min"),
                F("targeted_speed", "Targeted Casting Speed", unit="m/min"),
                F("cast_time_variance", "Variance vs Target", auto=True, unit="min"),
                F("superheat_1", "Superheat 1", unit="°C"),
                F("superheat_2", "Superheat 2", unit="°C"),
                F("superheat_3", "Superheat 3", unit="°C"),
                F("cast_delay_reason", "Casting Delay Reason"),
                F("shift_incharge", "Shift Incharge"),
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
                # 180 and 210 are the sections this plant actually casts
                # (BC2/BC3 "Section(MM)"); without them the real history
                # could not be recorded.
                F("billet_size", "Billet Size / Section", "select", req=True,
                  options=["100×100", "125×125", "150×150", "160×160",
                           "180×180", "210×210"]),
                F("billet_length", "Billet Length", req=True, unit="m"),
                F("billet_count", "Number of Billets", req=True, step="1"),
                F("billet_weight", "Total Billet Weight", req=True, unit="t"),
                F("ccm_no", "CCM (Caster No.)", "select", options=["BC-2", "BC-3"]),
                F("lm_weight", "Liquid Metal Weight", unit="t"),
                F("production_remarks", "Production Remarks", "textarea"),
            ]},
            {"key": "cutting", "label": "Cutting Details", "icon": "✂", "fields": [
                F("cut_start", "Cutting Start Time", "time"),
                F("cut_end", "Cutting End Time", "time"),
                F("length_setting", "Billet Length Setting", unit="m"),
                F("crop_end", "Crop End Length", unit="mm"),
            ]},
            # YIELD ACCOUNTING — where the liquid metal went. Every stream the
            # plant logbook tracks, so the yield figure is auditable rather
            # than asserted: what was cast, what came back, what was lost.
            {"key": "yield", "label": "Yield Accounting", "icon": "📉", "fields": [
                F("cut_reject", "Front & End Cut / Billet Reject", unit="t"),
                F("tundish_skull", "Tundish Skull", unit="t"),
                F("sample_wt", "Sample Weight", unit="t"),
                F("ladle_skull", "Ladle Skull", unit="t"),
                F("slag_pit", "Slag Pit", unit="t"),
                F("burn_off", "B/O — Burn Off", unit="t"),
                F("lm_returned", "Liquid Metal Returned (Others)", unit="t"),
                F("scale_cut_loss", "Scale & Cut Loss", unit="t"),
                F("total_losses", "Total Accounted Losses", auto=True, unit="t"),
                F("yield_pct", "Yield %", auto=True, unit="%"),
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
                    has_zpf_data: bool = False,
                    entry_stage: str = "eaf") -> list[str]:
    """THE PROCESS ROUTE LAW: every heat follows its OWN route.
    A heat is melted in ONE primary furnace — the EAF or the ZPF — and only
    that one is on its route. For an EAF heat the ZPF remains the optional
    post-EAF refining step, included only when the route decision selected it
    (or legacy data already exists). VD is included ONLY on a VD route.
    Nothing is forced — two heats of the same grade may take different paths."""
    if entry_stage == "zpf":
        base = ["zpf"]
    else:
        base = ["eaf"]
        if refining == "ZPF" or has_zpf_data:
            base.append("zpf")
    base.append("lrf")
    if is_vd_route(route):
        base.append("vd")
    return base + ["caster", "quality", "review"]


REFINING_ROUTES = ("ZPF", "LRF-5", "LRF-6")


def stage_def_for(entry_stage: str, stage: str) -> dict:
    """The stage definition AS THIS HEAT SEES IT.

    The ZPF carries a Charge Mix because heats can be melted there — but only
    for heats that actually started there. An EAF heat arriving at the ZPF as
    the optional post-EAF refining step already charged at the furnace, so
    showing it a second charge section would invite the same tonnage to be
    entered twice. Tiles marked `charge` are dropped in that case.

    Anything that decides whether a field is REQUIRED must read the stage
    through this function, or validation will demand fields the operator was
    never shown.
    """
    cfg = STAGE_DEFS[stage]
    if stage != "zpf" or (entry_stage or "eaf") == "zpf":
        return cfg
    return {**cfg, "tiles": [t for t in cfg["tiles"] if not t.get("charge")]}
