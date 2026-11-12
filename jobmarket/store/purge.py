"""Xoá toàn bộ dữ liệu của MỘT nguồn (user bỏ nguồn đó — 2026-11-12: bỏ CareerLink). Bảng danh mục/master giữ nguyên;
`reports` (báo cáo tháng đã chốt) giữ nguyên vì là bản chụp tại thời điểm lập."""

import sqlite3
from datetime import datetime
from pathlib import Path

JOBS = "SELECT id FROM job_posts WHERE source = :s"

# Thứ tự xoá theo khoá ngoại: bảng con trước
STEPS: tuple[tuple[str, str], ...] = (
    ("job_industry_labels", f"DELETE FROM job_industry_labels WHERE job_id IN ({JOBS})"),
    ("job_locations", f"DELETE FROM job_locations WHERE job_id IN ({JOBS})"),
    ("job_posts", "DELETE FROM job_posts WHERE source = :s"),
    (
        "companies",
        "DELETE FROM companies WHERE source = :s AND id NOT IN (SELECT company_id FROM job_posts "
        "WHERE company_id IS NOT NULL)",
    ),
    (
        "label_history",
        "DELETE FROM label_history WHERE label_id IN (SELECT id FROM labels WHERE source = :s)",
    ),
    ("labels", "DELETE FROM labels WHERE source = :s"),
    ("crawl_listings", "DELETE FROM crawl_listings WHERE source = :s"),
    ("sitemap_jobs", "DELETE FROM sitemap_jobs WHERE source = :s"),
    ("sitemap_fetches", "DELETE FROM sitemap_fetches WHERE source = :s"),
    ("runs", "DELETE FROM runs WHERE source = :s"),
)


def backup(conn: sqlite3.Connection, folder: Path) -> Path:
    """Sao lưu nhất quán (API backup của SQLite — an toàn khi tiến trình khác đang ghi)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"market-{datetime.now():%Y%m%d-%H%M%S}.db"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def purge_source(conn: sqlite3.Connection, source: str) -> dict[str, int]:
    """→ số dòng đã xoá theo bảng. Một giao dịch: lỗi giữa chừng thì không xoá gì."""
    counts: dict[str, int] = {}
    with conn:
        for table, sql in STEPS:
            counts[table] = conn.execute(sql, {"s": source}).rowcount
    return counts
