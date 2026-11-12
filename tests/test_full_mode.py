"""`run --full`: quét trọn site mỗi lần chạy — bỏ giới hạn, nhưng không gõ vào chỗ bị chặn/bị cấm và không bỏ qua
thời gian nghỉ khi bị chặn. Không gọi mạng."""

import json
from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

import jobmarket.__main__ as cli
from jobmarket.store.db import init_db, now_iso
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


def test_full_config_raises_only_enabled_tiers(tmp_cfg):  # noqa: F811
    raw = tmp_cfg.raw["sources"]
    raw["vieclam24h"].update(deep_pages_per_run=0, sitemap_max_per_run=300, max_pages=1)
    raw["careerviet"].update(deep_pages_per_run=75, detail_max_per_run=0)
    raw["careerlink"].update(full_min_delay_seconds=15, min_delay_seconds=10, detail_max_per_run=10)
    full = cli.full_config(tmp_cfg, ["vieclam24h", "careerviet", "careerlink"])
    v24, cv, cl = (full.source(n) for n in ("vieclam24h", "careerviet", "careerlink"))
    assert v24["deep_pages_per_run"] == 0 and v24["max_pages"] == 1  # trang 2 trả 403 → không quét sâu
    assert v24["sitemap_max_per_run"] == cli.UNLIMITED and v24["max_requests"] == cli.UNLIMITED
    assert cv["deep_pages_per_run"] == cli.UNLIMITED and cv["detail_max_per_run"] == 0  # robots cấm trang chi tiết
    assert cl["min_delay_seconds"] == 15 and cl["detail_max_per_run"] == cli.UNLIMITED
    assert tmp_cfg.source("careerviet")["deep_pages_per_run"] == 75  # cấu hình gốc không bị sửa


def _ago(hours: float) -> str:
    return (datetime.fromisoformat(now_iso()) - timedelta(hours=hours)).isoformat(timespec="seconds")


@pytest.fixture
def isolated(tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    return tmp_cfg


def _insert(conn, source, started, status, notes=()):
    with conn:
        return conn.execute(
            "INSERT INTO runs (source, scope, started_at, finished_at, status, posts_new, error_sample, app_version) "
            "VALUES (?, '{}', ?, ?, ?, 1, ?, 't')",
            (source, started, None if status == "running" else started, status, json.dumps(list(notes))),
        ).lastrowid


def test_full_ignores_spacing_but_not_cooldown_and_skips_mysql(isolated, monkeypatch):
    conn = init_db(isolated.path("db_file"))
    _insert(conn, "careerviet", _ago(0.5), "success")  # vừa chạy 30 phút trước
    _insert(conn, "careerlink", _ago(0.5), "failed", ["Blocked: 200 challenge at x"])
    seen, mirrored = [], []

    def run_source(conn, cfg, client, name, max_pages=None):
        seen.append((name, cfg.source(name)["max_requests"]))
        return _insert(conn, name, now_iso(), "success")

    monkeypatch.setattr(cli, "run_source", run_source)
    monkeypatch.setattr(cli, "_mirror_mysql", lambda *a: mirrored.append(1))
    assert CliRunner().invoke(cli.app, ["run", "--source", "careerviet", "--full"]).exit_code == 0
    assert seen == [("careerviet", cli.UNLIMITED)] and not mirrored
    out = CliRunner().invoke(cli.app, ["run", "--source", "careerlink", "--full"]).output
    assert "nghỉ tới" in out and len(seen) == 1  # đang bị chặn → --full cũng không chạy


def test_single_source_run_leaves_other_sources_long_run_alone(isolated, monkeypatch):
    conn = init_db(isolated.path("db_file"))
    other = _insert(conn, "vieclam24h", _ago(5), "running")  # `run --full` cửa sổ khác, đã chạy 5 giờ
    monkeypatch.setattr(cli, "run_source", lambda conn, cfg, client, name, max_pages=None: _insert(
        conn, name, now_iso(), "success"
    ))  # fmt: skip
    CliRunner().invoke(cli.app, ["run", "--source", "careerviet", "--full"])
    assert conn.execute("SELECT status FROM runs WHERE id = ?", (other,)).fetchone()[0] == "running"
