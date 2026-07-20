"""
SMS-2 IDOS — DATABASE ARCHITECTURE
===================================
Heat = aggregate root. Everything hangs off heat_no (Section 5.1).

Spine tables (constitutional):
  heat_events  — append-only. Powers ALL timelines (11.9 Universal Timeline
                 Policy), Gantt, handover summaries, Digital DNA (8.6).
  audit_log    — append-only. Never deleted (11.8 / 10.12).

Universal No-Delete Policy (11.15): no production table is ever physically
deleted. Rows carry `archived` flags; corrections supersede, never erase.

Configurable-over-coded (LAW-5): chemistry samples, material additions and
delays are NORMALIZED shared tables driven by master data — new elements,
materials and delay reasons are configuration, not schema changes.

Future expandability: plant_id present from day one (single-valued in v1).
"""
from __future__ import annotations
from datetime import datetime, date
from sqlalchemy import (Column, Integer, String, Float, Boolean, Date, DateTime,
                        Text, ForeignKey, JSON, UniqueConstraint, Index)
from sqlalchemy.orm import relationship
from .core.database import Base


def now():
    return datetime.now().replace(microsecond=0)


# ───────────────────────────── GOVERNANCE ─────────────────────────────

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, default="")
    permissions = Column(JSON, default=list)   # list[str] permission codes
    is_system = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    employee_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    designation = Column(String, default="")
    email = Column(String, default="")                  # for optional email notifications
    notification_prefs = Column(JSON, nullable=True)    # null = preference engine not yet asked
    department = Column(String, default="")
    password_hash = Column(String, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    active = Column(Boolean, default=True)
    archived = Column(Boolean, default=False)  # never deleted (10.13)
    theme = Column(String, default="dark")     # light | dark | black
    created_at = Column(DateTime, default=now)
    role = relationship("Role")


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=now)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False)
    user = relationship("User")


class AuditLog(Base):
    """APPEND-ONLY. The application layer never updates or deletes rows."""
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user_name = Column(String, default="")     # denormalized: survives archival
    category = Column(String, index=True)      # auth|production|approval|admin|report|ai|system
    action = Column(String)
    heat_no = Column(String, index=True, nullable=True)
    detail = Column(Text, default="")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)
    value = Column(String)
    description = Column(String, default="")


# ───────────────────────────── MASTERS ─────────────────────────────

class SteelGrade(Base):
    __tablename__ = "steel_grades"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, default="")
    archived = Column(Boolean, default=False)
    specs = relationship("GradeSpec", cascade="all", lazy="joined")


class GradeSpec(Base):
    """Target chemistry per grade: min / aim / max per element."""
    __tablename__ = "grade_specs"
    id = Column(Integer, primary_key=True)
    grade_id = Column(Integer, ForeignKey("steel_grades.id"), nullable=False)
    element = Column(String, nullable=False)   # C, Si, Mn, S, P, Ti, Cr, Ni, Al, Cu…
    min_pct = Column(Float, default=0.0)
    aim_pct = Column(Float, default=0.0)
    max_pct = Column(Float, default=0.0)
    __table_args__ = (UniqueConstraint("grade_id", "element"),)


class Material(Base):
    """Scrap, fluxes, ferro alloys, wires, additives — one master."""
    __tablename__ = "materials"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)  # scrap|flux|alloy|wire|additive
    unit = Column(String, default="kg")
    composition = Column(JSON, default=dict)   # {"Si": 75.0, "recovery": 90.0}
    archived = Column(Boolean, default=False)


class Equipment(Base):
    __tablename__ = "equipment"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)  # BF-1, BF-2, VD-2, VD-3, BC-2, BC-3, LD-01…
    kind = Column(String, nullable=False)               # blast_furnace|vd|caster|ladle|eaf|zpf|lrf|ltc
    status = Column(String, default="available")        # available|maintenance|offline (Section-5 machine states)
    active = Column(Boolean, default=True)
    archived = Column(Boolean, default=False)


class DelayReason(Base):
    __tablename__ = "delay_reasons"
    id = Column(Integer, primary_key=True)
    category = Column(String, nullable=False)   # Mechanical, Electrical, Process, Crane, Material, Utility, Quality, Other
    reason = Column(String, nullable=False)
    department = Column(String, default="")
    archived = Column(Boolean, default=False)


