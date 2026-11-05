"""Bản sao DB sang MySQL để xem bằng MySQL Workbench.

SQLite (`paths.db_file`) vẫn là DB chính: crawler, thống kê, dashboard chỉ đọc/ghi SQLite. Mỗi lần đồng bộ dựng lại
TOÀN BỘ từng bảng trong schema MySQL (`<bảng>__new` rồi đổi tên một lần) → người xem không thấy bảng rỗng giữa chừng;
sửa dữ liệu trong MySQL sẽ bị lần đồng bộ sau ghi đè.
"""

import os
import re
import sqlite3
import time

PASSWORD_ENV = "JOBMARKET_MYSQL_PASSWORD"
BATCH = 1000
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Tên không hợp lệ cho MySQL: {name!r}")
    return f"`{name}`"


def tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def column_type(declared: str, key_len: int | None) -> str:
    """Kiểu MySQL theo kiểu khai báo SQLite. Cột khoá kiểu chữ → VARCHAR so khớp nhị phân: khoá như 'ha noi' / 'hà nội'
    hay URL khác hoa/thường là khác nhau trong SQLite, collation mặc định của MySQL sẽ coi là trùng."""
    d = (declared or "").upper()
    if "INT" in d:
        return "BIGINT"
    if any(t in d for t in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE"
    if key_len is not None:
        return f"VARCHAR({max(64, key_len + 64)}) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
    return "LONGTEXT"


def create_sql(conn: sqlite3.Connection, table: str, name: str) -> tuple[str, list[str]]:
    cols = conn.execute(f"PRAGMA table_info({_ident(table)})").fetchall()  # cid, name, type, notnull, dflt, pk
    keys = [c[1] for c in sorted(cols, key=lambda c: c[5]) if c[5]]
    defs = []
    for c in cols:
        key_len = None
        if c[5]:
            key_len = conn.execute(f"SELECT COALESCE(MAX(LENGTH({_ident(c[1])})), 0) FROM {_ident(table)}").fetchone()[
                0
            ]
        defs.append(f"{_ident(c[1])} {column_type(c[2], key_len)}" + (" NOT NULL" if c[5] else ""))
    if keys:
        defs.append("PRIMARY KEY (" + ", ".join(_ident(k) for k in keys) + ")")
    ddl = f"CREATE TABLE {_ident(name)} ({', '.join(defs)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    return ddl, [c[1] for c in cols]


def connect(mysql_cfg: dict):
    import pymysql

    return pymysql.connect(
        host=mysql_cfg.get("host", "127.0.0.1"),
        port=int(mysql_cfg.get("port", 3306)),
        user=mysql_cfg.get("user", "root"),
        password=os.environ.get(PASSWORD_ENV, ""),
        charset="utf8mb4",
        autocommit=False,
    )


def sync(conn: sqlite3.Connection, mysql_cfg: dict) -> dict:
    """Chép mọi bảng SQLite sang schema `mysql_cfg['database']`. Trả về số dòng từng bảng (đã đối chiếu 2 bên)."""
    started = time.monotonic()
    schema = _ident(mysql_cfg.get("database", "jobmarket"))
    my = connect(mysql_cfg)
    counts: dict[str, int] = {}
    try:
        cur = my.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {schema} CHARACTER SET utf8mb4")
        cur.execute(f"USE {schema}")
        for table in tables(conn):
            new, old = _ident(f"{table}__new"), _ident(f"{table}__old")
            ddl, cols = create_sql(conn, table, f"{table}__new")
            cur.execute(f"DROP TABLE IF EXISTS {new}, {old}")
            cur.execute(ddl)
            insert = (
                f"INSERT INTO {new} ({', '.join(_ident(c) for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
            )
            src = conn.execute(f"SELECT {', '.join(_ident(c) for c in cols)} FROM {_ident(table)}")
            while batch := src.fetchmany(BATCH):
                cur.executemany(insert, [tuple(r) for r in batch])
            my.commit()
            cur.execute(f"SELECT COUNT(*) FROM {new}")
            copied = cur.fetchone()[0]
            expected = conn.execute(f"SELECT COUNT(*) FROM {_ident(table)}").fetchone()[0]
            if copied != expected:
                raise RuntimeError(f"{table}: MySQL có {copied} dòng, SQLite có {expected}")
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() "
                        "AND table_name = %s", (table,))  # fmt: skip
            if cur.fetchone()[0]:
                cur.execute(f"RENAME TABLE {_ident(table)} TO {old}, {new} TO {_ident(table)}")
                cur.execute(f"DROP TABLE {old}")
            else:
                cur.execute(f"RENAME TABLE {new} TO {_ident(table)}")
            counts[table] = copied
    finally:
        my.close()
    return {"database": mysql_cfg.get("database", "jobmarket"), "tables": counts,
            "seconds": round(time.monotonic() - started, 1)}  # fmt: skip
