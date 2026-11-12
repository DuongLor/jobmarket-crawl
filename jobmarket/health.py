"""Sức khoẻ thu thập: dọn lần chạy bị ngắt + chuỗi ngày liên tục có dữ liệu (mốc 1A ≥ 7 ngày, plan v3 §3.1)."""

import json
import sqlite3
from datetime import date, datetime, timedelta

from jobmarket.store.db import now_iso

# Lần chạy "có dữ liệu" = không thất bại và lưu được ít nhất 1 tin (mới hoặc cập nhật)
DATA_RUN = "status IN ('success', 'partial', 'interrupted') AND posts_new + posts_updated > 0"


def close_stale_runs(
    conn: sqlite3.Connection, source: str | None = None, older_than_minutes: float | None = None
) -> int:
    """Lần chạy kẹt 'running' (Ctrl+C, tắt máy, tiến trình chết) → 'interrupted'.

    Tin đã lưu trước khi bị ngắt vẫn còn (mỗi trang commit riêng) → đếm lại từ job_posts: tin mới = tin có lần thấy đầu
    là lần chạy này; cập nhật = tin có lần thấy cuối là lần chạy này (tối thiểu — lần chạy sau có thể đã ghi đè).
    `older_than_minutes`: chỉ dọn lần chạy bắt đầu trước N phút (khi khởi động: không đụng lần chạy của tiến trình khác
    đang chạy thật).
    """
    where, params = ["status = 'running'"], []
    if source:
        where.append("source = ?")
        params.append(source)
    if older_than_minutes is not None:
        cutoff = datetime.fromisoformat(now_iso()) - timedelta(minutes=older_than_minutes)
        where.append("started_at < ?")
        params.append(cutoff.isoformat(timespec="seconds"))
    note = json.dumps(["Lần chạy bị ngắt giữa chừng — số tin tính lại từ DB (tối thiểu)"], ensure_ascii=False)
    with conn:
        cur = conn.execute(
            f"""UPDATE runs SET status = 'interrupted', finished_at = ?, errors_count = errors_count + 1,
                   error_sample = ?,
                   posts_new = (SELECT COUNT(*) FROM job_posts p WHERE p.first_run_id = runs.id),
                   posts_updated = (SELECT COUNT(*) FROM job_posts p
                                    WHERE p.last_run_id = runs.id AND p.first_run_id <> runs.id)
                WHERE {" AND ".join(where)}""",
            (now_iso(), note, *params),
        )
    return cur.rowcount


def _was_blocked(error_sample: str | None) -> bool:
    """Lần chạy bị nguồn chặn: ghi chú đầu tiên là `Blocked…` (challenge, 403, 429, trang rỗng nghi hạn chế ngầm)."""
    try:
        notes = json.loads(error_sample or "[]")
    except ValueError:
        return False
    return bool(notes) and isinstance(notes[0], str) and notes[0].startswith("Blocked")


def blocked_cooldown(
    conn: sqlite3.Connection, source: str, hours: float, max_hours: float
) -> tuple[datetime, int] | None:
    """Nguồn vừa chặn ta N lần chạy liền → nghỉ `hours` × 2^(N-1) giờ (tối đa `max_hours`) tính từ lần bị chặn cuối.

    → (nghỉ tới lúc nào, N) nếu còn trong thời gian nghỉ, ngược lại None. `--force`/`--now` KHÔNG bỏ qua được: chạy
    lại dồn khi đang bị chặn chỉ kéo dài lệnh chặn (CareerLink 2026-11-09: 3 lần cách 15–30 phút → bị chặn ngay trang
    đầu). Lần chạy bị ngắt mà chưa lưu tin nào không tính (như khoảng cách giữa 2 lần chạy).
    """
    if hours <= 0:
        return None
    rows = conn.execute(
        "SELECT COALESCE(finished_at, started_at), error_sample FROM runs WHERE source = ? "
        "AND NOT (status = 'interrupted' AND posts_new + posts_updated = 0) ORDER BY started_at DESC LIMIT 10",
        (source,),
    ).fetchall()
    streak = 0
    for _, sample in rows:
        if not _was_blocked(sample):
            break
        streak += 1
    if not streak:
        return None
    until = datetime.fromisoformat(rows[0][0]) + timedelta(hours=min(hours * 2 ** (streak - 1), max_hours))
    return (until, streak) if datetime.fromisoformat(now_iso()) < until else None


def data_days(conn: sqlite3.Connection, source: str) -> set[date]:
    rows = conn.execute(f"SELECT DISTINCT date(started_at) FROM runs WHERE source = ? AND {DATA_RUN}", (source,))
    return {date.fromisoformat(r[0]) for r in rows if r[0]}


def streak(days: set[date], today: date) -> int:
    """Số ngày liên tục có dữ liệu tính lùi từ hôm nay; hôm nay chưa có thì tính từ hôm qua (ngày chưa hết)."""
    day = today if today in days else today - timedelta(days=1)
    count = 0
    while day in days:
        count, day = count + 1, day - timedelta(days=1)
    return count


def report(conn: sqlite3.Connection, sources: list[str], min_sources: int = 2, today: date | None = None) -> dict:
    """Chuỗi ngày theo nguồn + chuỗi ngày mà ≥ `min_sources` nguồn cùng có dữ liệu (định nghĩa nghiệm thu 1A)."""
    today = today or datetime.fromisoformat(now_iso()).date()
    per_source = {}
    by_source_days = {s: data_days(conn, s) for s in sources}
    since = (datetime.fromisoformat(now_iso()) - timedelta(hours=24)).isoformat(timespec="seconds")
    for s in sources:
        last = conn.execute(
            "SELECT started_at, status, posts_new, posts_updated FROM runs WHERE source = ? ORDER BY started_at DESC "
            "LIMIT 1",
            (s,),
        ).fetchone()
        failed_24h = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE source = ? AND started_at >= ? AND status = 'failed'", (s, since)
        ).fetchone()[0]
        per_source[s] = {
            "streak_days": streak(by_source_days[s], today),
            "data_days": len(by_source_days[s]),
            "last_run": dict(zip(("started_at", "status", "new", "updated"), last, strict=True)) if last else None,
            "failed_24h": failed_24h,
        }
    all_days = set().union(*by_source_days.values()) if sources else set()
    joint = {d for d in all_days if sum(d in by_source_days[s] for s in sources) >= min_sources}
    stuck = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'running'").fetchone()[0]
    return {
        "today": today.isoformat(),
        "sources": per_source,
        "joint_streak_days": streak(joint, today),
        "min_sources": min_sources,
        "running_now": stuck,
    }
