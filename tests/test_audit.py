"""Kiểm tra tay ngẫu nhiên (plan v3 §3.2): chọn mẫu có seed, chỉ tin còn mở, chấm % đúng theo nguồn × trường."""

from datetime import datetime, timedelta

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

import jobmarket.__main__ as cli
from jobmarket import audit
from jobmarket.store.db import init_db, now_iso
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


@pytest.fixture
def conn(tmp_cfg):  # noqa: F811
    c = init_db(tmp_cfg.path("db_file"))
    province = c.execute("SELECT code FROM master_provinces LIMIT 1").fetchone()[0]
    with c:
        c.execute(
            "INSERT INTO labels (id, label_type, source, raw_label, norm_key, status, master_code, first_seen_at) "
            "VALUES (1, 'location', 'careerviet', 'Hà Nội', 'ha noi', 'mapped', ?, 'x')",
            (province,),
        )
        c.execute(
            "INSERT INTO labels (id, label_type, source, raw_label, norm_key, status, first_seen_at) "
            "VALUES (2, 'industry', 'careerviet', 'Ngành lạ', 'nganh la', 'unmapped', 'x')"
        )
    with c:
        c.execute(
            "INSERT INTO runs (id, source, scope, started_at, status, app_version) "
            "VALUES (1, 'careerviet', '{}', 'x', 'success', 't')"
        )
    recent = now_iso()
    old = (datetime.fromisoformat(now_iso()) - timedelta(days=10)).isoformat(timespec="seconds")
    for i in range(12):
        status, seen = ("expired", recent) if i == 10 else ("unknown", old if i == 11 else recent)
        with c:
            c.execute(
                "INSERT INTO job_posts (source, source_job_id, url, title, location_label_id, industry_label_id, "
                "salary_raw, salary_type, salary_from, salary_to, salary_currency, status, dq_status, dq_flags, "
                "dedup_key, first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version) "
                "VALUES ('careerviet', ?, ?, ?, 1, 2, '12 - 14 Triệu', 'range', 12e6, 14e6, 'VND', ?, 'ok', '', ?, "
                "?, ?, 1, 1, 1)",
                (str(i), f"https://x/{i}", f"Tin {i}", status, f"k{i}", seen, seen),
            )
    return c


def test_salary_text():
    base = {"salary_currency": "VND", "salary_from": 12e6, "salary_to": 14e6}
    assert audit.salary_text(base | {"salary_type": "range"}) == "12 triệu – 14 triệu/tháng"
    assert audit.salary_text(base | {"salary_type": "up_to"}) == "đến 14 triệu/tháng"
    assert audit.salary_text({"salary_type": "negotiable", "salary_currency": None, "salary_from": None,
                              "salary_to": None}) == "Thoả thuận"  # fmt: skip


def test_sample_is_reproducible_and_only_open_recent_jobs(conn):
    df1, meta = audit.sample(conn, ["careerviet"], 5, seed=42)
    df2, _ = audit.sample(conn, ["careerviet"], 5, seed=42)
    assert df1.equals(df2) and meta["pool"] == {"careerviet": 10}  # bỏ tin hết hạn + tin không thấy 10 ngày
    assert len(df1) == 5 * len(audit.FIELDS)
    row = df1[df1["Trường"] == "Ngành"].iloc[0]
    assert (row["Giá trị gốc"], row["Sau chuẩn hoá"]) == ("Ngành lạ", audit.UNMAPPED)
    assert "Tin 10" not in set(df1["Tiêu đề"]) and "Tin 11" not in set(df1["Tiêu đề"])


def _mark(path, marks: dict[int, str]) -> None:
    wb = load_workbook(path)
    ws = wb[audit.SHEET]
    for row, mark in marks.items():
        ws[f"H{row}"] = mark
    wb.save(path)


def test_export_and_score(conn, tmp_path):
    path = audit.export(conn, ["careerviet"], 2, 7, tmp_path)
    assert "seed7" in path.name
    ws = load_workbook(path)[audit.SHEET]
    assert ws.data_validations.dataValidation and ws["C2"].hyperlink
    _mark(path, {2: "Đúng", 3: "Sai", 4: "Bỏ qua", 5: "Đúng"})  # dòng 2–7 = 6 trường của tin 1
    r = audit.score(path)
    s = r["sources"]["careerviet"]
    assert (s["correct"], s["n"], r["unmarked"]) == (2, 3, 8)
    assert s["fields"]["Ngành"] == {"correct": 1, "n": 1} and s["fields"]["Tỉnh/thành"] == {"correct": 0, "n": 1}
    md = audit.log_markdown(r)
    assert "66.7%" in md and "❌" in md and "Tỉnh/thành" in md


def test_score_rejects_unknown_mark(conn, tmp_path):
    path = audit.export(conn, ["careerviet"], 1, 1, tmp_path)
    _mark(path, {2: "ok"})
    with pytest.raises(ValueError):
        audit.score(path)


def test_cli_score_writes_log_under_project_root(conn, tmp_cfg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "load_config", lambda: tmp_cfg)
    out = CliRunner().invoke(cli.app, ["audit", "--sample", "1", "--source", "careerviet", "--seed", "3"])
    assert out.exit_code == 0, out.output
    path = next((tmp_cfg.path("data_dir") / "audit").glob("*.xlsx"))
    _mark(path, {r: "Đúng" for r in range(2, 8)})
    out = CliRunner().invoke(cli.app, ["audit", "--score", path.name])
    assert out.exit_code == 0, out.output
    assert "100.0%" in (tmp_cfg.root / "docs" / "audit-log.md").read_text(encoding="utf-8")
