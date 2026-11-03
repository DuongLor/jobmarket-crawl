"""JobsGO — trang danh sách theo NGÀNH `/viec-lam-<ngành>.html?page=N` (khảo sát 2026-11-03).

robots.txt cho phép (chỉ cấm /ajax, /api…); bot tự khai danh tính không bị chặn. ~53 tin/trang, ~26.000 tin.
Thẻ tin có: mã tin, tiêu đề, công ty, lương, địa điểm, kinh nghiệm. Ngành = ngành của trang đang duyệt.
"""

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean
from jobmarket.pii import scrub
from jobmarket.sources.base import ListingPage, RawJob

NAME = "jobsgo"
PROVIDES = {"source_job_id", "title", "company_name", "salary", "location", "experience", "industry"}
BASE = "https://jobsgo.vn"

_CATEGORY = re.compile(r"^(?:https://jobsgo\.vn)?/viec-lam-([a-z0-9-]+)\.html$")
# Trang lọc theo tỉnh ("tai-ha-noi") và trang đặc biệt không phải ngành. "tai-chinh-…" là ngành Tài chính.
_SPECIAL = {"gan-ban", "tuyen-gap", "noi-bat", "lao-dong-pho-thong", "khong-can-bang-cap", "online-tai-nha",
            "part-time", "thoi-vu", "remote"}  # fmt: skip
_LAST_TOP_LEVEL = "nganh-nghe-khac"


def _is_industry_slug(slug: str) -> bool:
    is_location = slug.startswith("tai-") and not slug.startswith("tai-chinh")
    return not is_location and slug not in _SPECIAL


def listings(start_url: str, start_html: str) -> list[ListingPage]:
    """Ngành cấp 1 theo thứ tự trên trang, dừng ở "Ngành Nghề Khác" (phía sau là ngành con chi tiết)."""
    tree = LexborHTMLParser(start_html)
    pages: dict[str, str] = {}
    for a in tree.css("a[href]"):
        match = _CATEGORY.match(a.attributes.get("href") or "")
        if not match or not _is_industry_slug(match.group(1)):
            continue
        url = urljoin(BASE, f"/viec-lam-{match.group(1)}.html")
        pages.setdefault(url, re.sub(r"^Việc làm\s+", "", clean(a.text())))
        if match.group(1) == _LAST_TOP_LEVEL:
            break
    return [ListingPage(url=u, industry_label=n) for u, n in pages.items()]


def page_url(listing_url: str, page: int) -> str:
    base = re.sub(r"[?&]page=\d+", "", listing_url)
    return base if page == 1 else f"{base}?page={page}"


def _attr(node, selector: str, name: str) -> str | None:
    found = node.css_first(selector)
    return clean(found.attributes.get(name)) or None if found else None


def parse_listing(html: str, industry_label: str | None) -> list[RawJob]:
    tree = LexborHTMLParser(html)
    jobs, seen = [], set()
    for card in tree.css("div.job-card"):
        link = card.css_first("h3.job-title a")
        href = (link.attributes.get("href") or "").split("#")[0] if link else ""
        match = re.search(r"-(\d{6,})\.html$", href)
        job_id = match.group(1) if match else None
        if job_id in seen:
            continue
        seen.add(job_id)
        meta = [clean(s.text()) for s in card.css("div.text-primary > span") if clean(s.text()) not in ("", "|")]
        card_html, removed = scrub(card.html)
        jobs.append(
            RawJob(
                source_job_id=job_id,
                url=urljoin(BASE, href) if href else None,
                title=_attr(card, "h3.job-title a", "title"),
                company_name=_attr(card, "a.company-title", "title"),
                salary_raw=meta[0] if meta else None,
                location_raw=meta[-1] if len(meta) > 1 else None,
                experience_raw=_attr_text(card, 'span[title="Yêu cầu kinh nghiệm"]'),
                industry_label=industry_label,
                raw_payload={"listing_card_html": card_html, "listing_industry": industry_label},
                pii_removed=removed,
            )
        )
    return jobs


def _attr_text(node, selector: str) -> str | None:
    found = node.css_first(selector)
    return clean(found.text()) or None if found else None