class HeatSeries(Base):
    __tablename__ = "heat_series"
    id = Column(Integer, primary_key=True)
    prefix = Column(String, nullable=False, default="E")
    next_number = Column(Integer, nullable=False, default=260001)
    pad = Column(Integer, default=6)
    active = Column(Boolean, default=True)


# ───────────────────────────── HEAT (aggregate root) ─────────────────────────────

STAGES = ["hot_metal", "eaf", "zpf", "lrf", "vd", "caster", "quality", "review"]

class Heat(Base):
    __tablename__ = "heats"
    id = Column(Integer, primary_key=True)
    plant_id = Column(Integer, default=1)               # multi-plant future
    heat_no = Column(String, unique=True, nullable=False, index=True)
    grade_id = Column(Integer, ForeignKey("steel_grades.id"))
    status = Column(String, default="created", index=True)
    # created|hot_metal|eaf|zpf|lrf|route_selected|vd|caster|quality|review|locked|aborted
    route = Column(String, nullable=True)                # VD-2|VD-3|BC-2|BC-3
    route_refining = Column(String, nullable=True)   # ZPF | LRF-5 | LRF-6 (post-EAF decision)
    route_vd = Column(String, nullable=True)             # VD unit if VD route
    route_caster = Column(String, nullable=True)         # BC unit
    plant_day = Column(Date, index=True)                 # derived by Plant Date Engine
    shift = Column(String)                               # shift at creation
    shift_engineer = Column(String, default="")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=now)
    locked_at = Column(DateTime, nullable=True)
    is_training = Column(Boolean, default=False, index=True)  # Sandbox/Training Mode
    aborted = Column(Boolean, default=False)
    abort_reason = Column(Text, default="")
    archived = Column(Boolean, default=False)
    grade = relationship("SteelGrade", lazy="joined")


class StageRecord(Base):
    """One row per (heat, stage). Well-defined fields live in `data` (JSON,
    schema-validated by the Stage Framework config); this keeps all eight
    modules on ONE engine while remaining queryable through the normalized
    child tables below (chemistry, materials, delays) for analytics."""
    __tablename__ = "stage_records"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), nullable=False, index=True)
    stage = Column(String, nullable=False)
    status = Column(String, default="pending")   # pending|in_progress|submitted|approved
    data = Column(JSON, default=dict)
    plant_day = Column(Date)
    shift = Column(String)
    started_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    version = Column(Integer, default=1)         # optimistic locking
    updated_by_name = Column(String, default="")  # hierarchy engine: who saved last
    __table_args__ = (UniqueConstraint("heat_id", "stage"),
                      Index("ix_stage_status", "stage", "status"))


class ChemistrySample(Base):
    __tablename__ = "chemistry_samples"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), nullable=False, index=True)
    stage = Column(String, nullable=False)
    sample_label = Column(String, default="")    # e.g. "EAF Bath", "LRF Final", "Lab"
    sample_time = Column(DateTime, nullable=True)
    values = Column(JSON, default=dict)          # {"C": 0.18, "Si": 0.22, …}
    entered_by = Column(Integer, ForeignKey("users.id"))
    entered_at = Column(DateTime, default=now)
    superseded = Column(Boolean, default=False)  # corrections supersede, never delete


class MaterialAddition(Base):
    __tablename__ = "material_additions"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), nullable=False, index=True)
    stage = Column(String, nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String, default="kg")
    addition_time = Column(DateTime, nullable=True)
    remarks = Column(String, default="")
    superseded = Column(Boolean, default=False)
    material = relationship("Material", lazy="joined")


class Delay(Base):
    __tablename__ = "delays"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), nullable=False, index=True)
    stage = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    duration_min = Column(Float, nullable=True)  # auto-calculated
    reason_id = Column(Integer, ForeignKey("delay_reasons.id"))
    remarks = Column(String, default="")
    superseded = Column(Boolean, default=False)
    reason = relationship("DelayReason", lazy="joined")


class Approval(Base):
    __tablename__ = "approvals"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), nullable=False, index=True)
    scope = Column(String, nullable=False)       # stage name or "final"
    level = Column(Integer, default=1)           # 1 Operator, 2 Shift Engineer, 3 Shift Incharge, 4 Prod Manager
    role_name = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    user_name = Column(String, default="")
    decision = Column(String, default="approved")  # approved|rejected
    signature = Column(String, default="")         # digital signature string
    at = Column(DateTime, default=now)
    remarks = Column(String, default="")


