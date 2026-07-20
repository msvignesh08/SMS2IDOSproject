"""Plant Date & Shift Engine — constitutional test suite.
Run: python -m pytest backend/tests/test_plant_time.py  (or plain python)"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, date
from app.core.plant_time import resolve, plant_day_window, shift_window, duration_minutes

CASES = [
    # (timestamp, expected plant_day, expected shift)
    (datetime(2026, 7, 16, 23, 30), date(2026, 7, 17), "C"),  # after 22:00 -> next plant day
    (datetime(2026, 7, 16, 22, 0),  date(2026, 7, 17), "C"),  # exactly 22:00 boundary
    (datetime(2026, 7, 17, 3, 0),   date(2026, 7, 17), "C"),  # small hours
    (datetime(2026, 7, 17, 5, 59),  date(2026, 7, 17), "C"),
    (datetime(2026, 7, 17, 6, 0),   date(2026, 7, 17), "A"),  # A boundary
    (datetime(2026, 7, 17, 9, 0),   date(2026, 7, 17), "A"),
    (datetime(2026, 7, 17, 13, 59), date(2026, 7, 17), "A"),
    (datetime(2026, 7, 17, 14, 0),  date(2026, 7, 17), "B"),  # B boundary
    (datetime(2026, 7, 17, 21, 59), date(2026, 7, 17), "B"),
    (datetime(2026, 7, 17, 22, 0),  date(2026, 7, 18), "C"),  # rollover again
    (datetime(2026, 12, 31, 23, 0), date(2027, 1, 1),  "C"),  # year boundary
    (datetime(2026, 2, 28, 22, 30), date(2026, 3, 1),  "C"),  # month boundary (non-leap-ish)
]

def test_resolution():
    for at, pd, sh in CASES:
        m = resolve(at)
        assert m.plant_day == pd, f"{at}: plant_day {m.plant_day} != {pd}"
        assert m.shift == sh, f"{at}: shift {m.shift} != {sh}"

def test_plant_day_window():
    s, e = plant_day_window(date(2026, 7, 17))
    assert s == datetime(2026, 7, 16, 22, 0)
    assert e == datetime(2026, 7, 17, 22, 0)
    # Every moment inside the window resolves back to the same plant day
    assert resolve(s).plant_day == date(2026, 7, 17)
    assert resolve(e).plant_day == date(2026, 7, 18)  # end is exclusive

def test_shift_windows_partition_plant_day():
    pd = date(2026, 7, 17)
    c = shift_window(pd, "C"); a = shift_window(pd, "A"); b = shift_window(pd, "B")
    assert c[1] == a[0] and a[1] == b[0]
    day = plant_day_window(pd)
    assert c[0] == day[0] and b[1] == day[1]

def test_durations_cross_midnight():
    assert duration_minutes(datetime(2026, 7, 16, 23, 40), datetime(2026, 7, 17, 0, 25)) == 45.0
    assert duration_minutes(None, datetime(2026, 7, 17, 0, 25)) is None
    try:
        duration_minutes(datetime(2026, 7, 17, 2, 0), datetime(2026, 7, 17, 1, 0))
        assert False, "reversed chronology must raise"
    except ValueError:
        pass

if __name__ == "__main__":
    test_resolution(); test_plant_day_window()
    test_shift_windows_partition_plant_day(); test_durations_cross_midnight()
    print("PLANT DATE ENGINE: ALL TESTS PASSED")
