"""Một lần chạy: tải trang → phân tích → chuẩn hoá → ghi DB (PLAN §B2)."""

import contextlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from selectolax.lexbor import LexborHTMLParser

from jobmarket import NORMALIZER_VERSION, __version__
from jobmarket.config import Config
from jobmarket.dedup import dedup_key
from jobmarket.fetch import sitemap
from jobmarket.fetch.client import Blocked, Disallowed, PoliteClient
from jobmarket.normalize.experience import experience_code, experience_months
from jobmarket.normalize.level import level_from_title
from jobmarket.normalize.location import province_candidates, split_location
from jobmarket.normalize.salary import parse_salary
from jobmarket.normalize.text import clean, norm_key
from jobmarket.quality import assess
from jobmarket.sources import topcv
from jobmarket.sources.base import ListingPage, RawJob
from jobmarket.store import raw as raw_store
from jobmarket.store.db import now_iso
from jobmarket.timeutil import from_timestamp


@dataclass
class RunStats:
    pages_fetched: int = 0
    posts_new: int = 0
    posts_updated: int = 0
    posts_rejected: int = 0
    industry_filled: int = 0  # tin đã có được ngành nhờ gặp lại ở trang ngành
    errors: list[str] = field(default_factory=list)
    blocked: str | None = None


_MASTER_TABLES = {
    "industry": "master_industries",
    "occupation": "master_occupations",
    "experience": "master_experiences",
    "level": "master_levels",
}


def _match_name(conn: sqlite3.Connection, table: str, raw: str | None) -> str | None:
    """Nhãn trùng ĐÚNG tên chuẩn (sau chuẩn hoá chữ) trong bảng master → mã đó. So khớp chính xác, không phải đoán."""
    key = norm_key(raw)
    for row in conn.execute(f"SELECT code, name FROM {table}"):
        if norm_key(row["name"]) == key:
            return row["code"]
    return None


def _exact_master_match(conn: sqlite3.Connection, label_type: str, raw: str) -> str | None:
    table = _MASTER_TABLES.get(label_type)
    return _match_name(conn, table, raw) if table else None


# Tiến độ crawl (từng trang): CLI `run`/`start` gắn handler in ra màn hình; mặc định im lặng (test, thư viện)
progress = logging.getLogger("jobmarket.progress")

IGNORE = "__ignore__"  # quy tắc nói nhãn này không phải giá trị của chiều (vd ngành "Tiếng Nhật")


def _rule_mapping(conn: sqlite3.Connection, label_type: str, raw: str) -> str | None:
    """Ánh xạ bằng quy tắc cố định khi nhãn mới xuất hiện. Không có quy tắc → None (chưa ánh xạ); IGNORE → bỏ qua."""
    if label_type == "location":
        for candidate in province_candidates(raw):
            row = conn.execute(
                "SELECT province_code FROM province_aliases WHERE alias_norm = ?", (candidate,)
            ).fetchone()
            if row:
                return row["province_code"]
        return None
    if code := _exact_master_match(conn, label_type, raw):
        return code
    if label_type == "industry":
        row = conn.execute(
            "SELECT industry_code FROM industry_aliases WHERE alias_norm = ?", (norm_key(raw),)
        ).fetchone()
        if row:
            return row["industry_code"] or IGNORE
        # Nhãn là một danh mục đã biết (hoặc tên khác của nó) → nhóm của danh mục
        category = category_code(conn, raw)
        if category:
            group = conn.execute("SELECT industry_code FROM master_categories WHERE code = ?", (category,)).fetchone()
            return group["industry_code"] if group else None
        return None
    row = conn.execute(
        "SELECT code FROM label_aliases WHERE label_type = ? AND alias_norm = ?", (label_type, norm_key(raw))
    ).fetchone()
    if row:
        return row["code"] or IGNORE
    if label_type == "experience":
        return experience_code(raw)
    return None


def label_id(conn: sqlite3.Connection, label_type: str, source: str, raw: str | None) -> int | None:
    raw = clean(raw)
    key = norm_key(raw)
    if not key:
        return None
    row = conn.execute(
        "SELECT id FROM labels WHERE label_type = ? AND source = ? AND norm_key = ?",
        (label_type, source, key),
    ).fetchone()
    if row:
        return row["id"]
    code = _rule_mapping(conn, label_type, raw)
    status = "ignored" if code == IGNORE else "mapped" if code else "unmapped"
    master = code if status == "mapped" else None
    cur = conn.execute(
        "INSERT INTO labels (label_type, source, raw_label, norm_key, status, master_code, mapped_via, "
        "first_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (label_type, source, raw, key, status, master, "rule" if code else None, now_iso()),
    )
    if code:
        conn.execute(
            "INSERT INTO label_history (label_id, at, old_value, new_value, reason, via) "
            "VALUES (?, ?, NULL, ?, ?, 'rule')",
            (cur.lastrowid, now_iso(), master or status, "quy tắc cố định khi nhãn xuất hiện lần đầu"),
        )
    if label_type == "industry":
        assign_category(conn, cur.lastrowid)
    return cur.lastrowid


# Nguồn mà danh mục ngành CHÍNH LÀ chuẩn danh mục (phân loại giống CareerLink): danh mục mới gặp → tự thêm
CATEGORY_SOURCE = "careerlink"


def category_code(conn: sqlite3.Connection, raw: str | None) -> str | None:
    """Danh mục (kiểu CareerLink) của một nhãn ngành: trùng tên danh mục, hoặc tên khác trong category_aliases."""
    key = norm_key(raw)
    if not key:
        return None
    row = conn.execute("SELECT category_code FROM category_aliases WHERE alias_norm = ?", (key,)).fetchone()
    return row["category_code"] if row else _match_name(conn, "master_categories", raw)


def assign_category(conn: sqlite3.Connection, label: int) -> bool:
    """Gán danh mục cho nhãn ngành; nhãn của CareerLink chưa có danh mục → thêm danh mục mới đúng tên CareerLink.

    Không gán cho nhãn bỏ qua ("Tiếng Nhật", "Khác") hay nhãn ghép nhiều mục ("A, B"). Danh mục chưa có nhóm mà nhãn
    đã ánh xạ nhóm → danh mục nhận nhóm đó. True nếu đổi gì."""
    row = conn.execute(
        "SELECT source, raw_label, status, master_code, category_code FROM labels "
        "WHERE id = ? AND label_type = 'industry'",
        (label,),
    ).fetchone()
    if not row or row["status"] == "ignored" or "," in (row["raw_label"] or ""):
        return False
    code = category_code(conn, row["raw_label"])
    if code is None and row["source"] == CATEGORY_SOURCE:
        number = conn.execute("SELECT COUNT(*) FROM master_categories").fetchone()[0] + 1
        while conn.execute("SELECT 1 FROM master_categories WHERE code = ?", (f"CAT{number:02d}",)).fetchone():
            number += 1
        code = f"CAT{number:02d}"
        order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM master_categories").fetchone()[0]
        conn.execute(
            "INSERT INTO master_categories (code, name, industry_code, sort_order) VALUES (?, ?, ?, ?)",
            (code, row["raw_label"], row["master_code"] if row["status"] == "mapped" else None, order),
        )
    changed = code != row["category_code"]
    if changed:
        conn.execute("UPDATE labels SET category_code = ? WHERE id = ?", (code, label))
    if code and row["status"] == "mapped":
        conn.execute(
            "UPDATE master_categories SET industry_code = ? WHERE code = ? AND industry_code IS NULL",
            (row["master_code"], code),
        )
    return changed


def assign_categories(conn: sqlite3.Connection) -> int:
    """Gán lại danh mục cho mọi nhãn ngành (sau khi sửa seeds/categories.csv, category_aliases.csv). Không request."""
    with conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM labels WHERE label_type = 'industry' ORDER BY id")]
        return sum(assign_category(conn, i) for i in ids)


def _location_ids(conn: sqlite3.Connection, source: str, location) -> list[int]:
    if not location:
        return []
    return list(dict.fromkeys(i for i in (label_id(conn, "location", source, p) for p in location.parts) if i))


def _store_positions(conn: sqlite3.Connection, table: str, job_id: int, ids: list[int]) -> None:
    """Thay danh sách nhãn có thứ tự của tin (bảng `table`: job_locations / job_industry_labels). Rỗng → giữ nguyên."""
    if not ids:
        return
    conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
    conn.executemany(
        f"INSERT INTO {table} (job_id, position, label_id) VALUES (?, ?, ?)",
        [(job_id, pos, lid) for pos, lid in enumerate(ids)],
    )


def store_locations(conn: sqlite3.Connection, job_id: int, ids: list[int]) -> None:
    """Mọi nơi làm việc của tin (theo thứ tự nguồn) → job_locations; thống kê theo tỉnh đếm tin ở MỖI nơi."""
    _store_positions(conn, "job_locations", job_id, ids)


