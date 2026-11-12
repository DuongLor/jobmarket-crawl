"""Yêu cầu kinh nghiệm → 6 nhóm theo MỨC TỐI THIỂU (PLAN §B4)."""

import re

from jobmarket.normalize.text import clean, norm_key, strip_accents

# (mã, số tháng tối thiểu của nhóm) — khớp seeds/experiences.csv
BUCKETS = [("NONE", 0), ("LT_1Y", 1), ("Y1_2", 12), ("Y2_3", 24), ("Y3_5", 36), ("Y5P", 60)]

_NONE_PHRASES = (
    "khong yeu cau",
    "khong can kinh nghiem",
    "chua co kinh nghiem",
    "no experience",
    "no requirement",
    "not required",
)


def bucket_for_months(months: float) -> str:
    code = BUCKETS[0][0]
    for bucket_code, min_months in BUCKETS:
        if months >= min_months:
            code = bucket_code
    return code


def experience_code(raw: str | None) -> str | None:
    """Trả mã nhóm, hoặc None nếu không đọc được (→ nhãn chưa ánh xạ, không đoán)."""
    text = norm_key(raw)
    if not text:
        return None
    if any(p in text for p in _NONE_PHRASES):
        return "NONE"
    if re.match(r"(duoi|it hon|under|less than) 1 nam", text):
        return "LT_1Y"
    if re.match(r"(duoi|it hon|under|less than) \d", text):
        return None
    months_match = re.search(r"(\d+)\s*thang", text)
    if months_match and "nam" not in text:
        return bucket_for_months(int(months_match.group(1)))
    years = re.findall(r"\d+(?:\.\d+)?", text)
    if years and ("nam" in text or "year" in text):
        return bucket_for_months(float(years[0]) * 12)
    return None


_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"\s*(nam|year|years|thang|month|months)\b"


def _months(value: str, unit: str) -> int:
    number = float(value.replace(",", "."))
    return round(number * 12) if unit.startswith(("nam", "year")) else round(number)


def experience_months(raw: str | None) -> tuple[int | None, int | None]:
    """Chữ kinh nghiệm nguồn ghi → (tối thiểu, tối đa) theo THÁNG; phần không rõ = None, không đoán.

    "Không yêu cầu" → (0, 0) · "Dưới 1 năm" → (0, 12) · "1 năm" / "12 tháng" → (12, None) — một con số = mức tối thiểu ·
    "1 - 2 năm" → (12, 24) · "Hơn/Trên/Từ 5 năm" → (60, None) · "6 tháng - 1 năm" → (6, 12).
    """
    # Bỏ dấu tiếng Việt nhưng GIỮ "-", ",", "." (norm_key xoá cả chúng: "1 - 2 năm" → "1 2 nam" là sai nghĩa)
    text = strip_accents(clean(raw)).lower()
    if not text:
        return None, None
    if any(p in norm_key(raw) for p in _NONE_PHRASES):
        return 0, 0
    span = re.search(rf"{_NUM}(?:{_UNIT})?\s*(?:-|–|den|toi|to)\s*{_NUM}{_UNIT}", text)
    if span:
        hi_unit = span.group(4)
        lo = _months(span.group(1), span.group(2) or hi_unit)
        hi = _months(span.group(3), hi_unit)
        return (lo, hi) if lo <= hi else (None, None)
    single = re.search(rf"{_NUM}{_UNIT}", text)
    if not single:
        return None, None
    months = _months(single.group(1), single.group(2))
    if re.match(r"(duoi|it hon|under|less than|toi da|max)\b", text):
        return 0, months
    return months, None


def experience_code_from_years(years: float | None) -> str | None:
    """Cho nguồn trả số năm dạng số (vd VietnamWorks `yearsOfExperience`)."""
    if years is None:
        return None
    return bucket_for_months(float(years) * 12)
