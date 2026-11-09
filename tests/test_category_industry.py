"""Ngành CareerLink theo danh mục (2026-11-09): tin tích luỹ mọi danh mục chứa nó, danh mục cùng mã là một,
ưu tiên quét danh mục, tỷ trọng ngành theo danh mục chỉ tính khi mọi danh mục đã quét trọn 1 vòng."""

import dataclasses

import httpx
import pandas as pd

from jobmarket import stats
from jobmarket.pipeline import RunStats, backfill_listing_industries, run_paged, save_job
from jobmarket.sources import careerlink
from jobmarket.store.db import init_db
from tests.test_paged_sources import CL_HTML, EMPTY, make_client
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

KE_TOAN = "https://www.careerlink.vn/viec-lam/ke-toan/1"
NGAN_HANG = "https://www.careerlink.vn/viec-lam/ngan-hang/5"


def _conn(cfg):
    conn = init_db(cfg.path("db_file"))
    with conn:
        conn.execute(
            "INSERT INTO runs (id, source, scope, started_at, app_version) "
            "VALUES (1, 'careerlink', '{}', '2026-11-09', 't')"
        )
    return conn


def _memberships(conn) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT l.raw_label FROM job_industry_labels j JOIN labels l ON l.id = j.label_id ORDER BY j.position"
        )
    ]


def test_listing_key_merges_category_slugs():
    assert careerlink.listing_key(KE_TOAN) == careerlink.listing_key(
        "https://www.careerlink.vn/viec-lam/ke-toan-kiem-toan/1"
    )
    assert careerlink.listing_key(KE_TOAN) != careerlink.listing_key(NGAN_HANG)
    assert careerlink.listing_key("https://www.careerlink.vn/vieclam/list") == "https://www.careerlink.vn/vieclam/list"


def test_job_accumulates_every_category_it_appears_in(tmp_cfg):  # noqa: F811
    conn = _conn(tmp_cfg)
    job = careerlink.parse_listing(CL_HTML, "Kế toán / Kiểm toán")[0]
    run = RunStats()
    for label in ("Kế toán / Kiểm toán", "Tiếng Nhật", "Ngân hàng", "Kế toán / Kiểm toán"):
        job.industry_label = label
        with conn:
            save_job(conn, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, run)
    assert _memberships(conn) == ["Kế toán / Kiểm toán", "Ngân hàng"]  # "Tiếng Nhật" không phải ngành, không lặp
    assert run.industry_filled == 1  # trang Ngân hàng có ích (thêm mục mới) → quét danh mục không dừng sớm


def test_backfill_reads_category_from_saved_cards(tmp_cfg):  # noqa: F811
    conn = _conn(tmp_cfg)
    jobs = careerlink.parse_listing(CL_HTML, "Ngân hàng")
    with conn:
        for job in jobs:
            save_job(conn, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, RunStats())
        conn.execute("DELETE FROM job_industry_labels")
    assert backfill_listing_industries(conn, tmp_cfg, "careerlink") == {"memberships_added": len(jobs)}
    assert set(_memberships(conn)) == {"Ngân hàng"}


def _site(calls: list[str]):
    nav = f'<nav><a href="{KE_TOAN[25:]}">Việc làm Kế toán</a><a href="/viec-lam/ke-toan-kiem-toan/1">Kế toán</a>'
    nav += '<a href="/tim-viec-lam-tai/ha-noi/HN">Hà Nội</a></nav>'

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if int(request.url.params.get("page", 1)) > 1:
            return httpx.Response(200, text=EMPTY)
        return httpx.Response(200, text=CL_HTML.replace("<ul>", nav + "<ul>", 1))

    return handler


def _with(cfg, **source):
    sources = {**cfg.raw["sources"], "careerlink": {**cfg.raw["sources"]["careerlink"], **source}}
    return dataclasses.replace(cfg, raw={**cfg.raw, "sources": sources})


