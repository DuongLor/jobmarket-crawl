import sqlite3

from jobmarket.timeutil import from_timestamp, now_local


def test_stored_time_has_no_timezone_suffix():
    value = now_local()
    assert "+" not in value and not value.endswith("Z") and len(value) == 19


def test_night_run_keeps_vietnam_date():
    # 2026-11-30 19:30 UTC = 2026-12-01 02:30 giờ VN (giờ chạy đêm) → phải tính vào ngày 1/12, tháng 12
    stored = from_timestamp(1_796_067_000)
    assert stored == "2026-12-01T02:30:00"
    day = sqlite3.connect(":memory:").execute("SELECT date(?)", (stored,)).fetchone()[0]
    assert day == "2026-12-01"


def test_local_date_converts_utc_to_vietnam_day():
    from jobmarket.timeutil import local_date

    assert local_date("2026-11-03T03:04:56.000Z") == "2026-11-03"
    assert local_date("2026-10-15T20:05:00.000Z") == "2026-10-16"  # 03:05 sáng 16/10 giờ VN
    assert local_date("2026-10-15") == "2026-10-15"
    assert local_date("không phải ngày") is None and local_date(None) is None
