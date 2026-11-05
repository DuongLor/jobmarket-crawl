"""CareerLink + JobsGO: bộ phân tích thẻ tin và bộ chạy phân trang (mạng giả lập, không gọi ra ngoài)."""

from pathlib import Path

import httpx
import pytest

from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import run_paged
from jobmarket.sources import careerlink, jobsgo
from jobmarket.store.db import init_db
from tests.test_pipeline import CHALLENGE, tmp_cfg  # noqa: F401  (fixture dùng chung)

FIXTURES = Path(__file__).parent / "fixtures"
CL_HTML = (FIXTURES / "careerlink" / "listing_sample.html").read_text(encoding="utf-8")
JG_HTML = (FIXTURES / "jobsgo" / "listing_sample.html").read_text(encoding="utf-8")
EMPTY = "<html><body>Không có việc làm phù hợp</body></html>"


def test_careerlink_fields():
    jobs = careerlink.parse_listing(CL_HTML, None)
    assert len(jobs) == 6
    first = jobs[0]
    assert first.source_job_id == "3665468"
    assert first.url == (
        "https://www.careerlink.vn/tim-viec-lam/flamingo-cat-ba-resort-nhan-vien-dieu-phoi-cong-viec-bo-phan-bep"
        "-coordinator/3665468"
    )
    assert first.company_name == "Flamingo Holdings Group"
    assert (first.salary_raw, first.level_raw, first.location_raw) == ("Thương lượng", "Nhân viên", "Hải Phòng")
    multi = next(j for j in jobs if j.source_job_id == "3642993")
    assert multi.location_raw == "Đà Nẵng, Hà Nội"
    assert multi.salary_raw == "12 triệu - 35 triệu"  # không dính "| cấp bậc" của hàng bao ngoài


def test_careerlink_page_url():
    assert careerlink.page_url("https://www.careerlink.vn/vieclam/list", 1) == "https://www.careerlink.vn/vieclam/list"
    assert careerlink.page_url("https://www.careerlink.vn/vieclam/list?page=4", 2).endswith("/vieclam/list?page=2")


def test_jobsgo_fields():
    jobs = jobsgo.parse_listing(JG_HTML, "Công Nghệ Thông Tin")
    assert len(jobs) == 6
    first = jobs[0]
    assert first.source_job_id == "29252601487"
    assert "#" not in first.url and first.url.endswith("-29252601487.html")
    assert first.company_name == "Công Ty TNHH Vận Chuyển Quốc Tế Shengyang"
    assert (first.salary_raw, first.location_raw, first.experience_raw) == ("13 - 20 triệu VNĐ", "Hà Nội", "1 - 2 năm")
    assert first.industry_label == "Công Nghệ Thông Tin"


def test_jobsgo_top_level_industries_only():
    pages = jobsgo.listings("https://jobsgo.vn/viec-lam.html", JG_HTML)
    names = [p.industry_label for p in pages]
    assert names[0] == "Tài Chính/Ngân Hàng"  # slug "tai-chinh-…" không bị nhầm là trang theo tỉnh
    assert names[-1] == "Ngành Nghề Khác" and len(pages) == 32
    assert not any("/viec-lam-tai-ha-noi" in p.url or "remote" in p.url for p in pages)
    assert "Lập Trình - Phát Triển Phần Mềm" not in names  # ngành con (sau "Ngành Nghề Khác") không duyệt


def paged_site(listing_html: str, pages_with_jobs: int, block_at: int | None = None):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api*\n")
        page = int(request.url.params.get("page", 1))
        if block_at and len([c for c in calls if "robots" not in c]) >= block_at:
            return httpx.Response(403, text=CHALLENGE)
        return httpx.Response(200, text=listing_html if page <= pages_with_jobs else EMPTY)

    return handler, calls


def make_client(cfg, handler):
    return PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler))


def test_careerlink_run_paginates_until_empty(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    handler, calls = paged_site(CL_HTML, pages_with_jobs=3)
    run_id = run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=10)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "success" and run["posts_new"] == 6
    # trang 1 (dùng lại trang bắt đầu), 2, 3 lặp lại cùng tin → dừng sau 3 trang "toàn tin đã biết" hoặc trang rỗng
    assert not any("page=5" in u for u in calls)
    row = conn.execute("SELECT * FROM v_jobs_unique WHERE source_job_id = '3651213'").fetchone()
    assert row["province_code"] == "DN"
    assert row["level_code"] == "MANAGER"  # "Quản lý / Trưởng phòng" trùng đúng tên chuẩn
    assert row["salary_mid"] == pytest.approx(17_500_000)


