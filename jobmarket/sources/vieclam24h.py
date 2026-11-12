"""Việc Làm 24h (vieclam24h.vn) — khảo sát 2026-11-04 (docs/spike-0.md lượt 8).

robots.txt cho phép (chỉ cấm /admin, /taikhoan, tham số `?q`…); bot tự khai danh tính nhận HTTP 200, không challenge.
Trang danh sách (Next.js) nhúng sẵn dữ liệu có cấu trúc trong `<script id="__NEXT_DATA__">`: 30 tin/trang, mỗi tin có
mã ngành/nghề/tỉnh/kinh nghiệm/cấp bậc, lương số, ngày tạo, hạn nộp; kèm BẢNG TRA tên của các mã đó trong cùng trang
→ đọc đúng tên nguồn ghi, không suy đoán từ chữ. Bản gốc chỉ giữ danh sách trắng trường thống kê (`_KEEP`) — không
lưu người liên hệ, nhân viên phụ trách, mô tả HTML.

Trang tìm nhanh chỉ phân trang tới ~34 trang (~1.000 tin / 17.000+) → duyệt thêm 53 trang theo nghề
`/viec-lam-<slug>-o<id>.html` (danh sách nghề lấy từ bảng tra trong trang).
"""

import json
import re
from urllib.parse import urljoin

from jobmarket.normalize.text import clean
from jobmarket.sources import jsonld
from jobmarket.sources.base import ListingPage, RawJob
from jobmarket.timeutil import from_timestamp

NAME = "vieclam24h"
BASE = "https://vieclam24h.vn"
PROVIDES = {
    "source_job_id", "title", "company_name", "salary", "location", "experience", "level", "industry",
    "occupation", "posted_date", "deadline",
}  # fmt: skip

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
NEGOTIABLE_RANGE = 11  # job_salary_range 11 = "Thoả thuận"
# Bản gốc chỉ giữ DANH SÁCH TRẮNG các trường cần cho thống kê/chuẩn hoá lại. Không dùng danh sách đen: nguồn có
# trường lồng nhau mang dữ liệu cá nhân (vd employer_info.assigned_staff_username = email nhân viên tuyển dụng,
# phát hiện 2026-11-04) — trường mới nguồn thêm vào sau này cũng không tự lọt vào đĩa.
_KEEP = {
    "id", "title", "title_slug", "created_at", "updated_at", "refresh_at", "approved_at", "resume_apply_expired",
    "salary_min", "salary_max", "salary_range", "salary_type", "salary_unit", "province_ids", "district_ids",
    "occupation_ids_main", "field_ids_main", "field_ids_sub", "experience_range", "level_requirement",
    "degree_requirement", "working_method", "vacancy_quantity", "probation_duration", "employer_id", "status",
    "job_post_type", "is_verified",
}  # fmt: skip
_KEEP_EMPLOYER = {"id", "name", "slug"}


def _payload(item: dict) -> dict:
    kept = {k: v for k, v in item.items() if k in _KEEP}
    employer = item.get("employer_info") or {}
    kept["employer_info"] = {k: v for k, v in employer.items() if k in _KEEP_EMPLOYER}
    return kept


def _data(html: str) -> dict | None:
    match = _NEXT_DATA.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _lookups(data: dict) -> dict[str, dict]:
    state = data.get("props", {}).get("initialState", {})
    common = state.get("api", {}).get("initCommon", {}).get("data", {}) or {}

    def coded(key: str) -> dict:
        return {str(x.get("value")): (x.get("name") or "").strip() for x in common.get(key) or []}

    return {
        "field": {x["id"]: x["name"] for x in common.get("job_field") or []},
        "occupation": {x["id"]: x["name"] for x in common.get("occupation") or []},
        "experience": coded("job_experience_range"),
        "level": coded("job_level_requirement"),
        "province": {p["id"]: p["name"] for p in state.get("AuLocation", {}).get("provinces") or []},
    }


