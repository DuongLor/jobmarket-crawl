"""CLI: python -m jobmarket <lệnh>"""

import copy
import dataclasses
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import typer

from jobmarket import audit, health, labels, mysql_mirror, report, stats
from jobmarket.config import DB_ENV, PROJECT_ROOT, load_config
from jobmarket.fetch.client import Blocked, Disallowed, PoliteClient
from jobmarket.pipeline import (
    backfill_experience_requirements,
    backfill_industry_labels,
    backfill_listing_industries,
    backfill_locations,
    backfill_posted_dates,
    enrich_details,
    import_saved_pages,
    raw_dir_exists,
    reapply_detail_industries,
    renormalize_salaries,
    run_paged,
    run_topcv,
)
from jobmarket.sources import careerlink, careerviet, itviec, jobsgo, vieclam24h, vietjobs
from jobmarket.sources.clean_csv import import_clean_csv
from jobmarket.store import purge
from jobmarket.store.db import init_db
from jobmarket.timeutil import now_local

# Console Windows mặc định cp1252 → in tiếng Việt bị UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(no_args_is_help=True, add_completion=False)


def keep_awake() -> None:
    """Windows: không cho máy ngủ khi tiến trình còn chạy (tự hết khi tiến trình thoát). Lần chạy 31 (11–12/11) đứng
    ~15 giờ vì máy ngủ qua đêm. Màn hình vẫn tắt bình thường."""
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


