"""Quy tắc thu thập 2026-11-09: sitemap (Việc Làm 24h), Crawl-delay của robots, nghỉ sau khi bị chặn, gom trang TopCV
userscript tải về. Mạng giả lập, không gọi ra ngoài."""

import copy
import dataclasses
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import jobmarket.__main__ as cli
from jobmarket import health
from jobmarket.fetch import sitemap
from jobmarket.fetch.client import Disallowed, PoliteClient
from jobmarket.fetch.robots import RobotsRules
from jobmarket.pipeline import run_paged
from jobmarket.sources import vieclam24h as v
from jobmarket.store.db import init_db, now_iso
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

UA = "jobmarket-crawl/0.1 (personal, non-commercial, low-rate)"
LISTING = (Path(__file__).parent / "fixtures" / "vieclam24h" / "listing_sample.html").read_text(encoding="utf-8")


# --- robots Crawl-delay ---------------------------------------------------------------------------------------------


def test_crawl_delay_from_matching_group():
    rules = RobotsRules(
        ["User-agent: *", "Crawl-delay: 5", "Disallow: /admin", "", "User-agent: other", "Crawl-delay: 99"]
    )
    assert rules.crawl_delay(UA) == 5
    assert rules.can_fetch(UA, "https://x.vn/a") and not rules.can_fetch(UA, "https://x.vn/admin")
    named = RobotsRules(["User-agent: *", "Crawl-delay: 5", "", "User-agent: jobmarket-crawl", "Crawl-delay: 20"])
    assert named.crawl_delay(UA) == 20  # nhóm đúng tên bot thắng nhóm *
    assert RobotsRules(["User-agent: *", "Crawl-delay: abc", "Crawl-delay: nan"]).crawl_delay(UA) is None
    assert RobotsRules(["User-agent: *", "Disallow:"]).crawl_delay(UA) is None


def _client(robots: str, seen: list[str] | None = None) -> PoliteClient:
    def handler(request):
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, text="ok")

    cfg = {"user_agent": UA, "min_delay_seconds": 0, "max_retries": 0, "timeout_seconds": 5}
    return PoliteClient(cfg, transport=httpx.MockTransport(handler))


def test_client_waits_crawl_delay_and_refuses_huge_delay():
    client = _client("User-agent: *\nCrawl-delay: 7\n")
    client._check("https://x.vn/a")
    assert client._host_delay["x.vn"] == 7  # max(min_delay 0, Crawl-delay 7)
    seen: list[str] = []
    with pytest.raises(Disallowed):
        _client("User-agent: *\nCrawl-delay: 3600\n", seen).get("https://x.vn/a")
    assert seen == ["/robots.txt"]  # không gửi request trang nào


# --- sitemap + adapter Việc Làm 24h ---------------------------------------------------------------------------------


def test_sitemap_parse_index_and_urlset():
    index = (
        '<?xml version="1.0"?><sitemapindex xmlns="x"><sitemap><loc>https://a/s-1.xml</loc></sitemap></sitemapindex>'
    )
    assert sitemap.parse(index) == (True, ["https://a/s-1.xml"])
    urlset = "<urlset><url><loc> https://a/x?p=1&amp;q=2 </loc></url></urlset>"
    assert sitemap.parse(urlset) == (False, ["https://a/x?p=1&q=2"])


def test_vieclam24h_sitemap_filters():
    assert v.follow_sitemap("https://cdn1.vieclam24h.vn/file/sitemap/daily/job-0.xml")
    assert v.follow_sitemap("https://cdn1.vieclam24h.vn/file/sitemap/job/tintuyendung-3.xml")
    assert not v.follow_sitemap("https://cdn1.vieclam24h.vn/file/sitemap/daily/blog-0.xml")
    assert not v.follow_sitemap("https://cdn1.vieclam24h.vn/file/sitemap/daily/employer-0.xml")
    assert v.job_id_from_url("https://vieclam24h.vn/an-ninh-bao-ve/nhan-vien-c2p210000160id2984788.html") == "2984788"
    assert v.job_id_from_url("https://vieclam24h.vn/viec-lam-it-phan-mem-o8.html") is None
    assert v.job_id_from_url("https://evil.vn/x-id123.html") is None


def _detail_html(job_id: int) -> str:
    """Trang chi tiết dựng từ dữ liệu trang danh sách mẫu: cùng `item` + bảng tra (cấu trúc trang thật 2026-11-09)."""
    data = v._data(LISTING)
    item = copy.deepcopy(v._jobs_response(data)["items"][0])
    item.update(id=job_id, contact_email="hr@example.com", contact_name="Chị A", contact_phone="0901234567")
    data["props"]["initialState"]["api"]["jobDetailHiddenContact"] = {"data": item}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_parse_detail_job_matches_listing_and_drops_contacts():
    listing = v.parse_listing(LISTING, None)[0]
    url = "https://vieclam24h.vn/x/y-c1p2id300000001.html"
    job = v.parse_detail_job(_detail_html(300000001), url)
    assert (job.source_job_id, job.url) == ("300000001", url)
    for field in ("title", "company_name", "salary_raw", "location_raw", "experience_raw", "level_raw",
                  "industry_label", "posted_date", "deadline"):  # fmt: skip
        assert getattr(job, field) == getattr(listing, field), field
    dumped = json.dumps(job.raw_payload, ensure_ascii=False)
    assert "@" not in dumped and "0901234567" not in dumped and "Chị A" not in dumped
    assert v.parse_detail_job("<html>Không có tin</html>", url) is None


