"""Nạp file CSV tin đã làm sạch (định dạng `job_posts_clean.csv` trong workbook mockup, tab Data_Jobs A–W).

Dùng để: (1) nạp 374 dòng mẫu làm dữ liệu kiểm thử thống kê/dashboard; (2) nạp dữ liệu từ nguồn khác
đã có sẵn dạng bảng. Ngành/nghề/tỉnh/kinh nghiệm/cấp bậc đi qua bảng nhãn như dữ liệu crawl, nên ánh xạ
nhãn và thống kê hoạt động y hệt.
"""

import csv
import json
import sqlite3
from pathlib import Path

from jobmarket import NORMALIZER_VERSION, __version__
from jobmarket.dedup import dedup_key
from jobmarket.pipeline import company_id, label_id
from jobmarket.store.db import now_iso

UNCLASSIFIED = {"(chưa phân loại)", "(chưa xác định)"}


def _value(row: dict, key: str) -> str | None:
    value = (row.get(key) or "").strip()
    return None if not value or value in UNCLASSIFIED else value


def _number(row: dict, key: str) -> float | None:
    value = _value(row, key)
    return float(value) if value is not None else None


def import_clean_csv(conn: sqlite3.Connection, path: Path, run_source: str = "csv_import") -> tuple[int, int]:
    """Trả (run_id, số dòng đã nạp). Dòng trùng (source, source_job_id) đã có thì bỏ qua."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES (?, ?, ?, ?)",
            (run_source, json.dumps({"mode": "clean_csv", "file": path.name}), now_iso(), __version__),
        ).lastrowid
        inserted = 0
        for row in rows:
            source = _value(row, "source") or run_source
            job_id = _value(row, "source_job_id")
            if (
                job_id
                and conn.execute(
                    "SELECT 1 FROM job_posts WHERE source = ? AND source_job_id = ?", (source, job_id)
                ).fetchone()
            ):
                continue
            # Thiếu first_seen_at mà có ngày đăng → coi như thấy ngày đăng. Dùng giờ nạp file thì mọi ngày đăng trong
            # file đều "trước ngày bắt đầu thu thập" → bị coi là tin tồn sẵn, mất hết chỉ số theo thời gian.
            posted = _value(row, "posted_date")
            first_seen = _value(row, "first_seen_at") or (f"{posted[:10]}T00:00:00" if posted else now_iso())
            location = _value(row, "location")
            # Khoá trùng có sẵn trong file (README workbook: "nếu CSV có dedup_key thì dùng giá trị đó")
            key = _value(row, "dedup_key") or dedup_key(row.get("company_name"), row.get("title"), location)
            conn.execute(
                """INSERT INTO job_posts (
                    source, source_job_id, title, company_id, industry_label_id, occupation_label_id,
                    location_label_id, location_count, experience_label_id, level_label_id, skills_raw,
                    salary_raw, salary_type, salary_from, salary_to, salary_tax, posted_date, deadline, status,
                    dq_status, dq_flags, dedup_key, first_seen_at, last_seen_at, first_run_id, last_run_id,
                    normalizer_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source,
                    job_id,
                    _value(row, "title"),
                    company_id(conn, source, _value(row, "company_name")),
                    label_id(conn, "industry", source, row.get("industry")),
                    label_id(conn, "occupation", source, _value(row, "occupation")),
                    label_id(conn, "location", source, location),
                    1 if location else None,
                    label_id(conn, "experience", source, _value(row, "experience")),
                    label_id(conn, "level", source, _value(row, "role")),
                    _value(row, "skills"),
                    _value(row, "salary_raw"),
                    _value(row, "salary_type") or "not_stated",
                    _number(row, "salary_from"),
                    _number(row, "salary_to"),
                    _value(row, "salary_tax") or "unknown",
                    _value(row, "posted_date"),
                    _value(row, "deadline"),
                    _value(row, "status") or "unknown",
                    _value(row, "dq_status") or "ok",
                    _value(row, "dq_flags") or "",
                    key,
                    first_seen,
                    first_seen,
                    run_id,
                    run_id,
                    NORMALIZER_VERSION,
                ),
            )
            inserted += 1
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'success', posts_new = ? WHERE id = ?",
            (now_iso(), inserted, run_id),
        )
    return run_id, inserted