class HeatEvent(Base):
    """APPEND-ONLY spine. Nothing exists without a timeline (11.9)."""
    __tablename__ = "heat_events"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), index=True, nullable=True)
    heat_no = Column(String, index=True, nullable=True)
    at = Column(DateTime, default=now, index=True)
    plant_day = Column(Date, index=True)
    shift = Column(String)
    kind = Column(String, index=True)  # created|stage_started|stage_submitted|approval|route_selected|validation|quality_decision|locked|unlocked|aborted|correction|note|system
    stage = Column(String, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_name = Column(String, default="")
    detail = Column(Text, default="")
    payload = Column(JSON, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now, index=True)
    kind = Column(String, default="production")  # production|approval|delay|quality|admin|system|validation
    priority = Column(String, default="normal")  # critical|high|normal
    title = Column(String, nullable=False)
    body = Column(Text, default="")
    heat_no = Column(String, nullable=True)
    audience_role = Column(String, nullable=True)  # None = everyone
    read_by = Column(JSON, default=list)           # list of user ids
    archived = Column(Boolean, default=False)


class ReportArchive(Base):
    """Digital Time Machine (11.14): generated reports preserved as
    immutable organizational knowledge assets (9.30)."""
    __tablename__ = "report_archive"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now, index=True)
    kind = Column(String, index=True)     # heat|daily|shift|delay|consumption
    ref = Column(String, index=True)      # heat_no or plant_day iso
    fmt = Column(String, default="xlsx")  # xlsx|html
    filename = Column(String)
    generated_by = Column(String, default="")


class KnowledgeNote(Base):
    """Knowledge Preservation Engine (Section 13): the WHY, not just the what.
    Engineers record practices, lessons, recommendations tied to heats/grades."""
    __tablename__ = "knowledge_notes"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=now)
    author = Column(String, default="")
    heat_no = Column(String, nullable=True, index=True)
    grade_code = Column(String, nullable=True, index=True)
    topic = Column(String, default="")     # e.g. "Why VD for this grade"
    body = Column(Text, nullable=False)
    tags = Column(String, default="")
    archived = Column(Boolean, default=False)


class RecordVersion(Base):
    """VERSION CONTROL DIRECTIVE (Constitution S.3): immutable snapshots.
    A version is written on every stage submission and every applied
    correction — Version-1 → Version-2 → ... without losing anything."""
    __tablename__ = "record_versions"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), index=True)
    heat_no = Column(String, index=True)
    scope = Column(String, nullable=False)         # stage name
    version_no = Column(Integer, nullable=False)
    kind = Column(String, default="submit")        # submit|correction
    data = Column(JSON, default=dict)              # full snapshot AFTER this version
    changes = Column(JSON, default=dict)           # {field: {"old":..., "new":...}} for corrections
    reason = Column(Text, default="")
    created_at = Column(DateTime, default=now)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_by_name = Column(String, default="")


class CorrectionRequest(Base):
    """CORRECTION SYSTEM (Constitution S.3): old value preserved →
    correction request → approval → new value → version → audit. Never
    old-value-delete-new-value."""
    __tablename__ = "correction_requests"
    id = Column(Integer, primary_key=True)
    heat_id = Column(Integer, ForeignKey("heats.id"), index=True)
    heat_no = Column(String, index=True)
    stage = Column(String, nullable=False)
    changes = Column(JSON, default=dict)           # {field: {"old":..., "new":...}}
    reason = Column(Text, nullable=False)
    status = Column(String, default="pending", index=True)  # pending|approved|rejected
    requested_by = Column(Integer, ForeignKey("users.id"))
    requested_by_name = Column(String, default="")
    requested_at = Column(DateTime, default=now)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_by_name = Column(String, default="")
    decided_at = Column(DateTime, nullable=True)
    decision_remarks = Column(Text, default="")
    applied_version = Column(Integer, nullable=True)


class PasswordResetRequest(Base):
    """FORGOT PASSWORD LAW: recovery is a REQUEST, never an automatic reset.
    Every request is reviewed by an administrator (Industrial Security Law)."""
    __tablename__ = "password_reset_requests"
    id = Column(Integer, primary_key=True)
    employee_id = Column(String, index=True, nullable=False)
    user_name = Column(String, default="")
    remarks = Column(Text, default="")
    status = Column(String, default="pending", index=True)   # pending|approved|rejected
    requested_at = Column(DateTime, default=now)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_by_name = Column(String, default="")
    decided_at = Column(DateTime, nullable=True)
    decision_remarks = Column(Text, default="")
    action = Column(String, default="")        # password_reset|account_disabled|rejected
