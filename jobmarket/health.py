"""Sức khoẻ thu thập: dọn lần chạy bị ngắt + chuỗi ngày liên tục có dữ liệu (mốc 1A ≥ 7 ngày, plan v3 §3.1)."""

import json
import sqlite3
from datetime import date, datetime, timedelta

from jobmarket.store.db import now_iso

# Lần chạy "có dữ liệu" = không thất bại và lưu được ít nhất 1 tin (mới hoặc cập nhật)
DATA_RUN = "status IN ('success', 'partial', 'interrupted') AND posts_new + posts_updated > 0"
# Lần chạy 'running' không lưu tin nào trong N phút = coi như tiến trình đã chết
ACTIVE_QUIET_MINUTES = 30


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
        # `run --full` kéo dài nhiều giờ: còn lưu tin gần đây = tiến trình còn sống → không đóng
        quiet = datetime.fromisoformat(now_iso()) - timedelta(minutes=min(older_than_minutes, ACTIVE_QUIET_MINUTES))
        where.append("NOT EXISTS (SELECT 1 FROM job_posts p WHERE p.last_run_id = runs.id AND p.last_seen_at >= ?)")
        params.append(quiet.isoformat(timespec="seconds"))
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


def active_run(conn: sqlite3.Connection, source: str, quiet_minutes: float = ACTIVE_QUIET_MINUTES) -> int | None:
    """Lần chạy 'running' của nguồn còn hoạt động thật (mới bắt đầu, hoặc vừa lưu tin trong `quiet_minutes` phút) →
    id; ngược lại None. Chặn 2 tiến trình đọc cùng 1 nguồn (2026-11-12: 2 cửa sổ `run --full` + `start` chạy song song
    → CareerViet bị đọc gấp đôi, CareerLink bị chặn sau 3 phút)."""
    cutoff = (datetime.fromisoformat(now_iso()) - timedelta(minutes=quiet_minutes)).isoformat(timespec="seconds")
    row = conn.execute(
        """SELECT r.id FROM runs r WHERE r.source = ? AND r.status = 'running' AND (r.started_at >= ?
           OR EXISTS (SELECT 1 FROM job_posts p WHERE p.last_run_id = r.id AND p.last_seen_at >= ?))
           ORDER BY r.started_at DESC LIMIT 1""",
        (source, cutoff, cutoff),
    ).fetchone()
    return row[0] if row else None


def pace_factor(conn: sqlite3.Connection, source: str, days: float, step: float, cap: float) -> tuple[float, int]:
    """Tự giãn nhịp sau khi bị chặn: mỗi lần chạy bị chặn trong `days` ngày qua nhân nhịp nghỉ thêm `step` (tối đa
    `cap`). Hết `days` ngày không bị chặn → về nhịp gốc. → (hệ số, số lần bị chặn). Việc Làm 24h / CareerLink chặn theo
    tốc độ đọc (2026-11-12: 5 giây/request → 403 sau ~1.400 request) — đọc chậm dưới ngưỡng thay vì vượt chặn."""
    if days <= 0 or step <= 1:
        return 1.0, 0
    since = (datetime.fromisoformat(now_iso()) - timedelta(days=days)).isoformat(timespec="seconds")
    samples = conn.execute("SELECT error_sample FROM runs WHERE source = ? AND started_at >= ?", (source, since))
    blocks = sum(_was_blocked(s) for (s,) in samples)
    return min(step**blocks, cap), blocks


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