@app.command()
def init() -> None:
    """Tạo/cập nhật DB (migrations) và nạp danh mục chuẩn."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "master_industries",
            "master_occupations",
            "master_provinces",
            "master_experiences",
            "master_levels",
            "province_aliases",
        )
    }
    typer.echo(f"DB: {cfg.path('db_file')}  {json.dumps(counts)}")


def _client_for(cfg, name: str, conn=None) -> PoliteClient:
    """Khoảng nghỉ riêng từng nguồn (vd CareerLink hạn chế khi đọc nhanh) ghi đè mặc định [crawl]; có `conn` → nhân
    thêm hệ số tự giãn sau các lần bị chặn gần đây (`pace_after_block_*`)."""
    delay = float(cfg.source(name).get("min_delay_seconds") or cfg.crawl["min_delay_seconds"])
    if conn is not None:
        factor, blocks = health.pace_factor(
            conn,
            name,
            days=float(cfg.crawl.get("pace_after_block_days", 0)),
            step=float(cfg.crawl.get("pace_after_block_step", 1)),
            cap=float(cfg.crawl.get("pace_after_block_max", 1)),
        )
        if factor > 1:
            echo(f"{name}: bị chặn {blocks} lần gần đây → giãn nhịp ×{factor:.2g}: {delay:g} → {delay * factor:g} giây")
            delay *= factor
    return PoliteClient({**cfg.crawl, "min_delay_seconds": delay})


PAGED_SOURCES = {
    "careerlink": careerlink,
    "jobsgo": jobsgo,
    "vieclam24h": vieclam24h,
    "careerviet": careerviet,
    "itviec": itviec,
}


def run_source(conn, cfg, client, name: str, max_pages: int | None) -> int:
    if name == "topcv":
        return run_topcv(conn, cfg, client, max_pages=max_pages)
    return run_paged(conn, cfg, client, PAGED_SOURCES[name], max_pages=max_pages)


def stamped(message: str) -> str:
    return f"[{datetime.now():%H:%M:%S}] {message}"


def echo(message: str) -> None:
    typer.echo(stamped(message))


class _ProgressHandler(logging.Handler):
    def __init__(self, say) -> None:
        super().__init__()
        self.say = say

    def emit(self, record: logging.LogRecord) -> None:
        self.say(record.getMessage())


def show_progress(say) -> None:
    """In tiến độ crawl từng trang (logger `jobmarket.progress` của pipeline) qua `say`."""
    log = logging.getLogger("jobmarket.progress")
    log.handlers.clear()
    log.addHandler(_ProgressHandler(say))
    log.setLevel(logging.INFO)
    log.propagate = False


@app.command()
def run(
    source: str = typer.Option("all", help="Tên nguồn, hoặc 'all' = mọi nguồn đang bật trong config.toml"),
    max_pages: int | None = typer.Option(None, help="Số trang tối đa mỗi danh sách (ghi đè config; lần đầu lấy nhiều)"),
    min_hours: float = typer.Option(
        3.5, help="Bỏ qua nguồn đã chạy trong N giờ qua (CareerLink hạn chế ~3,5 giờ; tránh dồn với `start`)"
    ),
    force: bool = typer.Option(False, "--force", help="Chạy dù chưa đủ khoảng cách (chỉ khi biết chắc)"),
    full: bool = typer.Option(
        False,
        "--full",
        help="Quét TRỌN site: bỏ giới hạn request/trang/trang chi tiết/sitemap mỗi lần chạy, bỏ khoảng cách giữa 2 "
        "lần chạy. Vẫn giữ nhịp nghỉ (full_min_delay_seconds), robots.txt, dừng + nghỉ khi bị chặn. "
        "Không đồng bộ MySQL.",
    ),
    ignore_cooldown: bool = typer.Option(
        False,
        "--ignore-cooldown",
        help="Bỏ thời gian nghỉ sau khi bị chặn (thử lại sớm). Vẫn dừng ngay nếu site còn trả 403/429 — "
        "thử dồn có thể kéo dài lệnh chặn.",
    ),
) -> None:
    """Thu thập + chuẩn hoá. Exit code 1 nếu có nguồn thất bại (để Task Scheduler biết)."""
    keep_awake()
    cfg = load_config()
    names = [n for n, s in cfg.raw["sources"].items() if s.get("enabled")] if source == "all" else [source]
    if source != "all" and not cfg.source(source).get("enabled"):
        raise typer.BadParameter(f"Nguồn {source} đang tắt trong config.toml")
    if full:
        cfg = full_config(cfg, names)
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    # Một nguồn cụ thể: chỉ dọn lần chạy kẹt của nguồn đó — không đụng `run --full` của nguồn khác đang chạy song song
    health.close_stale_runs(conn, source=None if source == "all" else source, older_than_minutes=STALE_RUN_MINUTES)
    show_progress(echo)
    failed = crawled = False
    for name in names:
        if busy := health.active_run(conn, name):
            echo(f"{name}: bỏ qua — lần chạy {busy} của tiến trình khác đang đọc nguồn này")
            continue
        if rest := _cooldown_note(conn, cfg, name):
            if not ignore_cooldown:
                echo(f"{name}: bỏ qua — {rest} (thêm --ignore-cooldown để thử lại sớm)")
                continue
            echo(f"{name}: bỏ thời gian nghỉ theo yêu cầu — {rest}")
        due, next_at = _crawl_due(conn, name, min_hours)
        if not (due or force or full):
            skipped = f"đã chạy trong {min_hours:g} giờ qua; lần tới {next_at}"
            echo(f"{name}: bỏ qua — {skipped} (thêm --force để chạy ngay)")
            continue
        pace = cfg.source(name).get("min_delay_seconds") or cfg.crawl["min_delay_seconds"]
        echo(f"{name}: bắt đầu crawl (~{pace} giây/request, mỗi trang đọc xong in 1 dòng)…")
        client = _client_for(cfg, name, conn)
        try:
            run_id = run_source(conn, cfg, client, name, max_pages)
        except KeyboardInterrupt:
            health.close_stale_runs(conn, source=name)
            echo(f"{name}: dừng giữa chừng — lần chạy ghi là 'interrupted', tin đã lưu vẫn giữ")
            raise
        finally:
            client.close()
        row = dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
        keys = ("id", "source", "status", "pages_fetched", "posts_new", "posts_updated", "posts_rejected",
                "errors_count", "error_sample")  # fmt: skip
        echo(
            f"{name}: XONG — {row['status']} · {row['posts_new']} tin mới · {row['posts_updated']} cập nhật · "
            f"{client.requests_made} request" + (f" · ghi chú: {row['error_sample']}" if row["errors_count"] else "")
        )
        typer.echo(json.dumps({k: row[k] for k in keys} | {"requests_made": client.requests_made}, ensure_ascii=False))
        failed |= row["status"] == "failed"
        crawled = True
    if (
        crawled and not full
    ):  # --full: nhiều cửa sổ song song, mỗi lần đồng bộ dựng lại cả schema → chạy tay `mysql-sync`
        _mirror_mysql(cfg, conn, typer.echo)
    if failed:
        raise typer.Exit(code=1)


# --full: "không giới hạn" mỗi lần chạy. Chỉ nâng tầng nào nguồn ĐANG bật (> 0) — Việc Làm 24h tắt quét sâu vì trang 2
# trả 403, CareerViet tắt trang chi tiết vì robots cấm → giữ nguyên, không vì "full" mà gõ vào chỗ bị chặn/bị cấm.
UNLIMITED = 10**9
FULL_IF_ENABLED = ("deep_pages_per_run", "detail_max_per_run", "sitemap_max_per_run")


def full_config(cfg, names: list[str]):
    raw = copy.deepcopy(cfg.raw)
    for name in names:
        src = raw["sources"][name]
        src["max_requests"] = UNLIMITED
        for key in FULL_IF_ENABLED:
            if int(src.get(key, 0)) > 0:
                src[key] = UNLIMITED
        if src.get("full_min_delay_seconds"):
            src["min_delay_seconds"] = src["full_min_delay_seconds"]
    return dataclasses.replace(cfg, raw=raw)


@app.command()
def enrich(
    source: str = typer.Option("careerlink", help="Nguồn có trang chi tiết"),
    limit: int = typer.Option(60, help="Số trang chi tiết tối đa lần này"),
) -> None:
    """Chỉ chạy tầng 2: bổ sung ngành/kinh nghiệm/ngày đăng/hạn nộp từ trang chi tiết cho tin chưa bổ sung."""
    cfg = load_config()
    adapter = PAGED_SOURCES[source]
    if not hasattr(adapter, "parse_detail"):
        raise typer.BadParameter(f"Nguồn {source} không có tầng trang chi tiết (robots cấm hoặc chưa hỗ trợ)")
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    client = _client_for(cfg, source)
    try:
        counts = enrich_details(conn, cfg, client, adapter, limit)
    except (Blocked, Disallowed) as exc:
        typer.echo(f"Dừng: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        client.close()
    pending = conn.execute(
        "SELECT COUNT(*) FROM job_posts WHERE source = ? AND detail_fetched_at IS NULL", (source,)
    ).fetchone()[0]
    typer.echo(f"{counts} · còn chờ bổ sung: {pending} tin · request: {client.requests_made}")


def _saved_pages(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".html", ".htm")]


# Tên file userscript `scripts/topcv-save.user.js` đặt cho trang TopCV bạn đang xem (tải về thư mục Downloads)
DOWNLOAD_PREFIX = "jobmarket-topcv-"


def _collect_downloads(cfg, inbox: Path) -> int:
    """Chuyển file `jobmarket-topcv-*.html` từ `[sources.topcv] downloads_dir` vào inbox. Chỉ đụng file đúng tiền tố
    này; trình duyệt chỉ đổi sang tên thật khi tải xong (.crdownload/.part) → không chuyển file đang tải dở."""
    folder = cfg.source("topcv").get("downloads_dir")
    source = Path(folder).expanduser() if folder else None
    if source is None or not source.is_dir():
        return 0
    moved = 0
    for path in source.glob(f"{DOWNLOAD_PREFIX}*"):
        if not path.is_file() or path.suffix.lower() not in (".html", ".htm"):
            continue
        target = inbox / path.name
        if target.exists():
            target = target.with_name(f"{path.stem}_{int(path.stat().st_mtime)}{path.suffix}")
        try:
            shutil.move(path, target)  # khác ổ đĩa (C: → D:) = chép (giữ giờ sửa = lúc lưu trang) rồi xoá
        except OSError:  # đang bị mở → lần kiểm tra sau
            continue
        moved += 1
    return moved


class InboxWatcher:
    """Nạp trang TopCV bạn tự lưu vào inbox, chỉ khi file đã lưu xong (kích thước + giờ sửa không đổi giữa 2 lần
    kiểm tra). File không nạp được (không phải trang tin) báo 1 lần, không thử lại tới khi file đổi."""

    def __init__(self, conn, cfg, folder: Path):
        self.conn, self.cfg, self.folder = conn, cfg, folder
        self.previous: dict[Path, tuple[int, float]] = {}
        self.tried: set[tuple[Path, float]] = set()
        folder.mkdir(parents=True, exist_ok=True)

    def poll(self) -> int | None:
        _collect_downloads(self.cfg, self.folder)
        current = {}
        for p in _saved_pages(self.folder):
            try:
                st = p.stat()
            except OSError:  # file vừa bị chuyển/xoá (vd run_nightly.ps1 nạp cùng lúc)
                continue
            current[p] = (st.st_size, st.st_mtime)
        ready = [p for p, sig in current.items() if self.previous.get(p) == sig and (p, sig[1]) not in self.tried]
        self.previous = current
        if not ready:
            return None
        run_id = import_saved_pages(self.conn, self.cfg, ready)
        self.tried.update((p, current[p][1]) for p in ready if p.exists())  # còn ở inbox = không nạp được
        return run_id


@app.command()
def watch(
    folder: Path = typer.Argument(None, help="Thư mục theo dõi (mặc định data/inbox/topcv)"),
    interval: float = typer.Option(10, help="Số giây giữa 2 lần kiểm tra"),
) -> None:
    """Chỉ tự nạp trang TopCV bạn vừa lưu (Ctrl+S) vào inbox — không request mạng. Dừng: Ctrl+C."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    watcher = InboxWatcher(conn, cfg, folder or cfg.path("data_dir") / "inbox" / "topcv")
    typer.echo(f"Đang theo dõi {watcher.folder} (mỗi {interval:g} giây). Ctrl+C để dừng.")
    try:
        while True:
            if (run_id := watcher.poll()) is not None:
                _print_run(conn, run_id)
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("Đã dừng theo dõi.")


