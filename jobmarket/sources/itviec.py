"""ITviec (itviec.com, chỉ việc làm IT) — khảo sát 2026-11-10 (docs/spike-0.md lượt 12).

robots.txt: `User-agent: *` chỉ cấm /subscriptions/new, `Allow: /`, khai sitemap; bot tự khai danh tính nhận 200 ở trang
danh sách (cả `?page=2+`) và trang chi tiết. Danh sách `/it-jobs`: 20 tin/trang, ~31 trang (~620 tin — cả site).
Thẻ tin: mã tin (`data-job-key`, UUID), tiêu đề, công ty, NGHỀ ("Backend Developer"), hình thức (At office/Remote),
tỉnh (tiếng Anh không dấu, "Ho Chi Minh"), kỹ năng, "Posted N minutes/hours/days ago".
Trang chi tiết: JSON-LD `JobPosting` — ngày đăng, hạn nộp, ngành ("Information Technology"), mô tả.

KHÔNG có: lương (ẩn, phải đăng nhập — "Sign in to view salary"; JSON-LD ghi "You'll love it"), cấp bậc. Kinh nghiệm
trong JSON-LD KHÔNG đáng tin (tin ghi "ít nhất 3 năm" mà `monthsOfExperience` = 10) → bỏ, không suy đoán (BR-05).
"""

import re
from datetime import date, timedelta
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean, norm_key
from jobmarket.pii import scrub
from jobmarket.sources import jsonld
from jobmarket.sources.base import ListingPage, RawJob
from jobmarket.timeutil import now_local

NAME = "itviec"
BASE = "https://itviec.com"
# Cả site là việc làm IT — đúng ngành nguồn tự ghi trong JSON-LD từng tin, không phải suy đoán
SITE_INDUSTRY = "Information Technology"
PROVIDES = {"source_job_id", "title", "company_name", "location", "industry", "occupation", "skills", "posted_date"}

_AGO = re.compile(r"(\d+)\s*(minute|hour|day|phút|giờ|ngày)", re.I)
_PAGE = re.compile(r"[?&]page=(\d+)")


def listings(start_url: str, start_html: str) -> list[ListingPage]:
    return [ListingPage(start_url, SITE_INDUSTRY)]


def page_url(listing_url: str, page: int) -> str:
    base = re.sub(r"[?&]page=\d+", "", listing_url)
    return base if page == 1 else f"{base}{'&' if '?' in base else '?'}page={page}"


def last_page(html: str, listing_url: str) -> int | None:
    pages = [int(n) for n in _PAGE.findall(html)]
    return max(pages) if pages else None


def posted_date(text: str | None, today: date | None = None) -> str | None:
    """ "Posted 31 minutes ago" / "2 hours ago" → hôm nay; "3 days ago" → hôm nay − 3. Tuần/tháng (không đủ chính xác
    tới ngày) → None, trang chi tiết bổ sung ngày đăng đúng."""
    match = _AGO.search(text or "")
    if not match:
        return None
    today = today or date.fromisoformat(now_local()[:10])
    unit = match.group(2).lower()
    days = int(match.group(1)) if unit in ("day", "ngày") else 0
    return (today - timedelta(days=days)).isoformat()


def _text(node, selector: str) -> str | None:
    found = node.css_first(selector)
    return clean(found.text()) or None if found else None


def _location(card) -> str | None:
    """ "Ho Chi Minh - Ha Noi" (thuộc tính title — chữ hiển thị có thể bị cắt) → "Ho Chi Minh, Ha Noi". Tên thành phố
    của ITviec không có gạch nối nên tách theo " - " ở đây (bộ tách chung không tách: "Bà Rịa - Vũng Tàu")."""
    node = card.css_first("div.text-rich-grey.text-truncate.text-nowrap")
    text = clean((node.attributes.get("title") or node.text()) if node else "")
    return ", ".join(p for p in (clean(x) for x in text.split(" - ")) if p) or None


def parse_listing(html: str, industry_label: str | None) -> list[RawJob]:
    tree = LexborHTMLParser(html)
    jobs, seen = [], set()
    for card in tree.css("div.job-card"):
        job_id = card.attributes.get("data-job-key")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        link = card.css_first("h3 a")
        href = link.attributes.get("href") if link else None
        url = urljoin(BASE, urlsplit(href).path) if href else None  # bỏ tham số theo dõi (?lab_feature=…)
        company = card.css_first('span a.text-rich-grey[href^="/companies/"]')
        skills = [clean(a.text()) for a in card.css("a.itag") if clean(a.text())]
        card_html, removed = scrub(card.html)
        jobs.append(
            RawJob(
                source_job_id=job_id,
                url=url,
                title=clean(link.text()) if link else None,
                company_name=clean(company.text()) if company else None,
                location_raw=_location(card),
                occupation_label=_text(card, "a.text-decoration-dot-underline"),
                industry_label=industry_label or SITE_INDUSTRY,
                skills_raw=", ".join(skills) or None,
                posted_date=posted_date(_text(card, "span.small-text.text-dark-grey")),
                raw_payload={"listing_card_html": card_html},
                pii_removed=removed,
            )
        )
    return jobs


def parse_detail(html: str) -> dict | None:
    """Trang chi tiết → trường JSON-LD (ngày đăng, hạn nộp, ngành, mô tả). Bỏ kinh nghiệm (không đáng tin, xem đầu
    file); lương không có (ẩn) — pipeline không đọc lương từ trang chi tiết."""
    posting = jsonld.find_job_posting(html)
    if not posting:
        return None
    detail = jsonld.parse_job_posting(posting)
    detail["experience"] = None
    sections = _sections(html)
    description, removed = sections.get("description", (None, []))
    requirements, removed_req = sections.get("requirements", (None, []))
    detail["description_text"] = description or detail["description_text"]
    detail["requirements_text"] = requirements
    detail["pii_removed"] = detail["pii_removed"] + removed + removed_req
    return detail


# Tiêu đề mục trên trang chi tiết (trang tiếng Việt /viec-lam-it/… và tiếng Anh /it-jobs/…), mỗi mục là
# `div.paragraph` > h2 + nội dung. Lấy đúng chữ nguồn ghi dưới tiêu đề đó.
_SECTION_TITLES = {
    "mo ta cong viec": "description",
    "job description": "description",
    "yeu cau cong viec": "requirements",
    "your skills and experience": "requirements",
}


def _sections(html: str) -> dict[str, tuple[str | None, list[str]]]:
    found: dict[str, tuple[str | None, list[str]]] = {}
    for heading in LexborHTMLParser(html).css("div.paragraph > h2"):
        key = _SECTION_TITLES.get(norm_key(heading.text()))
        if key and key not in found:
            body = heading.parent.html.replace(heading.html, "", 1)
            found[key] = jsonld.html_to_text(body)
    return found