def set_locations(conn: sqlite3.Connection, job_id: int, source: str, raw: str | None) -> list[int]:
    """Chuỗi địa điểm gốc → job_locations + job_posts.location_raw (một chỗ duy nhất ghi cả hai). Trả nhãn đã ghi."""
    ids = _location_ids(conn, source, split_location(raw))
    store_locations(conn, job_id, ids)
    conn.execute("UPDATE job_posts SET location_raw = ? WHERE id = ?", (clean(raw) or None, job_id))
    return ids


def company_id(conn: sqlite3.Connection, source: str, name: str | None) -> int | None:
    key = norm_key(name)
    if not key:
        return None
    row = conn.execute("SELECT id FROM companies WHERE source = ? AND name_norm = ?", (source, key)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO companies (source, name_raw, name_norm) VALUES (?, ?, ?)", (source, clean(name), key)
    ).lastrowid


def save_job(
    conn: sqlite3.Connection,
    cfg: Config,
    source: str,
    provides: set[str],
    job: RawJob,
    run_id: int,
    stats: RunStats,
    seen_at: str | None = None,
) -> None:
    """`seen_at` = thời điểm thấy tin (mặc định: bây giờ; file lưu tay: thời điểm lưu file)."""
    now = seen_at or now_iso()
    if job.source_job_id and not raw_store.SAFE_ID.match(job.source_job_id):
        stats.posts_rejected += 1
        stats.errors.append(f"Mã tin không hợp lệ (bỏ qua): {job.source_job_id[:40]!r}")
        return
    existing = (
        conn.execute(
            "SELECT id, industry_label_id, last_seen_at, location_raw FROM job_posts "
            "WHERE source = ? AND source_job_id = ?",
            (source, job.source_job_id),
        ).fetchone()
        if job.source_job_id
        else None
    )

    if existing:
        industry = existing["industry_label_id"]
        candidate = label_id(conn, "industry", source, job.industry_label)
        # Nhãn hiện tại đã xác định KHÔNG phải ngành (ignored, vd "Tiếng Nhật") → thay bằng ngành thật khi tin xuất
        # hiện ở trang ngành. Nhãn ngành thật mà chưa ánh xạ thì giữ (ánh xạ sau là có số), không ghi đè tuỳ tiện.
        filled = False
        if candidate and candidate != industry and _is_ignored(conn, industry) and _is_mapped(conn, candidate):
            industry = candidate
            filled = True
        industry = industry or candidate
        # Thêm mục ngành mới cho tin đã có cũng là "có ích" → quét danh mục không dừng sớm ở trang toàn tin đã biết
        if not job.industry_labels and _add_industry_label(conn, existing["id"], candidate):
            filled = True
        stats.industry_filled += filled
        if job.industry_labels:
            ids = _industry_label_ids(conn, source, job.industry_labels)
            _store_industry_labels(conn, existing["id"], ids)
            primary = _primary_industry(conn, ids)
            if primary and _is_mapped(conn, primary) and not _is_mapped(conn, industry):
                industry = primary
        # Nạp file cũ sau file mới vẫn giữ đúng lần thấy đầu/cuối; ngày đăng giữ giá trị đầu tiên biết được.
        # Hạn nộp lấy giá trị MỚI nhất nguồn hiển thị (nhà tuyển dụng gia hạn → không bị coi là đã đóng); file lưu
        # tay cũ hơn lần thấy cuối thì không ghi đè hạn nộp.
        # Tin bị đánh dấu hết hạn (vì không thấy) mà nay xuất hiện lại trên danh sách → còn tuyển, trừ khi hạn nộp
        # đã qua. Chỉ khi lần thấy này MỚI hơn lần thấy cuối (file lưu tay cũ không làm sống lại tin đã đóng).
        fresh = now >= (existing["last_seen_at"] or "")
        # Nơi làm việc theo lần thấy mới nhất; chuỗi không đổi → nhãn không đổi (đa số tin gặp lại), bỏ qua ghi
        if fresh and job.location_raw and clean(job.location_raw) != existing["location_raw"]:
            set_locations(conn, existing["id"], source, job.location_raw)
        conn.execute(
            "UPDATE job_posts SET first_seen_at = MIN(first_seen_at, :now), last_seen_at = MAX(last_seen_at, :now), "
            "last_run_id = :run, industry_label_id = :industry, posted_date = COALESCE(posted_date, :posted), "
            "deadline = COALESCE(:deadline, deadline), "
            "status = CASE WHEN :fresh AND status = 'expired' "
            "AND (COALESCE(:deadline, deadline) IS NULL OR COALESCE(:deadline, deadline) >= :today) "
            "THEN 'unknown' ELSE status END WHERE id = :id",
            {
                "now": now,
                "run": run_id,
                "industry": industry,
                "posted": job.posted_date,
                "deadline": job.deadline if fresh else None,
                "fresh": fresh,
                "today": now[:10],
                "id": existing["id"],
            },
        )
        stats.posts_updated += 1
        return

    salary = parse_salary(job.salary_raw, cfg.normalize)
    location = split_location(job.location_raw)
    location_label = label_id(conn, "location", source, location.first) if location else None
    flags = list(salary.flags)
    if location_label is None or not _is_mapped(conn, location_label):
        flags.append("location_unmapped")
    experience_label = label_id(conn, "experience", source, job.experience_raw)
    if job.experience_raw and not _is_mapped(conn, experience_label):
        flags.append("experience_unparsed")

    dq_status, flags = assess(
        {
            "source_job_id": job.source_job_id,
            "title": job.title,
            "company_name": job.company_name,
            "posted_date": job.posted_date,
        },
        flags,
        provides,
    )
    province = _master_code(conn, location_label)
    industry_ids = _industry_label_ids(conn, source, job.industry_labels)
    industry = _primary_industry(conn, industry_ids) or label_id(conn, "industry", source, job.industry_label)
    raw_path = raw_sha = None
    if job.source_job_id:
        payload = dict(job.raw_payload, pii_removed=job.pii_removed, fetched_at=now)
        raw_path, raw_sha = raw_store.save(cfg.path("raw_dir"), source, job.source_job_id, payload)

    cur = conn.execute(
        """INSERT INTO job_posts (
            source, source_job_id, url, title, company_id, industry_label_id, occupation_label_id,
            location_label_id, location_count, experience_label_id, level_label_id, level_from_title,
            skills_raw, salary_raw, salary_type, salary_from, salary_to, salary_currency, salary_tax,
            posted_date, deadline, status, description_text, raw_path, raw_sha256, dq_status, dq_flags,
            dedup_key, first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version, location_raw,
            requirements_text, experience_raw, experience_min_months, experience_max_months
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?)""",
        (
            source,
            job.source_job_id,
            job.url,
            clean(job.title) or None,
            company_id(conn, source, job.company_name),
            industry,
            label_id(conn, "occupation", source, job.occupation_label),
            location_label,
            location.count if location else None,
            experience_label,
            label_id(conn, "level", source, job.level_raw),
            level_from_title(job.title),
            job.skills_raw,
            job.salary_raw,
            salary.type,
            salary.from_vnd,
            salary.to_vnd,
            salary.currency,
            salary.tax,
            job.posted_date,
            job.deadline,
            "unknown",
            job.description_text,
            raw_path,
            raw_sha,
            dq_status,
            ",".join(flags),
            dedup_key(job.company_name, job.title, province or norm_key(location.first if location else "")),
            now,
            now,
            run_id,
            run_id,
            NORMALIZER_VERSION,
            clean(job.location_raw) or None,
            job.requirements_text,
            clean(job.experience_raw) or None,
            *experience_months(job.experience_raw),
        ),
    )
    store_locations(conn, cur.lastrowid, _location_ids(conn, source, location))
    _store_industry_labels(conn, cur.lastrowid, industry_ids)
    if not industry_ids:  # `industry` khi đó chính là nhãn ngành đơn của tin
        _add_industry_label(conn, cur.lastrowid, industry)
    stats.posts_new += 1
    if dq_status == "reject":
        stats.posts_rejected += 1


def _has_unseen_since(conn: sqlite3.Connection, source: str, jobs: list[RawJob], since: str) -> bool:
    """Trong `jobs` có tin chưa có trong DB hoặc lần thấy cuối trước mốc `since` (đọc TRƯỚC khi lưu trang này)."""
    ids = [j.source_job_id for j in jobs if j.source_job_id]
    if not ids:
        return False
    seen = dict(
        conn.execute(
            f"SELECT source_job_id, last_seen_at FROM job_posts WHERE source = ? AND source_job_id IN "
            f"({','.join('?' * len(ids))})",
            (source, *ids),
        )
    )
    return any(seen.get(i) is None or seen[i] < since for i in ids)


def _industry_label_ids(conn: sqlite3.Connection, source: str, names: list[str]) -> list[int]:
    return [lid for lid in (label_id(conn, "industry", source, n) for n in names or []) if lid]


