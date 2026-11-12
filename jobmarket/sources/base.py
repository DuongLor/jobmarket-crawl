from dataclasses import dataclass, field


@dataclass
class ListingPage:
    url: str
    industry_label: str | None  # ngành của trang đang duyệt (None với trang "mới nhất")


@dataclass
class RawJob:
    """Dữ liệu 1 tin đúng như nguồn ghi, chưa chuẩn hoá. Chuỗi văn bản đã xoá PII."""

    source_job_id: str | None
    url: str | None
    title: str | None
    company_name: str | None
    salary_raw: str | None = None
    location_raw: str | None = None
    experience_raw: str | None = None
    level_raw: str | None = None
    industry_label: str | None = None
    # Mọi mục ngành của chính tin, theo thứ tự nguồn (nguồn cho nhiều ngành/tin) → thống kê đếm chia đều
    industry_labels: list[str] = field(default_factory=list)
    occupation_label: str | None = None
    skills_raw: str | None = None
    posted_date: str | None = None
    deadline: str | None = None
    description_text: str | None = None
    requirements_text: str | None = None  # yêu cầu công việc (đã xoá PII)
    raw_payload: dict = field(default_factory=dict)
    pii_removed: list[str] = field(default_factory=list)