def _mirror_mysql(cfg, conn, say) -> None:
    """Đồng bộ sang MySQL nếu bật `[mysql] enabled`; lỗi chỉ báo, không làm hỏng lần crawl (SQLite đã lưu xong)."""
    if not cfg.raw.get("mysql", {}).get("enabled"):
        return
    try:
        result = mysql_mirror.sync(conn, cfg.raw["mysql"])
    except Exception as exc:
        say(f"MySQL: đồng bộ lỗi {type(exc).__name__}: {exc} (dữ liệu vẫn ở SQLite; chạy lại `mysql-sync`)")
        return
    say(f"MySQL: đã đồng bộ {sum(result['tables'].values())} dòng vào schema {result['database']} "
        f"({result['seconds']} giây)")  # fmt: skip


# Lần chạy 'running' bắt đầu trước N phút = tiến trình đã chết (một lần chạy thật ≤ ~15 phút)
STALE_RUN_MINUTES = 120


def _crawl_due(conn, name: str, hours: float) -> tuple[bool, str | None]:
    """Đến giờ crawl nguồn chưa: lần chạy gần nhất (kể cả lỗi/bị chặn, ghi trong DB) cách hiện tại ≥ `hours` giờ.

    Lần chạy bị ngắt mà chưa lưu được tin nào không tính (gần như chưa gửi request); bị ngắt sau khi đã lưu tin thì vẫn
    tính — đã tốn request, giữ khoảng cách với nguồn (CareerLink hạn chế theo số request trong vài giờ).
    """
    last = conn.execute(
        "SELECT MAX(started_at) FROM runs WHERE source = ? "
        "AND NOT (status = 'interrupted' AND posts_new + posts_updated = 0)",
        (name,),
    ).fetchone()[0]
    if not last:
        return True, None
    next_at = datetime.fromisoformat(last) + timedelta(hours=hours)
    return datetime.fromisoformat(now_local()) >= next_at, next_at.strftime("%H:%M %d/%m")