def _primary_industry(conn: sqlite3.Connection, ids: list[int]) -> int | None:
    """Nhãn ngành ĐẠI DIỆN (để lọc/hiển thị) của tin nhiều mục: mục đầu đã ánh xạ; chưa mục nào ánh xạ → mục đầu
    không bị bỏ qua. Không phải "ngành chính": CareerLink xếp mục theo MÃ danh mục tăng dần (kiểm 8/8 tin, 2026-11-09),
    nguồn không cho biết mục chính → thống kê chia đều tin cho mọi mục (stats.allocate)."""
    return next((i for i in ids if _is_mapped(conn, i)), None) or next(
        (i for i in ids if not _is_ignored(conn, i)), None
    )


def _add_industry_label(conn: sqlite3.Connection, job_id: int, label: int | None) -> bool:
    """Ghi thêm 1 mục ngành cho tin (tin gặp ở trang danh mục đó) nếu chưa có; bỏ qua nhãn không phải ngành.

    Trang danh mục là bằng chứng tin THUỘC danh mục → duyệt nhiều danh mục tích luỹ đủ mục ngành của tin mà không cần
    trang chi tiết (1 request ra ngành cho ~50 tin). True nếu vừa thêm."""
    if label is None or _is_ignored(conn, label):
        return False
    rows = conn.execute("SELECT position, label_id FROM job_industry_labels WHERE job_id = ?", (job_id,)).fetchall()
    if any(r["label_id"] == label for r in rows):
        return False
    position = max((r["position"] for r in rows), default=-1) + 1
    conn.execute(
        "INSERT INTO job_industry_labels (job_id, position, label_id) VALUES (?, ?, ?)", (job_id, position, label)
    )
    return True


def backfill_listing_industries(conn: sqlite3.Connection, cfg: Config, source: str) -> dict[str, int]:
    """Mục ngành từ trang danh mục lưu trong thẻ tin gốc (`listing_industry`) → job_industry_labels. Không request."""
    raw_dir = cfg.path("raw_dir")
    added = 0
    rows = conn.execute("SELECT id, raw_path, industry_label_id FROM job_posts WHERE source = ?", (source,)).fetchall()
    with conn:
        for row in rows:
            # Nhãn ngành đang gắn cũng là bằng chứng: thẻ tin lưu ở lần gặp ĐẦU (thường danh sách chung, không ngành),
            # ngành đến từ lần gặp sau ở trang danh mục.
            added += _add_industry_label(conn, row["id"], row["industry_label_id"])
            if not row["raw_path"]:
                continue
            try:
                name = raw_store.load(raw_dir, row["raw_path"]).get("listing_industry")
            except (OSError, ValueError):
                continue
            if name:
                added += _add_industry_label(conn, row["id"], label_id(conn, "industry", source, name))
    return {"memberships_added": added}


def _vieclam24h_province_names(conn: sqlite3.Connection, items: dict[int, dict]) -> dict:
    """Bản gốc Việc Làm 24h giữ MÃ tỉnh (`province_ids`), tên nằm ở bảng tra của trang (không lưu) → học mã → tên từ
    các tin chỉ có 1 tỉnh (tên = nhãn địa điểm đã lưu của tin đó). `items`: mã tin → `item` của bản gốc."""
    names: dict = {}
    for job_id, label in conn.execute(
        "SELECT p.id, l.raw_label FROM job_posts p JOIN labels l ON l.id = p.location_label_id "
        "WHERE p.source = 'vieclam24h' AND p.location_count = 1"
    ):
        ids = (items.get(job_id) or {}).get("province_ids") or []
        if len(ids) == 1:
            names.setdefault(ids[0], label)
    return names


def backfill_locations(conn: sqlite3.Connection, cfg: Config) -> dict[str, int]:
    """Mọi nơi làm việc (job_locations) + chuỗi gốc (location_raw) cho tin đã có, từ bản gốc đã lưu. Không request.

    CareerLink/CareerViet/TopCV: đọc lại thẻ tin; Việc Làm 24h: mã tỉnh trong `item`; VietJobs: dòng của file dataset
    (data/datasets/vietjobs/VietJobs.csv). Không đọc được → giữ nơi đầu tiên đã lưu."""
    from jobmarket.sources import careerlink, careerviet, itviec, topcv, vietjobs

    raw_dir = cfg.path("raw_dir")
    cards = {"careerlink": careerlink, "careerviet": careerviet, "itviec": itviec, "topcv": topcv}
    dataset = cfg.path("data_dir") / "datasets" / "vietjobs" / "VietJobs.csv"
    vj = vietjobs.locations_by_id(dataset) if dataset.exists() else {}
    rows = conn.execute("SELECT id, source, source_job_id, raw_path, location_label_id FROM job_posts").fetchall()
    v24_items: dict[int, dict] = {}
    for row in rows:
        if row["source"] == "vieclam24h" and row["raw_path"]:
            with contextlib.suppress(OSError, ValueError):
                v24_items[row["id"]] = raw_store.load(raw_dir, row["raw_path"]).get("item") or {}
    v24 = _vieclam24h_province_names(conn, v24_items)
    counts = {"jobs": 0, "multi": 0, "from_raw": 0}
    with conn:
        for row in rows:
            raw = None
            try:
                if row["source"] in cards and row["raw_path"]:
                    html = raw_store.load(raw_dir, row["raw_path"]).get("listing_card_html")
                    parsed = cards[row["source"]].parse_listing(html, None) if html else []
                    raw = parsed[0].location_raw if parsed else None
                elif row["id"] in v24_items:
                    province_ids = v24_items[row["id"]].get("province_ids") or []
                    raw = ", ".join(n for n in (v24.get(p) for p in province_ids) if n) or None
                elif row["source"] == "vietjobs":
                    raw = vj.get(row["source_job_id"])
            except (OSError, ValueError):
                raw = None
            ids = set_locations(conn, row["id"], row["source"], raw) if raw else []
            counts["from_raw"] += bool(raw)
            if not ids and row["location_label_id"]:
                ids = [row["location_label_id"]]
                store_locations(conn, row["id"], ids)
            counts["jobs"] += bool(ids)
            counts["multi"] += len(ids) > 1
    return counts


def _store_industry_labels(conn: sqlite3.Connection, job_id: int, ids: list[int]) -> None:
    _store_positions(conn, "job_industry_labels", job_id, ids)


def backfill_industry_labels(conn: sqlite3.Connection, cfg: Config, source: str) -> dict[str, int]:
    """Nạp danh sách mục ngành từ bản gốc đã lưu (`industries`) cho tin cũ + chọn lại ngành chính. Không request."""
    raw_dir = cfg.path("raw_dir")
    done = changed = 0
    rows = conn.execute(
        "SELECT id, industry_label_id, raw_path FROM job_posts WHERE source = ? AND raw_path IS NOT NULL", (source,)
    ).fetchall()
    with conn:
        for row in rows:
            try:
                names = raw_store.load(raw_dir, row["raw_path"]).get("industries") or []
            except (OSError, ValueError):
                continue
            ids = _industry_label_ids(conn, source, names)
            if not ids:
                continue
            _store_industry_labels(conn, row["id"], ids)
            done += 1
            primary = _primary_industry(conn, ids)
            if primary and primary != row["industry_label_id"] and _is_mapped(conn, primary):
                conn.execute("UPDATE job_posts SET industry_label_id = ? WHERE id = ?", (primary, row["id"]))
                changed += 1
    return {"jobs_with_labels": done, "primary_changed": changed}


def _is_mapped(conn: sqlite3.Connection, label: int | None) -> bool:
    return _master_code(conn, label) is not None


def choose_industry(conn: sqlite3.Connection, source: str, names: list[str | None], current: int | None) -> int | None:
    """Nhãn ngành đại diện cho tin từ danh sách mục ngành của trang chi tiết (CareerLink: xếp theo mã danh mục, không
    có mục chính — xem `_primary_industry`; thống kê dùng đủ các mục).

    Lấy mục đầu không bị bỏ qua (vd bỏ "Khác"). Mục đó đã ánh xạ → dùng. Chưa ánh xạ mà tin đã có ngành
    chuẩn (từ trang danh mục) → giữ ngành đó, không thay bằng nhãn chưa ánh xạ; tin chưa có ngành → nhận mục chính
    (vào hàng chờ duyệt, ánh xạ xong là có số).
    """
    labels_ = [label for label in (label_id(conn, "industry", source, n) for n in names) if label]
    primary = next((label for label in labels_ if not _is_ignored(conn, label)), None)
    if primary is None:
        return current
    if _is_mapped(conn, primary) or not _is_mapped(conn, current):
        return primary
    return current


def _is_ignored(conn: sqlite3.Connection, label: int | None) -> bool:
    if label is None:
        return False
    row = conn.execute("SELECT status FROM labels WHERE id = ?", (label,)).fetchone()
    return bool(row) and row["status"] == "ignored"