INDEX = (
    "<sitemapindex><sitemap><loc>https://cdn1.vieclam24h.vn/file/sitemap/daily/blog-0.xml</loc></sitemap>"
    "<sitemap><loc>https://cdn1.vieclam24h.vn/file/sitemap/daily/job-0.xml</loc></sitemap></sitemapindex>"
)
JOB_INDEX = "<sitemapindex><sitemap><loc>https://cdn1.vieclam24h.vn/file/sitemap/job/tintuyendung-0.xml</loc></sitemap></sitemapindex>"


def _site(urls: list[str], detail_status: dict[str, int] | None = None, calls: list[str] | None = None):
    detail_status = detail_status or {}

    def handler(request):
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 0\nDisallow: /admin\n")
        if path.endswith("sitemap-index.xml"):
            return httpx.Response(200, text=INDEX)
        if path.endswith("daily/job-0.xml"):
            return httpx.Response(200, text=JOB_INDEX)
        if path.endswith("tintuyendung-0.xml"):
            return httpx.Response(
                200, text="<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
            )
        if path.endswith("blog-0.xml"):
            raise AssertionError("không được đọc sitemap blog")
        job_id = v.job_id_from_url(str(request.url))
        if job_id:
            status = detail_status.get(job_id, 200)
            return httpx.Response(status, text=_detail_html(int(job_id)) if status == 200 else "gone")
        return httpx.Response(200, text=LISTING)  # trang theo nghề

    return handler


def _sitemap_cfg(tmp_cfg, **extra):  # noqa: F811
    raw = copy.deepcopy(tmp_cfg.raw)
    raw["sources"]["vieclam24h"].update(
        {"sitemap_url": "https://vieclam24h.vn/file/sitemap/sitemap-index.xml", "sitemap_max_per_run": 10, **extra}
    )
    return dataclasses.replace(tmp_cfg, raw=raw)


def _url(job_id) -> str:
    return f"https://vieclam24h.vn/nganh/tin-c1p2id{job_id}.html"


def test_sitemap_adds_unseen_jobs_skips_known_and_marks_gone(tmp_cfg):  # noqa: F811
    cfg = _sitemap_cfg(tmp_cfg)
    known = v.parse_listing(LISTING, None)[0].source_job_id
    urls = [_url(known), _url(300000002), _url(300000001), _url(300000003)]
    calls: list[str] = []
    handler = _site(urls, {"300000003": 404}, calls)
    conn = init_db(cfg.path("db_file"))
    run_id = run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    scope = json.loads(conn.execute("SELECT scope FROM runs WHERE id = ?", (run_id,)).fetchone()[0])
    assert scope["sitemap"]["urls"] == 4 and scope["sitemap"]["saved"] == 2 and scope["sitemap"]["gone"] == 1
    assert not any(known in c for c in calls)  # tin đã thấy ở trang danh sách: không đọc trang chi tiết
    ids = {r[0] for r in conn.execute("SELECT source_job_id FROM job_posts WHERE source = 'vieclam24h'")}
    assert {"300000001", "300000002"} <= ids and "300000003" not in ids
    outcome = dict(conn.execute("SELECT source_job_id, outcome FROM sitemap_jobs"))
    assert outcome["300000003"] == "gone" and outcome["300000001"] == "saved" and outcome[known] is None

    # Lần chạy sau trong 24 giờ: không tải lại sitemap, không đọc lại tin đã kiểm
    calls.clear()
    run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    assert not any("sitemap" in c or "id30000000" in c for c in calls)


def test_sitemap_newest_first_and_budget(tmp_cfg):  # noqa: F811
    cfg = _sitemap_cfg(tmp_cfg, sitemap_max_per_run=2)
    calls: list[str] = []
    urls = [_url(i) for i in (300000001, 300000010, 300000002, 99999999)]
    conn = init_db(cfg.path("db_file"))
    run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(_site(urls, calls=calls))), v, 1)
    fetched = [c for c in calls if "/nganh/" in c]
    assert fetched == ["/nganh/tin-c1p2id300000010.html", "/nganh/tin-c1p2id300000002.html"]


