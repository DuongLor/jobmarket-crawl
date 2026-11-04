"""CareerViet (careerviet.vn) — khảo sát 2026-11-04 (docs/spike-0.md lượt 8).

robots.txt: `User-agent: *` chỉ cấm /vi/jobs/*, /api/*, trang in/lưu tin… → trang danh sách `/viec-lam/…-vi.html` được
phép; bot tự khai danh tính nhận 200, không challenge. 50 tin/trang, phân trang `…-trang-<n>-vi.html`.
Thẻ tin có: mã tin, tiêu đề, công ty, lương, địa điểm (nhiều tỉnh trong thuộc tính title "A | B"), HẠN NỘP, ngày CẬP
NHẬT, danh sách ngành của tin (1–3 mục, `ul.welfare`). Không có kinh nghiệm, cấp bậc.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean
from jobmarket.pii import scrub
from jobmarket.sources.base import ListingPage, RawJob

NAME = "careerviet"
BASE = "https://careerviet.vn"
PROVIDES = {"source_job_id", "title", "company_name", "salary", "location", "industry", "posted_date", "deadline"}


def listings(start_url: str, start_html: str) -> list[ListingPage]:
    return [ListingPage(start_url, None)]


def page_url(listing_url: str, page: int) -> str:
    """`…/tat-ca-viec-lam-vi.html` → trang n: `…/tat-ca-viec-lam-trang-n-vi.html`."""
    base = re.sub(r"-trang-\d+(-(?:vi|en)\.html)$", r"\1", listing_url)
    return base if page == 1 else re.sub(r"(-(?:vi|en)\.html)$", rf"-trang-{page}\1", base)


# Số trang có trong dữ liệu trang (payload Next.js, có thể bị escape): `"pageCount":370` — thanh phân trang trên
# HTML chỉ hiện 5 trang gần nhất, không có trang cuối.
_PAGE_COUNT = re.compile(r'pageCount\\{0,3}"\s*:\s*(\d+)')


def last_page(html: str, listing_url: str) -> int | None:
    counts = [int(n) for n in _PAGE_COUNT.findall(html)]
    return max(counts) if counts else None


def _date(text: str | None) -> str | None:
    """'30-11-2026' → '2026-11-30'."""
    try:
        return datetime.strptime(clean(text), "%d-%m-%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def _time_value(card, label: str) -> str | None:
    for li in card.css("div.time li"):
        if label in (li.text() or ""):
            found = li.css_first("time")
            return _date(found.text()) if found else None
    return None


def parse_listing(html: str, industry_label: str | None) -> list[RawJob]:
    tree = LexborHTMLParser(html)
    jobs, seen = [], set()
    for card in tree.css("div.job-item"):
        job_id = (card.attributes.get("id") or "").removeprefix("job-item-") or None
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        link = card.css_first("div.title a.job_link") or card.css_first("a.job_link")
        company = card.css_first("a.company-name")
        salary = card.css_first("div.salary")
        location = card.css_first("div.location")
        provinces = (location.attributes.get("title") or location.text()) if location else ""
        industries = [clean(li.text()) for li in card.css("ul.welfare li") if clean(li.text())]
        card_html, removed = scrub(card.html)
        jobs.append(
            RawJob(
                source_job_id=job_id,
                url=urljoin(BASE, link.attributes.get("href")) if link and link.attributes.get("href") else None,
                title=clean(link.attributes.get("title") or link.text()) if link else None,
                company_name=clean(company.text()) if company else None,
                salary_raw=re.sub(r"^Lương:\s*", "", clean(salary.text())) or None if salary else None,
                location_raw=", ".join(p for p in (clean(x) for x in provinces.split("|")) if p) or None,
                # Ngành chính = mục đầu đã ánh xạ được (pipeline chọn); thống kê cơ cấu đếm chia đều mọi mục
                industry_label=(industries[0] if industries else None) or industry_label,
                industry_labels=industries,
                # Thẻ tin chỉ có ngày CẬP NHẬT (không có ngày đăng gốc) — như CareerLink
                posted_date=_time_value(card, "Cập nhật"),
                deadline=_time_value(card, "Hạn nộp"),
                raw_payload={"listing_card_html": card_html, "industries": industries},
                pii_removed=removed,
            )
        )
    return jobs
