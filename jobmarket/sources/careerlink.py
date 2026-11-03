"""CareerLink — trang danh sách `/vieclam/list?page=N` (docs/spike-0.md, khảo sát 2026-11-03).

robots.txt cho phép; bot tự khai danh tính không bị chặn ở tốc độ thấp. ~50 tin/trang, ~33.900 tin.
Đọc nhanh (~25 trang < 1 phút) thì nguồn trả trang không có tin (HTTP 200) → config giãn 10 s/request, ≤ 60/đêm.
Thẻ tin có: mã tin, tiêu đề, công ty, địa điểm, lương, CẤP BẬC, NGÀY ĐĂNG/CẬP NHẬT (`<time datetime>`, UTC;
khớp `datePosted` của trang chi tiết). Không có ngành (trừ khi đọc từ trang ngành), kinh nghiệm, hạn nộp → tầng 2.
"""

import json
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from jobmarket.normalize.text import clean
from jobmarket.pii import scrub
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


def listings(start_url: str, start_html: str) -> list[ListingPage]:
    """Danh sách chung trước (tin trang đầu được lưu ngay, kể cả khi bị chặn giữa chừng), rồi ngành, rồi tỉnh.

    Tin gặp trước ở danh sách không ngành được bổ sung ngành khi xuất hiện ở trang ngành (pipeline.save_job).
    """
    return [ListingPage(url=start_url, industry_label=None), *discover(start_html)]


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
    """Trang chi tiết `/tim-viec-lam/<slug>/<id>` → trường từ JSON-LD `JobPosting` (dữ liệu có cấu trúc chuẩn
    schema.org mà trang công bố cho máy tìm kiếm). None nếu trang không có JobPosting."""
    tree = LexborHTMLParser(html)
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        posting = next((i for i in items if isinstance(i, dict) and i.get("@type") == "JobPosting"), None)
        if not posting:
            continue
        description = posting.get("description") or ""
        text = LexborHTMLParser(f"<div>{description}</div>").body.text(separator="\n") if description else ""
        text, removed = scrub(re.sub(r"\n{3,}", "\n\n", text).strip())
        experience = clean(str(posting.get("experienceRequirements") or "")) or None
        if isinstance(posting.get("experienceRequirements"), dict):
            months = posting["experienceRequirements"].get("monthsOfExperience")
            experience = f"{months} tháng" if months is not None else None
        return {
            "industry": clean(str(posting.get("industry") or "")) or None,
            "experience": experience,
            "posted_date": str(posting.get("datePosted") or "")[:10] or None,
            "deadline": str(posting.get("validThrough") or "")[:10] or None,
            "employment_type": clean(str(posting.get("employmentType") or "")) or None,
            "description_text": text or None,
            "pii_removed": removed,
            "jsonld": {k: v for k, v in posting.items() if k != "description"},
        }
    return None


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
