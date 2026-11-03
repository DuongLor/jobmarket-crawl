"""Một lần chạy: tải trang → phân tích → chuẩn hoá → ghi DB (PLAN §B2)."""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from selectolax.lexbor import LexborHTMLParser

from jobmarket import NORMALIZER_VERSION, __version__
from jobmarket.config import Config
from jobmarket.dedup import dedup_key
from jobmarket.fetch.client import Blocked, Disallowed, PoliteClient
from jobmarket.normalize.experience import experience_code
from jobmarket.normalize.level import level_from_title
from jobmarket.normalize.location import split_location
from jobmarket.normalize.salary import parse_salary
from jobmarket.normalize.text import clean, norm_key
from jobmarket.quality import assess
from jobmarket.sources import topcv
from jobmarket.sources.base import RawJob
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


def _exact_master_match(conn: sqlite3.Connection, label_type: str, raw: str) -> str | None:
    """Nhãn trùng ĐÚNG tên chuẩn (sau chuẩn hoá chữ) → mã đó. So khớp chính xác, không phải đoán."""
    table = _MASTER_TABLES.get(label_type)
    if not table:
        return None
    key = norm_key(raw)
    for row in conn.execute(f"SELECT code, name FROM {table}"):
        if norm_key(row["name"]) == key:
            return row["code"]
    return None


IGNORE = "__ignore__"  # quy tắc nói nhãn này không phải giá trị của chiều (vd ngành "Tiếng Nhật")


