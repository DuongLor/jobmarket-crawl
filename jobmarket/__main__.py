"""CLI: python -m jobmarket <lệnh>"""

import json
import os
import subprocess
import sys
from pathlib import Path

import typer

from jobmarket import labels, report
from jobmarket.config import DB_ENV, PROJECT_ROOT, load_config
from jobmarket.fetch.client import Blocked, Disallowed, PoliteClient
from jobmarket.pipeline import (
    backfill_posted_dates,
    enrich_details,
    import_saved_pages,
    raw_dir_exists,
    run_paged,
    run_topcv,
)
from jobmarket.sources import careerlink, jobsgo, vietjobs
from jobmarket.sources.clean_csv import import_clean_csv
from jobmarket.store.db import init_db

# Console Windows mặc định cp1252 → in tiếng Việt bị UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(no_args_is_help=True, add_completion=False)


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


PAGED_SOURCES = {"careerlink": careerlink, "jobsgo": jobsgo}


def run_source(conn, cfg, client, name: str, max_pages: int | None) -> int:
    if name == "topcv":
        return run_topcv(conn, cfg, client, max_pages=max_pages)
    return run_paged(conn, cfg, client, PAGED_SOURCES[name], max_pages=max_pages)


@app.command()
def run(
    source: str = typer.Option("all", help="Tên nguồn, hoặc 'all' = mọi nguồn đang bật trong config.toml"),
    max_pages: int | None = typer.Option(None, help="Số trang tối đa mỗi danh sách (ghi đè config; lần đầu lấy nhiều)"),
) -> None:
    """Thu thập + chuẩn hoá. Exit code 1 nếu có nguồn thất bại (để Task Scheduler biết)."""
    cfg = load_config()
    names = [n for n, s in cfg.raw["sources"].items() if s.get("enabled")] if source == "all" else [source]
    if source != "all" and not cfg.source(source).get("enabled"):
        raise typer.BadParameter(f"Nguồn {source} đang tắt trong config.toml")
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    failed = False
    for name in names:
        # Khoảng nghỉ riêng từng nguồn (vd CareerLink hạn chế khi đọc nhanh) ghi đè mặc định [crawl]
        source_delay = cfg.source(name).get("min_delay_seconds")
        client = PoliteClient({**cfg.crawl, **({"min_delay_seconds": source_delay} if source_delay else {})})
        try:
            run_id = run_source(conn, cfg, client, name, max_pages)
        finally:
            client.close()
        row = dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
        keys = ("id", "source", "status", "pages_fetched", "posts_new", "posts_updated", "posts_rejected",
                "errors_count", "error_sample")  # fmt: skip
        typer.echo(json.dumps({k: row[k] for k in keys} | {"requests_made": client.requests_made}, ensure_ascii=False))
        failed |= row["status"] == "failed"
    if failed:
        raise typer.Exit(code=1)


@app.command()
def enrich(
    source: str = typer.Option("careerlink", help="Nguồn có trang chi tiết"),
    limit: int = typer.Option(60, help="Số trang chi tiết tối đa lần này"),
) -> None:
    """Chỉ chạy tầng 2: bổ sung ngành/kinh nghiệm/ngày đăng/hạn nộp từ trang chi tiết cho tin chưa bổ sung."""
    cfg = load_config()
    adapter = PAGED_SOURCES[source]
    conn = init_db(cfg.path("db_file"))
    raw_dir_exists(cfg)
    delay = cfg.source(source).get("min_delay_seconds")
    client = PoliteClient({**cfg.crawl, **({"min_delay_seconds": delay} if delay else {})})
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


@app.command("import-html")
def import_html(
    folder: Path = typer.Argument(None, help="Thư mục chứa trang TopCV đã lưu (mặc định data/inbox/topcv)"),
) -> None:
    """Nạp trang danh sách TopCV do bạn tự lưu bằng trình duyệt (Ctrl+S, dạng 'Webpage, HTML only')."""
    cfg = load_config()
    folder = folder or cfg.path("data_dir") / "inbox" / "topcv"
    folder.mkdir(parents=True, exist_ok=True)
    files = [p for p in folder.iterdir() if p.suffix.lower() in (".html", ".htm")]
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
    """Áp lại quy tắc cố định cho nhãn chưa ánh xạ (sau khi bổ sung seeds, vd quận/huyện → tỉnh)."""
    conn = init_db(load_config().path("db_file"))
    changed = labels.remap_rules(conn)
    pending = conn.execute("SELECT COUNT(*) FROM labels WHERE status = 'unmapped'").fetchone()[0]
    typer.echo(f"Đã ánh xạ thêm: {changed or 'không có'} · còn chờ ánh xạ: {pending} nhãn")


@app.command()
def backfill(source: str = typer.Option("careerlink", help="Nguồn có thẻ tin đã lưu trong data/raw")) -> None:
    """Điền ngày đăng còn trống từ thẻ tin đã lưu (data/raw) — không request mạng."""
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    typer.echo(f"{backfill_posted_dates(conn, cfg, PAGED_SOURCES[source])}")


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
    """Mở dashboard Streamlit (http://localhost:8501)."""
    env = dict(os.environ)
    if demo:
        env[DB_ENV] = str(load_config().path("data_dir") / "demo.db")
    app_file = PROJECT_ROOT / "dashboard" / "app.py"
    raise typer.Exit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app_file)], env=env))


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
