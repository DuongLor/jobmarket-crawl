"""Yêu cầu kinh nghiệm → 6 nhóm theo MỨC TỐI THIỂU (PLAN §B4)."""

import re

from jobmarket.normalize.text import norm_key

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


def experience_code_from_years(years: float | None) -> str | None:
    """Cho nguồn trả số năm dạng số (vd VietnamWorks `yearsOfExperience`)."""
    if years is None:
        return None
    return bucket_for_months(float(years) * 12)
