"""
SMS-2 IDOS — MASTER DATA SEED
Bootstraps roles, users, grades (with target chemistry), materials (with
compositions & recoveries for the Calculation Engine), equipment, delay
reason codebook and the heat number series. Idempotent — safe to re-run.
"""
import os

from sqlalchemy.orm import Session
from . import models as M
from .core.security import hash_password

# ── SEED PASSWORDS ─────────────────────────────────────────────────────────
# The defaults below are convenience credentials for local development and are
# published in this repository, so they are PUBLIC. On any deployment that is
# reachable from the internet, set these environment variables to real secrets:
#
#     SMS2_ADMIN_PASSWORD   password for OWNER and ADMIN
#     SMS2_SEED_PASSWORD    password for the demo operator accounts
#
# They only apply when the database is first created. An existing database
# keeps whatever passwords it already has — change those in Admin → Users.
_ADMIN_ENV = (os.environ.get("SMS2_ADMIN_PASSWORD") or "").strip()
_USER_ENV = (os.environ.get("SMS2_SEED_PASSWORD") or "").strip()

# OWNER and ADMIN keep their DISTINCT published defaults when nothing is
# configured — collapsing them onto one value silently changed OWNER's
# password and broke the login. When the variable is set, both use it.
_OWNER_PW = _ADMIN_ENV or "owner@123"
_ADMIN_PW = _ADMIN_ENV or "admin@123"
_USER_PW = _USER_ENV or "steel@123"

# True while nothing has been configured, i.e. the published defaults are live.
SEED_PASSWORDS_ARE_DEFAULT = not (_ADMIN_ENV or _USER_ENV)

PERMS_ALL = ["production.entry", "production.approve.l1", "production.approve.l2",
             "production.approve.l3", "reports.view", "reports.export",
             "search", "admin", "knowledge.write", "heats.create", "heats.abort"]

ROLES = [
    ("Owner", PERMS_ALL, True),
    ("Super Administrator", PERMS_ALL, True),
    ("Administrator", PERMS_ALL, True),
    ("Shift Incharge", ["production.entry", "production.approve.l1", "production.approve.l3",
                         "reports.view", "reports.export", "search", "knowledge.write",
                         "heats.create", "heats.abort"], True),
    ("Shift Engineer", ["production.entry", "production.approve.l1", "production.approve.l2",
                         "reports.view", "reports.export", "search", "knowledge.write",
                         "heats.create", "heats.abort"], True),
    ("Operator", ["production.entry", "production.approve.l1", "reports.view", "search"], True),
    ("Quality Engineer", ["production.entry", "reports.view", "reports.export", "search",
                           "knowledge.write"], True),
    ("Laboratory Technician", ["production.entry", "reports.view", "search"], True),
    ("Production Manager", ["reports.view", "reports.export", "search", "knowledge.write"], True),
    ("Plant Head", ["reports.view", "reports.export", "search"], True),
    ("View Only", ["reports.view", "search"], True),
]

USERS = [  # employee_id, name, designation, department, role, password
    ("OWNER", "Plant Owner", "Owner", "Management", "Owner", _OWNER_PW),
    ("ADMIN", "System Administrator", "Administrator", "IT", "Administrator", _ADMIN_PW),
    ("SE001", "R. Sharma", "Shift Engineer", "SMS-2", "Shift Engineer", _USER_PW),
    ("SI001", "K. Patnaik", "Shift Incharge", "SMS-2", "Shift Incharge", _USER_PW),
    ("OP001", "M. Behera", "EAF Operator", "SMS-2", "Operator", _USER_PW),
    ("QE001", "S. Mishra", "Quality Engineer", "Quality", "Quality Engineer", _USER_PW),
    ("PM001", "A. Rao", "Production Manager", "SMS-2", "Production Manager", _USER_PW),
    ("VIEW01", "Guest Viewer", "Observer", "Management", "View Only", _USER_PW),
]