def _master_code(conn: sqlite3.Connection, label: int | None) -> str | None:
    if label is None:
        return None
    row = conn.execute("SELECT master_code FROM labels WHERE id = ? AND status = 'mapped'", (label,)).fetchone()
    return row["master_code"] if row else None


def run_topcv(conn: sqlite3.Connection, cfg: Config, client: PoliteClient, max_pages: int | None = None) -> int:
    source_cfg = cfg.source("topcv")
    start_url = source_cfg["start_url"]
    stats = RunStats()
    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES (?, ?, ?, ?)",
            (
                "topcv",
                json.dumps({"mode": "page1_per_category", "start_url": start_url}),
                now_iso(),
                __version__,
            ),
        ).lastrowid

    pages_done: list[str] = []
    try:
        start_html = client.get(start_url)
        stats.pages_fetched += 1
        categories = topcv.category_pages(start_html)
        if max_pages is not None:
            categories = categories[: max(0, max_pages - 1)]
        for page in categories:
            try:
                html = client.get(page.url)
            except (Blocked, Disallowed):
                raise
            except Exception as exc:  # lỗi mạng/HTTP lẻ: ghi lại, sang trang khác
                stats.errors.append(f"{page.url}: {exc}")
                if len(stats.errors) / (stats.pages_fetched + 1) > float(cfg.crawl["stop_error_rate"]):
                    break
                continue
            stats.pages_fetched += 1
            with conn:
                for job in topcv.parse_listing(html, page.industry_label):
                    save_job(conn, cfg, "topcv", topcv.PROVIDES, job, run_id, stats)
            pages_done.append(page.url)
        # Trang "mới nhất" xử lý sau cùng để tin đã có ngành từ trang ngành giữ ngành đó
        with conn:
            for job in topcv.parse_listing(start_html, None):
                save_job(conn, cfg, "topcv", topcv.PROVIDES, job, run_id, stats)
        pages_done.append(start_url)
    except (Blocked, Disallowed) as exc:
        stats.blocked = f"{type(exc).__name__}: {exc}"
        progress.warning("Dừng nguồn (không vượt chặn): %s", stats.blocked)
    except Exception as exc:  # mất mạng, lỗi HTTP ở trang đầu…: ghi nhận, không để run kẹt ở 'running'
        stats.errors.append(f"{type(exc).__name__}: {exc}")

    status = "failed" if not pages_done else "partial" if (stats.blocked or stats.errors) else "success"
    with conn:
        conn.execute(
            """UPDATE runs SET finished_at = ?, status = ?, scope = ?, pages_fetched = ?, posts_new = ?,
               posts_updated = ?, posts_rejected = ?, errors_count = ?, error_sample = ? WHERE id = ?""",
            (
                now_iso(),
                status,
                json.dumps({"mode": "page1_per_category", "start_url": start_url, "pages": pages_done}),
                stats.pages_fetched,
                stats.posts_new,
                stats.posts_updated,
                stats.posts_rejected,
                len(stats.errors) + (1 if stats.blocked else 0),
                json.dumps(([stats.blocked] if stats.blocked else []) + stats.errors[:5], ensure_ascii=False),
                run_id,
            ),
        )
    return run_id


