import csv
import sqlite3
from pathlib import Path

from jobmarket.config import PROJECT_ROOT
from jobmarket.normalize.text import norm_key
from jobmarket.timeutil import now_local

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
SEEDS_DIR = PROJECT_ROOT / "seeds"


def now_iso() -> str:
    """Giờ Việt Nam, không kèm múi giờ (xem jobmarket/timeutil.py)."""
    return now_local()


def connect(db_file: Path) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        with conn:
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (path.name, now_iso()))
        applied.append(path.name)
    return applied


def _rows(name: str) -> list[dict]:
    with (SEEDS_DIR / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def seed(conn: sqlite3.Connection) -> None:
    """Nạp danh mục chuẩn; INSERT OR IGNORE nên chạy lại an toàn, không ghi đè sửa tay."""
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO master_industries VALUES (:code, :name, :sort_order)",
            _rows("industries.csv"),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO master_occupations VALUES (:code, :name, :industry_code)",
            _rows("occupations.csv"),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO master_provinces VALUES (:code, :name, :name_en)", _rows("provinces.csv")
        )
        conn.executemany(
            "INSERT OR IGNORE INTO master_experiences "
            "VALUES (:code, :name, :min_months, NULLIF(:max_months, ''), :sort_order)",
            _rows("experiences.csv"),
        )
        conn.executemany("INSERT OR IGNORE INTO master_levels VALUES (:code, :name, :rank_order)", _rows("levels.csv"))
        conn.executemany(
            "INSERT OR IGNORE INTO province_aliases VALUES (?, ?)",
            [(norm_key(r["alias"]), r["province_code"]) for r in _rows("province_aliases.csv")],
        )
        # Bảng ánh xạ là quy tắc (không phải sửa tay trên DB) → sửa seed rồi `remap` là có hiệu lực
        conn.executemany(
            "INSERT OR REPLACE INTO industry_aliases VALUES (?, ?)",
            [(norm_key(r["alias"]), r["industry_code"] or None) for r in _rows("industry_aliases.csv")],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO label_aliases VALUES (?, ?, ?)",
            [(r["label_type"], norm_key(r["alias"]), r["code"] or None) for r in _rows("label_aliases.csv")],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO master_categories VALUES "
            "(:code, :name, NULLIF(:industry_code, ''), NULLIF(:careerlink_id, ''), :sort_order)",
            _rows("categories.csv"),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO category_aliases VALUES (?, ?)",
            [(norm_key(r["alias"]), r["category_code"]) for r in _rows("category_aliases.csv")],
        )


def init_db(db_file: Path) -> sqlite3.Connection:
    conn = connect(db_file)
    migrate(conn)
    seed(conn)
    return conn