GRADES = {  # code: {element: (min, aim, max)}
    "Fe500D": {"C": (0.18, 0.22, 0.25), "Si": (0.15, 0.20, 0.30), "Mn": (0.60, 0.80, 1.00),
               "S": (0.0, 0.020, 0.030), "P": (0.0, 0.025, 0.035), "Ti": (0.0, 0.0, 0.01)},
    "Fe550D": {"C": (0.20, 0.24, 0.28), "Si": (0.15, 0.22, 0.30), "Mn": (0.80, 1.00, 1.20),
               "S": (0.0, 0.018, 0.025), "P": (0.0, 0.022, 0.030), "Ti": (0.0, 0.0, 0.01)},
    "SAE1018": {"C": (0.15, 0.18, 0.20), "Si": (0.10, 0.18, 0.30), "Mn": (0.60, 0.75, 0.90),
                "S": (0.0, 0.020, 0.035), "P": (0.0, 0.020, 0.030)},
    "SAE1541": {"C": (0.36, 0.40, 0.44), "Si": (0.15, 0.22, 0.30), "Mn": (1.35, 1.45, 1.65),
                "S": (0.0, 0.020, 0.035), "P": (0.0, 0.020, 0.030)},
    "EN8": {"C": (0.36, 0.40, 0.44), "Si": (0.10, 0.20, 0.40), "Mn": (0.60, 0.80, 1.00),
            "S": (0.0, 0.030, 0.050), "P": (0.0, 0.030, 0.050)},
    "20MnCr5": {"C": (0.17, 0.20, 0.22), "Si": (0.15, 0.25, 0.40), "Mn": (1.10, 1.25, 1.40),
                "Cr": (1.00, 1.15, 1.30), "S": (0.0, 0.025, 0.035), "P": (0.0, 0.020, 0.025)},
}