def test_known_pages_stop_early(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    handler, _ = paged_site(CL_HTML, pages_with_jobs=100)
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=50)
    handler2, calls2 = paged_site(CL_HTML, pages_with_jobs=100)
    run2 = run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler2), careerlink, max_pages=50)
    listing_calls = [u for u in calls2 if "robots" not in u]
    n_listings = 1 + len(careerlink.discover(CL_HTML))  # danh sách chung + các tỉnh thấy trên thẻ tin
    # stop_after_known_pages = 3: mỗi danh sách dừng sau 3 trang toàn tin đã biết, không duyệt 50 trang
    assert len(listing_calls) == 3 * n_listings
    assert not any("page=4" in u for u in listing_calls)
    assert conn.execute("SELECT posts_new FROM runs WHERE id = ?", (run2,)).fetchone()[0] == 0


def test_jobsgo_run_assigns_industry_from_category(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    handler, calls = paged_site(JG_HTML, pages_with_jobs=1)
    run_id = run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), jobsgo, max_pages=2)
    assert conn.execute("SELECT status, posts_new FROM runs WHERE id = ?", (run_id,)).fetchone()[:] == ("success", 6)
    industry = conn.execute(
        "SELECT l.raw_label FROM job_posts p JOIN labels l ON l.id = p.industry_label_id WHERE p.source_job_id = ?",
        ("29252601487",),
    ).fetchone()[0]
    assert industry == "Tài Chính/Ngân Hàng"  # ngành của trang ngành đầu tiên chứa tin
    assert not any("/api" in u or "/ajax" in u for u in calls)


def test_block_mid_run_stops_source_keeps_data(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    handler, calls = paged_site(CL_HTML, pages_with_jobs=100, block_at=2)
    run_id = run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=10)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "partial" and "Blocked" in run["error_sample"]
    assert run["posts_new"] == 6
    assert len([u for u in calls if "robots" not in u]) == 2  # gặp chặn là dừng, không thử lại


def test_careerlink_discovers_province_and_category_listings():
    html = CL_HTML.replace(
        "<ul>",
        '<nav><a href="/viec-lam/ke-toan/1">Việc làm Kế toán</a><a href="/viec-lam/cntt-phan-mem/19">'
        "Việc làm IT - Phần mềm</a></nav><ul>",
        1,
    )
    found = careerlink.discover(html)
    assert [(p.url, p.industry_label) for p in found[:2]] == [
        ("https://www.careerlink.vn/viec-lam/ke-toan/1", "Kế toán"),
        ("https://www.careerlink.vn/viec-lam/cntt-phan-mem/19", "IT - Phần mềm"),
    ]
    provinces = [p.url for p in found[2:]]
    assert "https://www.careerlink.vn/tim-viec-lam-tai/hai-phong/HP" in provinces
    assert all(p.industry_label is None for p in found[2:])
    first = careerlink.listings("https://www.careerlink.vn/vieclam/list", html)[0]
    assert first.url == "https://www.careerlink.vn/vieclam/list"  # danh sách chung xử lý trước


def test_industry_filled_when_job_later_seen_on_category_page(tmp_cfg):  # noqa: F811
    nav = '<nav><a href="/viec-lam/ke-toan/1">Việc làm Kế toán</a></nav>'

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if int(request.url.params.get("page", 1)) > 1:
            return httpx.Response(200, text=EMPTY)
        return httpx.Response(200, text=CL_HTML.replace("<ul>", nav + "<ul>", 1))

    conn = init_db(tmp_cfg.path("db_file"))
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=3)
    industries = {
        r[0]
        for r in conn.execute("SELECT l.raw_label FROM job_posts p LEFT JOIN labels l ON l.id = p.industry_label_id")
    }
    assert industries == {"Kế toán"}  # tin gặp trước ở danh sách chung (không ngành) đã được bổ sung ngành