def _cooldown_note(conn, cfg, name: str) -> str | None:
    """Nguồn đang trong thời gian nghỉ sau khi chặn ta → lý do để in; không thì None. Không có cách bỏ qua."""
    hours = float(cfg.crawl.get("blocked_cooldown_hours", 0))
    rest = health.blocked_cooldown(conn, name, hours, float(cfg.crawl.get("blocked_cooldown_max_hours", hours)))
    if rest is None:
        return None
    until, streak = rest
    return (
        f"nguồn đã chặn {streak} lần chạy liền, nghỉ tới {until:%H:%M %d/%m} "
        "(--force/--now không bỏ qua được; chạy dồn khi đang bị chặn chỉ kéo dài lệnh chặn)"
    )


@app.command()
def start(
    every_hours: float = typer.Option(
        4, help="Khoảng cách tối thiểu giữa 2 lần crawl mỗi nguồn (CareerLink: ≥ ~3,5 giờ)"
    ),
    port: int = typer.Option(8501, help="Cổng dashboard"),
    crawl: bool = typer.Option(True, help="--no-crawl: chỉ dashboard + tự nạp TopCV"),
    open_browser: bool = typer.Option(True, help="Mở dashboard trên trình duyệt"),
    now: bool = typer.Option(False, "--now", help="Crawl ngay khi mở (bỏ qua khoảng cách lần đầu), sau đó cách đều"),
) -> None:
    """MỘT LỆNH CHẠY TẤT CẢ: dashboard (chỉ máy này) + tự nạp trang TopCV bạn lưu + crawl nguồn đang bật cách nhau
    ≥ `every_hours` giờ (tính từ lần chạy cuối trong DB, tắt/mở lại không crawl dồn). Dừng: Ctrl+C."""
    if crawl:
        keep_awake()
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    if closed := health.close_stale_runs(conn, older_than_minutes=STALE_RUN_MINUTES):
        typer.echo(f"Đã đóng {closed} lần chạy bị ngắt từ trước (trạng thái 'interrupted').")
    log_dir = cfg.path("data_dir") / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    def say(message: str) -> None:
        line = stamped(message)
        typer.echo(line)
        with (log_dir / f"start_{datetime.now():%Y-%m-%d}.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    show_progress(say)  # mỗi trang crawl xong in 1 dòng (và ghi log)
    url = f"http://127.0.0.1:{port}"
    dashboard = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "dashboard" / "app.py"), "--server.address",
         "127.0.0.1", "--server.port", str(port), "--server.headless", "true"],
        stdout=(log_dir / "dashboard.log").open("a", encoding="utf-8"), stderr=subprocess.STDOUT,
    )  # fmt: skip
    say(f"Dashboard: {url} (chỉ máy này mở được)")
    if open_browser:
        time.sleep(4)
        webbrowser.open(url)
    watcher = InboxWatcher(conn, cfg, cfg.path("data_dir") / "inbox" / "topcv")
    say(f"Tự nạp trang TopCV bạn lưu vào: {watcher.folder}")
    sources = [n for n, s in cfg.raw["sources"].items() if s.get("enabled")] if crawl else []
    say(f"Crawl tự động: {', '.join(sources) or 'tắt'} — mỗi nguồn cách nhau ≥ {every_hours:g} giờ. Ctrl+C để dừng.")
    announced: dict[str, str | None] = {}
    forced = set(sources) if now else set()  # --now: mỗi nguồn crawl ngay 1 lần, sau đó theo khoảng cách
    try:
        while True:
            if dashboard.poll() is not None:
                say(f"Dashboard dừng (mã {dashboard.returncode}) — xem data/logs/dashboard.log (cổng {port} đang bận?)")
                raise typer.Exit(code=1)
            try:
                run_id = watcher.poll()
            except Exception as exc:  # lỗi tự nạp không làm tắt dashboard + crawl
                say(f"TopCV: lỗi tự nạp {type(exc).__name__}: {exc}")
                run_id = None
            sync_needed = run_id is not None
            if run_id is not None:
                row = conn.execute("SELECT posts_new, posts_updated, error_sample FROM runs WHERE id = ?", (run_id,))
                new, updated, errors = row.fetchone()
                say(f"TopCV: nạp {new} tin mới, cập nhật {updated}" + (f" · lỗi: {errors}" if errors != "[]" else ""))
            for name in sources:
                if busy := health.active_run(conn, name):
                    note = f"lần chạy {busy} của tiến trình khác đang đọc nguồn này — chờ"
                    if announced.get(name) != note:
                        say(f"{name}: {note}")
                        announced[name] = note
                    continue
                if rest := _cooldown_note(conn, cfg, name):
                    forced.discard(name)
                    if announced.get(name) != rest:
                        say(f"{name}: {rest}")
                        announced[name] = rest
                    continue
                due, next_at = _crawl_due(conn, name, every_hours)
                due = due or name in forced
                forced.discard(name)
                if not due:
                    if announced.get(name) != next_at:
                        say(f"{name}: lần crawl tới lúc {next_at}")
                        announced[name] = next_at
                    continue
                budget = int(cfg.source(name).get("max_requests", cfg.crawl.get("max_requests", 50)))
                detail = int(cfg.source(name).get("detail_max_per_run", 0))
                extra = f" + {detail} trang chi tiết" if detail else ""
                if sitemap_pages := int(cfg.source(name).get("sitemap_max_per_run", 0)):
                    extra += f" + {sitemap_pages} tin qua sitemap"
                pace = cfg.source(name).get("min_delay_seconds") or cfg.crawl["min_delay_seconds"]
                say(f"{name}: bắt đầu crawl (≤ {budget} request danh sách{extra}, ~{pace} giây/request)…")
                sync_needed = True
                client = _client_for(cfg, name, conn)
                try:
                    try:
                        run_id = run_source(conn, cfg, client, name, None)
                    except KeyboardInterrupt:
                        health.close_stale_runs(conn, source=name)
                        say(f"{name}: dừng giữa chừng — lần chạy ghi là 'interrupted', tin đã lưu vẫn giữ")
                        raise
                    r = conn.execute("SELECT status, posts_new, posts_updated, error_sample FROM runs WHERE id = ?",
                                     (run_id,)).fetchone()  # fmt: skip
                    say(f"{name}: {r['status']} · {r['posts_new']} tin mới · {r['posts_updated']} cập nhật · "
                        f"{client.requests_made} request · {r['error_sample']}")  # fmt: skip
                except Exception as exc:  # lỗi 1 lần chạy không làm dừng cả chương trình
                    say(f"{name}: lỗi {type(exc).__name__}: {exc}")
                finally:
                    client.close()
            if sync_needed:
                _mirror_mysql(cfg, conn, say)
            time.sleep(10)
    except KeyboardInterrupt:
        say("Đang dừng…")
    finally:
        dashboard.terminate()
        say("Đã dừng dashboard, tự nạp và crawl.")