def _jobs_response(data: dict) -> dict:
    props = data.get("props", {})
    response = props.get("initialProps", {}).get("pageProps", {}).get("jobsResponse")
    if response is None:
        response = props.get("initialState", {}).get("api", {}).get("getJobList", {}).get("data")
    return response or {}


def occupation_pages(html: str) -> list[ListingPage]:
    """53 trang danh sách theo nghề, từ bảng tra `occupation` trong trang (không phụ thuộc menu)."""
    data = _data(html) or {}
    common = data.get("props", {}).get("initialState", {}).get("api", {}).get("initCommon", {}).get("data", {}) or {}
    pages = []
    for occ in common.get("occupation") or []:
        if occ.get("slug") and occ.get("id") and occ.get("status", 1) == 1:
            pages.append(ListingPage(f"{BASE}/viec-lam-{occ['slug']}-o{occ['id']}.html", None))
    return pages


def listings(start_url: str, start_html: str) -> list[ListingPage]:
    return [ListingPage(start_url, None), *occupation_pages(start_html)]


def page_url(listing_url: str, page: int) -> str:
    base = re.sub(r"[?&]page=\d+", "", listing_url)
    return base if page == 1 else f"{base}{'&' if '?' in base else '?'}page={page}"


def last_page(html: str, listing_url: str) -> int | None:
    data = _data(html)
    if not data:
        return None
    last = _jobs_response(data).get("last") or _jobs_response(data).get("total_pages")
    return int(last) if last else None


def salary_text(item: dict) -> str | None:
    """Lương số của nguồn → chuỗi đi qua cùng bộ chuẩn hoá. 0/0 hoặc mức "Thoả thuận" → thoả thuận."""
    lo, hi = item.get("salary_min") or 0, item.get("salary_max") or 0
    if item.get("salary_range") == NEGOTIABLE_RANGE or (not lo and not hi):
        return "Thoả thuận"

    def fmt(v: float) -> str:
        return f"{v / 1_000_000:g} triệu"

    if lo and hi and lo != hi:
        return f"{fmt(lo)} - {fmt(hi)}"
    if lo and not hi:
        return f"Từ {fmt(lo)}"
    if hi and not lo:
        return f"Tới {fmt(hi)}"
    return fmt(hi)


def _local_date(epoch) -> str | None:
    try:
        return from_timestamp(int(epoch))[:10] if epoch else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _job_url(html: str, item: dict) -> str | None:
    match = re.search(rf'href="(/[^"]*?id{item["id"]}\.html)', html)
    return urljoin(BASE, match.group(1)) if match else None


def _raw_job(item: dict, names: dict[str, dict], url: str | None, industry_label: str | None) -> RawJob:
    """Một tin (cùng cấu trúc `item` ở trang danh sách và trang chi tiết) → RawJob — một cách đọc cho cả hai."""
    provinces = [names["province"].get(p) for p in item.get("province_ids") or []]
    occupations = item.get("occupation_ids_main") or []
    employer = item.get("employer_info") or {}
    return RawJob(
        source_job_id=str(item["id"]),
        url=url,
        title=item.get("title"),
        company_name=employer.get("name"),
        salary_raw=salary_text(item),
        location_raw=", ".join(p for p in provinces if p) or None,
        experience_raw=names["experience"].get(str(item.get("experience_range"))),
        level_raw=names["level"].get(str(item.get("level_requirement"))),
        # Ngành = NGHỀ chính (chức năng công việc, vd "Bán hàng - Kinh doanh"), cùng cách phân loại với
        # CareerLink/VietJobs. `field_ids_main` là lĩnh vực của công ty (vd bán hàng ở Coca-Cola → "Thực
        # phẩm - Đồ uống") → chỉ giữ trong bản gốc.
        industry_label=(names["occupation"].get(occupations[0]) if occupations else None) or industry_label,
        posted_date=_local_date(item.get("created_at")),
        deadline=_local_date(item.get("resume_apply_expired")),
        raw_payload={"item": _payload(item)},
    )