def test_sitemap_three_empty_details_stop_as_blocked(tmp_cfg):  # noqa: F811
    cfg = _sitemap_cfg(tmp_cfg)
    base = _site([_url(i) for i in range(300000001, 300000006)])

    def handler(request):
        if "/nganh/" in request.url.path:
            return httpx.Response(200, text="<html>trang rỗng</html>")  # HTTP 200 nhưng không có tin
        return base(request)

    conn = init_db(cfg.path("db_file"))
    run_id = run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    row = conn.execute("SELECT status, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "partial" and json.loads(row["error_sample"])[0].startswith("Blocked (sitemap)")
    # Trang rỗng chưa được đánh dấu đã kiểm (có thể do bị hạn chế) → lần sau đọc lại
    assert conn.execute("SELECT COUNT(*) FROM sitemap_jobs WHERE checked_at IS NOT NULL").fetchone()[0] == 0


# --- nghỉ sau khi bị chặn -------------------------------------------------------------------------------------------


def _ago(hours: float) -> str:
    return (datetime.fromisoformat(now_iso()) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _run(conn, hours_ago: float, notes: list[str], status="failed", source="careerlink", new=0):
    with conn:
        conn.execute(
            "INSERT INTO runs (source, scope, started_at, finished_at, status, posts_new, error_sample, app_version) "
            "VALUES (?, '{}', ?, ?, ?, ?, ?, 't')",
            (source, _ago(hours_ago), _ago(hours_ago), status, new, json.dumps(notes)),
        )


BLOCK = ["Blocked: 200 challenge at https://www.careerlink.vn/vieclam/list"]


def test_cooldown_doubles_per_consecutive_block_and_caps(tmp_path):
    conn = init_db(tmp_path / "c.db")
    assert health.blocked_cooldown(conn, "careerlink", 6, 24) is None  # chưa chạy
    _run(conn, 10, [], status="success", new=5)
    _run(conn, 5, BLOCK)
    assert health.blocked_cooldown(conn, "careerlink", 6, 24)[1] == 1  # 1 lần: nghỉ 6 giờ, mới qua 5
    assert health.blocked_cooldown(conn, "careerlink", 4, 24) is None  # nghỉ 4 giờ đã qua
    _run(conn, 4, BLOCK)
    until, streak = health.blocked_cooldown(conn, "careerlink", 3, 24)  # 2 lần liền → 3 × 2 = 6 giờ từ lần cuối
    assert streak == 2 and until == datetime.fromisoformat(_ago(4)) + timedelta(hours=6)
    _run(conn, 3, ["Blocked (trang chi tiết): 403 at x"], status="partial", new=12)
    _run(conn, 2.9, ["x"], status="interrupted")  # bị ngắt, chưa lưu tin → không tính
    until, streak = health.blocked_cooldown(conn, "careerlink", 6, 10)
    assert streak == 3 and until == datetime.fromisoformat(_ago(3)) + timedelta(hours=10)  # 24 giờ → trần 10


def test_cooldown_resets_after_a_clean_run_and_ignores_robots(tmp_path):
    conn = init_db(tmp_path / "c.db")
    _run(conn, 3, BLOCK)
    _run(conn, 1, [], status="success", new=3)
    assert health.blocked_cooldown(conn, "careerlink", 6, 24) is None
    _run(conn, 0.5, ["Disallowed: https://x/vi/jobs/1"], status="partial", new=1)  # robots cấm ≠ bị chặn
    assert health.blocked_cooldown(conn, "careerlink", 6, 24) is None


@pytest.fixture
def isolated(tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    return tmp_cfg


def test_force_does_not_skip_cooldown(isolated, monkeypatch):
    conn = init_db(isolated.path("db_file"))
    _run(conn, 0.5, BLOCK)
    calls = []
    monkeypatch.setattr(cli, "run_source", lambda *a, **k: calls.append(a) or 1)
    out = CliRunner().invoke(cli.app, ["run", "--source", "careerlink", "--force"]).output
    assert not calls and "nghỉ tới" in out and "--force" in out


# --- gom trang TopCV userscript tải về --------------------------------------------------------------------------------


def test_collect_downloads_moves_only_prefixed_html(tmp_cfg, tmp_path):  # noqa: F811
    downloads, inbox = tmp_path / "Downloads", tmp_path / "inbox"
    downloads.mkdir()
    inbox.mkdir()
    ours = downloads / "jobmarket-topcv-listing-20261109-120000-tim-viec-lam.html"
    ours.write_text("<html>tin</html>", encoding="utf-8")
    os.utime(ours, (1_700_000_000, 1_700_000_000))
    (downloads / "jobmarket-topcv-detail-x.html.crdownload").write_text("dở", encoding="utf-8")
    (downloads / "hoa-don.html").write_text("riêng tư", encoding="utf-8")
    raw = copy.deepcopy(tmp_cfg.raw)
    raw["sources"]["topcv"]["downloads_dir"] = str(downloads)
    cfg = dataclasses.replace(tmp_cfg, raw=raw)
    assert cli._collect_downloads(cfg, inbox) == 1
    moved = inbox / ours.name
    assert moved.exists() and int(moved.stat().st_mtime) == 1_700_000_000  # giờ lưu trang = ngày thấy tin
    assert sorted(p.name for p in downloads.iterdir()) == ["hoa-don.html", "jobmarket-topcv-detail-x.html.crdownload"]
    assert cli._collect_downloads(tmp_cfg, inbox) == 0  # downloads_dir rỗng = tắt
