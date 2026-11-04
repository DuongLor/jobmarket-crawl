"""JSON-LD JobPosting dùng chung (sources/jsonld.py) + trang chi tiết TopCV tự lưu (import-html)."""

import os
from pathlib import Path

import pytest

from jobmarket.pipeline import import_saved_pages
from jobmarket.sources import jsonld, topcv
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

DETAIL = (Path(__file__).parent / "fixtures" / "topcv" / "detail_synthetic.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("base", "text"),
    [
        ({"currency": "VND", "value": {"minValue": 12_000_000, "maxValue": 20_000_000, "unitText": "MONTH"}},
         "12 triệu - 20 triệu"),
        ({"currency": "USD", "value": {"minValue": 1000, "maxValue": 1500, "unitText": "MONTH"}},
         "1,000 USD - 1,500 USD"),
        ({"currency": "VND", "value": {"value": 25_000, "unitText": "HOUR"}}, "25.000 VND/giờ"),
        ({"currency": "VND", "value": {"minValue": 15_000_000, "unitText": "MONTH"}}, "Từ 15 triệu"),
        ({"currency": "VND", "value": {"minValue": 0, "maxValue": 0}}, None),  # 0 = thoả thuận
        (None, None),
    ],
)  # fmt: skip
def test_salary_text(base, text):
    assert jsonld.salary_text(base) == text


def test_parse_topcv_detail_page():
    job, detail = topcv.parse_detail_page(DETAIL)
    assert job.source_job_id == "2342204"  # cùng mã với data-job-id trên thẻ tin danh sách
    assert (job.company_name, job.location_raw) == ("CÔNG TY CỔ PHẦN MISA", "Hà Nội")
    assert job.salary_raw == "12 triệu - 20 triệu"
    assert detail["industries"] == ["Kinh doanh / Bán hàng", "IT - Phần mềm"]
    assert (detail["experience"], detail["posted_date"], detail["deadline"]) == ("12 tháng", "2026-10-20", "2026-11-30")
    assert "hr@example.com" not in detail["description_text"] and "0912" not in detail["description_text"]


def test_job_id_from_url():
    assert topcv.job_id_from_url("https://www.topcv.vn/viec-lam/nhan-vien-ke-toan/2335343.html") == "2335343"
    assert topcv.job_id_from_url("https://www.topcv.vn/brand/misa/tuyen-dung/sale-j2342204.html?ta_source=x") == (
        "2342204"
    )
    assert topcv.job_id_from_url("https://www.topcv.vn/cong-ty/misa/123.html?x") == "123"
    assert topcv.job_id_from_url(None) is None


def test_import_detail_page_enriches_job_seen_on_listing(tmp_cfg, topcv_listing_html, tmp_path):  # noqa: F811
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    listing, detail = inbox / "list.html", inbox / "chi-tiet.html"
    listing.write_text(topcv_listing_html, encoding="utf-8")
    detail.write_text(DETAIL, encoding="utf-8")
    os.utime(listing, (1_793_000_000, 1_793_000_000))
    os.utime(detail, (1_793_000_100, 1_793_000_100))
    conn = init_db(tmp_cfg.path("db_file"))
    run_id = import_saved_pages(conn, tmp_cfg, [listing, detail])
    run = conn.execute("SELECT status, posts_new, errors_count FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert tuple(run) == ("success", 8, 0)  # trang chi tiết bổ sung tin đã có, không tạo tin trùng
    row = conn.execute(
        "SELECT p.posted_date, p.deadline, p.status, p.detail_fetched_at, le.master_code AS exp, li.raw_label AS ind "
        "FROM job_posts p LEFT JOIN labels le ON le.id = p.experience_label_id "
        "LEFT JOIN labels li ON li.id = p.industry_label_id WHERE p.source_job_id = '2342204'"
    ).fetchone()
    assert (row["posted_date"], row["deadline"], row["exp"]) == ("2026-10-20", "2026-11-30", "Y1_2")
    assert row["detail_fetched_at"] is not None