def run_paged(
    conn: sqlite3.Connection,
    cfg: Config,
    client: PoliteClient,
    adapter,
    max_pages: int | None = None,
) -> int:
    """Nguồn có phân trang hợp lệ (CareerLink, JobsGO): duyệt từng danh sách, trang 1 → N.

    Dừng một danh sách khi: hết tin, đủ `max_pages`, hoặc `stop_after_known_pages` trang liên tiếp không có tin
    mới (các đêm sau chỉ tốn vài request). Gặp chặn/robots cấm → dừng cả nguồn, không thử vượt.
    """
    source_cfg = cfg.source(adapter.NAME)
    start_url = source_cfg["start_url"]
    limit = int(max_pages if max_pages is not None else source_cfg.get("max_pages", 5))
    stop_after_known = int(source_cfg.get("stop_after_known_pages", 2))
    stats = RunStats()
    scope = {"mode": "paged", "start_url": start_url, "max_pages": limit}
    run_started = now_iso()
    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES (?, ?, ?, ?)",
            (adapter.NAME, json.dumps(scope), now_iso(), __version__),
        ).lastrowid

    pages_done: list[str] = []
    max_listings = int(source_cfg.get("max_listings", 200))
    max_requests = int(source_cfg.get("max_requests", 10_000))
    try:
        start_html = client.get(start_url)
        stats.pages_fetched += 1
        last_crawled = dict(
            conn.execute("SELECT url, last_crawled_at FROM crawl_listings WHERE source = ?", (adapter.NAME,))
        )
        key = getattr(adapter, "listing_key", lambda url: url)
        queue, queued = [], set()
        for item in adapter.listings(start_url, start_html):  # giữ thứ tự adapter: danh sách chung đầu tiên
            if key(item.url) not in queued:
                queued.add(key(item.url))
                queue.append(item)
        # Danh mục đã biết từ các lần trước (kể cả phát hiện trên trang chi tiết) — trang chủ chỉ liên kết vài danh mục
        known = [
            ListingPage(url, label)
            for url, label in conn.execute(
                "SELECT url, label FROM crawl_listings WHERE source = ? AND label IS NOT NULL ORDER BY url",
                (adapter.NAME,),
            )
        ]
        # `category_first`: danh sách chung cũng vào vòng xoay (duyệt lâu nhất trước) → ngân sách request dồn cho trang
        # danh mục chưa quét (mỗi trang ra ngành cho ~50 tin); mặc định danh sách chung luôn đi đầu (phủ tin mới nhất).
        category_first = bool(source_cfg.get("category_first"))
        # Trang theo tỉnh không gắn được ngành, tin của nó đã có trong danh sách chung → bỏ khi ưu tiên danh mục.
        keep = (lambda item: item.industry_label or item.url == start_url) if category_first else (lambda item: True)
        queue = [item for item in queue if keep(item)]
        _enqueue(queue, queued, 0 if category_first else 1, known, max_listings, last_crawled, key)
        resume = dict(
            conn.execute(
                "SELECT url, resume_page FROM crawl_listings WHERE source = ? AND resume_page IS NOT NULL",
                (adapter.NAME,),
            )
        )
        sweep_started = dict(
            conn.execute(
                "SELECT url, sweep_started_at FROM crawl_listings WHERE source = ? AND sweep_started_at IS NOT NULL",
                (adapter.NAME,),
            )
        )
        deep_pages = int(source_cfg.get("deep_pages_per_run", 0))
        overlap = int(source_cfg.get("deep_overlap_pages", 0))
        index = 0
        empty_first_pages = 0
        last_page: dict[str, int] = {}  # số trang cuối thấy trên thanh phân trang của từng danh sách

        def visit(listing, page: int, sweep_since: str | None = None) -> str:
            """Đọc 1 trang danh sách → 'useful' | 'known' | 'empty' | 'error'.

            `sweep_since` (nguồn quét sâu): trang còn tin CHƯA thấy từ lúc bắt đầu vòng quét hiện tại cũng là 'useful'.
            """
            nonlocal empty_first_pages
            url = adapter.page_url(listing.url, page)
            if url == start_url and page == 1:
                html = start_html
            else:
                if client.requests_made >= max_requests:
                    stats.errors.append(f"Đạt giới hạn {max_requests} request/lần chạy — dừng (tiếp tục lần sau)")
                    raise _Done
                try:
                    html = client.get(url)
                except (Blocked, Disallowed):
                    raise
                except Exception as exc:  # lỗi lẻ 1 trang: ghi lại, sang danh sách khác
                    stats.errors.append(f"{url}: {type(exc).__name__}: {exc}")
                    return "error"
                stats.pages_fetched += 1
            if hasattr(adapter, "discover"):
                found = [item for item in adapter.discover(html) if keep(item)]
                _enqueue(queue, queued, index, found, max_listings, last_crawled, key)
            if hasattr(adapter, "last_page") and (n := adapter.last_page(html, listing.url)):
                last_page[listing.url] = max(last_page.get(listing.url, 0), n)
            jobs = adapter.parse_listing(html, listing.industry_label)
            if not jobs:
                # Trang đầu danh sách chung trống, nhiều danh sách liên tiếp trống ngay trang 1, hoặc trang giữa
                # chừng (chưa tới trang cuối trên thanh phân trang) trống = nguồn đang hạn chế ngầm (HTTP 200 nhưng
                # không có tin) → dừng, không cố tiếp.
                if page == 1:
                    empty_first_pages += 1
                    if url == start_url or empty_first_pages >= 3:
                        raise Blocked(f"trang danh sách không có tin ({url}) — nghi nguồn đang hạn chế truy cập")
                elif page <= last_page.get(listing.url, 0):
                    raise Blocked(f"trang {page}/{last_page[listing.url]} không có tin ({url}) — nghi bị hạn chế")
                return "empty"
            empty_first_pages = 0
            before = (stats.posts_new, stats.industry_filled)
            new0, updated0 = stats.posts_new, stats.posts_updated
            unseen = sweep_since is not None and _has_unseen_since(conn, adapter.NAME, jobs, sweep_since)
            with conn:
                for job in jobs:
                    save_job(conn, cfg, adapter.NAME, adapter.PROVIDES, job, run_id, stats)
                if page == 1:
                    conn.execute(
                        "INSERT INTO crawl_listings (source, url, label, last_crawled_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT (source, url) DO UPDATE SET last_crawled_at = excluded.last_crawled_at",
                        (adapter.NAME, listing.url, listing.industry_label, now_iso()),
                    )
            pages_done.append(url)
            progress.info(
                "%s · %s · trang %d: %d tin (%d mới, %d cập nhật) · tổng lần này %d mới · %d request",
                adapter.NAME,
                listing.industry_label or ("danh sách chung" if listing.url == start_url else "theo tỉnh/nghề"),
                page,
                len(jobs),
                stats.posts_new - new0,
                stats.posts_updated - updated0,
                stats.posts_new,
                client.requests_made,
            )
            # Trang "có ích" = có tin mới, gắn được ngành cho tin đã có, hoặc có tin chưa thấy trong vòng quét này
            return "useful" if unseen or (stats.posts_new, stats.industry_filled) != before else "known"

        def save_resume(listing, page: int | None) -> None:
            with conn:
                conn.execute(
                    "UPDATE crawl_listings SET resume_page = ? WHERE source = ? AND url = ?",
                    (page, adapter.NAME, listing.url),
                )

        while index < len(queue):
            listing = queue[index]
            index += 1
            # 1) Đầu danh sách (tin mới nhất): dừng khi hết tin, đủ `max_pages`, hoặc vài trang liền không có ích
            known_streak, read_until, ended = 0, 0, False
            # Nguồn quét sâu: chỉ dừng phần đầu khi gặp vài trang liền mà MỌI tin đã thấy trong vòng quét hiện tại.
            # Dừng theo "không có tin mới" thì tin cũ vừa được làm mới (nhảy từ dưới con trỏ quét sâu lên đầu danh
            # sách) không được đọc lại trong vòng này → bị đánh hết hạn nhầm cuối vòng (review 2026-11-05, M4).
            # Vòng mới (chưa có mốc) → mốc = lúc bắt đầu lần chạy: phần đầu danh sách đọc tới `max_pages`.
            sweep_since = sweep_started.get(listing.url, run_started) if deep_pages > 0 else None
            for page in range(1, limit + 1):
                result = visit(listing, page, sweep_since)
                if result in ("empty", "error"):
                    ended = result == "empty"
                    break
                read_until = page
                known_streak = 0 if result == "useful" else known_streak + 1
                if known_streak >= stop_after_known:
                    break
            # 2) Quét sâu: đi tiếp từ trang dừng lần trước (`resume_page`), mỗi lần chạy thêm `deep_pages_per_run`
            # trang → sau nhiều lần chạy phủ toàn bộ danh sách, không chỉ vài trang đầu. Hết danh sách → quay vòng.
            # Lùi `deep_overlap_pages` trang: tin phía trên hết hạn làm các tin dịch lên trang đã đọc → không sót.
            if not ended and read_until and deep_pages > 0:
                previous = resume.get(listing.url)
                if previous is None:  # bắt đầu vòng quét mới (phần đầu danh sách vừa đọc ở lần chạy này)
                    with conn:
                        conn.execute(
                            "UPDATE crawl_listings SET sweep_started_at = ? WHERE source = ? AND url = ?",
                            (run_started, adapter.NAME, listing.url),
                        )
                deep = max((previous or 0) - overlap, read_until + 1)
                try:
                    for _ in range(deep_pages):
                        # Xong một vòng quét trọn CHỈ khi đã đọc qua trang cuối mà thanh phân trang báo. Trang trống
                        # khi không biết trang cuối có thể là nguồn hạn chế ngầm (HTTP 200, 0 tin) → không chốt vòng,
                        # vì chốt vòng sẽ đánh hết hạn mọi tin ở các trang chưa đọc lại (sự cố mô phỏng 2026-11-04).
                        known_last = last_page.get(listing.url)
                        if known_last and deep > known_last:
                            deep = None
                            with conn:
                                conn.execute(
                                    "UPDATE crawl_listings SET last_full_sweep_started_at = "
                                    "COALESCE(sweep_started_at, last_full_sweep_started_at), sweep_started_at = NULL "
                                    "WHERE source = ? AND url = ?",
                                    (adapter.NAME, listing.url),
                                )
                            break
                        result = visit(listing, deep)
                        if result == "error":
                            break
                        if result == "empty":
                            stats.errors.append(
                                f"{listing.url}: trang {deep} trống, không biết trang cuối — không chốt vòng quét"
                            )
                            break
                        deep += 1
                finally:  # hết ngân sách request / bị chặn giữa chừng vẫn nhớ chỗ dừng
                    save_resume(listing, deep)
            if len(stats.errors) > 3 and len(stats.errors) / max(stats.pages_fetched, 1) > float(
                cfg.crawl["stop_error_rate"]
            ):
                break
    except _Done:
        pass
    except (Blocked, Disallowed) as exc:
        stats.blocked = f"{type(exc).__name__}: {exc}"
        progress.warning("Dừng nguồn (không vượt chặn): %s", stats.blocked)
    except Exception as exc:
        stats.errors.append(f"{type(exc).__name__}: {exc}")

    # Tầng 2: bổ sung trang chi tiết (chỉ khi tầng danh sách không bị chặn), ngân sách riêng mỗi lần chạy
    detail_limit = int(source_cfg.get("detail_max_per_run", 0))
    if hasattr(adapter, "parse_detail") and detail_limit > 0 and not stats.blocked:
        try:
            scope["detail"] = enrich_details(conn, cfg, client, adapter, detail_limit, stats)
        except (Blocked, Disallowed) as exc:
            stats.blocked = f"{type(exc).__name__} (trang chi tiết): {exc}"
            progress.warning("Dừng đọc trang chi tiết (không vượt chặn): %s", stats.blocked)
        except Exception as exc:
            stats.errors.append(f"trang chi tiết: {type(exc).__name__}: {exc}")

    # Tầng 3: tin chỉ thấy qua sitemap (nguồn khoá phân trang) — đọc trang chi tiết tin chưa có, ngân sách riêng
    sitemap_limit = int(source_cfg.get("sitemap_max_per_run", 0))
    sitemap_on = source_cfg.get("sitemap_url") and sitemap_limit > 0 and hasattr(adapter, "parse_detail_job")
    if sitemap_on and not stats.blocked:
        try:
            scope["sitemap"] = discover_from_sitemap(conn, cfg, client, adapter, run_id, stats, sitemap_limit)
        except (Blocked, Disallowed) as exc:
            stats.blocked = f"{type(exc).__name__} (sitemap): {exc}"
            progress.warning("Dừng phần sitemap (không vượt chặn): %s", stats.blocked)
        except Exception as exc:
            stats.errors.append(f"sitemap: {type(exc).__name__}: {exc}")

    status = "failed" if not pages_done else "partial" if (stats.blocked or stats.errors) else "success"
    if status != "failed" and source_cfg.get("expire_unseen", True):
        # Nguồn có hạn nộp từng tin (expire_unseen = false) → còn tuyển/hết hạn theo hạn nộp, không theo "không thấy"
        if int(source_cfg.get("deep_pages_per_run", 0)) > 0:
            # Quét sâu: một vòng mất nhiều ngày → chỉ coi hết hạn khi cả một vòng quét trọn không thấy lại tin
            row = conn.execute(
                "SELECT last_full_sweep_started_at FROM crawl_listings WHERE source = ? AND url = ?",
                (adapter.NAME, start_url),
            ).fetchone()
            if row and row[0]:
                mark_expired(conn, adapter.NAME, before=row[0])
        else:
            mark_expired(conn, adapter.NAME, int(cfg.crawl.get("expire_unseen_days", 14)))
    if status == "failed" and not (stats.blocked or stats.errors):
        stats.errors.append("Không trang nào có tin — kiểm tra cấu trúc trang hoặc nguồn đang hạn chế")
    with conn:
        conn.execute(
            """UPDATE runs SET finished_at = ?, status = ?, scope = ?, pages_fetched = ?, posts_new = ?,
               posts_updated = ?, posts_rejected = ?, errors_count = ?, error_sample = ? WHERE id = ?""",
            (
                now_iso(),
                status,
                json.dumps({**scope, "pages": len(pages_done)}),
                stats.pages_fetched,
                stats.posts_new,
                stats.posts_updated,
                stats.posts_rejected,
                len(stats.errors) + (1 if stats.blocked else 0),
                json.dumps(([stats.blocked] if stats.blocked else []) + stats.errors[:5], ensure_ascii=False),
                run_id,
            ),
        )
    return run_id


