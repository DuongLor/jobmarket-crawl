"""Bản sao MySQL: kiểu cột, khoá so khớp nhị phân, lỗi đồng bộ không làm hỏng lần crawl, test không chạm MySQL thật."""

import dataclasses

import pytest
from typer.testing import CliRunner

import jobmarket.__main__ as cli
from jobmarket import mysql_mirror
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


def test_column_types_follow_sqlite_affinity():
    assert mysql_mirror.column_type("INTEGER", None) == "BIGINT"
    assert mysql_mirror.column_type("REAL", None) == "DOUBLE"
    assert mysql_mirror.column_type("TEXT", None) == "LONGTEXT"
    assert "utf8mb4_bin" in mysql_mirror.column_type("TEXT", 10)  # 'ha noi' ≠ 'hà nội' như trong SQLite


def test_create_sql_keeps_composite_primary_key(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    ddl, cols = mysql_mirror.create_sql(conn, "label_aliases", "label_aliases__new")
    assert cols == ["label_type", "alias_norm", "code"]
    assert "PRIMARY KEY (`label_type`, `alias_norm`)" in ddl and "`code` LONGTEXT" in ddl


def test_rejects_unsafe_identifier():
    with pytest.raises(ValueError):
        mysql_mirror._ident("x`; DROP DATABASE y")


def test_tests_never_reach_real_mysql(tmp_cfg):  # noqa: F811
    with pytest.raises(AssertionError):
        mysql_mirror.sync(init_db(tmp_cfg.path("db_file")), {"database": "jobmarket"})


def _fake_run(conn, cfg, client, name, max_pages=None):
    with conn:
        return conn.execute(
            "INSERT INTO runs (source, scope, started_at, finished_at, status, app_version) "
            "VALUES (?, '{}', '2026-11-05T10:00:00', '2026-11-05T10:05:00', 'success', 't')",
            (name,),
        ).lastrowid


def _enabled(tmp_cfg, monkeypatch):  # noqa: F811
    cfg = dataclasses.replace(tmp_cfg, raw={**tmp_cfg.raw, "mysql": {"enabled": True, "database": "jobmarket"}})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "run_source", _fake_run)


def test_run_syncs_mysql_when_enabled(tmp_cfg, monkeypatch):  # noqa: F811
    _enabled(tmp_cfg, monkeypatch)
    calls = []
    monkeypatch.setattr(mysql_mirror, "sync", lambda conn, c: calls.append(c) or {
        "database": "jobmarket", "tables": {"runs": 1}, "seconds": 0.1})  # fmt: skip
    out = CliRunner().invoke(cli.app, ["run", "--source", "careerviet"]).output
    assert len(calls) == 1 and "MySQL: đã đồng bộ 1 dòng" in out


def test_mysql_error_does_not_fail_crawl(tmp_cfg, monkeypatch):  # noqa: F811
    _enabled(tmp_cfg, monkeypatch)
    result = CliRunner().invoke(cli.app, ["run", "--source", "careerviet"])  # connect bị chặn → lỗi
    assert result.exit_code == 0 and "MySQL: đồng bộ lỗi" in result.output
