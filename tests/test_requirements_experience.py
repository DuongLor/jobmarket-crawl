"""Mô tả + yêu cầu công việc + kinh nghiệm theo tháng (migration 011, 2026-11-11). Không gọi mạng."""

import copy
import dataclasses
import json

import httpx
import pytest

from jobmarket.fetch.client import PoliteClient
from jobmarket.normalize.experience import experience_months
from jobmarket.pipeline import backfill_experience_requirements, run_paged
from jobmarket.sources import careerlink, itviec
from jobmarket.sources import vieclam24h as v
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)
from tests.test_sitemap_cooldown import LISTING, _site, _url


@pytest.mark.parametrize(
    ("raw", "months"),
    [
        ("Chưa có kinh nghiệm", (0, 0)),
        ("Không yêu cầu kinh nghiệm", (0, 0)),
        ("no requirements", (0, 0)),
        ("Dưới 1 năm", (0, 12)),
        ("Under 1 year", (0, 12)),
        ("1 năm", (12, None)),  # một con số = mức tối thiểu
        ("12 tháng", (12, None)),
        ("1,5 năm", (18, None)),
        ("1 - 2 năm", (12, 24)),  # hồi quy: norm_key xoá "-" → "1 2 nam" bị đọc thành 24 tháng
        ("2-5 năm", (24, 60)),
        ("6 tháng - 1 năm", (6, 12)),
        ("Từ 2 đến 4 năm", (24, 48)),
        ("2 to 3 years", (24, 36)),
        ("Hơn 5 năm", (60, None)),
        ("Tối đa 2 năm", (0, 24)),
        ("5 - 3 năm", (None, None)),  # ngược → không đoán
        ("Kinh nghiệm là lợi thế", (None, None)),
        (None, (None, None)),
    ],
)
def test_experience_months(raw, months):
    assert experience_months(raw) == months


def test_careerlink_splits_requirements_from_description():
    text = "Tư vấn khách hàng.\n\nChăm sóc khách hàng.\n\n* Kinh nghiệm / Kỹ năng chi tiết:\n\nTốt nghiệp CĐ trở lên."
    assert careerlink.split_requirements(text) == (
        "Tư vấn khách hàng.\n\nChăm sóc khách hàng.",
        "Tốt nghiệp CĐ trở lên.",
    )
    assert careerlink.split_requirements("Chỉ có mô tả.") == ("Chỉ có mô tả.", None)
    assert careerlink.split_requirements(None) == (None, None)


def _itviec_detail(lang: str = "vi") -> str:
    titles = (
        ("Mô tả công việc", "Yêu cầu công việc") if lang == "vi" else ("Job description", "Your skills and experience")
    )
    posting = {"@type": "JobPosting", "title": "Backend Engineer", "description": "<p>mô tả JSON-LD</p>",
               "experienceRequirements": {"monthsOfExperience": 10}, "datePosted": "2026-11-09"}  # fmt: skip
    return (
        f"<html><script type='application/ld+json'>{json.dumps(posting)}</script>"
        f"<div class='imy-5 paragraph'><h2>{titles[0]}</h2><div><p>Xây dựng API.</p></div></div>"
        f"<div class='imy-5 paragraph'><h2>{titles[1]}</h2><div><ul><li>3+ năm Python</li>"
        "<li>Liên hệ hr@example.com</li></ul></div></div>"
        "<div class='paragraph'><h2>Việc làm tương tự dành cho bạn</h2></div></html>"
    )


@pytest.mark.parametrize("lang", ["vi", "en"])
def test_itviec_sections(lang):
    detail = itviec.parse_detail(_itviec_detail(lang))
    assert detail["description_text"] == "Xây dựng API."
    assert detail["requirements_text"].startswith("3+ năm Python")
    assert "@" not in detail["requirements_text"] and detail["pii_removed"]  # email bị xoá
    assert detail["experience"] is None  # JSON-LD không đáng tin → không lấy