def apply_detail(conn: sqlite3.Connection, cfg: Config, source: str, row, detail: dict, stale: bool = False) -> None:
    """Ghi dữ liệu trang chi tiết (JSON-LD JobPosting) vào tin `row` (cần id, dq_flags, industry_label_id).

    Dùng chung cho trang chi tiết crawl tự động (CareerLink) và trang chi tiết bạn tự lưu (TopCV, import-html):
    ngành (mục chính), kinh nghiệm, ngày đăng gốc, hạn nộp → trạng thái, loại hình, mô tả.
    `stale`: trang lưu tay CŨ hơn lần thấy cuối → không ghi đè hạn nộp/trạng thái mới hơn (chỉ điền khi còn trống).
    """
    flags = [f for f in (row["dq_flags"] or "").split(",") if f and f != "experience_unparsed"]
    experience_label = label_id(conn, "experience", source, detail["experience"])
    if detail["experience"] and not _is_mapped(conn, experience_label):
        flags.append("experience_unparsed")
    deadline = detail["deadline"]
    status = "expired" if deadline and deadline < now_iso()[:10] else "active"
    experience_raw = clean(detail["experience"]) or None
    exp_min, exp_max = experience_months(experience_raw)
    raw_store.save(
        cfg.path("raw_dir"), source, f"{row['id']}-detail",
        {"jsonld": detail["jsonld"], "pii_removed": detail["pii_removed"], "fetched_at": now_iso()},
    )  # fmt: skip
    with conn:
        conn.execute(
            """UPDATE job_posts SET
                   industry_label_id = COALESCE(?, industry_label_id),
                   experience_label_id = COALESCE(?, experience_label_id),
                   posted_date = COALESCE(?, posted_date),
                   deadline = CASE WHEN ? THEN COALESCE(deadline, ?) ELSE COALESCE(?, deadline) END,
                   status = CASE WHEN ? THEN status ELSE ? END,
                   employment_type = ?, description_text = COALESCE(?, description_text),
                   requirements_text = COALESCE(?, requirements_text),
                   experience_raw = COALESCE(?, experience_raw),
                   experience_min_months = CASE WHEN ? IS NULL THEN experience_min_months ELSE ? END,
                   experience_max_months = CASE WHEN ? IS NULL THEN experience_max_months ELSE ? END,
                   dq_flags = ?,
                   dq_status = CASE WHEN dq_status = 'reject' THEN 'reject' WHEN ? = '' THEN 'ok' ELSE 'warn' END,
                   detail_fetched_at = ?
               WHERE id = ?""",
            (
                choose_industry(
                    conn, source, detail.get("industries") or [detail["industry"]], row["industry_label_id"]
                ),
                experience_label,
                detail["posted_date"],
                stale,
                deadline,
                deadline,
                stale,
                status,
                detail["employment_type"],
                detail["description_text"],
                detail.get("requirements_text"),
                experience_raw,
                experience_raw,
                exp_min,
                experience_raw,
                exp_max,
                ",".join(flags),
                ",".join(flags),
                now_iso(),
                row["id"],
            ),
        )
        _store_industry_labels(conn, row["id"], _industry_label_ids(conn, source, detail.get("industries") or []))


# Lỗi tạm thời (timeout, 5xx) ở trang chi tiết: bỏ qua tin đó (lần sau đọc lại), đi tiếp. N trang LIỀN lỗi = máy chủ
# đang trục trặc → dừng tầng này (lần chạy sau thử lại). Không phải tín hiệu chặn → không kích hoạt thời gian nghỉ.
MAX_FAILURE_STREAK = 5


class _FailureStreak:
    def __init__(self, stats: RunStats, what: str):
        self.stats, self.what, self.count = stats, what, 0

    def fail(self, url: str, exc: Exception) -> None:
        self.count += 1
        self.stats.errors.append(f"{url}: {type(exc).__name__} (bỏ qua, lần sau đọc lại)")
        if self.count >= MAX_FAILURE_STREAK:
            raise RuntimeError(f"{self.what}: {self.count} trang liền lỗi mạng/máy chủ — dừng, lần chạy sau thử lại")

    def ok(self) -> None:
        self.count = 0


def enrich_details(
    conn: sqlite3.Connection,
    cfg: Config,
    client: PoliteClient,
    adapter,
    limit: int,
    stats: RunStats | None = None,
) -> dict[str, int]:
    """Tầng 2: đọc trang chi tiết (JSON-LD) của tối đa `limit` tin chưa bổ sung, mới nhất trước.

    Bổ sung ngành (ghi đè nhãn ngành suy từ trang danh mục — trang chi tiết là nguồn chính xác cho từng tin),
    kinh nghiệm, ngày đăng, hạn nộp, loại hình, mô tả; trạng thái active/expired theo hạn nộp.
    404/410 → 'removed'. Bị chặn/robots cấm → ném lỗi để bộ chạy dừng nguồn (không thử vượt).
    """
    counts = {"enriched": 0, "removed": 0, "no_jsonld": 0}
    pending = conn.execute(
        "SELECT id, url, dq_flags, industry_label_id FROM job_posts "
        "WHERE source = ? AND detail_fetched_at IS NULL AND url IS NOT NULL "
        "ORDER BY first_seen_at DESC, id DESC LIMIT ?",
        (adapter.NAME, int(limit)),
    ).fetchall()
    # Trang không có JSON-LD: có thể thật sự không có, cũng có thể là nguồn đang hạn chế ngầm (HTTP 200, trang rỗng).
    # Chỉ đánh dấu "đã đọc" khi một trang sau đó đọc được (nguồn vẫn bình thường); 3 trang liền không có → dừng.
    no_jsonld_ids: list[int] = []
    failures = _FailureStreak(stats or RunStats(), f"{adapter.NAME} trang chi tiết")
    for row in pending:
        try:
            html = client.get(row["url"])
        except (Blocked, Disallowed):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                with conn:
                    conn.execute(
                        "UPDATE job_posts SET status = 'removed', detail_fetched_at = ? WHERE id = ?",
                        (now_iso(), row["id"]),
                    )
                counts["removed"] += 1
                continue
            failures.fail(row["url"], exc)
            continue
        except httpx.HTTPError as exc:  # timeout / mất kết nối sau khi đã thử lại → bỏ qua tin này, lần sau đọc lại
            failures.fail(row["url"], exc)
            continue
        failures.ok()
        if hasattr(adapter, "discover"):  # trang chi tiết liên kết các danh mục của tin → nhớ để lần sau duyệt
            with conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO crawl_listings (source, url, label, last_crawled_at) VALUES (?, ?, ?, '')",
                    [(adapter.NAME, p.url, p.industry_label) for p in adapter.discover(html) if p.industry_label],
                )
        detail = adapter.parse_detail(html)
        if detail is None:
            no_jsonld_ids.append(row["id"])
            if len(no_jsonld_ids) >= 3:
                raise Blocked("3 trang chi tiết liền không có dữ liệu tin — nghi nguồn đang hạn chế truy cập")
            continue
        if no_jsonld_ids:
            counts["no_jsonld"] += len(no_jsonld_ids)
            with conn:
                conn.executemany(
                    "UPDATE job_posts SET detail_fetched_at = ? WHERE id = ?", [(now_iso(), i) for i in no_jsonld_ids]
                )
            no_jsonld_ids.clear()
        apply_detail(conn, cfg, adapter.NAME, row, detail)
        counts["enriched"] += 1
        progress.info("%s · trang chi tiết %d/%d đã đọc", adapter.NAME, counts["enriched"], len(pending))
    return counts


def refresh_sitemap(
    conn: sqlite3.Connection, client: PoliteClient, adapter, root: str, max_files: int
) -> dict[str, int]:
    """Tải sitemap index `root` → sitemap tin (adapter.follow_sitemap) → ghi mọi URL tin vào `sitemap_jobs`."""
    queue, seen, found = [root], set(), {}
    while queue and len(seen) < max_files:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        is_index, locs = sitemap.parse(client.get(url))
        if is_index:
            queue.extend(u for u in locs if adapter.follow_sitemap(u))
            continue
        for loc in locs:
            if (job_id := adapter.job_id_from_url(loc)) and raw_store.SAFE_ID.match(job_id):
                found[job_id] = loc
    now = now_iso()
    with conn:
        conn.executemany(
            "INSERT INTO sitemap_jobs (source, source_job_id, url, first_listed_at, last_listed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (source, source_job_id) DO UPDATE SET "
            "url = excluded.url, last_listed_at = excluded.last_listed_at",
            [(adapter.NAME, job_id, url, now, now) for job_id, url in found.items()],
        )
        conn.execute(
            "INSERT INTO sitemap_fetches (source, fetched_at, files, urls) VALUES (?, ?, ?, ?) ON CONFLICT (source) "
            "DO UPDATE SET fetched_at = excluded.fetched_at, files = excluded.files, urls = excluded.urls",
            (adapter.NAME, now, len(seen), len(found)),
        )
    progress.info("%s · sitemap: %d file, %d URL tin", adapter.NAME, len(seen), len(found))
    return {"files": len(seen), "urls": len(found)}


