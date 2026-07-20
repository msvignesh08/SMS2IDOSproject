"""
SMS-2 IDOS — PLANT DATE & SHIFT ENGINE
=======================================
THE single source of truth for Plant Day and Shift logic.
No other component in SMS-2 may compute plant days or shifts independently.

CONSTITUTIONAL RULES (FROZEN — Sections 7.12, 8.11, 10.7):
  • Plant Day begins at 22:00 hours.
  • A Shift : 06:00 – 14:00
  • B Shift : 14:00 – 22:00
  • C Shift : 22:00 – 06:00   (crosses midnight)

CONVENTION (documented, standard steel-plant practice):
  Plant Day "D" runs from 22:00 on calendar day (D-1) until 21:59:59 on
  calendar day D. Therefore C Shift is the FIRST shift of every Plant Day.

  Example: 16-Jul 23:30  -> Plant Day 17-Jul, Shift C
           17-Jul 03:00  -> Plant Day 17-Jul, Shift C
           17-Jul 09:00  -> Plant Day 17-Jul, Shift A
           17-Jul 15:00  -> Plant Day 17-Jul, Shift B
           17-Jul 22:10  -> Plant Day 18-Jul, Shift C

All timestamps in SMS-2 are stored as naive local plant time (single-plant,
single-timezone deployment). Derived plant_day / shift values are persisted
on records for query speed, but this engine is the ONLY writer.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta

PLANT_DAY_START = time(22, 0)          # 22:00 — frozen by the constitution

SHIFTS = (
    ("C", time(22, 0), time(6, 0)),    # first shift of the plant day
    ("A", time(6, 0),  time(14, 0)),
    ("B", time(14, 0), time(22, 0)),
)

SHIFT_ORDER = {"C": 1, "A": 2, "B": 3}


@dataclass(frozen=True)
class PlantMoment:
    """A timestamp resolved into plant intelligence."""
    at: datetime
    plant_day: date
    shift: str
    shift_start: datetime
    shift_end: datetime

    @property
    def label(self) -> str:
        return f"{self.plant_day.isoformat()} / {self.shift} Shift"


def resolve(at: datetime) -> PlantMoment:
    """Resolve any timestamp into (plant_day, shift, shift window)."""
    t = at.time()
    d = at.date()

    if t >= PLANT_DAY_START:
        # 22:00–23:59  -> C shift of NEXT calendar day's plant day
        plant_day = d + timedelta(days=1)
        shift = "C"
        s_start = datetime.combine(d, time(22, 0))
        s_end = datetime.combine(d + timedelta(days=1), time(6, 0))
    elif t < time(6, 0):
        # 00:00–05:59  -> C shift, plant day = today
        plant_day = d
        shift = "C"
        s_start = datetime.combine(d - timedelta(days=1), time(22, 0))
        s_end = datetime.combine(d, time(6, 0))
    elif t < time(14, 0):
        plant_day = d
        shift = "A"
        s_start = datetime.combine(d, time(6, 0))
        s_end = datetime.combine(d, time(14, 0))
    else:
        plant_day = d
        shift = "B"
        s_start = datetime.combine(d, time(14, 0))
        s_end = datetime.combine(d, time(22, 0))

    return PlantMoment(at=at, plant_day=plant_day, shift=shift,
                       shift_start=s_start, shift_end=s_end)


def now_moment() -> PlantMoment:
    return resolve(datetime.now().replace(microsecond=0))


def plant_day_window(plant_day: date) -> tuple[datetime, datetime]:
    """Absolute time window [start, end) covered by a plant day."""
    start = datetime.combine(plant_day - timedelta(days=1), PLANT_DAY_START)
    end = datetime.combine(plant_day, PLANT_DAY_START)
    return start, end


def shift_window(plant_day: date, shift: str) -> tuple[datetime, datetime]:
    """Absolute time window of a given shift within a plant day."""
    prev = plant_day - timedelta(days=1)
    if shift == "C":
        return (datetime.combine(prev, time(22, 0)),
                datetime.combine(plant_day, time(6, 0)))
    if shift == "A":
        return (datetime.combine(plant_day, time(6, 0)),
                datetime.combine(plant_day, time(14, 0)))
    if shift == "B":
        return (datetime.combine(plant_day, time(14, 0)),
                datetime.combine(plant_day, time(22, 0)))
    raise ValueError(f"Unknown shift: {shift}")


def duration_minutes(start: datetime | None, end: datetime | None) -> float | None:
    """Chronology-aware duration. Handles midnight / plant-day crossings
    because all math is on absolute datetimes. Returns None if incomplete,
    raises ValueError on reversed chronology (validation engine surfaces it)."""
    if not start or not end:
        return None
    delta = (end - start).total_seconds() / 60.0
    if delta < 0:
        raise ValueError("End time precedes start time")
    return round(delta, 1)


def shift_handover_pairs(moment: PlantMoment) -> tuple[str, str]:
    """(outgoing_shift, incoming_shift) relative to a moment — used by
    handover summaries."""
    order = ["C", "A", "B"]
    idx = order.index(moment.shift)
    return order[idx - 1], moment.shift