def test_soft_block_empty_start_page_is_reported(tmp_cfg):  # noqa: F811
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text="<html><title>CareerLink</title><body>vỏ trang, không có tin</body></html>")

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=5)
    run = conn.execute("SELECT status, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "failed" and "hạn chế" in run["error_sample"]


def test_soft_block_midway_stops_after_three_empty_listings(tmp_cfg):  # noqa: F811
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/vieclam/list" and "page" not in request.url.params:
            return httpx.Response(200, text=CL_HTML)  # trang đầu có tin
        return httpx.Response(200, text=EMPTY)  # sau đó mọi trang trống

    conn = init_db(tmp_cfg.path("db_file"))
    run_id = run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=5)
    run = conn.execute("SELECT status, posts_new, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "partial" and run["posts_new"] == 6 and "hạn chế" in run["error_sample"]
    province_calls = [u for u in calls if "tim-viec-lam-tai" in u]
    assert len(province_calls) == 3  # dừng sau 3 danh sách trống liên tiếp, không duyệt hết


def test_max_requests_caps_run(tmp_cfg):  # noqa: F811
    import dataclasses

    raw = {**tmp_cfg.raw, "sources": {**tmp_cfg.raw["sources"]}}
    raw["sources"]["careerlink"] = {**raw["sources"]["careerlink"], "max_requests": 4}
    capped = dataclasses.replace(tmp_cfg, raw=raw)
    conn = init_db(capped.path("db_file"))
    handler, calls = paged_site(CL_HTML, pages_with_jobs=100)
    run_id = run_paged(conn, capped, make_client(capped, handler), careerlink, max_pages=50)
    run = conn.execute("SELECT status, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert len(calls) <= 4  # robots + tối đa 3 trang
    assert run["status"] == "partial" and "giới hạn 4 request" in run["error_sample"]


def test_mark_expired_only_jobs_not_seen_recently(tmp_cfg):  # noqa: F811
    from jobmarket.pipeline import mark_expired
    from jobmarket.store.db import now_iso

    conn = init_db(tmp_cfg.path("db_file"))
    handler, _ = paged_site(CL_HTML, pages_with_jobs=1)
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=1)
    ids = [r[0] for r in conn.execute("SELECT id FROM job_posts ORDER BY id")]
    today = now_iso()
    with conn:
        conn.execute("UPDATE job_posts SET last_seen_at = '2020-01-01T00:00:00' WHERE id = ?", (ids[0],))
        conn.execute(
            "UPDATE job_posts SET last_seen_at = ?, status = 'active' WHERE id = ?", ("2020-01-01T00:00:00", ids[1])
        )
        conn.execute("UPDATE job_posts SET last_seen_at = ? WHERE id = ?", (today[:10] + "T00:00:01", ids[2]))
    assert mark_expired(conn, "careerlink", 14) == 2
    status = dict(conn.execute("SELECT id, status FROM job_posts"))
    assert status[ids[0]] == "expired"  # unknown + lâu không thấy
    assert status[ids[1]] == "expired"  # active (từ trang chi tiết) nhưng đã gỡ khỏi danh sách → không tuyển mãi
    assert status[ids[2]] == "unknown"  # vừa thấy hôm nay (cùng ngày, khác giờ)


def test_careerlink_card_has_posted_date():
    jobs = careerlink.parse_listing(CL_HTML, None)
    assert {j.posted_date for j in jobs} == {"2026-11-03"}  # <time datetime="2026-11-03T03:04:56.000Z"> → ngày VN


def test_backfill_posted_dates_from_saved_cards(tmp_cfg):  # noqa: F811
    from jobmarket.pipeline import backfill_posted_dates

    conn = init_db(tmp_cfg.path("db_file"))
    handler, _ = paged_site(CL_HTML, pages_with_jobs=1)
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=1)
    with conn:
        conn.execute("UPDATE job_posts SET posted_date = NULL")  # như dữ liệu crawl trước khi đọc ngày trên thẻ
    counts = backfill_posted_dates(conn, tmp_cfg, careerlink)
    assert counts == {"filled": 6, "no_date": 0, "missing_raw": 0}
    assert {r[0] for r in conn.execute("SELECT posted_date FROM job_posts")} == {"2026-11-03"}


