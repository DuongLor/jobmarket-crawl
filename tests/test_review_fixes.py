"""Hồi quy cho các lỗi tìm ra ở lượt rà soát 2026-11-05 (risk-reviewer + tester)."""

import httpx
import pandas as pd
import pytest

from jobmarket import stats
from jobmarket.fetch.client import Blocked, PoliteClient
from jobmarket.fetch.robots import RobotsRules
from jobmarket.pii import scrub
from jobmarket.pipeline import RunStats, apply_detail, save_job
from jobmarket.sources import careerlink
from jobmarket.store import raw as raw_store
from jobmarket.store.db import init_db
from tests.test_paged_sources import CL_HTML
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

UA = "jobmarket-crawl/0.1 (personal, non-commercial, low-rate)"


@pytest.fixture
def conn(tmp_cfg):  # noqa: F811
    db = init_db(tmp_cfg.path("db_file"))
    with db:
        db.execute(
            "INSERT INTO runs (id, source, scope, started_at, app_version) "
            "VALUES (1, 'careerlink', '{\"start_url\": \"https://x.vn/list\"}', '2026-11-03', 't')"
        )
    return db


def _save(conn, cfg, job, seen_at):
    with conn:
        save_job(conn, cfg, "careerlink", careerlink.PROVIDES, job, 1, RunStats(), seen_at=seen_at)


def test_old_file_does_not_revive_expired_job(conn, tmp_cfg):  # noqa: F811
    job = careerlink.parse_listing(CL_HTML, None)[0]
    job.deadline = "2026-12-31"
    _save(conn, tmp_cfg, job, "2026-11-01T08:00:00")
    with conn:
        conn.execute("UPDATE job_posts SET status = 'expired'")
    _save(conn, tmp_cfg, job, "2026-10-20T08:00:00")  # file lưu tay cũ hơn lần thấy cuối
    assert conn.execute("SELECT status FROM job_posts").fetchone()[0] == "expired"
    _save(conn, tmp_cfg, job, "2026-11-05T08:00:00")  # thấy lại thật trên danh sách
    assert conn.execute("SELECT status FROM job_posts").fetchone()[0] == "unknown"


def test_stale_detail_page_keeps_newer_deadline(conn, tmp_cfg):  # noqa: F811
    job = careerlink.parse_listing(CL_HTML, None)[0]
    _save(conn, tmp_cfg, job, "2026-11-01T08:00:00")
    with conn:
        conn.execute("UPDATE job_posts SET deadline = '2099-12-31', status = 'active'")
    row = conn.execute("SELECT id, dq_flags, industry_label_id FROM job_posts").fetchone()
    detail = {
        "experience": None, "deadline": "2020-10-31", "jsonld": {}, "pii_removed": [], "industries": [],
        "industry": None, "posted_date": None, "employment_type": None, "description_text": None,
    }  # fmt: skip
    apply_detail(conn, tmp_cfg, "careerlink", row, detail, stale=True)
    assert tuple(conn.execute("SELECT deadline, status FROM job_posts").fetchone()) == ("2099-12-31", "active")
    apply_detail(conn, tmp_cfg, "careerlink", row, detail)  # trang chi tiết mới → nguồn chính xác, ghi đè
    assert tuple(conn.execute("SELECT deadline, status FROM job_posts").fetchone()) == ("2020-10-31", "expired")


def test_status_known_needs_sweep_of_start_url_not_any_category(conn):
    df = pd.DataFrame({"status": ["unknown"], "deadline": [pd.NaT], "source": ["careerlink"]})
    with conn:
        conn.execute(
            "INSERT INTO crawl_listings (source, url, last_crawled_at, last_full_sweep_started_at) "
            "VALUES ('careerlink', 'https://x.vn/cat-nho', '2026-11-03', '2026-11-03')"
        )
    assert not stats._status_known(conn, df).iloc[0]
    with conn:
        conn.execute(
            "INSERT INTO crawl_listings (source, url, last_crawled_at, last_full_sweep_started_at) "
            "VALUES ('careerlink', 'https://x.vn/list', '2026-11-03', '2026-11-03')"
        )
    assert stats._status_known(conn, df).iloc[0]


@pytest.mark.parametrize("values", [[1 / 3] * 3, [1 / 3] * 6 + [0.5, 0.5], [2 / 3, 1 / 3, 1 / 3, 2 / 3, 1.0]])
def test_weighted_counts_keep_exact_total(values):
    tidy = stats._tidy_counts(pd.Series(values))
    assert tidy.sum() == pytest.approx(round(sum(values), 1)) and (tidy * 10 % 1 < 1e-9).all()