def test_category_first_reads_categories_before_general_list_and_skips_provinces(tmp_cfg):  # noqa: F811
    cfg = _with(tmp_cfg, category_first=True)
    conn = init_db(cfg.path("db_file"))
    with conn:
        conn.execute(
            "INSERT INTO crawl_listings (source, url, label, last_crawled_at) VALUES "
            "('careerlink', 'https://www.careerlink.vn/vieclam/list', NULL, '2026-11-05T10:00:00')"
        )
    calls: list[str] = []
    run_paged(conn, cfg, make_client(cfg, _site(calls)), careerlink, max_pages=2)
    pages = [u for u in calls if "robots" not in u]
    assert pages.index(KE_TOAN) < pages.index("https://www.careerlink.vn/vieclam/list?page=2")
    assert not any("ke-toan-kiem-toan" in u for u in pages)  # cùng mã 1 → một danh sách
    assert not any("tim-viec-lam-tai" in u for u in pages)  # trang tỉnh không ra ngành


def test_default_order_keeps_general_list_first(tmp_cfg):  # noqa: F811
    cfg = _with(tmp_cfg, category_first=False)
    conn = init_db(cfg.path("db_file"))
    calls: list[str] = []
    run_paged(conn, cfg, make_client(cfg, _site(calls)), careerlink, max_pages=2)
    pages = [u for u in calls if "robots" not in u]
    assert pages.index("https://www.careerlink.vn/vieclam/list?page=2") < pages.index(KE_TOAN)
    assert any("tim-viec-lam-tai" in u for u in pages)


def _listing(conn, url, label, swept):
    with conn:
        conn.execute(
            "INSERT INTO crawl_listings (source, url, label, last_crawled_at, last_full_sweep_started_at) "
            "VALUES ('careerlink', ?, ?, '2026-11-08T10:00:00', ?)",
            (url, label, "2026-11-08T10:00:00" if swept else None),
        )


def test_sweep_progress_counts_category_ids_once_and_skips_non_industry(tmp_cfg):  # noqa: F811
    conn2 = _conn(tmp_cfg)
    job = careerlink.parse_listing(CL_HTML, "Tiếng Nhật")[0]  # tạo nhãn 'Tiếng Nhật' (bỏ qua — không phải ngành)
    with conn2:
        save_job(conn2, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, RunStats())
    _listing(conn2, KE_TOAN, "Kế toán", swept=True)
    _listing(conn2, "https://www.careerlink.vn/viec-lam/ke-toan-kiem-toan/1", "Kế toán / Kiểm toán", swept=False)
    _listing(conn2, "https://www.careerlink.vn/viec-lam/tieng-nhat/154", "Tiếng Nhật", swept=False)
    _listing(conn2, NGAN_HANG, "Ngân hàng", swept=False)
    assert stats.category_sweep_progress(conn2, "careerlink") == (1, 2)
    with conn2:
        conn2.execute("UPDATE crawl_listings SET last_full_sweep_started_at = 'x' WHERE url = ?", (NGAN_HANG,))
    assert stats.category_sweep_progress(conn2, "careerlink") == (2, 2)


def test_category_industry_counts_only_after_every_category_swept(tmp_cfg):  # noqa: F811
    conn = _conn(tmp_cfg)
    with conn:
        for job in careerlink.parse_listing(CL_HTML, "Ngân hàng"):
            save_job(conn, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, RunStats())
    _listing(conn, NGAN_HANG, "Ngân hàng", swept=False)
    _listing(conn, KE_TOAN, "Kế toán", swept=True)
    df = pd.DataFrame({"id": [1], "source": ["careerlink"]})
    assert stats._industry_basis(conn, df).tolist() == ["listing"]
    with conn:
        conn.execute("UPDATE crawl_listings SET last_full_sweep_started_at = 'x'")
    assert stats._industry_basis(conn, df).tolist() == ["category"]
    assert len(stats.industry_comparable(df.assign(industry_basis=["category"]))) == 1