def _v24_detail(job_id: int) -> str:
    data = v._data(LISTING)
    item = copy.deepcopy(v._jobs_response(data)["items"][0])
    item.update(
        id=job_id,
        description_html="<p>Canh gác tài sản;<br>Gọi 0901234567 khi cần</p>",
        other_requirement_html="<p>Có tối thiểu 01 năm kinh nghiệm</p>",
        job_requirement=None,
        contact_email="hr@example.com",
    )
    data["props"]["initialState"]["api"]["jobDetailHiddenContact"] = {"data": item}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_vieclam24h_detail_fields():
    detail = v.parse_detail(_v24_detail(300000001))
    assert detail["description_text"].startswith("Canh gác tài sản;") and "0901234567" not in detail["description_text"]
    assert detail["requirements_text"] == "Có tối thiểu 01 năm kinh nghiệm"
    assert detail["experience"] == v.parse_listing(LISTING, None)[0].experience_raw  # "2 năm" — cùng bảng tra
    assert "hr@example.com" not in json.dumps(detail["jsonld"], ensure_ascii=False)
    assert v.parse_detail("<html></html>") is None


def test_sitemap_job_gets_description_requirements_experience(tmp_cfg, monkeypatch):  # noqa: F811
    raw = copy.deepcopy(tmp_cfg.raw)
    raw["sources"]["vieclam24h"].update(
        sitemap_url="https://vieclam24h.vn/file/sitemap/sitemap-index.xml", sitemap_max_per_run=5
    )
    cfg = dataclasses.replace(tmp_cfg, raw=raw)
    base = _site([_url(300000001)])

    def handler(request):
        if "/nganh/" in request.url.path:
            return httpx.Response(200, text=_v24_detail(300000001))
        return base(request)

    conn = init_db(cfg.path("db_file"))
    run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    row = conn.execute(
        "SELECT description_text, requirements_text, experience_raw, experience_min_months, experience_max_months, "
        "detail_fetched_at FROM job_posts WHERE source_job_id = '300000001'"
    ).fetchone()
    assert row["description_text"] and row["requirements_text"] == "Có tối thiểu 01 năm kinh nghiệm"
    assert (row["experience_raw"], row["experience_min_months"], row["experience_max_months"]) == ("2 năm", 24, None)
    assert row["detail_fetched_at"]  # tầng trang chi tiết không đọc lại


def test_backfill_fills_months_from_labels_and_splits_careerlink(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    run_paged(
        conn,
        tmp_cfg,
        PoliteClient(tmp_cfg.crawl, transport=httpx.MockTransport(_site([]))),
        v,
        max_pages=1,
    )
    with conn:  # giả lập tin trước migration 011
        conn.execute("UPDATE job_posts SET experience_raw = NULL, experience_min_months = NULL")
        run_id = conn.execute("SELECT MAX(id) FROM runs").fetchone()[0]
        conn.execute(
            "INSERT INTO job_posts (source, source_job_id, title, description_text, status, dq_status, dq_flags, "
            "first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version) "
            "VALUES ('careerlink', '9', 't', ?, 'unknown', 'ok', '', 'x', 'x', ?, ?, 1)",
            ("Mô tả.\n* Kinh nghiệm / Kỹ năng chi tiết:\nYêu cầu.", run_id, run_id),
        )
    counts = backfill_experience_requirements(conn)
    assert counts["requirements"] == 1 and counts["experience"] == 3
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM job_posts WHERE experience_label_id IS NOT NULL AND experience_min_months IS NULL"
        ).fetchone()[0]
        == 0
    )
    cl = conn.execute(
        "SELECT description_text, requirements_text FROM job_posts WHERE source = 'careerlink'"
    ).fetchone()
    assert tuple(cl) == ("Mô tả.", "Yêu cầu.")
    assert backfill_experience_requirements(conn) == {"experience": 0, "requirements": 0}  # chạy lại không đổi gì


@pytest.mark.parametrize(
    ("low", "high", "text"),
    [
        (None, None, "chưa rõ"),
        (0, 0, "không yêu cầu"),
        (0, 12, "dưới 1 năm"),
        (12, None, "từ 1 năm"),
        (12, 24, "1 năm – 2 năm"),
        (6, 12, "6 tháng – 1 năm"),
        (18, None, "từ 18 tháng (~1,5 năm)"),
    ],
)
def test_dashboard_months_range(low, high, text):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
    import common

    assert common.months_range(low, high) == text