MATERIALS = [
    # code, name, category, unit, composition
    ("HSC", "Heavy Scrap", "scrap", "t", {}),
    ("LSC", "Light Scrap", "scrap", "t", {}),
    ("ASC", "Alloy Scrap", "scrap", "t", {}),
    ("SKL", "Return Skull", "scrap", "t", {}),
    ("PIG", "Pig Iron", "scrap", "t", {}),
    ("DRI", "DRI / HBI", "scrap", "t", {}),
    ("LIME", "Lime", "flux", "kg", {}),
    ("DOLO", "Dolomite", "flux", "kg", {}),
    ("FLSP", "Fluorspar", "flux", "kg", {}),
    ("CARB", "Carbon Injection", "flux", "kg", {"C": 98.0, "recovery": 85.0}),
    ("COKE", "Coke Breeze", "flux", "kg", {"C": 85.0, "recovery": 75.0}),
    ("SSLAG", "Synthetic Slag", "flux", "kg", {}),
    ("FESI", "FeSi (75%)", "alloy", "kg", {"Si": 75.0, "C": 0.1, "recovery": 92.0}),
    ("FEMNHC", "FeMn HC", "alloy", "kg", {"Mn": 70.0, "C": 7.0, "recovery": 93.0}),
    ("FEMNLC", "FeMn LC", "alloy", "kg", {"Mn": 80.0, "C": 0.5, "recovery": 93.0}),
    ("SIMN", "SiMn", "alloy", "kg", {"Mn": 62.0, "Si": 15.0, "C": 1.8, "recovery": 92.0}),
    ("FECR", "FeCr HC", "alloy", "kg", {"Cr": 62.0, "C": 8.0, "recovery": 95.0}),
    ("FECRLC", "FeCr LC", "alloy", "kg", {"Cr": 65.0, "C": 0.1, "recovery": 95.0}),
    ("FEMO", "FeMo", "alloy", "kg", {"Mo": 60.0, "recovery": 98.0}),
    ("FEV", "FeV", "alloy", "kg", {"V": 50.0, "recovery": 95.0}),
    ("FEAL", "FeAl", "alloy", "kg", {"Al": 40.0, "recovery": 85.0}),
    ("FETI", "FeTi", "alloy", "kg", {"Ti": 70.0, "recovery": 55.0}),
    ("ALB", "Aluminium Bar", "alloy", "kg", {"Al": 97.0, "recovery": 60.0}),
    ("NI", "Nickel Plate", "alloy", "kg", {"Ni": 99.0, "recovery": 98.0}),
    ("CU", "Copper", "alloy", "kg", {"Cu": 99.0, "recovery": 98.0}),
    ("CASI", "CaSi Wire", "wire", "m", {"Ca": 30.0, "Si": 60.0, "recovery": 12.0}),
    ("CWC", "Carbon Cored Wire", "wire", "m", {"C": 98.0, "recovery": 80.0}),
    ("CWS", "Sulphur Cored Wire", "wire", "m", {"S": 90.0, "recovery": 70.0}),
    ("CWTI", "FeTi Cored Wire", "wire", "m", {"Ti": 70.0, "recovery": 60.0}),

    # ── Additions from the plant logbook ──────────────────────────────────
    # These are the remaining columns of the shop-floor sheet. They are
    # MATERIALS, not form fields: the operator picks one and enters a
    # quantity, and the same material can be added at tapping (EAF) and again
    # at LRF — which is exactly what the "Tapping X" / "X" column pairs mean.
    # Anything missing can be added in Admin -> Masters without a code change.
    ("BUNDLE", "Bundle Scrap", "scrap", "t", {}),
    ("IRET", "Internal Return", "scrap", "t", {}),
    ("NISCR", "Nickel Scrap / Reformer Tube", "scrap", "t", {"Ni": 8.0, "recovery": 95.0}),
    ("FEMNMC", "FeMn MC", "alloy", "kg", {"Mn": 78.0, "C": 1.5, "recovery": 93.0}),
    ("FECRMC", "FeCr MC", "alloy", "kg", {"Cr": 64.0, "C": 2.0, "recovery": 95.0}),
    ("FECRLTI", "FeCr HC (Low Ti)", "alloy", "kg", {"Cr": 62.0, "C": 8.0, "recovery": 95.0}),
    ("ELCSIMN", "ELC SiMn", "alloy", "kg", {"Mn": 65.0, "Si": 17.0, "C": 0.1, "recovery": 92.0}),
    ("SIMET", "Silicon Metal", "alloy", "kg", {"Si": 98.0, "recovery": 90.0}),
    ("MNMET", "Manganese Metal", "alloy", "kg", {"Mn": 99.0, "recovery": 95.0}),
    ("FEB", "FeB", "alloy", "kg", {"B": 18.0, "recovery": 60.0}),
    ("FENB", "FeNb", "alloy", "kg", {"Nb": 65.0, "recovery": 95.0}),
    ("FEP", "Fe-P", "alloy", "kg", {"P": 25.0, "recovery": 90.0}),
    ("MOCA", "Mo-Calcium (Mo Metal)", "alloy", "kg", {"Mo": 60.0, "recovery": 95.0}),
    ("SULST", "Sulphur Stick", "alloy", "kg", {"S": 95.0, "recovery": 70.0}),
    ("FESIF", "FeSi Fines", "alloy", "kg", {"Si": 75.0, "recovery": 85.0}),
    ("ALSHOT", "Aluminium Shots", "alloy", "kg", {"Al": 97.0, "recovery": 55.0}),
    ("ALMIX", "Almix / Dolomite", "flux", "kg", {}),
    ("RADEX", "Radex", "flux", "kg", {}),
    ("LCC", "LCC", "flux", "kg", {}),
    ("GRF", "Graphite Fines", "flux", "kg", {"C": 95.0, "recovery": 75.0}),
    ("METCOKE", "Met Coke", "flux", "kg", {"C": 87.0, "recovery": 75.0}),
    ("HCOKE", "Injection / Hard Coke", "flux", "kg", {"C": 90.0, "recovery": 78.0}),
    ("PETCOKE", "Pet Coke / Dololime Top", "flux", "kg", {"C": 92.0, "recovery": 80.0}),
    ("SWIRE", "Sulphur Wire", "wire", "m", {"S": 90.0, "recovery": 70.0}),
    ("GRWIRE", "Graphite Wire", "wire", "m", {"C": 95.0, "recovery": 80.0}),
    ("ALWIRE", "Aluminium Wire", "wire", "m", {"Al": 97.0, "recovery": 65.0}),
    ("CAFEAL", "Ca-Fe-Al Wire 13 mm", "wire", "m", {"Ca": 20.0, "Al": 25.0, "recovery": 15.0}),
    ("CAWIRE9", "Ca Wire 9 mm", "wire", "m", {"Ca": 98.0, "recovery": 12.0}),
    ("PBWIRE", "Lead Wire", "wire", "m", {"Pb": 98.0, "recovery": 40.0}),
    ("NMNWIRE", "Nitrated Mn Wire", "wire", "m", {"Mn": 75.0, "N": 6.0, "recovery": 70.0}),
    ("FETIW", "FeTi Wire", "wire", "m", {"Ti": 70.0, "recovery": 60.0}),
]

