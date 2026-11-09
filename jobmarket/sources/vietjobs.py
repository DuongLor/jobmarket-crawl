"""Nạp bộ dữ liệu lịch sử VietJobs (48.092 tin, thu thập 07–10/2025) — nguồn `vietjobs`.

Nguồn: https://huggingface.co/datasets/dinhieufam/VietJobs (repo tác giả github.com/VinNLP/VietJobs, giấy phép MIT;
dataset card không ghi giấy phép riêng). Paper: VietJobs, LREC 2026.

Bộ dữ liệu KHÔNG có tên công ty, mã tin, ngày đăng:
- mã tin = băm (tiêu đề | địa điểm | mô tả) → nạp lại không trùng;
- khoá trùng thay "công ty" bằng dấu vân tay mô tả (cùng tiêu đề + tỉnh nhưng mô tả khác ≠ trùng);
- thời điểm thấy tin = 31/10/2025 (cuối đợt thu thập), trạng thái 'expired' (tin lịch sử);
- lương "0.0 triệu" = không ghi lương.
"""

import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from jobmarket import NORMALIZER_VERSION, __version__
from jobmarket.config import Config
from jobmarket.dedup import dedup_key
from jobmarket.normalize.level import level_from_title
from jobmarket.normalize.location import split_location
from jobmarket.normalize.salary import parse_salary
from jobmarket.normalize.text import clean, norm_key
from jobmarket.pii import scrub
from jobmarket.pipeline import _is_mapped, _location_ids, label_id, store_locations
from jobmarket.quality import assess
from jobmarket.store.db import now_iso

NAME = "vietjobs"
PROVIDES = {"title", "salary", "location", "experience", "industry"}
SEEN_AT = "2025-10-31T00:00:00"
DATASET_URL = "https://huggingface.co/datasets/dinhieufam/VietJobs/resolve/main/VietJobs.csv"


def _salary_text(row: dict) -> str | None:
    raw = clean(row.get("salary"))
    try:
        if float(row.get("salary_min") or 0) == 0 and float(row.get("salary_max") or 0) == 0:
            return None
    except ValueError:
        pass
    return raw or None


def _category(raw: str | None) -> str | None:
    """ "kinh_doanh_bán_hàng_chăm_sóc_khách_hàng" → "kinh doanh bán hàng chăm sóc khách hàng"."""
    return clean((raw or "").replace("_", " ")) or None


def _hash_id(title: str | None, location: str | None, fingerprint: str) -> str:
    return hashlib.md5(f"{norm_key(title)}|{norm_key(location)}|{fingerprint}".encode()).hexdigest()


def row_id(row: dict) -> str:
    """Mã tin = băm (tiêu đề | địa điểm | dấu vân tay mô tả) — ổn định giữa các lần nạp."""
    description, _ = scrub(row.get("description") or "")
    return _hash_id(clean(row.get("job_title")) or None, row.get("location"), norm_key(description)[:300])


def locations_by_id(path: Path) -> dict[str, str]:
    """mã tin → chuỗi địa điểm gốc của file dataset (nạp lại mọi nơi làm việc, không đọc mạng)."""
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open(encoding="utf-8", newline="") as f:
        return {row_id(row): row["location"] for row in csv.DictReader(f) if row.get("location")}


def import_vietjobs(conn: sqlite3.Connection, cfg: Config, path: Path) -> tuple[int, int]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    stats = {"new": 0, "rejected": 0, "skipped": 0}
    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES (?, ?, ?, ?)",
            (
                NAME,
                json.dumps({"mode": "dataset_import", "file": path.name, "url": DATASET_URL}),
                now_iso(),
                __version__,
            ),
        ).lastrowid
    with path.open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            title = clean(row.get("job_title")) or None
            description, _ = scrub(row.get("description") or "")
            requirements, _ = scrub(row.get("requirements_text") or "")
            fingerprint = norm_key(description)[:300]
            job_id = _hash_id(title, row.get("location"), fingerprint)  # = row_id(row), không scrub lại
            if conn.execute(
                "SELECT 1 FROM job_posts WHERE source = ? AND source_job_id = ?", (NAME, job_id)
            ).fetchone():
                stats["skipped"] += 1  # dòng trùng hệt trong file, hoặc đã nạp lần trước
                continue
            salary = parse_salary(_salary_text(row), cfg.normalize)
            location = split_location(row.get("location"))
            location_label = label_id(conn, "location", NAME, location.first) if location else None
            experience_label = label_id(conn, "experience", NAME, row.get("experience_required"))
            flags = list(salary.flags)
            if location_label is None or not _is_mapped(conn, location_label):
                flags.append("location_unmapped")
            if row.get("experience_required") and not _is_mapped(conn, experience_label):
                flags.append("experience_unparsed")
            dq_status, flags = assess({"source_job_id": job_id, "title": title}, flags, PROVIDES | {"source_job_id"})
            province_row = conn.execute(
                "SELECT master_code FROM labels WHERE id = ? AND status = 'mapped'", (location_label,)
            ).fetchone()
            province = province_row[0] if province_row else norm_key(location.first if location else "")
            new_id = conn.execute(
                """INSERT INTO job_posts (
                    source, source_job_id, title, industry_label_id, location_label_id, location_count,
                    experience_label_id, level_from_title, skills_raw, salary_raw, salary_type, salary_from, salary_to,
                    salary_currency, salary_tax, status, description_text, dq_status, dq_flags, dedup_key,
                    first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version, location_raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'expired', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    NAME, job_id, title,
                    label_id(conn, "industry", NAME, _category(row.get("category"))),
                    location_label, location.count if location else None, experience_label, level_from_title(title),
                    clean(row.get("technical_skills")) or None, _salary_text(row),
                    salary.type, salary.from_vnd, salary.to_vnd, salary.currency, salary.tax,
                    "\n\n".join(p for p in (description, requirements) if p) or None,
                    dq_status, ",".join(flags), dedup_key(fingerprint, title, province),
                    SEEN_AT, SEEN_AT, run_id, run_id, NORMALIZER_VERSION, clean(row.get("location")) or None,
                ),
            ).lastrowid  # fmt: skip
            store_locations(conn, new_id, _location_ids(conn, NAME, location))
            stats["new"] += 1
            stats["rejected"] += dq_status == "reject"
            if i % 5000 == 0:
                conn.commit()
    with conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'success', posts_new = ?, posts_rejected = ?, error_sample = ? "
            "WHERE id = ?",
            (now_iso(), stats["new"], stats["rejected"], json.dumps({"skipped_duplicates": stats["skipped"]}), run_id),
        )
    return run_id, stats["new"]
