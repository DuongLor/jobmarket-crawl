"""VietnamWorks (vietnamworks.com) — khảo sát 2026-11-12 (docs/spike-0.md lượt 15), user cho phép.

robots.txt: www 404, ms.vietnamworks.com 404 → không hạn chế. Trang tìm kiếm không có tin trong HTML; trình duyệt gọi
API tìm kiếm `POST ms.vietnamworks.com/job-search/v1.0/search` — công khai, KHÔNG đăng nhập/token/cookie. Bot tự khai
danh tính nhận 200: 9.683 tin, phân trang `page`/`hitsPerPage`.

Mỗi tin trong kết quả đã có đủ trường (không cần trang chi tiết): lương số + tiền tệ, cấp bậc, số năm kinh nghiệm, nhóm
chức năng (ngành), nơi làm việc, ngày duyệt, hạn nộp, kỹ năng, mô tả + yêu cầu (HTML).
Có `contactName`, `emailAddress` → KHÔNG lưu (bản gốc chỉ giữ danh sách trắng `_KEEP`; mô tả/yêu cầu qua pii.scrub).
"""

import json

from jobmarket.sources import jsonld
from jobmarket.sources.base import RawJob
from jobmarket.timeutil import local_date

NAME = "vietnamworks"
SEARCH_URL = "https://ms.vietnamworks.com/job-search/v1.0/search"
PROVIDES = {
    "source_job_id", "title", "company_name", "salary", "location", "experience", "level", "industry",
    "occupation", "skills", "posted_date", "deadline",
}  # fmt: skip

# Bản gốc chỉ giữ trường phục vụ thống kê/kiểm tra lại — không người liên hệ, email, mô tả HTML
_KEEP = (
    "jobId", "jobTitle", "jobUrl", "companyId", "companyName", "companySize", "salaryMin", "salaryMax",
    "salaryCurrency", "prettySalary", "isSalaryVisible", "jobLevel", "jobLevelVI", "yearsOfExperience",
    "industriesV3", "jobFunction", "approvedOn", "onlineOn", "expiredOn", "typeWorkingId", "numberOfRecruits",
)  # fmt: skip


def search_body(page: int, per_page: int) -> dict:
    """Trang `page` (đếm từ 0) của toàn bộ tin, mới duyệt trước."""
    return {"userId": 0, "query": "", "filter": [], "ranges": [], "order": [], "hitsPerPage": per_page, "page": page}


def salary_text(job: dict) -> str | None:
    """Lương số → chuỗi đi qua cùng bộ chuẩn hoá lương. Ẩn lương / 0-0 → thoả thuận."""
    lo, hi = job.get("salaryMin") or 0, job.get("salaryMax") or 0
    if not job.get("isSalaryVisible") or (not lo and not hi):
        return "Thoả thuận"
    currency = (job.get("salaryCurrency") or "VND").upper()

    def fmt(v: float) -> str:
        # Số nguyên đầy đủ, không dạng mũ ("2e+07") — bộ chuẩn hoá lương không đọc được dạng mũ. Nguồn đôi khi ghi
        # "USD" cho số VND (20.000.000 USD/tháng) → để bộ chuẩn hoá gắn cờ salary_invalid, không đoán lại tiền tệ.
        return f"{v / 1_000_000:g} triệu" if currency == "VND" else f"{v:.0f} {currency}"

    if lo and hi and lo != hi:
        return f"{fmt(lo)} - {fmt(hi)}"
    if lo and not hi:
        return f"Từ {fmt(lo)}"
    if hi and not lo:
        return f"Tới {fmt(hi)}"
    return fmt(hi)


def experience_text(years) -> str | None:
    """Số năm kinh nghiệm. 0 = nguồn không ghi rõ (cả tin "Mới tốt nghiệp" lẫn tin không điền) → None, không đoán."""
    try:
        n = int(years)
    except (TypeError, ValueError):
        return None
    return f"{n} năm" if n > 0 else None


def _provinces(job: dict) -> list[str]:
    names: list[str] = []
    for loc in job.get("workingLocations") or []:
        name = loc.get("cityNameVI") or loc.get("cityName")
        if name and name not in names:
            names.append(name)
    return names


def _function(job: dict) -> tuple[str | None, str | None]:
    """(nhóm chức năng cha, chức năng con đầu tiên) — "Kiến Trúc/Xây Dựng", "Xây Dựng"."""
    fn = job.get("jobFunction") or {}
    children = fn.get("children") or []
    child = children[0].get("nameVI") or children[0].get("name") if children else None
    return fn.get("parentNameVI") or fn.get("parentName"), child


def parse_job(job: dict) -> RawJob | None:
    job_id = str(job.get("jobId") or "")
    if not job_id.isdigit():
        return None
    industry, occupation = _function(job)
    description, removed = jsonld.html_to_text(job.get("jobDescription"))
    requirements, removed_req = jsonld.html_to_text(job.get("jobRequirement"))
    skills = [s.get("skillName") for s in job.get("skills") or [] if s.get("skillName")]
    return RawJob(
        source_job_id=job_id,
        url=job.get("jobUrl"),
        title=job.get("jobTitle"),
        company_name=None if job.get("isAnonymous") else job.get("companyName"),
        salary_raw=salary_text(job),
        location_raw=", ".join(_provinces(job)) or None,
        experience_raw=experience_text(job.get("yearsOfExperience")),
        level_raw=job.get("jobLevelVI") or job.get("jobLevel"),
        # Ngành = nhóm CHỨC NĂNG công việc (cùng cách phân loại với Việc Làm 24h/CareerViet); `industriesV3` là lĩnh vực
        # của công ty → chỉ giữ trong bản gốc
        industry_label=industry,
        occupation_label=occupation,
        skills_raw=", ".join(skills) or None,
        posted_date=local_date(job.get("approvedOn")),
        deadline=local_date(job.get("expiredOn")),
        description_text=description,
        requirements_text=requirements,
        raw_payload={"item": {k: job[k] for k in _KEEP if k in job}},
        pii_removed=removed + removed_req,
    )


def parse_search(text: str) -> tuple[list[RawJob], int, int]:
    """Kết quả API → (tin, tổng số trang, tổng số tin). Không phải JSON đúng dạng → ValueError."""
    data = json.loads(text)
    meta = data.get("meta") or {}
    if not isinstance(data.get("data"), list):
        raise ValueError(f"VietnamWorks: kết quả tìm kiếm không có 'data' (meta={meta})")
    jobs = [j for j in (parse_job(item) for item in data["data"]) if j]
    return jobs, int(meta.get("nbPages") or 0), int(meta.get("nbHits") or 0)