EQUIPMENT = (
    [("BF-1", "blast_furnace"), ("BF-2", "blast_furnace"),
     ("EAF-1", "eaf"), ("ZPF-1", "zpf"), ("LRF-1", "lrf"), ("LRF-2", "lrf"),
     ("LRF-5", "lrf"), ("LRF-6", "lrf"), ("LTC-1", "ltc"), ("LTC-2", "ltc"),
     ("VD-2", "vd"), ("VD-3", "vd"), ("BC-2", "caster"), ("BC-3", "caster")]
    + [(f"LD-{i:02d}", "ladle") for i in range(1, 16)]
)

# The delay taxonomy is the plant's own, imported verbatim from the company's
# "Delay Codes for Reference.xlsx" and baked into delay_codes_data.py. It is
# organised stage → AGENCY → reason; the Delay dialog offers it as two
# cascading dropdowns. Regenerate with tools/gen_delay_codes.py.
from .delay_codes_data import DELAY_CODES  # (stage, agency, reason) rows


def seed(db: Session):
    for name, perms, sys in ROLES:
        if not db.query(M.Role).filter_by(name=name).first():
            db.add(M.Role(name=name, permissions=perms, is_system=sys,
                          description=f"{name} — system role"))
    db.flush()
    roles = {r.name: r for r in db.query(M.Role)}

    _issued = {}
    for emp, name, desig, dept, role, pwd in USERS:
        if not db.query(M.User).filter_by(employee_id=emp).first():
            _issued[emp] = pwd
            db.add(M.User(employee_id=emp, name=name, designation=desig,
                          department=dept, role_id=roles[role].id,
                          password_hash=hash_password(pwd)))
    # User Management Law: the ledger exists from the first boot
    if _issued:
        try:
            from .engines.credentials import rewrite
            db.flush(); rewrite(db, _issued)
        except Exception:
            pass

    if db.query(M.SteelGrade).count() == 0:
        for code, specs in GRADES.items():
            g = M.SteelGrade(code=code, name=code)
            db.add(g); db.flush()
            for el, (mn, aim, mx) in specs.items():
                db.add(M.GradeSpec(grade_id=g.id, element=el,
                                   min_pct=mn, aim_pct=aim, max_pct=mx))

    if db.query(M.Material).count() == 0:
        for code, name, cat, unit, comp in MATERIALS:
            db.add(M.Material(code=code, name=name, category=cat,
                              unit=unit, composition=comp))

    for code, kind in EQUIPMENT:
        if not db.query(M.Equipment).filter_by(code=code).first():
            db.add(M.Equipment(code=code, kind=kind))

    if db.query(M.DelayReason).count() == 0:
        for stage, agency, reason in DELAY_CODES:
            db.add(M.DelayReason(category=agency, reason=reason,
                                 stage=stage, department=agency))
        # PPC appears in the plant's own delay history but not in the codes
        # sheet, so it needs a home or those delays import without a reason.
        for st in ("eaf", "zpf", "lrf", "vd", "caster"):
            db.add(M.DelayReason(category="PPC", reason="Production planning",
                                 stage=st, department="PPC"))

    if db.query(M.HeatSeries).count() == 0:
        # One series per melting furnace: the plant numbers EAF heats E… and
        # ZPF heats F…, and the two never share a number.
        db.add(M.HeatSeries(prefix="E", next_number=260151, pad=6, unit="eaf"))
        db.add(M.HeatSeries(prefix="F", next_number=263581, pad=6, unit="zpf"))

    defaults = {
        "plant_name": "Steel Melting Shop — SMS-2",
        "plant_day_start": "22:00",
        "approval_chain": "Operator,Shift Engineer,Shift Incharge",
        "maintenance_mode": "off",
        "training_banner": "TRAINING MODE — records are excluded from production reports and organizational knowledge.",
    }
    for k, v in defaults.items():
        if not db.get(M.SystemSetting, k):
            db.add(M.SystemSetting(key=k, value=v, description=""))
    db.commit()
