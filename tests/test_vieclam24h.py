"""Việc Làm 24h: đọc dữ liệu có cấu trúc (__NEXT_DATA__) của trang danh sách — mẫu rút gọn từ trang thật 2026-11-04."""

import json
from pathlib import Path

import httpx
import pytest

from jobmarket import stats
from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import run_paged
from jobmarket.sources import vieclam24h as v
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

HTML = (Path(__file__).parent / "fixtures" / "vieclam24h" / "listing_sample.html").read_text(encoding="utf-8")


def test_parse_listing_fields_from_structured_data():
    jobs = {j.source_job_id: j for j in v.parse_listing(HTML, None)}
    assert len(jobs) == 3
    j = jobs["200982377"]
    assert j.title == "Trưởng Phòng Kinh Doanh"
    assert j.salary_raw == "15 triệu - 20 triệu"
    assert (j.location_raw, j.experience_raw, j.level_raw) == ("TP.HCM", "2 năm", "Trưởng phòng")
    assert (j.posted_date, j.deadline) == ("2026-11-03", "2026-11-30")
    assert j.url == "https://vieclam24h.vn/xay-dung/truong-phong-kinh-doanh-c31p210000187id200982377.html"
    # Ngành = nghề chính (chức năng), không phải lĩnh vực công ty
    assert jobs["200978026"].industry_label == "Bán hàng - Kinh doanh"
    assert jobs["200970224"].location_raw == "Lào Cai, Hà Nội"


def test_raw_payload_has_no_contact_info():
    """Hồi quy 2026-11-04: email nhân viên nằm LỒNG trong employer_info.assigned_staff_username."""
    assert "assigned_staff_username" in HTML  # mẫu thật có trường này → test có ý nghĩa
    for job in v.parse_listing(HTML, None):
        item = job.raw_payload["item"]
        assert set(item) <= v._KEEP | {"employer_info"}
        assert set(item["employer_info"]) <= v._KEEP_EMPLOYER
        assert "@" not in json.dumps(job.raw_payload, ensure_ascii=False)


def test_bad_epoch_does_not_break_page():
    assert v._local_date("abc") is None and v._local_date(None) is None


@pytest.mark.parametrize(
    ("item", "text"),
    [
        ({"salary_min": 15_000_000, "salary_max": 20_000_000, "salary_range": 21}, "15 triệu - 20 triệu"),
        ({"salary_min": 0, "salary_max": 0, "salary_range": 11}, "Thoả thuận"),
        ({"salary_min": 8_500_000, "salary_max": 0, "salary_range": 21}, "Từ 8.5 triệu"),
        ({"salary_min": 12_000_000, "salary_max": 12_000_000, "salary_range": 21}, "12 triệu"),
    ],
)
def test_salary_text(item, text):
    assert v.salary_text(item) == text


def test_pagination_and_occupation_listings():
    assert v.last_page(HTML, v.BASE) == 34
    pages = v.occupation_pages(HTML)
    assert len(pages) == 53 and pages[0].url == "https://vieclam24h.vn/viec-lam-hanh-chinh-thu-ky-o1.html"
    assert v.page_url("https://vieclam24h.vn/viec-lam-it-phan-mem-o8.html", 3).endswith("o8.html?page=3")


def test_run_maps_industry_level_experience_and_uses_deadline(tmp_cfg):  # noqa: F811
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /admin/\n")
        if request.url.path == "/tim-kiem-viec-lam-nhanh" and int(request.url.params.get("page", 1)) == 1:
            return httpx.Response(200, text=HTML)
        return httpx.Response(200, text="<html></html>")

    conn = init_db(tmp_cfg.path("db_file"))
    run_paged(conn, tmp_cfg, PoliteClient(tmp_cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    df = stats.load_unique(conn).set_index("source_job_id")
    row = df.loc["200982377"]
    assert (row["province_code"], row["experience_code"], row["level_code"]) == ("HCM", "Y2_3", "MANAGER")
    assert df.loc["200978026", "industry_code"] == "IND03"  # Bán hàng - Kinh doanh
    assert row["salary_mid"] == pytest.approx(17_500_000)
    assert conn.execute("SELECT COUNT(*) FROM job_posts WHERE status = 'expired'").fetchone()[0] == 0