def discover_from_sitemap(
    conn: sqlite3.Connection, cfg: Config, client: PoliteClient, adapter, run_id: int, stats: RunStats, limit: int
) -> dict[str, int]:
    """Tầng 3: tin có trong sitemap mà chưa thấy ở trang danh sách → đọc trang chi tiết (tối đa `limit`, mã tin lớn
    = mới hơn trước) và lưu như tin thấy ở danh sách. Sitemap chỉ tải lại sau `sitemap_refresh_hours`.

    404/410 → 'gone' (không đọc lại). Trang không có dữ liệu tin: 3 trang liền → coi là bị hạn chế ngầm, dừng.
    """
    source_cfg = cfg.source(adapter.NAME)
    counts = {"saved": 0, "gone": 0, "no_data": 0}
    last = conn.execute("SELECT fetched_at FROM sitemap_fetches WHERE source = ?", (adapter.NAME,)).fetchone()
    refresh_hours = float(source_cfg.get("sitemap_refresh_hours", 24))
    if not last or datetime.fromisoformat(now_iso()) - datetime.fromisoformat(last[0]) >= timedelta(
        hours=refresh_hours
    ):
        max_files = int(source_cfg.get("sitemap_max_files", 10))
        counts.update(refresh_sitemap(conn, client, adapter, source_cfg["sitemap_url"], max_files))
    pending = conn.execute(
        "SELECT s.source_job_id, s.url FROM sitemap_jobs s WHERE s.source = ? AND s.checked_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM job_posts j WHERE j.source = s.source AND j.source_job_id = s.source_job_id) "
        "ORDER BY length(s.source_job_id) DESC, s.source_job_id DESC LIMIT ?",
        (adapter.NAME, int(limit)),
    ).fetchall()

    def mark(job_id: str, outcome: str) -> None:
        conn.execute(
            "UPDATE sitemap_jobs SET checked_at = ?, outcome = ? WHERE source = ? AND source_job_id = ?",
            (now_iso(), outcome, adapter.NAME, job_id),
        )

    no_data: list[str] = []
    failures = _FailureStreak(stats, f"{adapter.NAME} sitemap")
    for job_id, url in pending:
        try:
            html = client.get(url)
        except (Blocked, Disallowed):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (404, 410):
                failures.fail(url, exc)
                continue
            with conn:
                mark(job_id, "gone")
            counts["gone"] += 1
            continue
        except httpx.HTTPError as exc:  # timeout / mất kết nối sau khi đã thử lại → bỏ qua tin này, lần sau đọc lại
            failures.fail(url, exc)
            continue
        failures.ok()
        job = adapter.parse_detail_job(html, url)
        if job is None:
            no_data.append(job_id)
            if len(no_data) >= 3:
                raise Blocked("3 trang chi tiết (sitemap) liền không có dữ liệu tin — nghi nguồn đang hạn chế truy cập")
            continue
        with conn:
            for earlier in no_data:  # nguồn vẫn trả dữ liệu bình thường → các trang trước thật sự không có tin
                mark(earlier, "no_data")
            counts["no_data"] += len(no_data)
            no_data.clear()
            save_job(conn, cfg, adapter.NAME, adapter.PROVIDES, job, run_id, stats)
            mark(job_id, "saved")
        # Cùng trang đó là trang chi tiết → ghi luôn mô tả/yêu cầu/kinh nghiệm, đánh dấu đã đọc (tầng 2 không đọc lại)
        detail = adapter.parse_detail(html) if hasattr(adapter, "parse_detail") else None
        row = conn.execute(
            "SELECT id, dq_flags, industry_label_id FROM job_posts WHERE source = ? AND source_job_id = ?",
            (adapter.NAME, job.source_job_id),
        ).fetchone()
        if detail and row:
            apply_detail(conn, cfg, adapter.NAME, row, detail)
        counts["saved"] += 1
        progress.info("%s · sitemap: trang chi tiết %d/%d đã đọc", adapter.NAME, counts["saved"], len(pending))
    counts["pending_left"] = conn.execute(
        "SELECT COUNT(*) FROM sitemap_jobs s WHERE s.source = ? AND s.checked_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM job_posts j WHERE j.source = s.source AND j.source_job_id = s.source_job_id)",
        (adapter.NAME,),
    ).fetchone()[0]
    return counts


def backfill_experience_requirements(conn: sqlite3.Connection) -> dict[str, int]:
    """Tin đã có (trước migration 011): chữ kinh nghiệm + khoảng tháng từ nhãn kinh nghiệm đã lưu; tách yêu cầu khỏi mô
    tả CareerLink đã đọc. Không request mạng, chỉ điền ô đang trống."""
    from jobmarket.sources.careerlink import split_requirements

    counts = {"experience": 0, "requirements": 0}
    rows = conn.execute(
        "SELECT j.id, l.raw_label FROM job_posts j JOIN labels l ON l.id = j.experience_label_id "
        "WHERE j.experience_raw IS NULL"
    ).fetchall()
    with conn:
        for job_id, raw in rows:
            low, high = experience_months(raw)
            conn.execute(
                "UPDATE job_posts SET experience_raw = ?, experience_min_months = ?, experience_max_months = ? "
                "WHERE id = ?",
                (clean(raw) or None, low, high, job_id),
            )
            counts["experience"] += 1
        for job_id, text in conn.execute(
            "SELECT id, description_text FROM job_posts WHERE source = 'careerlink' "
            "AND description_text IS NOT NULL AND requirements_text IS NULL"
        ).fetchall():
            description, requirements = split_requirements(text)
            if requirements:
                conn.execute(
                    "UPDATE job_posts SET description_text = ?, requirements_text = ? WHERE id = ?",
                    (description, requirements, job_id),
                )
                counts["requirements"] += 1
    return counts


def mark_expired(conn: sqlite3.Connection, source: str, days: int | None = None, before: str | None = None) -> int:
    """Tin 'unknown' hoặc 'active' không còn thấy trên nguồn → 'expired'. Trả số tin đã đổi.

    'active' (đặt khi đọc trang chi tiết) cũng hết hạn khi không còn trên danh sách: tin gỡ sớm trước hạn nộp, hoặc
    không có hạn nộp, không được tính "đang tuyển" mãi (review 2026-11-04, H3).

    `days`: không thấy quá N ngày. `before`: lần cuối thấy trước mốc này (= lúc bắt đầu vòng quét trọn gần nhất).
    Chỉ gọi sau lần chạy có dữ liệu (không đánh dấu hàng loạt khi nguồn bị chặn/hạn chế).
    """
    if before is None:
        # strftime giữ đúng dạng lưu 'YYYY-MM-DDTHH:MM:SS' (datetime() trả dấu cách → so chuỗi sai)
        before = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%S', ?, ?)", (now_iso(), f"-{int(days)} days")
        ).fetchone()[0]
    with conn:
        cur = conn.execute(
            "UPDATE job_posts SET status = 'expired' WHERE source = ? AND status IN ('unknown', 'active') "
            "AND last_seen_at < ?",
            (source, before),
        )
    return cur.rowcount


class _Done(Exception):
    """Dừng sớm có chủ đích (đạt giới hạn request)."""


SALARY_FLAGS = {"salary_invalid", "salary_ambiguous"}


def renormalize_salaries(conn: sqlite3.Connection, cfg: Config, source: str) -> dict[str, int]:
    """Chuẩn hoá lại lương từ `salary_raw` đã lưu (sau khi đổi quy tắc/ngưỡng lương) — không request mạng.

    Thay cờ lương cũ bằng cờ mới, giữ các cờ khác; tin reject giữ nguyên reject.
    """
    counts = {"checked": 0, "changed": 0}
    rows = conn.execute(
        "SELECT id, salary_raw, salary_type, salary_from, salary_to, dq_status, dq_flags FROM job_posts "
        "WHERE source = ?",
        (source,),
    ).fetchall()
    updates = []
    for row in rows:
        counts["checked"] += 1
        salary = parse_salary(row["salary_raw"], cfg.normalize)
        kept = [f for f in (row["dq_flags"] or "").split(",") if f and f not in SALARY_FLAGS]
        flags = ",".join(dict.fromkeys(kept + salary.flags))
        status = "reject" if row["dq_status"] == "reject" else ("warn" if flags else "ok")
        new = (salary.type, salary.from_vnd, salary.to_vnd, status, flags)
        if new != (row["salary_type"], row["salary_from"], row["salary_to"], row["dq_status"], row["dq_flags"] or ""):
            updates.append((*new, salary.tax, row["id"]))
    with conn:
        conn.executemany(
            "UPDATE job_posts SET salary_type = ?, salary_from = ?, salary_to = ?, dq_status = ?, dq_flags = ?, "
            "salary_tax = ? WHERE id = ?",
            updates,
        )
    counts["changed"] = len(updates)
    return counts