def parse_listing(html: str, industry_label: str | None) -> list[RawJob]:
    data = _data(html)
    if not data:
        return []
    names = _lookups(data)
    jobs, seen = [], set()
    for item in _jobs_response(data).get("items") or []:
        job_id = str(item.get("id") or "")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        jobs.append(_raw_job(item, names, _job_url(html, item), industry_label))
    return jobs


# --- Sitemap (2026-11-09): robots.txt khai `Sitemap: …/sitemap-index.xml`; sitemap tin `daily/job-0.xml` → các file
# `job/tintuyendung-<n>.xml` (≤ 5.000 URL/file, cập nhật hằng ngày). Trang chi tiết nhúng cùng cấu trúc `item` như
# trang danh sách (`jobDetailHiddenContact.data`, kèm bảng tra tên) → đọc đúng như trang danh sách.
_JOB_SITEMAP = re.compile(r"/sitemap/(daily/job-\d+|job/tintuyendung-\d+)\.xml$")
_JOB_URL_ID = re.compile(r"^https://vieclam24h\.vn/[^?#]*?id(\d+)\.html$")


def follow_sitemap(url: str) -> bool:
    """Sitemap con cần đọc trong sitemap index (chỉ sitemap tin, bỏ blog/nhà tuyển dụng/trang SEO)."""
    return bool(_JOB_SITEMAP.search(url))


def job_id_from_url(url: str) -> str | None:
    match = _JOB_URL_ID.match(url)
    return match.group(1) if match else None


def _detail_item(html: str) -> tuple[dict, dict] | None:
    data = _data(html)
    if not data:
        return None
    api = data.get("props", {}).get("initialState", {}).get("api", {})
    item = (api.get("jobDetailHiddenContact") or {}).get("data")
    if not isinstance(item, dict) or not item.get("id"):
        return None
    return item, data


def parse_detail_job(html: str, url: str) -> RawJob | None:
    """Trang chi tiết → RawJob; trang không có dữ liệu tin (đã gỡ, đổi cấu trúc, bị hạn chế ngầm) → None."""
    found = _detail_item(html)
    return _raw_job(found[0], _lookups(found[1]), url, None) if found else None


# Phần "yêu cầu" của tin nằm rải ở các trường này (bản _html ưu tiên — giữ xuống dòng)
_REQUIREMENT_FIELDS = ("job_requirement", "skill_requirement", "other_requirement")


def parse_detail(html: str) -> dict | None:
    """Trang chi tiết → trường cho `pipeline.apply_detail`: mô tả, yêu cầu công việc, kinh nghiệm (chữ nguồn ghi, vd
    "1 năm"), hạn nộp… — đọc cùng `item` + bảng tra như trang danh sách (ngành = nghề chính, ngày đăng = created_at)."""
    found = _detail_item(html)
    if not found:
        return None
    item, data = found
    job = _raw_job(item, _lookups(data), None, None)
    description, removed = jsonld.html_to_text(item.get("description_html") or item.get("description"))
    parts = [item.get(f"{name}_html") or item.get(name) for name in _REQUIREMENT_FIELDS]
    requirements, removed_req = jsonld.html_to_text("<br>".join(p for p in parts if isinstance(p, str) and p.strip()))
    posting = jsonld.find_job_posting(html) or {}
    return {
        "industry": job.industry_label,
        "industries": [],
        "experience": job.experience_raw,
        "posted_date": job.posted_date,
        "deadline": job.deadline,
        "employment_type": clean(str(posting.get("employmentType") or "")) or None,
        "description_text": description,
        "requirements_text": requirements,
        "pii_removed": removed + removed_req,
        "jsonld": {"item": _payload(item)},  # bản gốc: chỉ danh sách trắng, không liên hệ
    }