@app.command("mysql-sync")
def mysql_sync() -> None:
    """Chép toàn bộ DB SQLite sang MySQL (`[mysql]` trong config.toml) để xem bằng Workbench. Không request mạng."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    result = mysql_mirror.sync(conn, cfg.raw.get("mysql", {}))
    for table, n in result["tables"].items():
        typer.echo(f"  {table:<22} {n:>8} dòng")
    typer.echo(
        f"Đã đồng bộ {len(result['tables'])} bảng vào MySQL schema {result['database']} ({result['seconds']} giây)"
    )


def _crawl_sources(cfg) -> list[str]:
    """Nguồn crawl đang bật (TopCV chỉ nạp trang tự lưu → không tính)."""
    return [n for n, s in cfg.raw["sources"].items() if s.get("enabled") and n != "topcv"]


@app.command("audit")
def audit_cmd(
    sample: int = typer.Option(30, "--sample", help="Số tin ngẫu nhiên mỗi nguồn"),
    source: str = typer.Option("all", help="Một nguồn, hoặc all = mọi nguồn crawl đang bật"),
    seed: int = typer.Option(None, help="Seed chọn mẫu (bỏ trống = ngẫu nhiên, ghi vào tên file để chọn lại được)"),
    score: Path = typer.Option(None, "--score", help="Chấm file đã đánh dấu (đường dẫn, hoặc tên trong data/audit)"),
) -> None:
    """Kiểm tra tay ngẫu nhiên: xuất data/audit/*.xlsx để so với trang thật; --score chấm, ghi docs/audit-log.md."""
    cfg = load_config()
    out_dir = cfg.path("data_dir") / "audit"
    if score is not None:
        path = score if score.exists() else out_dir / score.name
        if not path.exists():
            raise typer.BadParameter(f"Không thấy file {score}")
        result = audit.score(path)
        if not result["sources"]:
            typer.echo("Chưa có ô nào được đánh dấu Đúng/Sai — chưa chấm.")
            raise typer.Exit(code=1)
        md = audit.log_markdown(result)
        log = cfg.root / "docs" / "audit-log.md"
        log.parent.mkdir(parents=True, exist_ok=True)
        if not log.exists():
            log.write_text("# Nhật ký kiểm tra tay (plan v3 §3.2 — mục tiêu ≥ 95% trường đúng mỗi nguồn)\n", "utf-8")
        with log.open("a", encoding="utf-8") as f:
            f.write("\n" + md)
        typer.echo(md)
        typer.echo(f"Đã ghi vào {log}")
        return
    names = _crawl_sources(cfg) if source == "all" else [source]
    conn = init_db(cfg.path("db_file"))
    path = audit.export(conn, names, sample, seed, out_dir)
    typer.echo(f"Đã tạo {path}")
    typer.echo("Mở file → bấm Link → so cột 'Sau chuẩn hoá' với trang thật → chọn Đúng / Sai / Bỏ qua (cột Đánh dấu).")
    typer.echo(f"Xong thì chạy: .venv/Scripts/python -m jobmarket audit --score {path.name}")


@app.command("health")
def health_cmd(
    min_sources: int = typer.Option(2, help="Số nguồn tối thiểu cùng có dữ liệu trong 1 ngày (nghiệm thu 1A)"),
    target: int = typer.Option(7, help="Số ngày liên tục cần đạt"),
) -> None:
    """Sức khoẻ thu thập: chuỗi ngày liên tục có dữ liệu theo nguồn, lần chạy gần nhất, lỗi 24 giờ. Không request."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    closed = health.close_stale_runs(conn, older_than_minutes=STALE_RUN_MINUTES)
    sources = _crawl_sources(cfg)
    r = health.report(conn, sources, min_sources=min_sources)
    if closed:
        typer.echo(f"Đã đóng {closed} lần chạy kẹt → 'interrupted'.")
    typer.echo(f"Hôm nay {r['today']} · đang chạy: {r['running_now']}")
    for name, info in r["sources"].items():
        last = info["last_run"]
        last_txt = (
            f"{last['started_at']} {last['status']} (+{last['new']} mới, {last['updated']} cập nhật)" if last else "—"
        )
        typer.echo(
            f"  {name:<11} liên tục {info['streak_days']} ngày · tổng {info['data_days']} ngày có dữ liệu · "
            f"lỗi 24h: {info['failed_24h']} · gần nhất: {last_txt}"
        )
    for name in sorted(stats.LISTING_INDUSTRY_SOURCES & set(sources)):
        done, total = stats.category_sweep_progress(conn, name)
        if total:
            note = "ngành theo danh mục đã vào thống kê" if done == total else "chưa tính tỷ trọng ngành theo danh mục"
            typer.echo(f"  {name:<11} danh mục ngành đã quét trọn: {done}/{total} — {note}")
    joint = r["joint_streak_days"]
    verdict = "ĐẠT" if joint >= target else f"chưa đạt (còn {target - joint} ngày)"
    typer.echo(f"Chuỗi ngày có ≥ {min_sources} nguồn cùng có dữ liệu: {joint}/{target} — {verdict}")


@app.command("import-html")
def import_html(
    folder: Path = typer.Argument(None, help="Thư mục chứa trang TopCV đã lưu (mặc định data/inbox/topcv)"),
) -> None:
    """Nạp trang TopCV (danh sách hoặc chi tiết tin) do bạn tự lưu bằng trình duyệt (Ctrl+S, 'Webpage, HTML only')."""
    cfg = load_config()
    folder = folder or cfg.path("data_dir") / "inbox" / "topcv"
    folder.mkdir(parents=True, exist_ok=True)
    _collect_downloads(cfg, folder)
    files = _saved_pages(folder)
    if not files:
        typer.echo(f"Không có file .html trong {folder}")
        raise typer.Exit()
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    run_id = import_saved_pages(conn, cfg, files)
    _print_run(conn, run_id)


@app.command("import-vietjobs")
def import_vietjobs_cmd(
    path: Path = typer.Argument(None, help="VietJobs.csv (mặc định data/datasets/vietjobs/VietJobs.csv)"),
) -> None:
    """Nạp bộ dữ liệu lịch sử VietJobs (48.092 tin, 07–10/2025) làm nguồn riêng `vietjobs`."""
    cfg = load_config()
    path = path or cfg.path("data_dir") / "datasets" / "vietjobs" / "VietJobs.csv"
    if not path.exists():
        typer.echo(f"Không thấy {path}. Tải: curl -L -o {path} {vietjobs.DATASET_URL}")
        raise typer.Exit(code=1)
    conn = init_db(cfg.path("db_file"))
    run_id, inserted = vietjobs.import_vietjobs(conn, cfg, path)
    _print_run(conn, run_id)
    typer.echo(f"Đã nạp {inserted} tin mới từ {path.name}")


@app.command()
def remap() -> None:
    """Áp lại quy tắc cố định sau khi sửa seeds: nhãn chưa ánh xạ + nhãn do quy tắc ánh xạ (không đụng nhãn sửa tay)."""
    conn = init_db(load_config().path("db_file"))
    changed = labels.remap_rules(conn)
    pending = conn.execute("SELECT COUNT(*) FROM labels WHERE status = 'unmapped'").fetchone()[0]
    typer.echo(f"Đã đổi theo quy tắc: {changed or 'không có'} · còn chờ ánh xạ: {pending} nhãn")


@app.command()
def backfill(source: str = typer.Option("careerlink", help="Nguồn có thẻ tin đã lưu trong data/raw")) -> None:
    """Tính lại từ bản gốc đã lưu (data/raw), không request mạng: ngày đăng còn trống, ngành từ trang chi tiết."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    adapter = PAGED_SOURCES[source]
    if hasattr(adapter, "card_date"):
        typer.echo(f"ngày đăng: {backfill_posted_dates(conn, cfg, adapter)}")
    if hasattr(adapter, "parse_detail"):
        typer.echo(f"ngành (trang chi tiết): {reapply_detail_industries(conn, cfg, adapter)}")
    typer.echo(f"mục ngành của tin (đếm chia đều): {backfill_industry_labels(conn, cfg, source)}")
    typer.echo(f"mục ngành theo trang danh mục đã gặp: {backfill_listing_industries(conn, cfg, source)}")


@app.command("backfill-details")
def backfill_details_cmd() -> None:
    """Tin đã có: kinh nghiệm theo tháng (từ nhãn đã lưu) + tách yêu cầu khỏi mô tả CareerLink. Không request."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    typer.echo(json.dumps(backfill_experience_requirements(conn), ensure_ascii=False))


@app.command("backfill-locations")
def backfill_locations_cmd() -> None:
    """Mọi nơi làm việc của tin (job_locations) từ bản gốc đã lưu, mọi nguồn — không request mạng."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    typer.echo(f"nơi làm việc: {backfill_locations(conn, cfg)}")


@app.command("purge-source")
def purge_source_cmd(
    source: str = typer.Option(..., help="Nguồn cần xoá hết dữ liệu (vd careerlink)"),
    yes: bool = typer.Option(False, "--yes", help="Xác nhận xoá (không có thì chỉ in số dòng sẽ xoá)"),
) -> None:
    """Xoá MỌI dữ liệu của 1 nguồn (tin, công ty, nhãn, lần chạy, sitemap). Tự sao lưu DB vào data/backup trước khi
    xoá. Nguồn phải đang tắt trong config.toml và không có tiến trình nào đang đọc."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    if cfg.raw["sources"].get(source, {}).get("enabled"):
        raise typer.BadParameter(f"{source} đang bật trong config.toml — tắt trước để không bị crawl lại")
    if busy := health.active_run(conn, source):
        raise typer.BadParameter(f"lần chạy {busy} của {source} đang chạy — dừng tiến trình đó trước")
    jobs = conn.execute("SELECT COUNT(*) FROM job_posts WHERE source = ?", (source,)).fetchone()[0]
    runs = conn.execute("SELECT COUNT(*) FROM runs WHERE source = ?", (source,)).fetchone()[0]
    if not yes:
        typer.echo(f"{source}: sẽ xoá {jobs} tin, {runs} lần chạy (+ công ty, nhãn, sitemap). Thêm --yes để xoá.")
        return
    saved = purge.backup(conn, cfg.path("data_dir") / "backup")
    typer.echo(f"Đã sao lưu: {saved}")
    counts = purge.purge_source(conn, source)
    typer.echo(f"{source}: đã xoá " + ", ".join(f"{t} {n}" for t, n in counts.items() if n))


@app.command()
def renormalize(
    source: list[str] = typer.Option([], help="Nguồn cần chuẩn hoá lại lương (mặc định: mọi nguồn có trong DB)"),
) -> None:
    """Chuẩn hoá lại lương từ chuỗi gốc đã lưu (sau khi đổi quy tắc/ngưỡng trong config.toml) — không request mạng."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    for name in source or [r[0] for r in conn.execute("SELECT DISTINCT source FROM job_posts ORDER BY source")]:
        typer.echo(f"{name}: {renormalize_salaries(conn, cfg, name)}")


@app.command("report")
def report_cmd(
    period: str = typer.Argument(..., help="Kỳ báo cáo YYYY-MM"),
    publish: bool = typer.Option(False, "--publish", help="Chốt (lưu cố định + xuất Excel); không chốt lại được"),
) -> None:
    """Báo cáo tháng: xem nhanh, hoặc chốt và xuất Excel vào data/reports/."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    try:
        if publish:
            payload, path = report.publish(conn, cfg, period)
            typer.echo(f"Đã chốt {period} → {path}")
        else:
            payload = report.build(conn, cfg, period)
    except report.ReportError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    for line in payload["insights"]:
        typer.echo(f"• {line}")


@app.command("import-csv")
def import_csv(path: Path, source: str = typer.Option("csv_import", help="Tên nguồn nếu file không có cột source")):
    """Nạp file CSV tin đã làm sạch (định dạng job_posts_clean.csv / tab Data_Jobs của mockup)."""
    conn = init_db(load_config().path("db_file"))
    run_id, inserted = import_clean_csv(conn, path, run_source=source)
    typer.echo(f"run #{run_id}: nạp {inserted} dòng từ {path.name}")


@app.command()
def demo() -> None:
    """Tạo data/demo.db từ 374 dòng dữ liệu mẫu của mockup (không đụng DB thật)."""
    cfg = load_config()
    target = cfg.path("data_dir") / "demo.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    conn = init_db(target)
    _, inserted = import_clean_csv(conn, PROJECT_ROOT / "tests" / "fixtures" / "mockup_jobs.csv")
    typer.echo(f"{target}: {inserted} tin mẫu. Mở dashboard: python -m jobmarket dashboard --demo")


@app.command()
def dashboard(demo: bool = typer.Option(False, "--demo", help="Dùng data/demo.db (dữ liệu mẫu)")) -> None:
    """Mở dashboard Streamlit (http://127.0.0.1:8501 — chỉ máy này)."""
    env = dict(os.environ)
    if demo:
        env[DB_ENV] = str(load_config().path("data_dir") / "demo.db")
    app_file = PROJECT_ROOT / "dashboard" / "app.py"
    # Chỉ máy này mở được (mặc định Streamlit nghe mọi card mạng → máy khác trong mạng LAN cũng xem được dữ liệu)
    args = [sys.executable, "-m", "streamlit", "run", str(app_file), "--server.address", "127.0.0.1"]
    raise typer.Exit(subprocess.call(args, env=env))


def _print_run(conn, run_id: int) -> None:
    row = dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
    keys = ("id", "status", "pages_fetched", "posts_new", "posts_updated", "posts_rejected", "errors_count")
    typer.echo(json.dumps({k: row[k] for k in keys} | {"error_sample": row["error_sample"]}, ensure_ascii=False))


@app.command()
def summary() -> None:
    """Tóm tắt nhanh dữ liệu đang có (trước khi có dashboard)."""
    conn = init_db(load_config().path("db_file"))
    q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731
    typer.echo(f"Tin (bảng):        {q('SELECT COUNT(*) FROM job_posts')[0][0]}")
    typer.echo(f"Tin duy nhất:      {q('SELECT COUNT(*) FROM v_jobs_unique')[0][0]}")
    typer.echo(f"dq_status:         {dict(q('SELECT dq_status, COUNT(*) FROM job_posts GROUP BY 1'))}")
    typer.echo(f"salary_type:       {dict(q('SELECT salary_type, COUNT(*) FROM v_jobs_unique GROUP BY 1'))}")
    typer.echo(
        f"Nhãn theo trạng thái: "
        f"{[tuple(r) for r in q('SELECT label_type, status, COUNT(*) FROM labels GROUP BY 1, 2')]}"
    )
    top = q("SELECT province_code, COUNT(*) c FROM v_jobs_unique GROUP BY 1 ORDER BY c DESC LIMIT 6")
    typer.echo(f"Top tỉnh:          {[tuple(r) for r in top]}")


if __name__ == "__main__":
    app()
