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