def test_language_category_is_ignored_and_replaced_by_real_industry(tmp_cfg):  # noqa: F811
    """'Tiếng Nhật' là danh mục kỹ năng (seeds/industry_aliases.csv → bỏ qua): tin gặp lại ở trang ngành thật
    thì nhận ngành đó. Nhãn ngành thật (kể cả chưa ánh xạ) thì không bị ghi đè."""
    from jobmarket.pipeline import RunStats, save_job

    conn = init_db(tmp_cfg.path("db_file"))
    with conn:
        conn.execute(
            "INSERT INTO runs (id, source, scope, started_at, app_version) "
            "VALUES (1, 'careerlink', '{}', '2026-11-03', 't')"
        )
    job = careerlink.parse_listing(CL_HTML, "Tiếng Nhật")[0]
    stats_ = RunStats()
    with conn:
        save_job(conn, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, stats_)
    label = conn.execute("SELECT l.status FROM job_posts p JOIN labels l ON l.id = p.industry_label_id").fetchone()
    assert label["status"] == "ignored"
    job.industry_label = "Kế toán"
    with conn:
        save_job(conn, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, stats_)
    row = conn.execute("SELECT l.master_code FROM job_posts p JOIN labels l ON l.id = p.industry_label_id").fetchone()
    assert row["master_code"] == "IND04" and stats_.industry_filled == 1
    job.industry_label = "Sản xuất"
    with conn:
        save_job(conn, tmp_cfg, "careerlink", careerlink.PROVIDES, job, 1, stats_)
    row = conn.execute("SELECT l.master_code FROM job_posts p JOIN labels l ON l.id = p.industry_label_id").fetchone()
    assert row["master_code"] == "IND04"  # đã có ngành → giữ ngành đầu tiên


def test_rotation_puts_never_or_least_recently_crawled_first():
    from jobmarket.pipeline import _rotate
    from jobmarket.sources.base import ListingPage

    queue = [
        ListingPage("main", None),
        ListingPage("cat-a", "A"),
        ListingPage("cat-b", "B"),
        ListingPage("prov-x", None),
        ListingPage("cat-c", "C"),
    ]
    last = {"cat-a": "2026-11-03T10:00:00", "cat-b": "2026-11-03T08:00:00"}
    _rotate(queue, 1, last)
    assert [p.url for p in queue] == ["main", "cat-c", "prov-x", "cat-b", "cat-a"]


def test_second_run_starts_from_listings_not_crawled_last_time(tmp_cfg):  # noqa: F811
    nav = '<nav><a href="/viec-lam/ke-toan/1">Việc làm Kế toán</a><a href="/viec-lam/ngan-hang/5">Ngân hàng</a></nav>'
    html = CL_HTML.replace("<ul>", nav + "<ul>", 1)
    conn = init_db(tmp_cfg.path("db_file"))
    with conn:
        conn.execute(
            "INSERT INTO crawl_listings (source, url, label, last_crawled_at) VALUES ('careerlink', "
            "'https://www.careerlink.vn/viec-lam/ke-toan/1', "
            "'Kế toán', '2026-11-03T10:00:00')"
        )
    handler, calls = paged_site(html, pages_with_jobs=1)
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=1)
    listings = [u for u in calls if "robots" not in u]
    assert listings.index("https://www.careerlink.vn/viec-lam/ngan-hang/5") < listings.index(
        "https://www.careerlink.vn/viec-lam/ke-toan/1"
    )
    crawled = {r[0] for r in conn.execute("SELECT url FROM crawl_listings")}
    assert "https://www.careerlink.vn/viec-lam/ngan-hang/5" in crawled


def _deep_site(last: int, empty_from: int | None = None, content_of: dict[int, int] | None = None):
    """Danh sách chung `last` trang, mỗi trang 6 tin KHÁC NHAU (mã tin gắn số trang); `empty_from` = giả lập bị
    hạn chế ngầm từ trang đó (trả trang rỗng dù chưa tới trang cuối); `content_of` {trang: trang nguồn} = trang hiện
    tin của trang khác (giả lập tin được làm mới nhảy lên đầu danh sách; sửa dict giữa các lần chạy)."""
    import re

    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        page = int(request.url.params.get("page", 1))
        calls.append(page)
        if page > last or (empty_from and page >= empty_from):
            return httpx.Response(200, text=EMPTY)
        src = (content_of or {}).get(page, page)
        html = re.sub(r"/(\d{7})\"", lambda m: f'/{src:03d}{m.group(1)}"', CL_HTML)
        return httpx.Response(200, text=html.replace("<ul>", f'<a href="/vieclam/list?page={last}">{last}</a><ul>', 1))

    return handler, calls


