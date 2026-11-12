"""Chạy pipeline thật trên DB tạm, mạng giả lập bằng httpx.MockTransport (không gọi ra ngoài)."""

import dataclasses

import httpx
import pytest

from jobmarket.config import Config
from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import run_topcv
from jobmarket.store.db import init_db

CHALLENGE = "<html><title>Just a moment...</title>Enable JavaScript and cookies to continue</html>"


TEST_ENABLED_SOURCES = {"careerlink", "vieclam24h", "careerviet", "itviec"}


@pytest.fixture
def tmp_cfg(cfg, tmp_path):
    raw = {**cfg.raw, "paths": {"data_dir": "data", "db_file": "data/test.db", "raw_dir": "data/raw"}}
    raw["crawl"] = {**cfg.crawl, "min_delay_seconds": 0}
    # test tầng danh sách không đọc trang chi tiết, không quét sâu, giữ thứ tự mặc định (test riêng tự bật)
    # không đọc sitemap, không đụng thư mục Downloads thật của máy (test riêng tự bật)
    # Bật/tắt nguồn cố định cho test — không phụ thuộc user tắt nguồn nào trong config.toml thật (2026-11-12)
    raw["sources"] = {
        name: {
            **src,
            "enabled": name in TEST_ENABLED_SOURCES,
            "detail_max_per_run": 0,
            "deep_pages_per_run": 0,
            "min_delay_seconds": 0,
            "category_first": False,
            "sitemap_max_per_run": 0,
            "downloads_dir": "",
        }
        for name, src in cfg.raw["sources"].items()
    }
    return dataclasses.replace(cfg, raw=raw, root=tmp_path)


def make_client(tmp_cfg, handler):
    return PoliteClient(tmp_cfg.crawl, transport=httpx.MockTransport(handler))


def site(listing_html, category_status=200):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /xem-cv/\n")
        if request.url.path == "/tim-viec-lam-moi-nhat":
            return httpx.Response(200, text=listing_html)
        if category_status != 200:
            return httpx.Response(category_status, text=CHALLENGE)
        return httpx.Response(200, text=listing_html)

    return handler, calls


def test_run_stores_jobs_labels_and_view(tmp_cfg: Config, topcv_listing_html):
    conn = init_db(tmp_cfg.path("db_file"))
    handler, calls = site(topcv_listing_html)
    run_id = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler), max_pages=3)

    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "success"
    assert run["posts_new"] == 8
    assert all("page=" not in u and "/viec-lam/" not in u for u in calls)  # không phân trang, không trang chi tiết

    assert conn.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 8
    assert conn.execute("SELECT COUNT(*) FROM v_jobs_unique").fetchone()[0] == 8
    # Ngành lấy từ trang ngành đầu tiên chứa tin, không bị trang "mới nhất" (không ngành) ghi đè
    industries = {
        r[0] for r in conn.execute("SELECT l.raw_label FROM job_posts p JOIN labels l ON l.id = p.industry_label_id")
    }
    assert industries == {"Kinh doanh/Bán hàng"}
    # Tỉnh và kinh nghiệm ánh xạ bằng quy tắc; ngành "Kinh doanh/Bán hàng" trùng đúng tên chuẩn
    # "Kinh doanh – Bán hàng" sau chuẩn hoá chữ → ánh xạ (so khớp chính xác, không đoán)
    row = conn.execute("SELECT * FROM v_jobs_unique WHERE source_job_id = '2304851'").fetchone()
    assert row["province_code"] == "HP"
    assert row["experience_code"] == "Y3_5"
    assert row["industry_code"] == "IND03"
    assert row["salary_type"] == "negotiable" and row["salary_mid"] is None
    usd = conn.execute("SELECT salary_mid FROM v_jobs_unique WHERE source_job_id = '2143663'").fetchone()
    assert usd["salary_mid"] == pytest.approx(2000 * 25500)
    # Bản gốc đã lưu
    assert (
        tmp_cfg.path("raw_dir")
        / conn.execute("SELECT raw_path FROM job_posts WHERE source_job_id = '2342204'").fetchone()[0]
    ).exists()


def test_label_not_matching_standard_name_stays_unmapped(tmp_cfg):
    from jobmarket.pipeline import label_id

    conn = init_db(tmp_cfg.path("db_file"))
    exact = label_id(conn, "industry", "topcv", "Công nghệ Thông tin")
    fuzzy = label_id(conn, "industry", "topcv", "Tư vấn chuyên môn")  # không có trong bảng ánh xạ → chờ duyệt
    rows = {r["id"]: r for r in conn.execute("SELECT id, status, master_code FROM labels")}
    assert (rows[exact]["status"], rows[exact]["master_code"]) == ("mapped", "IND01")
    assert (rows[fuzzy]["status"], rows[fuzzy]["master_code"]) == ("unmapped", None)


def test_second_run_updates_not_duplicates(tmp_cfg, topcv_listing_html):
    conn = init_db(tmp_cfg.path("db_file"))
    handler, _ = site(topcv_listing_html)
    run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler), max_pages=2)
    run2 = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler), max_pages=2)
    assert conn.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 8
    r = conn.execute("SELECT posts_new, posts_updated FROM runs WHERE id = ?", (run2,)).fetchone()
    assert (r["posts_new"], r["posts_updated"]) == (0, 16)


def test_challenge_stops_source_without_retrying(tmp_cfg, topcv_listing_html):
    conn = init_db(tmp_cfg.path("db_file"))
    handler, calls = site(topcv_listing_html, category_status=403)
    run_id = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler), max_pages=5)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "failed"
    assert "Blocked" in run["error_sample"]
    category_calls = [u for u in calls if "-cr" in u]
    assert len(category_calls) == 1  # gặp challenge là dừng, không thử lại, không sang trang khác


def test_robots_disallow_is_respected(tmp_cfg, topcv_listing_html):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError(f"không được gọi {request.url}")

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler))
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "failed"


def test_network_error_marks_run_failed_not_stuck(tmp_cfg):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        raise httpx.ConnectError("mất mạng", request=request)

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler))
    run = conn.execute("SELECT status, finished_at, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "failed" and run["finished_at"] is not None
    assert "ConnectError" in run["error_sample"]


def test_robots_server_error_means_disallow_all(tmp_cfg):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(503, text="lỗi máy chủ")
        raise AssertionError(f"không được gọi {request.url}")

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler))
    run = conn.execute("SELECT status, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "failed" and "Disallowed" in run["error_sample"]


def test_robots_missing_means_allowed(tmp_cfg, topcv_listing_html):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=topcv_listing_html)

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = run_topcv(conn, tmp_cfg, make_client(tmp_cfg, handler), max_pages=1)
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == "success"
