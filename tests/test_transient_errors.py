"""Lỗi tạm thời (timeout, 502/504) không được làm dừng cả lần quét — Việc Làm 24h 2026-11-11: 1 ReadTimeout làm dừng
14.700 trang sitemap còn lại. Mạng giả lập, không gọi ra ngoài."""

import copy
import dataclasses
import json

import httpx
import pytest

from jobmarket import health
from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import MAX_FAILURE_STREAK, run_paged
from jobmarket.sources import vieclam24h as v
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)
from tests.test_sitemap_cooldown import _site, _url

CFG = {"user_agent": "jobmarket-crawl/0.1", "min_delay_seconds": 0, "max_retries": 2, "timeout_seconds": 5}


def _client(responses: list) -> tuple[PoliteClient, list[str]]:
    """`responses`: lần lượt cho trang /a — số = mã HTTP, Exception = lỗi mạng."""
    calls: list[str] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        item = responses[min(len(calls), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, text="ok")

    return PoliteClient(CFG, transport=httpx.MockTransport(handler)), calls


def test_timeout_is_retried():
    client, calls = _client([httpx.ReadTimeout("chậm"), 200])
    assert client.get("https://x.vn/a") == "ok" and len(calls) == 2


@pytest.mark.parametrize("status", [502, 504])
def test_gateway_error_is_retried(status):
    client, calls = _client([status, 200])
    assert client.get("https://x.vn/a") == "ok" and len(calls) == 2


def test_gives_up_with_ordinary_error_not_blocked():
    client, calls = _client([502, 502, 502])
    with pytest.raises(httpx.HTTPStatusError):  # không phải Blocked → không kích hoạt thời gian nghỉ
        client.get("https://x.vn/a")
    assert len(calls) == 3
    client, _ = _client([httpx.ConnectError("mất mạng")])
    with pytest.raises(httpx.ConnectError):
        client.get("https://x.vn/a")


def _cfg(tmp_cfg):  # noqa: F811
    raw = copy.deepcopy(tmp_cfg.raw)
    raw["sources"]["vieclam24h"].update(
        sitemap_url="https://vieclam24h.vn/file/sitemap/sitemap-index.xml", sitemap_max_per_run=20
    )
    raw["crawl"]["max_retries"] = 0
    return dataclasses.replace(tmp_cfg, raw=raw)


def test_sitemap_skips_failing_job_and_keeps_going(tmp_cfg):  # noqa: F811
    cfg = _cfg(tmp_cfg)
    base = _site([_url(i) for i in (300000003, 300000002, 300000001)])

    def handler(request):
        if request.url.path.endswith("id300000002.html"):
            raise httpx.ReadTimeout("chậm")
        return base(request)

    conn = init_db(cfg.path("db_file"))
    run_id = run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    run = conn.execute("SELECT status, scope, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert json.loads(run["scope"])["sitemap"]["saved"] == 2  # tin trước VÀ sau tin lỗi đều được đọc
    assert run["status"] == "partial" and "ReadTimeout" in run["error_sample"]
    pending = conn.execute("SELECT source_job_id FROM sitemap_jobs WHERE checked_at IS NULL").fetchall()
    assert [r[0] for r in pending] == ["300000002"]  # chưa đánh dấu → lần sau đọc lại
    assert health.blocked_cooldown(conn, "vieclam24h", 6, 24) is None  # lỗi mạng ≠ bị chặn


def test_sitemap_stops_after_failure_streak(tmp_cfg):  # noqa: F811
    cfg = _cfg(tmp_cfg)
    ids = list(range(300000001, 300000001 + MAX_FAILURE_STREAK + 3))
    base = _site([_url(i) for i in ids])
    detail_calls: list[str] = []

    def handler(request):
        if "/nganh/" in request.url.path:
            detail_calls.append(request.url.path)
            return httpx.Response(502, text="Bad Gateway")
        return base(request)

    conn = init_db(cfg.path("db_file"))
    run_id = run_paged(conn, cfg, PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler)), v, max_pages=1)
    assert len(detail_calls) == MAX_FAILURE_STREAK  # máy chủ đang lỗi → dừng, không gõ tiếp
    errors_count = conn.execute("SELECT errors_count FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
    assert errors_count == MAX_FAILURE_STREAK + 1  # 5 trang lỗi + 1 dòng "… trang liền lỗi — dừng"
    assert health.blocked_cooldown(conn, "vieclam24h", 6, 24) is None