def _rule_mapping(conn: sqlite3.Connection, label_type: str, raw: str) -> str | None:
    """Ánh xạ bằng quy tắc cố định khi nhãn mới xuất hiện. Không có quy tắc → None (chưa ánh xạ); IGNORE → bỏ qua."""
    if label_type == "location":
        key = norm_key(raw)
        # "tp vinh" / "thành phố nha trang" / "huyện củ chi" → thử thêm bản bỏ tiền tố hành chính
        stripped = re.sub(r"^(tp|thanh pho|tx|thi xa|huyen|tinh)\s+", "", key)
        for candidate in dict.fromkeys((key, stripped)):
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
        return None
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
    return cur.lastrowid


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
    existing = (
        conn.execute(
            "SELECT id, industry_label_id FROM job_posts WHERE source = ? AND source_job_id = ?",
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
        if candidate and candidate != industry and _is_ignored(conn, industry) and _is_mapped(conn, candidate):
            industry = candidate
            stats.industry_filled += 1
        industry = industry or candidate
        # Nạp file cũ sau file mới vẫn giữ đúng lần thấy đầu/cuối; ngày đăng giữ giá trị đầu tiên biết được
        conn.execute(
            "UPDATE job_posts SET first_seen_at = MIN(first_seen_at, ?), last_seen_at = MAX(last_seen_at, ?), "
            "last_run_id = ?, industry_label_id = ?, posted_date = COALESCE(posted_date, ?) WHERE id = ?",
            (now, now, run_id, industry, job.posted_date, existing["id"]),
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
    raw_path = raw_sha = None
    if job.source_job_id:
        payload = dict(job.raw_payload, pii_removed=job.pii_removed, fetched_at=now)
        raw_path, raw_sha = raw_store.save(cfg.path("raw_dir"), source, job.source_job_id, payload)

    conn.execute(
        """INSERT INTO job_posts (
            source, source_job_id, url, title, company_id, industry_label_id, occupation_label_id,
            location_label_id, location_count, experience_label_id, level_label_id, level_from_title,
            skills_raw, salary_raw, salary_type, salary_from, salary_to, salary_currency, salary_tax,
            posted_date, deadline, status, description_text, raw_path, raw_sha256, dq_status, dq_flags,
            dedup_key, first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?)""",
        (
            source,
            job.source_job_id,
            job.url,
            clean(job.title) or None,
            company_id(conn, source, job.company_name),
            label_id(conn, "industry", source, job.industry_label),
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
        ),
    )
    stats.posts_new += 1
    if dq_status == "reject":
        stats.posts_rejected += 1


def _is_mapped(conn: sqlite3.Connection, label: int | None) -> bool:
    return _master_code(conn, label) is not None


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
        queue = adapter.listings(start_url, start_html)
        queued = {item.url for item in queue}
        _rotate(queue, 1, last_crawled)
        index = 0
        empty_first_pages = 0
        while index < len(queue):
            listing = queue[index]
            index += 1
            known_streak = 0
            for page in range(1, limit + 1):
                url = adapter.page_url(listing.url, page)
                if url == start_url and page == 1:
                    html = start_html
                else:
                    if client.requests_made >= max_requests:
                        stats.errors.append(f"Đạt giới hạn {max_requests} request/lần chạy — dừng (tiếp tục đêm sau)")
                        raise _Done
                    try:
                        html = client.get(url)
                    except (Blocked, Disallowed):
                        raise
                    except Exception as exc:  # lỗi lẻ 1 trang: ghi lại, sang danh sách khác
                        stats.errors.append(f"{url}: {type(exc).__name__}: {exc}")
                        break
                    stats.pages_fetched += 1
                if hasattr(adapter, "discover"):
                    _enqueue(queue, queued, index, adapter.discover(html), max_listings, last_crawled)
                jobs = adapter.parse_listing(html, listing.industry_label)
                if not jobs:
                    # Trang đầu danh sách chung trống, hoặc nhiều danh sách liên tiếp trống ngay trang 1 = nguồn
                    # đang hạn chế ngầm (HTTP 200 nhưng không có tin) → dừng, không cố tiếp.
                    if page == 1:
                        empty_first_pages += 1
                        if url == start_url or empty_first_pages >= 3:
                            raise Blocked(f"trang danh sách không có tin ({url}) — nghi nguồn đang hạn chế truy cập")
                    break
                empty_first_pages = 0
                before = (stats.posts_new, stats.industry_filled)
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
                # Trang "có ích" = có tin mới HOẶC gắn được ngành cho tin đã có; vài trang liền không → danh sách khác
                useful = (stats.posts_new, stats.industry_filled) != before
                known_streak = 0 if useful else known_streak + 1
                if known_streak >= stop_after_known:
                    break
            if len(stats.errors) > 3 and len(stats.errors) / max(stats.pages_fetched, 1) > float(
                cfg.crawl["stop_error_rate"]
            ):
                break
    except _Done:
        pass
    except (Blocked, Disallowed) as exc:
        stats.blocked = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        stats.errors.append(f"{type(exc).__name__}: {exc}")

    # Tầng 2: bổ sung trang chi tiết (chỉ khi tầng danh sách không bị chặn), ngân sách riêng mỗi lần chạy
    detail_limit = int(source_cfg.get("detail_max_per_run", 0))
    if hasattr(adapter, "parse_detail") and detail_limit > 0 and not stats.blocked:
        try:
            scope["detail"] = enrich_details(conn, cfg, client, adapter, detail_limit)
        except (Blocked, Disallowed) as exc:
            stats.blocked = f"{type(exc).__name__} (trang chi tiết): {exc}"
        except Exception as exc:
            stats.errors.append(f"trang chi tiết: {type(exc).__name__}: {exc}")

    status = "failed" if not pages_done else "partial" if (stats.blocked or stats.errors) else "success"
    if status != "failed":
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


def enrich_details(conn: sqlite3.Connection, cfg: Config, client: PoliteClient, adapter, limit: int) -> dict[str, int]:
    """Tầng 2: đọc trang chi tiết (JSON-LD) của tối đa `limit` tin chưa bổ sung, mới nhất trước.

    Bổ sung ngành (ghi đè nhãn ngành suy từ trang danh mục — trang chi tiết là nguồn chính xác cho từng tin),
    kinh nghiệm, ngày đăng, hạn nộp, loại hình, mô tả; trạng thái active/expired theo hạn nộp.
    404/410 → 'removed'. Bị chặn/robots cấm → ném lỗi để bộ chạy dừng nguồn (không thử vượt).
    """
    counts = {"enriched": 0, "removed": 0, "no_jsonld": 0}
    pending = conn.execute(
        "SELECT id, url, dq_flags FROM job_posts WHERE source = ? AND detail_fetched_at IS NULL AND url IS NOT NULL "
        "ORDER BY first_seen_at DESC, id DESC LIMIT ?",
        (adapter.NAME, int(limit)),
    ).fetchall()
    today = now_iso()[:10]
    # Trang không có JSON-LD: có thể thật sự không có, cũng có thể là nguồn đang hạn chế ngầm (HTTP 200, trang rỗng).
    # Chỉ đánh dấu "đã đọc" khi một trang sau đó đọc được (nguồn vẫn bình thường); 3 trang liền không có → dừng.
    no_jsonld_ids: list[int] = []
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
            raise
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
        flags = [f for f in (row["dq_flags"] or "").split(",") if f and f != "experience_unparsed"]
        experience_label = label_id(conn, "experience", adapter.NAME, detail["experience"])
        if detail["experience"] and not _is_mapped(conn, experience_label):
            flags.append("experience_unparsed")
        deadline = detail["deadline"]
        status = "expired" if deadline and deadline < today else "active"
        raw_path, raw_sha = raw_store.save(
            cfg.path("raw_dir"), adapter.NAME, f"{row['id']}-detail",
            {"jsonld": detail["jsonld"], "pii_removed": detail["pii_removed"], "fetched_at": now_iso()},
        )  # fmt: skip
        with conn:
            conn.execute(
                """UPDATE job_posts SET
                       industry_label_id = COALESCE(?, industry_label_id),
                       experience_label_id = COALESCE(?, experience_label_id),
                       posted_date = COALESCE(?, posted_date), deadline = COALESCE(?, deadline), status = ?,
                       employment_type = ?, description_text = COALESCE(?, description_text), dq_flags = ?,
                       dq_status = CASE WHEN dq_status = 'reject' THEN 'reject' WHEN ? = '' THEN 'ok' ELSE 'warn' END,
                       detail_fetched_at = ?
                   WHERE id = ?""",
                (
                    label_id(conn, "industry", adapter.NAME, detail["industry"]),
                    experience_label,
                    detail["posted_date"],
                    deadline,
                    status,
                    detail["employment_type"],
                    detail["description_text"],
                    ",".join(flags),
                    ",".join(flags),
                    now_iso(),
                    row["id"],
                ),
            )
        counts["enriched"] += 1
    return counts


def mark_expired(conn: sqlite3.Connection, source: str, days: int) -> int:
    """Tin trạng thái 'unknown' không còn thấy trên nguồn quá `days` ngày → 'expired'. Trả số tin đã đổi.

    Chỉ gọi sau lần chạy có dữ liệu (không đánh dấu hàng loạt khi nguồn bị chặn/hạn chế).
    """
    with conn:
        cur = conn.execute(
            "UPDATE job_posts SET status = 'expired' WHERE source = ? AND status = 'unknown' "
            # strftime giữ đúng dạng lưu 'YYYY-MM-DDTHH:MM:SS' (datetime() trả dấu cách → so chuỗi sai)
            "AND last_seen_at < strftime('%Y-%m-%dT%H:%M:%S', ?, ?)",
            (source, now_iso(), f"-{int(days)} days"),
        )
    return cur.rowcount


class _Done(Exception):
    """Dừng sớm có chủ đích (đạt giới hạn request)."""


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


def _rotate(queue: list, start: int, last_crawled: dict[str, str]) -> None:
    """Sắp các danh sách còn chờ: chưa duyệt bao giờ → duyệt lâu nhất; cùng mức thì trang ngành trước trang tỉnh."""
    queue[start:] = sorted(
        queue[start:], key=lambda item: (last_crawled.get(item.url, ""), 0 if item.industry_label else 1)
    )


def _enqueue(
    queue: list, queued: set[str], index: int, found: list, max_listings: int, last_crawled: dict[str, str]
) -> None:
    """Thêm danh sách mới phát hiện rồi sắp lại phần còn chờ theo vòng xoay."""
    for item in found:
        if item.url in queued or len(queue) >= max_listings:
            continue
        queued.add(item.url)
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
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        html = path.read_text(encoding="utf-8", errors="replace")
        if not topcv.is_listing_page(html):
            stats.errors.append(f"{path.name}: không phải trang danh sách TopCV")
            continue
        seen_at = from_timestamp(path.stat().st_mtime)
        with conn:
            for job in topcv.parse_listing(html, topcv.page_industry(html)):
                save_job(conn, cfg, "topcv", topcv.PROVIDES, job, run_id, stats, seen_at=seen_at)
        stats.pages_fetched += 1
        target = path.parent / "done" / path.name
        target.parent.mkdir(exist_ok=True)
        path.replace(target)
        done.append(path.name)
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
    return run_id


def raw_dir_exists(cfg: Config) -> Path:
    path = cfg.path("raw_dir")
    path.mkdir(parents=True, exist_ok=True)
    return path