def test_mom_blank_for_source_starting_mid_month():
    df = pd.DataFrame(
        {
            "event_date": pd.to_datetime(["2026-09-10", "2026-10-10", "2026-11-10", "2026-11-12", "2026-12-10"]),
            "source": ["a", "a", "a", "b", "a"],
            "collection_start": pd.to_datetime(["2026-09-01"] * 3 + ["2026-11-04"] + ["2026-09-01"]),
        }
    )
    m = stats.monthly(df, pd.Timestamp("2026-12-01").date(), months=4).set_index("month")
    assert pd.isna(m.loc["2026-09", "mom"]) and not pd.isna(m.loc["2026-10", "mom"])
    assert pd.isna(m.loc["2026-11", "mom"]) and pd.isna(m.loc["2026-12", "mom"])  # tháng b vào giữa chừng + tháng sau
    assert stats.months_of_data(df) == 1  # tháng 11 không trọn → chuỗi liên tục chỉ còn tháng 12


def test_robots_with_utf8_bom_still_applies():
    assert not RobotsRules(["﻿User-agent: *", "Disallow: /p"]).can_fetch(UA, "https://x.vn/p/1")


def _client(handler):
    cfg = {"user_agent": UA, "min_delay_seconds": 0, "max_retries": 2, "timeout_seconds": 5}
    return PoliteClient(cfg, transport=httpx.MockTransport(handler))


def test_robots_429_means_disallow_all():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429) if request.url.path == "/robots.txt" else httpx.Response(200, text="ok")

    with pytest.raises(Exception, match="Disallowed|x.vn"):
        _client(handler).get("https://x.vn/a")
    assert calls == ["/robots.txt"]


@pytest.mark.parametrize("retry_after", ["-5", "nan", "Wed, 21 Oct 2099 07:28:00 GMT", "garbage"])
def test_bad_or_far_retry_after_never_crashes(retry_after, monkeypatch):
    monkeypatch.setattr("jobmarket.fetch.client.time.sleep", lambda s: None)

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503, headers={"retry-after": retry_after}, text="bảo trì")

    with pytest.raises(Blocked):  # hết lượt thử / nghỉ quá lâu → Blocked, không ValueError hay HTTPStatusError
        _client(handler).get("https://x.vn/a")


@pytest.mark.parametrize("text", ["Gọi 1900 1234", "Hotline 1900.54.55.66", "18006789"])
def test_hotline_numbers_are_scrubbed(text):
    assert "[đã xoá]" in scrub(text)[0]


@pytest.mark.parametrize("text", ["năm 1900", "lương 19.001.234", "tọa độ 1900.5"])
def test_hotline_rule_keeps_ordinary_numbers(text):
    assert scrub(text)[0] == text


def test_hotline_and_fax_keys_are_dropped():
    removed: set[str] = set()
    assert raw_store.sanitize({"hotline": 19001234, "fax": "x", "title": "Kế toán"}, removed) == {"title": "Kế toán"}


def test_csv_without_first_seen_keeps_posting_dates(tmp_cfg, tmp_path):  # noqa: F811
    import csv

    from jobmarket.sources.clean_csv import import_clean_csv
    from tests.test_report import FIXTURE

    with FIXTURE.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))[:5]
    no_seen = tmp_path / "no_first_seen.csv"
    with no_seen.open("w", encoding="utf-8", newline="") as f:
        fields = [k for k in rows[0] if k != "first_seen_at"]
        writer = csv.DictWriter(f, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    db = init_db(tmp_cfg.path("db_file"))
    import_clean_csv(db, no_seen)
    df = stats.load_unique(db)
    assert len(df) and df["date_known"].all() and df["event_date"].notna().all()


def test_remap_follows_changed_seed_but_keeps_manual_mappings(conn):
    from jobmarket import labels
    from jobmarket.pipeline import label_id

    with conn:
        auto = label_id(conn, "industry", "careerlink", "Dịch vụ khách hàng")
        manual = label_id(conn, "industry", "vieclam24h", "Chăm sóc khách hàng")
    assert labels.apply(conn, manual, "map", "IND10", reason="tự chọn", via="cli")
    code = dict(conn.execute("SELECT id, master_code FROM labels"))
    assert code[auto] == "IND03"  # seed hiện tại: CSKH → Kinh doanh – Bán hàng
    with conn:  # giả lập bản seed cũ đã ánh xạ CSKH vào IND10
        conn.execute("UPDATE labels SET master_code = 'IND10' WHERE id = ?", (auto,))
    labels.remap_rules(conn)
    code = dict(conn.execute("SELECT id, master_code FROM labels"))
    assert code[auto] == "IND03" and code[manual] == "IND10"  # quy tắc đi theo seed; ánh xạ tay giữ nguyên