def _main_only():
    """Adapter CareerLink chỉ có danh sách chung (không tự phát hiện trang tỉnh/ngành) để test quét sâu."""
    from types import SimpleNamespace

    return SimpleNamespace(
        NAME=careerlink.NAME,
        PROVIDES=careerlink.PROVIDES,
        listings=lambda url, html: [careerlink.ListingPage(url, None)],
        page_url=careerlink.page_url,
        parse_listing=careerlink.parse_listing,
        last_page=careerlink.last_page,
    )


def _deep_cfg(tmp_cfg, deep=3):  # noqa: F811
    import dataclasses

    raw = {**tmp_cfg.raw, "sources": {**tmp_cfg.raw["sources"]}}
    raw["sources"]["careerlink"] = {**raw["sources"]["careerlink"], "deep_pages_per_run": deep}
    return dataclasses.replace(tmp_cfg, raw=raw)


def _resume(conn):
    return conn.execute("SELECT resume_page FROM crawl_listings").fetchone()[0]


def test_deep_crawl_continues_where_last_run_stopped(tmp_cfg):  # noqa: F811
    cfg = _deep_cfg(tmp_cfg)
    conn = init_db(cfg.path("db_file"))
    handler, calls = _deep_site(last=20)
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)
    assert calls == [1, 2, 3, 4, 5] and _resume(conn) == 6  # 2 trang đầu + 3 trang sâu
    calls.clear()
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)
    # đi tiếp từ trang 6, lùi 2 trang (deep_overlap_pages) phòng tin dịch lên → 4, 5, 6; không đọc lại trang 3
    assert calls == [1, 2, 4, 5, 6] and _resume(conn) == 7
    assert conn.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 6 * 6


def test_deep_crawl_wraps_around_at_end_of_list(tmp_cfg):  # noqa: F811
    cfg = _deep_cfg(tmp_cfg, deep=5)
    conn = init_db(cfg.path("db_file"))
    handler, calls = _deep_site(last=4)
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)
    assert calls == [1, 2, 3, 4] and _resume(conn) is None  # đã đọc tới trang cuối 4 → hết vòng, không tải trang 5


def test_refreshed_jobs_above_sweep_cursor_are_read_again(tmp_cfg):  # noqa: F811
    """Hồi quy M4 (2026-11-05): tin cũ ở trang sâu được làm mới → nhảy lên đầu danh sách (trang 1–5). Chúng đã có
    trong DB nên trang không có "tin mới"; nếu phần đầu dừng sau 3 trang như vậy thì tin ở trang 4–5 không được thấy
    lại trong vòng quét hiện tại → bị đánh hết hạn nhầm khi vòng kết thúc."""
    cfg = _deep_cfg(tmp_cfg, deep=3)
    conn = init_db(cfg.path("db_file"))
    refreshed = {1: 20, 2: 21, 3: 22, 4: 23, 5: 24}
    shown: dict[int, int] = dict(refreshed)
    handler, calls = _deep_site(last=30, content_of=shown)
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=5)  # đưa tin trang 20–24 vào DB
    with conn:  # ...như thể thấy ở vòng quét TRƯỚC; bắt đầu vòng mới
        conn.execute("UPDATE job_posts SET last_seen_at = '2026-01-01T00:00:00'")
        conn.execute("DELETE FROM crawl_listings")
    shown.clear()
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=10)  # vòng mới: trang 1–10 + sâu
    shown.update(refreshed)
    calls.clear()
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=10)
    assert calls[:8] == [1, 2, 3, 4, 5, 6, 7, 8], calls  # trang 1–5 có tin chưa thấy trong vòng → đọc tiếp
    stale = conn.execute(
        "SELECT COUNT(*) FROM job_posts WHERE substr(source_job_id, 1, 3) IN ('023', '024') "
        "AND last_seen_at < '2026-02-01'"
    ).fetchone()[0]
    assert stale == 0  # tin làm mới ở trang 4–5 đã được thấy lại trong vòng này


