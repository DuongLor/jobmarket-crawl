"""Lần chạy bị ngắt (Ctrl+C / tắt máy) không kẹt 'running' và không làm hoãn nguồn; chuỗi ngày liên tục (mốc 1A)."""

from datetime import date, datetime, timedelta

import pytest
from typer.testing import CliRunner

import jobmarket.__main__ as cli
from jobmarket import health
from jobmarket.store.db import init_db, now_iso
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


@pytest.fixture
def conn(tmp_cfg):  # noqa: F811
    return init_db(tmp_cfg.path("db_file"))


def _run(conn, source="vieclam24h", started="2026-11-05T10:56:08", status="running", new=0, updated=0) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO runs (source, scope, started_at, status, posts_new, posts_updated, app_version) "
            "VALUES (?, '{}', ?, ?, ?, ?, 't')",
            (source, started, status, new, updated),
        ).lastrowid


def _job(conn, job_id: str, first_run: int, last_run: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO job_posts (source, source_job_id, title, status, dq_status, dq_flags, dedup_key, "
            "first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version) "
            "VALUES ('vieclam24h', ?, 't', 'unknown', 'ok', '', ?, 'x', 'x', ?, ?, 'v')",
            (job_id, f"k{job_id}", first_run, last_run),
        )


def test_interrupted_run_is_closed_with_counts_recomputed(conn):
    old = _run(conn, source="careerviet", status="success")
    run = _run(conn)
    _job(conn, "a", run, run)  # mới trong lần chạy bị ngắt
    _job(conn, "b", old, run)  # cập nhật trong lần chạy bị ngắt
    assert health.close_stale_runs(conn, source="vieclam24h") == 1
    row = conn.execute("SELECT status, posts_new, posts_updated, finished_at FROM runs WHERE id = ?", (run,)).fetchone()
    assert (row["status"], row["posts_new"], row["posts_updated"]) == ("interrupted", 1, 1) and row["finished_at"]


def test_startup_cleanup_leaves_recent_running_run_alone(conn):
    recent = _run(conn, started=now_iso())  # có thể là tiến trình khác đang chạy thật
    stale = _run(conn, started=(datetime.fromisoformat(now_iso()) - timedelta(hours=3)).isoformat(timespec="seconds"))
    assert health.close_stale_runs(conn, older_than_minutes=120) == 1
    status = dict(conn.execute("SELECT id, status FROM runs"))
    assert status[recent] == "running" and status[stale] == "interrupted"


def _ago(minutes: float) -> str:
    return (datetime.fromisoformat(now_iso()) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _seen(conn, run: int, at: str) -> None:
    _job(conn, f"j{run}", run, run)
    with conn:
        conn.execute("UPDATE job_posts SET last_seen_at = ? WHERE last_run_id = ?", (at, run))


def test_long_full_run_still_saving_is_not_closed_and_blocks_second_process(conn):
    busy = _run(conn, started=_ago(600))  # `run --full` chạy 10 giờ, vừa lưu tin 2 phút trước
    _seen(conn, busy, _ago(2))
    dead = _run(conn, source="careerviet", started=_ago(600))  # im lặng 3 giờ = tiến trình đã chết
    _seen(conn, dead, _ago(180))
    assert health.active_run(conn, "vieclam24h") == busy
    assert health.active_run(conn, "careerviet") is None
    assert health.close_stale_runs(conn, older_than_minutes=120) == 1
    status = dict(conn.execute("SELECT id, status FROM runs"))
    assert status[busy] == "running" and status[dead] == "interrupted"


def test_just_started_run_counts_as_active(conn):
    run = _run(conn, started=_ago(1))
    assert health.active_run(conn, "vieclam24h") == run
    assert health.active_run(conn, "itviec") is None


def test_run_skips_source_another_process_is_reading(tmp_cfg, conn, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    monkeypatch.setattr(cli, "keep_awake", lambda: None)
    called: list[str] = []
    monkeypatch.setattr(cli, "run_source", lambda *a, **k: called.append(a[3]))
    _run(conn, source="careerlink", started=_ago(1))
    result = CliRunner().invoke(cli.app, ["run", "--source", "careerlink", "--full"])
    assert "đang đọc nguồn này" in result.output and called == []


def test_interrupted_run_without_data_does_not_delay_next_crawl(conn):
    _run(conn, started=now_iso(), status="interrupted")
    assert cli._crawl_due(conn, "vieclam24h", 4)[0]
    _run(conn, started=now_iso(), status="interrupted", new=5)  # đã gửi request, lưu tin → giữ khoảng cách
    assert not cli._crawl_due(conn, "vieclam24h", 4)[0]


def test_ctrl_c_during_run_marks_run_interrupted(tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)

    def run_source(conn, cfg, client, name, max_pages=None):
        _run(conn, source=name, started=now_iso())
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_source", run_source)
    CliRunner().invoke(cli.app, ["run", "--source", "careerviet"])
    db = init_db(tmp_cfg.path("db_file"))
    assert db.execute("SELECT status FROM runs WHERE source = 'careerviet'").fetchone()[0] == "interrupted"


def test_streak_counts_back_from_today_or_yesterday():
    d = date(2026, 11, 10)
    days = {d - timedelta(days=i) for i in range(1, 5)} | {d - timedelta(days=7)}
    assert health.streak(days, d) == 4  # hôm nay chưa chạy → tính từ hôm qua
    assert health.streak(days | {d}, d) == 5
    assert health.streak(set(), d) == 0


def test_joint_streak_needs_min_sources_with_data_each_day(conn):
    for i, src in [(0, "careerviet"), (0, "vieclam24h"), (1, "careerviet"), (1, "careerlink"), (2, "careerviet")]:
        _run(conn, source=src, started=f"2026-11-0{5 - i}T10:00:00", status="partial", new=1)
    _run(conn, source="careerlink", started="2026-11-05T12:00:00", status="failed", new=0)
    r = health.report(conn, ["careerlink", "careerviet", "vieclam24h"], min_sources=2, today=date(2026, 11, 5))
    assert r["joint_streak_days"] == 2  # 05 (careerviet + vieclam24h), 04 (careerviet + careerlink); 03 chỉ 1 nguồn
    assert r["sources"]["careerviet"]["streak_days"] == 3 and r["sources"]["careerlink"]["streak_days"] == 1


def test_health_command_prints_verdict(tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    out = CliRunner().invoke(cli.app, ["health"]).output
    assert "0/7" in out and "chưa đạt" in out


def test_pace_slows_after_recent_blocks_and_recovers(conn):
    blocked = '["Blocked: 403 at https://vieclam24h.vn/x"]'
    for started in (_ago(60), _ago(600), _ago(60 * 24 * 5)):  # 2 lần trong 3 ngày qua, 1 lần đã cũ
        rid = _run(conn, started=started, status="partial")
        with conn:
            conn.execute("UPDATE runs SET error_sample = ? WHERE id = ?", (blocked, rid))
    assert health.pace_factor(conn, "vieclam24h", days=3, step=1.5, cap=3) == (2.25, 2)
    assert health.pace_factor(conn, "vieclam24h", days=3, step=1.5, cap=2) == (2, 2)
    assert health.pace_factor(conn, "careerviet", days=3, step=1.5, cap=3) == (1.0, 0)
    assert health.pace_factor(conn, "vieclam24h", days=0, step=1.5, cap=3) == (1.0, 0)
