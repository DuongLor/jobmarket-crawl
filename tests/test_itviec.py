"""ITviec: thẻ tin trang danh sách (mẫu rút gọn từ trang thật 2026-11-10) + JSON-LD trang chi tiết. Mạng giả lập."""

import copy
import dataclasses
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from jobmarket import stats
from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import run_paged
from jobmarket.sources import itviec
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

HTML = (Path(__file__).parent / "fixtures" / "itviec" / "listing_sample.html").read_text(encoding="utf-8")
MULTI = "a002be9c-8c0e-4883-ae5d-06894d985572"


def test_listing_fields():
    jobs = {j.source_job_id: j for j in itviec.parse_listing(HTML, None)}
    assert len(jobs) == 3
    j = jobs[MULTI]
    assert j.title == "Lead Penetration Tester (Pentester)"
    assert j.company_name == "ITviec Recruitment Consulting"
    assert j.url == "https://itviec.com/it-jobs/lead-penetration-tester-pentester-itviec-recruitment-consulting-2038"
    assert j.location_raw == "Ho Chi Minh, Ha Noi"  # "Ho Chi Minh - Ha Noi" → 2 nơi
    assert (j.occupation_label, j.industry_label) == ("Manager", "Information Technology")
    assert j.skills_raw.startswith("Cybersecurity, Cloud Security")
    assert j.salary_raw is None and j.experience_raw is None  # lương ẩn; kinh nghiệm không lấy
    assert all("?" not in job.url for job in jobs.values())  # bỏ tham số theo dõi
    assert "@" not in json.dumps([job.raw_payload for job in jobs.values()], ensure_ascii=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Posted 31 minutes ago", "2026-11-10"),
        ("Posted\n  20 hours ago", "2026-11-10"),
        ("Posted 1 day ago", "2026-11-09"),
        ("Posted 3 days ago", "2026-11-07"),
        ("Đăng 2 ngày trước", "2026-11-08"),
        ("Posted 2 weeks ago", None),  # không đủ chính xác tới ngày → trang chi tiết bổ sung
        ("", None),
        (None, None),
    ],
)
def test_posted_date_relative(text, expected):
    assert itviec.posted_date(text, today=date(2026, 11, 10)) == expected


def test_pagination():
    assert itviec.last_page(HTML, itviec.BASE) == 31
    assert itviec.page_url("https://itviec.com/it-jobs", 1) == "https://itviec.com/it-jobs"
    assert itviec.page_url("https://itviec.com/it-jobs?page=4", 2) == "https://itviec.com/it-jobs?page=2"


def _detail(months: int = 10) -> str:
    """JSON-LD cùng cấu trúc trang thật (2026-11-10); `monthsOfExperience` mâu thuẫn mô tả như tin thật."""
    posting = {
        "@context": "http://schema.org",
        "@type": "JobPosting",
        "industry": "Information Technology",
        "title": "Lead Penetration Tester (Pentester)",
        "datePosted": "2026-11-09",
        "validThrough": "2026-12-14",
        "skills": "Cybersecurity, Cloud Security",
        "hiringOrganization": {"@type": "Organization", "name": "ITviec Recruitment Consulting"},
        "employmentType": "FULL_TIME",
        "experienceRequirements": {"@type": "OccupationalExperienceRequirements", "monthsOfExperience": months},
        "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressRegion": "Hồ Chí Minh"}}],
        "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"value": "You'll love it"}},
        "description": "<p>At least 3 years of experience in penetration testing.</p>",
    }
    return f"<html><script type='application/ld+json'>{json.dumps(posting)}</script></html>"


def test_detail_drops_unreliable_experience_and_hidden_salary():
    detail = itviec.parse_detail(_detail())
    assert detail["experience"] is None
    assert detail["salary_raw"] is None
    assert (detail["posted_date"], detail["deadline"], detail["industry"]) == (
        "2026-11-09",
        "2026-12-14",
        "Information Technology",
    )
    assert itviec.parse_detail("<html>không có tin</html>") is None


def test_run_end_to_end(tmp_cfg):  # noqa: F811
    raw = copy.deepcopy(tmp_cfg.raw)
    raw["sources"]["itviec"].update(detail_max_per_run=5)
    cfg = dataclasses.replace(tmp_cfg, raw=raw)

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /subscriptions/new\nUser-Agent: *\nAllow: /\n")
        if request.url.path.startswith("/it-jobs/"):
            return httpx.Response(200, text=_detail())
        return httpx.Response(200, text=HTML)

    conn = init_db(cfg.path("db_file"))
    run_id = run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), itviec, 1)
    assert conn.execute("SELECT status, posts_new FROM runs WHERE id = ?", (run_id,)).fetchone()[:] == ("success", 3)
    df = stats.load_unique(conn).set_index("source_job_id")
    assert set(df["industry_code"]) == {"IND01"}  # "Information Technology" → CNTT (seeds/industry_aliases.csv)
    assert df["salary_mid"].isna().all() and df["experience_code"].isna().all()
    row = conn.execute(
        "SELECT id, deadline, status, detail_fetched_at FROM job_posts WHERE source_job_id = ?", (MULTI,)
    ).fetchone()
    assert (row["deadline"], row["status"]) == ("2026-12-14", "active") and row["detail_fetched_at"]
    provinces = {
        r[0]
        for r in conn.execute(
            "SELECT l.master_code FROM job_locations jl JOIN labels l ON l.id = jl.label_id WHERE jl.job_id = ?",
            (row["id"],),
        )
    }
    assert provinces == {"HCM", "HN"}
