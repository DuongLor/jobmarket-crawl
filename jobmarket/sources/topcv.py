"""TopCV — Cloudflare chặn bot tự khai danh tính (kể cả sitemap.xml, kiểm tra 2026-11-04) → KHÔNG crawl tự động,
không vượt chặn. Dữ liệu vào qua trang BẠN tự lưu khi duyệt web (Ctrl+S → data/inbox/topcv/, lệnh import-html):

- Trang danh sách: mã tin, tiêu đề, công ty, lương, địa điểm, kinh nghiệm, tag (không có cấp bậc, ngày, hạn nộp).
- Trang chi tiết: JSON-LD `JobPosting` (sources/jsonld.py) → thêm ngành, kinh nghiệm, ngày đăng gốc, hạn nộp, loại
  hình, mô tả. Trường không có → để trống, không suy đoán.
"""

import re

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean
from jobmarket.pii import scrub
from jobmarket.sources import jsonld
from jobmarket.sources.base import ListingPage, RawJob

NAME = "topcv"
# Trường TopCV có cung cấp ở trang danh sách — dùng cho cờ chất lượng
PROVIDES = {"source_job_id", "title", "company_name", "salary", "location", "experience", "industry"}
# Trang chi tiết có thêm ngày đăng (datePosted) và hạn nộp (validThrough)
PROVIDES_DETAIL = PROVIDES | {"posted_date", "deadline"}
# Mã tin trong URL: /viec-lam/<slug>/<id>.html hoặc /brand/<cty>/tuyen-dung/<slug>-j<id>.html (= data-job-id thẻ tin)
_JOB_URL_ID = re.compile(r"(?:/|-j)(\d+)\.html(?:$|[?#])")

_CATEGORY_URL = re.compile(r"^https://www\.topcv\.vn/tim-viec-lam-[a-z0-9-]+-cr\d+$")


def category_pages(html: str) -> list[ListingPage]:
    """Các trang ngành cấp 1 (`…-cr<id>`) liệt kê trên trang danh sách, giữ thứ tự xuất hiện."""
    tree = LexborHTMLParser(html)
    seen: dict[str, str] = {}
    for a in tree.css('a[href*="-cr"]'):
        href = (a.attributes.get("href") or "").strip()
        if _CATEGORY_URL.match(href) and href not in seen:
            name = clean(a.text())
            seen[href] = re.sub(r"^Việc làm\s+", "", name)
    return [ListingPage(url=u, industry_label=n) for u, n in seen.items()]


_TITLE_INDUSTRY = re.compile(r"việc làm\s+(.+?)\s*\[", re.IGNORECASE)


def page_industry(html: str) -> str | None:
    """Ngành của trang danh sách đã lưu, đọc từ <title>.

    "Tuyển dụng 2.616 việc làm Công nghệ Thông tin [Update 03/11/2026]" → "Công nghệ Thông tin";
    trang "mới nhất" ("Tuyển dụng 41.600 việc làm [Update …]") → None.
    """
    title = LexborHTMLParser(html).css_first("title")
    match = _TITLE_INDUSTRY.search(clean(title.text()) if title else "")
    return match.group(1) if match else None


def is_listing_page(html: str) -> bool:
    return "job-item-search-result" in html


def _text(node, selector: str) -> str | None:
    found = node.css_first(selector)
    return clean(found.text()) or None if found else None


def parse_listing(html: str, industry_label: str | None) -> list[RawJob]:
    tree = LexborHTMLParser(html)
    jobs: list[RawJob] = []
    seen_ids: set[str] = set()
    for card in tree.css("div.job-item-search-result"):
        job_id = card.attributes.get("data-job-id")
        if job_id in seen_ids:
            continue
        if job_id:
            seen_ids.add(job_id)
        title_link = card.css_first("h3.title a")
        title_span = card.css_first("h3.title a span")
        url = title_link.attributes.get("href") if title_link else None
        url = url.split("?")[0] if url else None
        tags = [clean(t.text()) for t in card.css(".tag .item-tag") if clean(t.text())]

        card_html, removed = scrub(card.html)
        job = RawJob(
            source_job_id=job_id,
            url=url,
            title=(title_span.attributes.get("title") if title_span else None) or _text(card, "h3.title"),
            company_name=_text(card, "span.company-name"),
            salary_raw=_text(card, "label.salary"),
            location_raw=_text(card, "span.city-text"),
            experience_raw=_text(card, "label.exp"),
            industry_label=industry_label,
            skills_raw=", ".join(tags) or None,
            raw_payload={"listing_card_html": card_html, "listing_industry": industry_label},
            pii_removed=removed,
        )
        jobs.append(job)
    return jobs


def job_id_from_url(url: str | None) -> str | None:
    match = _JOB_URL_ID.search(url or "")
    return match.group(1) if match else None


def _page_url(tree) -> str | None:
    """URL gốc của trang đã lưu (Ctrl+S giữ <link rel=canonical> / og:url)."""
    for selector, attr in (('link[rel="canonical"]', "href"), ('meta[property="og:url"]', "content")):
        node = tree.css_first(selector)
        if node and node.attributes.get(attr):
            return node.attributes.get(attr).split("?")[0]
    return None


def parse_detail_page(html: str) -> tuple[RawJob, dict] | None:
    """Trang chi tiết TopCV đã lưu → (tin, dữ liệu chi tiết). None nếu trang không có JSON-LD JobPosting."""
    posting = jsonld.find_job_posting(html)
    if not posting:
        return None
    detail = jsonld.parse_job_posting(posting)
    url = _page_url(LexborHTMLParser(html)) or detail["url"]
    job_id = job_id_from_url(url) or job_id_from_url(detail["url"]) or detail["identifier"]
    job = RawJob(
        source_job_id=job_id,
        url=url,
        title=detail["title"],
        company_name=detail["company_name"],
        salary_raw=detail["salary_raw"],
        location_raw=detail["location"],
        experience_raw=detail["experience"],
        industry_label=detail["industry"],
        posted_date=detail["posted_date"],
        deadline=detail["deadline"],
        description_text=detail["description_text"],
        raw_payload={"jsonld": detail["jsonld"]},
        pii_removed=detail["pii_removed"],
    )
    return job, detail
