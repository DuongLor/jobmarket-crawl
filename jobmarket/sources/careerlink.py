"""CareerLink — trang danh sách `/vieclam/list?page=N` (docs/spike-0.md, khảo sát 2026-11-03).

robots.txt cho phép; bot tự khai danh tính không bị chặn ở tốc độ thấp. ~50 tin/trang, ~33.900 tin.
Đọc nhanh (~25 trang < 1 phút) thì nguồn trả trang không có tin (HTTP 200) →
config giãn 10 s/request, ≤ 40 trang danh sách + 10 trang chi tiết mỗi lần, cách ≥ 4 giờ.
Thẻ tin có: mã tin, tiêu đề, công ty, địa điểm, lương, CẤP BẬC, NGÀY ĐĂNG/CẬP NHẬT (`<time datetime>`, UTC;
khớp `datePosted` của trang chi tiết). Không có ngành (trừ khi đọc từ trang ngành), kinh nghiệm, hạn nộp → tầng 2.
"""

import re
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean
from jobmarket.pii import scrub
from jobmarket.sources import jsonld
from jobmarket.sources.base import ListingPage, RawJob
from jobmarket.timeutil import local_date

NAME = "careerlink"
PROVIDES = {"source_job_id", "title", "company_name", "salary", "location", "level", "posted_date"}
BASE = "https://www.careerlink.vn"


_CATEGORY = re.compile(r"^/viec-lam/[a-z0-9-]+/\d+$")
_PROVINCE = re.compile(r"^/tim-viec-lam-tai/[a-z0-9-]+/[A-Z0-9]+$")


def discover(html: str) -> list[ListingPage]:
    """Trang theo ngành (`/viec-lam/<ngành>/<id>`, có nhãn ngành) rồi theo tỉnh (`/tim-viec-lam-tai/…`) có trên trang.

    Trang theo ngành gắn được ngành cho tin; trang theo tỉnh mở rộng phạm vi. Đều là trang công khai,
    robots cho phép, có link từ chính thẻ tin.
    """
    tree = LexborHTMLParser(html)
    categories: dict[str, str] = {}
    provinces: dict[str, None] = {}
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if _CATEGORY.match(href):
            categories.setdefault(urljoin(BASE, href), re.sub(r"^Việc làm\s+", "", clean(a.text())) or None)
        elif _PROVINCE.match(href):
            provinces.setdefault(urljoin(BASE, href), None)
    return [ListingPage(u, n) for u, n in categories.items()] + [ListingPage(u, None) for u in provinces]


def listing_key(url: str) -> str:
    """Khoá danh sách: trang danh mục định danh bằng MÃ (`/viec-lam/ke-toan/1` = `/viec-lam/ke-toan-kiem-toan/1`)."""
    path = urlsplit(url).path
    return f"category:{path.rsplit('/', 1)[1]}" if _CATEGORY.match(path) else url


def listings(start_url: str, start_html: str) -> list[ListingPage]:
    """Danh sách chung trước (tin trang đầu được lưu ngay, kể cả khi bị chặn giữa chừng), rồi ngành, rồi tỉnh.

    Tin gặp trước ở danh sách không ngành được bổ sung ngành khi xuất hiện ở trang ngành (pipeline.save_job).
    """
    return [ListingPage(url=start_url, industry_label=None), *discover(start_html)]


def last_page(html: str, listing_url: str) -> int | None:
    """Số trang cuối trên thanh phân trang của chính danh sách này (vd `/vieclam/list?page=690`)."""
    path = urlsplit(listing_url).path
    pages = []
    for a in LexborHTMLParser(html).css("a[href]"):
        parts = urlsplit(urljoin(BASE, a.attributes.get("href") or ""))
        if parts.path == path and (m := re.search(r"(?:^|&)page=(\d+)", parts.query)):
            pages.append(int(m.group(1)))
    return max(pages) if pages else None


def page_url(listing_url: str, page: int) -> str:
    base = re.sub(r"[?&]page=\d+", "", listing_url)
    return base if page == 1 else f"{base}{'&' if '?' in base else '?'}page={page}"


def _attr(node, selector: str, name: str) -> str | None:
    found = node.css_first(selector)
    return clean(found.attributes.get(name)) or None if found else None


def _text(node, selector: str) -> str | None:
    found = node.css_first(selector)
    return clean(found.text()) or None if found else None


def _text_by_class_suffix(node, suffix: str) -> str | None:
    """Class CSS-module có hash (`JobIndex-module__HZLWJW__salary`); `*=` khớp nhầm `__salaryRow`.

    → so đuôi tên class chính xác.
    """
    for el in node.css("[class]"):
        if any(token.endswith(suffix) for token in (el.attributes.get("class") or "").split()):
            return clean(el.text()) or None
    return None


def card_date(node) -> str | None:
    """Ngày đăng/cập nhật trên thẻ tin (`…__timestamp <time datetime="…Z">`) → ngày theo giờ VN."""
    found = node.css_first('[class*="__timestamp"] time[datetime]') or node.css_first("time[datetime]")
    return local_date(found.attributes.get("datetime")) if found else None


def parse_detail(html: str) -> dict | None:
    """Trang chi tiết `/tim-viec-lam/<slug>/<id>` → trường từ JSON-LD `JobPosting` (sources/jsonld.py).
    `industry` của CareerLink là danh sách mục cách nhau dấu phẩy, xếp theo MÃ danh mục tăng dần (không có mục chính —
    kiểm 8/8 tin nhiều mục, 2026-11-09). None nếu không có."""
    posting = jsonld.find_job_posting(html)
    return jsonld.parse_job_posting(posting) if posting else None


def parse_listing(html: str, industry_label: str | None) -> list[RawJob]:
    tree = LexborHTMLParser(html)
    jobs, seen = [], set()
    for details in tree.css('[class*="JobIndex-module"][class*="__details"]'):
        link = details.css_first('a[class*="__jobLink"]')
        href = link.attributes.get("href") if link else None
        match = re.search(r"/(\d+)$", href or "")
        job_id = match.group(1) if match else None
        if job_id in seen:
            continue
        seen.add(job_id)
        locations = [clean(a.attributes.get("title") or a.text()) for a in details.css('[class*="__location"] a')]
        card_html, removed = scrub(details.html)
        jobs.append(
            RawJob(
                source_job_id=job_id,
                url=urljoin(BASE, href) if href else None,
                title=_attr(details, 'a[class*="__jobLink"]', "title") or _text(details, '[class*="__name"]'),
                company_name=_attr(details, 'a[class*="__company"]', "title") or _text(details, '[class*="__company"]'),
                salary_raw=_text_by_class_suffix(details, "__salary"),
                location_raw=", ".join(loc for loc in locations if loc) or None,
                level_raw=_text_by_class_suffix(details, "__careerLevel"),
                posted_date=card_date(details),
                industry_label=industry_label,
                raw_payload={"listing_card_html": card_html, "listing_industry": industry_label},
                pii_removed=removed,
            )
        )
    return jobs