def backfill_posted_dates(conn: sqlite3.Connection, cfg: Config, adapter) -> dict[str, int]:
    """Điền ngày đăng còn trống từ thẻ tin đã lưu trong data/raw — không request mạng.

    Dùng khi bộ đọc thẻ tin được bổ sung trường mới sau khi đã crawl (vd ngày đăng CareerLink, 2026-11-03).
    """
    counts = {"filled": 0, "no_date": 0, "missing_raw": 0}
    rows = conn.execute(
        "SELECT id, raw_path FROM job_posts WHERE source = ? AND posted_date IS NULL AND raw_path IS NOT NULL",
        (adapter.NAME,),
    ).fetchall()
    raw_dir = cfg.path("raw_dir")
    updates = []
    for row in rows:
        try:
            card = raw_store.load(raw_dir, row["raw_path"]).get("listing_card_html")
        except FileNotFoundError:
            counts["missing_raw"] += 1
            continue
        posted = adapter.card_date(LexborHTMLParser(card)) if card else None
        if posted:
            updates.append((posted, row["id"]))
        else:
            counts["no_date"] += 1
    with conn:
        conn.executemany("UPDATE job_posts SET posted_date = ? WHERE id = ?", updates)
    counts["filled"] = len(updates)
    return counts


def reapply_detail_industries(conn: sqlite3.Connection, cfg: Config, adapter) -> dict[str, int]:
    """Tính lại ngành của tin đã đọc trang chi tiết từ JSON-LD đã lưu (data/raw/<nguồn>/<tháng>/<id>-detail) — không
    request mạng. Dùng sau khi đổi cách chọn ngành (vd tách danh sách nhiều mục ngành, 2026-11-04)."""
    counts = {"checked": 0, "changed": 0, "missing_raw": 0}
    raw_dir = cfg.path("raw_dir")
    rows = conn.execute(
        "SELECT id, raw_path, industry_label_id FROM job_posts WHERE source = ? AND detail_fetched_at IS NOT NULL",
        (adapter.NAME,),
    ).fetchall()
    for row in rows:
        found = sorted(raw_dir.glob(f"{adapter.NAME}/*/{row['id']}-detail.json.gz"))
        if not found:
            counts["missing_raw"] += 1
            continue
        counts["checked"] += 1
        jsonld = raw_store.load(raw_dir, found[-1].relative_to(raw_dir).as_posix()).get("jsonld") or {}
        names = [clean(p) for p in str(jsonld.get("industry") or "").split(",") if clean(p)]
        # Ngành gốc từ trang danh mục (lưu cùng thẻ tin) — trước khi bị nhãn trang chi tiết ghi đè
        listing = None
        if row["raw_path"]:
            with contextlib.suppress(FileNotFoundError):
                listing = raw_store.load(raw_dir, row["raw_path"]).get("listing_industry")
        current = label_id(conn, "industry", adapter.NAME, listing) if listing else None
        chosen = choose_industry(conn, adapter.NAME, names, current) or row["industry_label_id"]
        if chosen != row["industry_label_id"]:
            with conn:
                conn.execute("UPDATE job_posts SET industry_label_id = ? WHERE id = ?", (chosen, row["id"]))
            counts["changed"] += 1
    return counts


def _rotate(queue: list, start: int, last_crawled: dict[str, str]) -> None:
    """Sắp các danh sách còn chờ: chưa duyệt bao giờ → duyệt lâu nhất; cùng mức thì trang ngành trước trang tỉnh."""
    queue[start:] = sorted(
        queue[start:], key=lambda item: (last_crawled.get(item.url, ""), 0 if item.industry_label else 1)
    )


def _enqueue(
    queue: list,
    queued: set[str],
    index: int,
    found: list,
    max_listings: int,
    last_crawled: dict[str, str],
    key=lambda url: url,
) -> None:
    """Thêm danh sách mới phát hiện rồi sắp lại phần còn chờ theo vòng xoay.

    `key`: hai URL cùng khoá là một danh sách (CareerLink đổi slug, giữ mã: `ke-toan/1` = `ke-toan-kiem-toan/1`)."""
    for item in found:
        if key(item.url) in queued or len(queue) >= max_listings:
            continue
        queued.add(key(item.url))
        queue.append(item)
    _rotate(queue, index, last_crawled)


def import_saved_pages(conn: sqlite3.Connection, cfg: Config, files: list[Path]) -> int:
    """Nạp trang danh sách TopCV do NGƯỜI DÙNG tự lưu bằng trình duyệt (Ctrl+S) — không có request mạng.

    Thời điểm thấy tin = thời điểm file được lưu (mtime). File đã nạp được chuyển vào thư mục `done/`.
    """
    stats = RunStats()
    with conn:
        run_id = conn.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES (?, ?, ?, ?)",
            ("topcv", json.dumps({"mode": "manual_saved_pages"}), now_iso(), __version__),
        ).lastrowid
    done: list[str] = []
    for path in sorted((p for p in files if p.exists()), key=lambda p: p.stat().st_mtime):
        try:
            if _import_one_saved_page(conn, cfg, path, run_id, stats):
                done.append(path.name)
        except (OSError, ValueError, sqlite3.Error) as exc:  # 1 file lỗi (đang bị mở/di chuyển…) không làm hỏng lần nạp
            stats.errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    _finish_import_run(conn, run_id, stats, done)
    return run_id


MAX_SAVED_PAGE_BYTES = 30_000_000


def _import_one_saved_page(conn: sqlite3.Connection, cfg: Config, path: Path, run_id: int, stats: RunStats) -> bool:
    if path.stat().st_size > MAX_SAVED_PAGE_BYTES:
        stats.errors.append(f"{path.name}: file quá lớn (> {MAX_SAVED_PAGE_BYTES // 1_000_000} MB), bỏ qua")
        return False
    html = path.read_text(encoding="utf-8", errors="replace")
    seen_at = from_timestamp(path.stat().st_mtime)
    if topcv.is_listing_page(html):
        with conn:
            for job in topcv.parse_listing(html, topcv.page_industry(html)):
                save_job(conn, cfg, "topcv", topcv.PROVIDES, job, run_id, stats, seen_at=seen_at)
    elif parsed := topcv.parse_detail_page(html):
        # Trang chi tiết: tạo tin nếu chưa có (cùng mã tin với thẻ trên trang danh sách), rồi ghi trường chi tiết
        job, detail = parsed
        if not job.source_job_id:
            stats.errors.append(f"{path.name}: trang chi tiết không xác định được mã tin")
            return False
        before = conn.execute(
            "SELECT last_seen_at FROM job_posts WHERE source = 'topcv' AND source_job_id = ?", (job.source_job_id,)
        ).fetchone()
        with conn:
            save_job(conn, cfg, "topcv", topcv.PROVIDES_DETAIL, job, run_id, stats, seen_at=seen_at)
        row = conn.execute(
            "SELECT id, dq_flags, industry_label_id FROM job_posts WHERE source = 'topcv' AND source_job_id = ?",
            (job.source_job_id,),
        ).fetchone()
        if row is None:  # tin bị loại khi lưu (vd mã tin không hợp lệ) — đã ghi lỗi
            return False
        apply_detail(conn, cfg, "topcv", row, detail, stale=bool(before and seen_at < before[0]))
    else:
        stats.errors.append(f"{path.name}: không phải trang danh sách hay trang chi tiết tin TopCV")
        return False
    stats.pages_fetched += 1
    target = path.parent / "done" / path.name
    if target.exists():  # đã có file cùng tên (lưu lại cùng trang) → không ghi đè bản trước
        target = target.with_name(f"{path.stem}_{int(path.stat().st_mtime)}{path.suffix}")
    target.parent.mkdir(exist_ok=True)
    path.replace(target)
    return True


def _finish_import_run(conn: sqlite3.Connection, run_id: int, stats: RunStats, done: list[str]) -> None:
    status = "failed" if not done else "partial" if stats.errors else "success"
    with conn:
        conn.execute(
            """UPDATE runs SET finished_at = ?, status = ?, scope = ?, pages_fetched = ?, posts_new = ?,
               posts_updated = ?, posts_rejected = ?, errors_count = ?, error_sample = ? WHERE id = ?""",
            (
                now_iso(),
                status,
                json.dumps({"mode": "manual_saved_pages", "files": done}, ensure_ascii=False),
                stats.pages_fetched,
                stats.posts_new,
                stats.posts_updated,
                stats.posts_rejected,
                len(stats.errors),
                json.dumps(stats.errors[:5], ensure_ascii=False),
                run_id,
            ),
        )


def raw_dir_exists(cfg: Config) -> Path:
    path = cfg.path("raw_dir")
    path.mkdir(parents=True, exist_ok=True)
    return path
