"""Nạp bộ dữ liệu VietJobs — dùng file mẫu chính thức của tác giả (data/VietJobs_sample.csv, MIT)."""

from pathlib import Path

import pytest

from jobmarket import stats
from jobmarket.sources.vietjobs import import_vietjobs
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

SAMPLE = Path(__file__).parent / "fixtures" / "vietjobs_sample.csv"


@pytest.fixture
def loaded(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    run_id, inserted = import_vietjobs(conn, tmp_cfg, SAMPLE)
    return conn, run_id, inserted


def test_imports_all_rows_as_historical(loaded):
    conn, run_id, inserted = loaded
    assert inserted == 5
    rows = conn.execute("SELECT * FROM job_posts WHERE source = 'vietjobs'").fetchall()
    assert {r["status"] for r in rows} == {"expired"}
    assert {r["first_seen_at"] for r in rows} == {"2025-10-31T00:00:00"}
    assert {r["dq_status"] for r in rows} <= {"ok", "warn"}  # thiếu tên công ty không làm reject
    assert all(r["description_text"] for r in rows)


def test_normalized_fields(loaded):
    conn, _, _ = loaded
    df = stats.load_unique(conn)
    first = df[df["title"] == "Nhân Viên Vận Hành Hệ Thống Xử Lý Nước Thải"].iloc[0]
    assert first["province_code"] == "HCM"
    assert first["experience_code"] == "Y1_2"
    assert first["salary_type"] == "range"
    assert first["salary_mid"] == pytest.approx(18_500_000)
    multi = df[df["title"].str.startswith("Chuyên Gia Đánh Giá ISO")].iloc[0]
    assert multi["province_code"] == "HN"  # "hà nội, hồ chí minh" → địa điểm đầu
    industry = conn.execute("SELECT DISTINCT raw_label FROM labels WHERE label_type = 'industry'").fetchall()
    assert [r[0] for r in industry] == ["nông nghiệp năng lượng môi trường"]


def test_reimport_is_idempotent(loaded, tmp_cfg):  # noqa: F811
    conn, _, _ = loaded
    _, again = import_vietjobs(conn, tmp_cfg, SAMPLE)
    assert again == 0
    assert conn.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 5


def test_zero_salary_means_not_stated(tmp_cfg, tmp_path):  # noqa: F811
    text = SAMPLE.read_text(encoding="utf-8")
    header, first, *_ = text.splitlines(keepends=True)
    zero = first.replace("15.0 - 22.0 triệu", "0.0 triệu").replace(",15,22,18.5", ",0,0,0.0")
    path = tmp_path / "zero.csv"
    path.write_text(header + zero, encoding="utf-8")
    conn = init_db(tmp_cfg.path("db_file"))
    import_vietjobs(conn, tmp_cfg, path)
    row = conn.execute("SELECT salary_type, salary_raw, dq_flags FROM job_posts").fetchone()
    assert row["salary_type"] == "not_stated" and row["salary_raw"] is None
    assert "salary_invalid" not in row["dq_flags"]


def test_history_has_no_known_posting_date(loaded):
    """Ảnh chụp không có ngày đăng → không vào chỉ số theo thời gian (tách khỏi dữ liệu hiện tại do dashboard
    chọn "Bộ dữ liệu", xem tests/test_dashboard.py::test_datasets_are_not_mixed)."""
    from datetime import date

    conn, _, _ = loaded
    df = stats.load_unique(conn)
    assert (~df["date_known"]).all() and df["event_date"].isna().all()
    o = stats.overview(df, date(2025, 10, 31), 30)
    assert o["new_window"] == 0 and o["unique_jobs"] == 5  # vẫn đếm vào hiện trạng, không vào "tin mới"


def test_level_from_title_is_set(loaded):
    conn, _, _ = loaded
    titles = dict(conn.execute("SELECT title, level_from_title FROM job_posts"))
    assert titles["Chuyên Gia Đánh Giá ISO 22000–HACCP–VIETGAP"] is None  # không có từ khoá → không đoán
    assert all(
        v in (None, "INTERN", "JUNIOR", "STAFF", "SENIOR", "LEAD", "MANAGER", "DIRECTOR") for v in titles.values()
    )