def test_empty_page_without_known_last_page_does_not_close_sweep(tmp_cfg):  # noqa: F811
    """Hồi quy B1 (2026-11-04): adapter không đọc được trang cuối + nguồn trả trang rỗng giữa chừng (hạn chế ngầm)
    → KHÔNG được coi là quét trọn (sẽ đánh hết hạn nhầm mọi tin ở các trang sau)."""
    from types import SimpleNamespace

    cfg = _deep_cfg(tmp_cfg, deep=5)
    conn = init_db(cfg.path("db_file"))
    handler, _ = _deep_site(last=20, empty_from=4)
    adapter = SimpleNamespace(**{k: v for k, v in vars(_main_only()).items() if k != "last_page"})
    run_paged(conn, cfg, make_client(cfg, handler), adapter, max_pages=2)
    _add_unknown_job(conn, "deep-page-job", "2026-01-01T00:00:00")
    run_id = run_paged(conn, cfg, make_client(cfg, handler), adapter, max_pages=2)
    full = conn.execute("SELECT last_full_sweep_started_at FROM crawl_listings").fetchone()[0]
    assert full is None and _resume(conn) == 4
    assert conn.execute("SELECT status FROM job_posts WHERE source_job_id = 'deep-page-job'").fetchone()[0] == "unknown"
    assert "không chốt vòng quét" in conn.execute("SELECT error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()[0]


def test_empty_page_before_last_page_is_soft_block_and_keeps_position(tmp_cfg):  # noqa: F811
    cfg = _deep_cfg(tmp_cfg)
    conn = init_db(cfg.path("db_file"))
    handler, _ = _deep_site(last=20, empty_from=4)
    run_id = run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)
    run = conn.execute("SELECT status, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "partial" and "trang 4/20" in run["error_sample"]
    assert _resume(conn) == 4  # lần sau đọc lại đúng trang bị chặn, không bỏ sót


def _add_unknown_job(conn, job_id: str, last_seen: str, status: str = "unknown") -> None:
    with conn:
        conn.execute(
            "INSERT INTO job_posts (source, source_job_id, title, status, dq_status, dq_flags, dedup_key, "
            "first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version) "
            "VALUES ('careerlink', ?, 't', ?, 'ok', '', ?, ?, ?, 1, 1, 'v')",
            (job_id, status, f"k{job_id}", last_seen, last_seen),
        )


def test_expiry_waits_for_a_full_sweep(tmp_cfg):  # noqa: F811
    """Quét sâu: tin cũ chưa thấy lại KHÔNG bị hết hạn khi vòng quét chưa xong (có thể nằm ở trang chưa tới)."""
    cfg = _deep_cfg(tmp_cfg, deep=2)
    conn = init_db(cfg.path("db_file"))
    handler, _ = _deep_site(last=20)
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)
    _add_unknown_job(conn, "old", "2026-01-01T00:00:00")
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)
    assert conn.execute("SELECT status FROM job_posts WHERE source_job_id = 'old'").fetchone()[0] == "unknown"


def test_full_sweep_expires_jobs_not_seen_again(tmp_cfg):  # noqa: F811
    cfg = _deep_cfg(tmp_cfg, deep=5)
    conn = init_db(cfg.path("db_file"))
    handler, _ = _deep_site(last=4)
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=1)  # thêm tin của danh sách
    _add_unknown_job(conn, "gone", "2026-01-01T00:00:00")
    run_paged(conn, cfg, make_client(cfg, handler), _main_only(), max_pages=2)  # vòng quét trọn trang 1 → 4
    statuses = dict(conn.execute("SELECT source_job_id, status FROM job_posts"))
    assert statuses.pop("gone") == "expired"
    assert set(statuses.values()) == {"unknown"}  # tin thấy trong vòng quét vẫn còn tuyển


def test_expired_job_seen_again_is_open_again(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    handler, _ = paged_site(CL_HTML, pages_with_jobs=1)
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=1)
    with conn:
        conn.execute("UPDATE job_posts SET status = 'expired'")
    run_paged(conn, tmp_cfg, make_client(tmp_cfg, handler), careerlink, max_pages=1)
    assert {r[0] for r in conn.execute("SELECT status FROM job_posts")} == {"unknown"}
