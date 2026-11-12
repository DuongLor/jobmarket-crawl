"""Đọc JSON-LD `JobPosting` (chuẩn schema.org mà trang tin tuyển dụng công bố cho máy tìm kiếm) — dùng chung mọi nguồn.

Chỉ đọc dữ liệu có cấu trúc trang tự công bố; không đoán. Trường không có → None.
"""

import json
import re

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean
from jobmarket.pii import scrub

_PERIOD = {"HOUR": "/giờ", "DAY": "/ngày", "WEEK": "/tuần", "MONTH": "", "YEAR": "/năm"}


def find_job_posting(html: str) -> dict | None:
    """Khối JobPosting đầu tiên trong các thẻ <script type="application/ld+json"> (kể cả trong @graph / mảng)."""
    tree = LexborHTMLParser(html)
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(), strict=False)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("JobPosting", ["JobPosting"]):
                return item
    return None


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def salary_text(base_salary) -> str | None:
    """`baseSalary` (MonetaryAmount) → chuỗi lương theo cách các nguồn ghi ("10 triệu - 15 triệu", "1000 USD"),
    để đi qua CÙNG bộ chuẩn hoá lương như tin từ trang danh sách."""
    if not isinstance(base_salary, dict):
        return None
    currency = str(base_salary.get("currency") or "VND").upper()
    value = base_salary.get("value")
    if isinstance(value, dict):
        lo, hi = _number(value.get("minValue")), _number(value.get("maxValue"))
        single = _number(value.get("value"))
        unit = str(value.get("unitText") or "MONTH").upper()
    else:
        lo = hi = None
        single = _number(value)
        unit = "MONTH"
    if single is not None and lo is None and hi is None:
        lo = hi = single
    if not lo and not hi:
        return None  # 0 / không ghi = thoả thuận, không phải mức lương

    def fmt(amount: float) -> str:
        if currency == "USD":
            return f"{amount:,.0f} USD"
        if amount >= 100_000:
            return f"{amount / 1_000_000:g} triệu"
        return f"{amount:,.0f}".replace(",", ".") + " VND"

    period = _PERIOD.get(unit, "")
    if lo and hi and lo != hi:
        return f"{fmt(lo)} - {fmt(hi)}{period}"
    if lo and not hi:
        return f"Từ {fmt(lo)}{period}"
    if hi and not lo:
        return f"Tới {fmt(hi)}{period}"
    return f"{fmt(hi)}{period}"


def _location(posting: dict) -> str | None:
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    names = []
    for place in places:
        address = place.get("address") if isinstance(place, dict) else None
        if isinstance(address, dict):
            name = clean(address.get("addressRegion") or address.get("addressLocality") or "")
            if name and name not in names:
                names.append(name)
    return ", ".join(names) or None


def html_to_text(html: str | None) -> tuple[str | None, list[str]]:
    """HTML mô tả/yêu cầu → chữ nhiều dòng, đã xoá SĐT/email (pii.scrub). Rỗng → (None, [])."""
    if not html:
        return None, []
    text = LexborHTMLParser(f"<div>{html}</div>").body.text(separator="\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text, removed = scrub(re.sub(r"\n{3,}", "\n\n", text).strip())
    return text or None, removed


def parse_job_posting(posting: dict) -> dict:
    """Trường chuẩn hoá-sẵn-sàng từ khối JobPosting. `industries`: danh sách mục ngành (dấu phẩy), mục đầu = chính."""
    text, removed = html_to_text(posting.get("description"))
    raw_industry = posting.get("industry")
    raw_industry = ", ".join(map(str, raw_industry)) if isinstance(raw_industry, list) else str(raw_industry or "")
    industries = [clean(part) for part in raw_industry.split(",") if clean(part)]
    experience = posting.get("experienceRequirements")
    if isinstance(experience, dict):
        months = experience.get("monthsOfExperience")
        experience = f"{months} tháng" if months is not None else None
    else:
        experience = clean(str(experience or "")) or None
    organization = posting.get("hiringOrganization")
    company = clean(organization.get("name") or "") or None if isinstance(organization, dict) else None
    identifier = posting.get("identifier")
    job_id = identifier.get("value") if isinstance(identifier, dict) else None
    return {
        "title": clean(posting.get("title") or "") or None,
        "company_name": company,
        "identifier": str(job_id) if job_id else None,
        "url": posting.get("url"),
        "location": _location(posting),
        "salary_raw": salary_text(posting.get("baseSalary")),
        "industry": industries[0] if industries else None,
        "industries": industries,
        "experience": experience,
        "posted_date": str(posting.get("datePosted") or "")[:10] or None,
        "deadline": str(posting.get("validThrough") or "")[:10] or None,
        "employment_type": clean(str(posting.get("employmentType") or "")) or None,
        "description_text": text,
        "requirements_text": None,  # JSON-LD không tách yêu cầu; adapter tách nếu trang có
        "pii_removed": removed,
        "jsonld": {k: v for k, v in posting.items() if k != "description"},
    }
