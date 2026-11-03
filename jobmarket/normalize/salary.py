"""Chuỗi lương của nguồn → dạng lương + khoảng VND/tháng (PLAN §B4)."""

import re
from dataclasses import dataclass, field

from jobmarket.normalize.text import clean, strip_accents

NEGOTIABLE_WORDS = ("thoa thuan", "thuong luong", "canh tranh", "negotiable", "competitive", "deal")
UP_TO_WORDS = ("toi", "den", "up to", "upto", "len den", "len toi", "max")
FROM_WORDS = ("tu", "tren", "from", "min", "hon")

_NUMBER = r"\d+(?:[.,]\d+)*"
_UNIT = r"(?:trieu|tr|m|nghin|ngan|k|usd|vnd|d)?"
_RANGE = re.compile(rf"\$?\s*({_NUMBER})\s*{_UNIT}\s*(?:-|–|~|den|toi|to)\s*\$?\s*({_NUMBER})")
_SINGLE = re.compile(rf"({_NUMBER})")


@dataclass
class Salary:
    type: str  # range | fixed | up_to | from | negotiable | not_stated
    from_vnd: float | None = None
    to_vnd: float | None = None
    currency: str | None = None
    tax: str = "unknown"
    flags: list[str] = field(default_factory=list)


def _to_float(token: str, currency: str) -> float:
    if currency == "USD":
        # USD dùng dấu phẩy/chấm phân tách hàng nghìn: "1,000", "1.500"
        return float(re.sub(r"[.,]", "", token))
    # VND theo "triệu": "6.8" hoặc "6,8" là số thập phân; "15.000.000" là số đầy đủ
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3}){2,}", token):
        return float(re.sub(r"[.,]", "", token))
    return float(token.replace(",", "."))


def _unit_multiplier(text: str, currency: str, cfg: dict) -> float | None:
    """None = VND không ghi đơn vị (vd "15 - 20") — không đoán là triệu."""
    if currency == "USD":
        return float(cfg["fx_usd_vnd"])
    if re.search(r"(?<![a-z])(trieu|tr|m)(?![a-z])", text):
        return 1_000_000
    if re.search(r"(?<![a-z])(nghin|ngan|k)(?![a-z])", text):
        return 1_000
    if re.search(r"\d{1,3}(?:[.,]\d{3}){2,}", text):
        return 1
    return None


def _period_multiplier(text: str, cfg: dict) -> float:
    if re.search(r"/\s*(nam|year)\b|\bmoi nam\b|\bper year\b", text):
        return 1 / 12
    if re.search(r"/\s*(tuan|week)\b", text):
        return float(cfg["week_to_month"])
    if re.search(r"/\s*(gio|hour|h)\b", text):
        return float(cfg["hour_to_month"])
    return 1


def _starts_with_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.match(rf"{re.escape(w)}\b", text) for w in words)


def parse_salary(raw: str | None, cfg: dict) -> Salary:
    text = strip_accents(clean(raw)).lower()
    if not text:
        return Salary(type="not_stated")

    tax = "gross" if "gross" in text else "net" if re.search(r"\bnet\b", text) else "unknown"
    if any(w in text for w in NEGOTIABLE_WORDS):
        return Salary(type="negotiable", tax=tax)

    currency = "USD" if ("usd" in text or "$" in text) else "VND"
    salary = Salary(type="not_stated", currency=currency, tax=tax)
    unit = _unit_multiplier(text, currency, cfg)
    if unit is None:
        salary.flags.append("salary_ambiguous")
        return salary
    factor = unit * _period_multiplier(text, cfg)

    if m := _RANGE.search(text):
        lo, hi = (_to_float(m.group(i), currency) * factor for i in (1, 2))
        if lo > hi:
            lo, hi = hi, lo
        salary.type, salary.from_vnd, salary.to_vnd = ("fixed", lo, lo) if lo == hi else ("range", lo, hi)
    elif m := _SINGLE.search(text):
        value = _to_float(m.group(1), currency) * factor
        if _starts_with_any(text, UP_TO_WORDS):
            salary.type, salary.to_vnd = "up_to", value
        elif _starts_with_any(text, FROM_WORDS):
            salary.type, salary.from_vnd = "from", value
        else:
            salary.type, salary.from_vnd, salary.to_vnd = "fixed", value, value
    else:
        salary.flags.append("salary_ambiguous")
        return salary

    lo_ok, hi_ok = cfg["salary_min_valid"], cfg["salary_max_valid"]
    values = [v for v in (salary.from_vnd, salary.to_vnd) if v is not None]
    if any(v < lo_ok or v > hi_ok for v in values):
        salary.flags.append("salary_invalid")
        salary.from_vnd = salary.to_vnd = None
    return salary
