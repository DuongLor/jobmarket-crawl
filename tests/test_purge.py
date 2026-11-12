"""Xoá dữ liệu 1 nguồn (2026-11-12: bỏ CareerLink) — nguồn khác giữ nguyên, có bản sao lưu trước khi xoá."""

import sqlite3

from typer.testing import CliRunner

import jobmarket.__main__ as cli
from jobmarket.store import purge
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


def _seed(conn, source: str, job_id: str) -> None:
    with conn:
        run = conn.execute(
            "INSERT INTO runs (source, scope, started_at, status, posts_new, posts_updated, app_version) "
            "VALUES (?, '{}', '2026-11-10T10:00:00', 'success', 1, 0, 't')",
            (source,),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_posts (source, source_job_id, title, status, dq_status, dq_flags, dedup_key, "
            "first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version) "
            "VALUES (?, ?, 't', 'unknown', 'ok', '', ?, 'x', 'x', ?, ?, 'v')",
            (source, job_id, f"{source}:{job_id}", run, run),
        )
        conn.execute(
            "INSERT INTO crawl_listings (source, url, last_crawled_at) VALUES (?, ?, 'x')",
            (source, f"https://x/{job_id}"),
        )


def _count(conn, table: str, source: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE source = ?", (source,)).fetchone()[0]


def test_purge_removes_only_that_source(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    _seed(conn, "careerlink", "1")
    _seed(conn, "itviec", "2")
    counts = purge.purge_source(conn, "careerlink")
    assert counts["job_posts"] == 1 and counts["runs"] == 1
    for table in ("job_posts", "runs", "crawl_listings"):
        assert _count(conn, table, "careerlink") == 0 and _count(conn, table, "itviec") == 1


def test_cli_refuses_enabled_source_and_backs_up_before_delete(tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    conn = init_db(tmp_cfg.path("db_file"))
    _seed(conn, "careerlink", "1")
    runner = CliRunner()
    assert "đang bật" in runner.invoke(cli.app, ["purge-source", "--source", "careerlink", "--yes"]).output
    tmp_cfg.raw["sources"]["careerlink"]["enabled"] = False
    dry = runner.invoke(cli.app, ["purge-source", "--source", "careerlink"]).output
    assert "sẽ xoá 1 tin" in dry and _count(conn, "job_posts", "careerlink") == 1
    out = runner.invoke(cli.app, ["purge-source", "--source", "careerlink", "--yes"]).output
    assert "Đã sao lưu" in out and _count(conn, "job_posts", "careerlink") == 0
    (saved,) = (tmp_cfg.path("data_dir") / "backup").glob("market-*.db")
    assert sqlite3.connect(saved).execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 1
