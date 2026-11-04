"""CLI: exit code phản ánh kết quả lần chạy (Task Scheduler dựa vào đây). Không gọi mạng."""

import pytest
from typer.testing import CliRunner

import jobmarket.__main__ as cli


@pytest.fixture
def isolated(tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    return tmp_cfg


def _fake_run(status):
    def run_source(conn, cfg, client, name, max_pages=None):
        with conn:
            return conn.execute(
                "INSERT INTO runs (source, scope, started_at, status, app_version) VALUES ('topcv', '{}', 'x', ?, 't')",
                (status,),
            ).lastrowid

    return run_source


@pytest.mark.parametrize(("status", "code"), [("success", 0), ("partial", 0), ("failed", 1)])
def test_run_exit_code(isolated, monkeypatch, status, code):
    monkeypatch.setattr(cli, "run_source", _fake_run(status))
    result = CliRunner().invoke(cli.app, ["run"])
    assert result.exit_code == code, result.output
    assert f'"status": "{status}"' in result.output


def test_import_csv_and_summary(isolated):
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "mockup_jobs.csv"
    runner = CliRunner()
    assert "nạp 374 dòng" in runner.invoke(cli.app, ["import-csv", str(fixture)]).output
    assert "nạp 0 dòng" in runner.invoke(cli.app, ["import-csv", str(fixture)]).output
    summary = runner.invoke(cli.app, ["summary"]).output
    assert "Tin duy nhất:      348" in summary


from tests.test_pipeline import tmp_cfg  # noqa: E402, F401  (fixture dùng chung)


def test_crawl_due_counts_from_last_run_in_db(tmp_path):
    """`start` crawl lại một nguồn chỉ khi lần chạy cuối (kể cả lỗi/bị chặn) đã cách ≥ N giờ — tắt/mở lại lệnh không
    làm crawl dồn (CareerLink hạn chế khi đọc dày)."""
    from datetime import datetime, timedelta

    from jobmarket.__main__ import _crawl_due
    from jobmarket.store.db import init_db
    from jobmarket.timeutil import now_local

    conn = init_db(tmp_path / "c.db")
    assert _crawl_due(conn, "careerlink", 4) == (True, None)  # chưa chạy bao giờ
    recent = (datetime.fromisoformat(now_local()) - timedelta(hours=1)).isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "INSERT INTO runs (source, scope, started_at, status, app_version) "
            "VALUES ('careerlink', '{}', ?, 'failed', 't')",
            (recent,),
        )
    due, next_at = _crawl_due(conn, "careerlink", 4)
    assert not due and next_at  # bị chặn 1 giờ trước → chờ đủ 4 giờ
    assert _crawl_due(conn, "careerlink", 0.5)[0]


def test_run_skips_source_crawled_recently_unless_forced(isolated, monkeypatch):
    """Task Scheduler + `start` cùng lúc không được crawl dồn một nguồn (review M5)."""
    calls = []

    def run_source(conn, cfg, client, name, max_pages=None):
        calls.append(name)
        return _fake_run("success")(conn, cfg, client, name, max_pages)

    monkeypatch.setattr(cli, "run_source", run_source)
    monkeypatch.setattr(cli, "_crawl_due", lambda conn, name, hours: (False, "18:00 04/11"))
    result = CliRunner().invoke(cli.app, ["run"])
    assert result.exit_code == 0 and calls == [] and "skipped" in result.output
    assert CliRunner().invoke(cli.app, ["run", "--force"]).exit_code == 0 and calls


def test_watcher_survives_file_moved_away(isolated, tmp_path):
    from jobmarket.store.db import init_db

    folder = tmp_path / "inbox"
    watcher = cli.InboxWatcher(init_db(isolated.path("db_file")), isolated, folder)
    page = folder / "a.html"
    page.write_text("<html>không phải trang tin</html>", encoding="utf-8")
    assert watcher.poll() is None  # lần 1: chờ file ổn định
    page.unlink()  # bị nơi khác chuyển đi giữa 2 lần kiểm tra
    assert watcher.poll() is None
