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

def test_plant_clock_ignores_server_timezone():
    """The plant's clock must not be the SERVER's clock.

    A cloud host runs in UTC. Read naively, 17:39 in an Indian shop is 12:09
    on the server: B shift reported as A, and heats filed against the wrong
    shift and plant day. SMS2_PLANT_TZ must make the server's own zone
    irrelevant.
    """
    import os, importlib
    from datetime import datetime, timezone
    from app.core import plant_time as pt

    prev = os.environ.get("SMS2_PLANT_TZ")
    try:
        os.environ["SMS2_PLANT_TZ"] = "Asia/Kolkata"
        importlib.reload(pt)
        if pt._TZ is None:           # no tz database on this machine
            print("  (skipped: tzdata unavailable)")
            return
        utc = datetime.now(timezone.utc).replace(tzinfo=None)
        ist = pt.plant_now()
        offset = round((ist - utc).total_seconds() / 3600, 1)
        assert offset == 5.5, f"expected IST = UTC+5.5, got {offset}"
        # and the shift follows the PLANT clock, not the server's
        assert pt.now_moment().shift == pt.resolve(ist).shift
    finally:
        if prev is None:
            os.environ.pop("SMS2_PLANT_TZ", None)
        else:
            os.environ["SMS2_PLANT_TZ"] = prev
        importlib.reload(pt)


if __name__ == "__main__":
    test_resolution(); test_plant_day_window()
    test_shift_windows_partition_plant_day(); test_durations_cross_midnight()
    test_plant_clock_ignores_server_timezone()
    print("PLANT DATE ENGINE: ALL TESTS PASSED")
