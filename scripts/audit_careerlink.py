"""Đối chiếu DB với thẻ tin CareerLink đã lưu (data/raw) — không request mạng.

Với mỗi tin: phân tích lại thẻ bằng bộ đọc hiện tại rồi so từng trường đã lưu (tiêu đề, công ty, lương gốc, địa
điểm, cấp bậc, ngày đăng) và kết quả chuẩn hoá (loại lương, khoảng lương, tỉnh, cấp bậc chuẩn).
Chạy: .venv/Scripts/python scripts/audit_careerlink.py [db]   → in số lệch theo trường + ví dụ.
"""

import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from jobmarket.config import load_config  # noqa: E402
from jobmarket.normalize.location import split_location  # noqa: E402
from jobmarket.normalize.salary import parse_salary  # noqa: E402
from jobmarket.normalize.text import clean, norm_key  # noqa: E402
from jobmarket.sources import careerlink  # noqa: E402
from jobmarket.store import raw as raw_store  # noqa: E402

cfg = load_config()
conn = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else cfg.path("db_file"))
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT p.*, c.name_raw AS company, lo.raw_label AS loc_raw, lo.status AS loc_status, lo.master_code AS prov,
              lv.raw_label AS lvl_raw, lv.status AS lvl_status, lv.master_code AS lvl_code
       FROM job_posts p
       LEFT JOIN companies c ON c.id = p.company_id
       LEFT JOIN labels lo ON lo.id = p.location_label_id
       LEFT JOIN labels lv ON lv.id = p.level_label_id
       WHERE p.source = 'careerlink'"""
).fetchall()

diffs: dict[str, list] = defaultdict(list)
counts = Counter()
for r in rows:
    try:
        card = raw_store.load(cfg.path("raw_dir"), r["raw_path"])["listing_card_html"]
    except (FileNotFoundError, KeyError, TypeError):
        diffs["raw_missing"].append(r["id"])
        continue
    jobs = careerlink.parse_listing(f"<div>{card}</div>", None)
    if len(jobs) != 1:
        diffs["parse_count"].append((r["id"], len(jobs)))
        continue
    j = jobs[0]
    location = split_location(j.location_raw)
    salary = parse_salary(j.salary_raw, cfg.normalize)
    checks = {
        "source_job_id": (r["source_job_id"], j.source_job_id),
        "url": (r["url"], j.url),
        "title": (r["title"], j.title),
        "company": (norm_key(r["company"]), norm_key(j.company_name)),  # cùng công ty, khác hoa/thường = không lệch
        "salary_raw": (r["salary_raw"], j.salary_raw),
        "salary_type": (r["salary_type"], salary.type),
        "salary_from": (r["salary_from"], salary.from_vnd),
        "salary_to": (r["salary_to"], salary.to_vnd),
        "location_first": (r["loc_raw"], clean(location.first) if location else None),
        "location_count": (r["location_count"], location.count if location else None),
        "level_raw": (r["lvl_raw"], j.level_raw),
        # Tin đã đọc trang chi tiết: ngày đăng = datePosted (ngày đăng gốc), thẻ tin chỉ có ngày CẬP NHẬT → không so
        **({} if r["detail_fetched_at"] else {"posted_date": (r["posted_date"], j.posted_date)}),
    }
    for field, (stored, parsed) in checks.items():
        counts[field] += 1
        if stored != parsed:
            diffs[field].append((r["id"], stored, parsed))
    if r["loc_status"] != "mapped":
        diffs["location_not_mapped"].append((r["id"], r["loc_raw"], r["loc_status"]))
    if j.level_raw and r["lvl_status"] != "mapped":
        diffs["level_not_mapped"].append((r["id"], r["lvl_raw"]))

print(f"{len(rows)} tin CareerLink")
for field in [*counts, "location_not_mapped", "level_not_mapped", "raw_missing", "parse_count"]:
    bad = diffs.get(field, [])
    print(f"  {field:20} lệch {len(bad):5}" + (f"  vd: {bad[:3]}" if bad else ""))
