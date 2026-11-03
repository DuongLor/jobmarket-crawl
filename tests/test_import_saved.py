import os

import pytest

from jobmarket.pipeline import import_saved_pages
from jobmarket.sources import topcv
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


@pytest.mark.parametrize(
    ("title", "industry"),
    [
        ("Tuyển dụng 2.616 việc làm Công nghệ Thông tin [Update 03/11/2026]", "Công nghệ Thông tin"),
        ("Tuyển dụng 512 việc làm Kế toán/Kiểm toán/Thuế [Update 03/11/2026]", "Kế toán/Kiểm toán/Thuế"),
        ("Tuyển dụng 41.600 việc làm [Update 03/11/2026]", None),
        ("", None),
    ],
)
def test_page_industry(title, industry):
    assert topcv.page_industry(f"<html><head><title>{title}</title></head></html>") == industry


def test_import_saved_pages(tmp_cfg, topcv_listing_html, tmp_path):  # noqa: F811
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    page = inbox / "cntt.html"
    page.write_text(
        topcv_listing_html.replace(
            "<html>",
            "<html><head><title>Tuyển dụng 2.616 việc làm Công nghệ Thông tin [Update]</title></head>",
            1,
        ),
        encoding="utf-8",
    )
    saved_at = 1_793_000_000  # thời điểm lưu file giả định
    os.utime(page, (saved_at, saved_at))
    junk = inbox / "khac.html"
    junk.write_text("<html><title>Trang khác</title></html>", encoding="utf-8")

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = import_saved_pages(conn, tmp_cfg, [page, junk])

    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert (run["status"], run["posts_new"], run["errors_count"]) == ("partial", 8, 1)
    assert (inbox / "done" / "cntt.html").exists() and not page.exists()
    assert junk.exists()  # file không hợp lệ giữ nguyên chỗ cũ
    row = conn.execute("SELECT * FROM v_jobs_unique WHERE source_job_id = '2342204'").fetchone()
    assert row["industry_code"] == "IND01"
    # theo mtime của file (giờ VN, không múi giờ), không phải lúc nạp: 1_793_000_000 = 2026-10-26 07:33:20 UTC
    assert row["first_seen_at"] == "2026-10-26T14:33:20"
