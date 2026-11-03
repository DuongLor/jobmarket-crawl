"""Thời gian lưu trong DB = giờ Việt Nam, KHÔNG kèm hậu tố múi giờ.

SQLite `date('…+07:00')` quy về UTC trước khi cắt ngày → tin thấy lúc 02:00 sáng VN bị tính sang hôm trước
(sai thống kê theo ngày/tháng). Lưu giờ địa phương dạng không múi giờ thì `date()` giữ đúng ngày VN.
Việt Nam không có giờ mùa hè → UTC+7 cố định (khỏi phụ thuộc tzdata, vốn không có sẵn trên Windows).
"""

from datetime import datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7))


def _fmt(moment: datetime) -> str:
    return moment.astimezone(VN_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def now_local() -> str:
    return _fmt(datetime.now(VN_TZ))


def from_timestamp(ts: float) -> str:
    return _fmt(datetime.fromtimestamp(ts, VN_TZ))


def local_date(value: str | None) -> str | None:
    """Mốc ISO có múi giờ (vd "2026-10-15T20:05:00.000Z") → ngày giờ VN "2026-10-16"; sai định dạng → None."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.date().isoformat()
    return moment.astimezone(VN_TZ).date().isoformat()


def current_month() -> str:
    return datetime.now(VN_TZ).strftime("%Y-%m")
