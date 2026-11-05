"""Ánh xạ nhãn nguồn → tên chuẩn. Một chỗ ghi duy nhất cho dashboard và CSV (PLAN §B7).

Mọi thay đổi ghi `label_history` (thời điểm, cũ → mới, lý do, kênh). Không có thao tác xoá nhãn.
Số liệu đổi ngay vì view v_jobs_unique nối nhãn lúc truy vấn (BR-10).
"""

import sqlite3
from difflib import SequenceMatcher

import pandas as pd

from jobmarket.normalize.text import norm_key
from jobmarket.store.db import now_iso

MASTER_TABLES = {
    "industry": "master_industries",
    "occupation": "master_occupations",
    "location": "master_provinces",
    "experience": "master_experiences",
    "level": "master_levels",
}
POST_COLUMNS = {
    "industry": "industry_label_id",
    "occupation": "occupation_label_id",
    "location": "location_label_id",
    "experience": "experience_label_id",
    "level": "level_label_id",
}
# Danh mục mở rộng được bằng tay; tỉnh/kinh nghiệm/cấp bậc là bộ cố định
EXTENSIBLE = {"industry", "occupation"}


class LabelError(ValueError):
    pass


def masters(conn: sqlite3.Connection, label_type: str) -> dict[str, str]:
    """{mã: tên} của danh mục chuẩn ứng với loại nhãn."""
    return dict(conn.execute(f"SELECT code, name FROM {MASTER_TABLES[label_type]}").fetchall())


def suggest(raw_label: str, candidates: dict[str, str], min_ratio: float = 0.6) -> str | None:
    """Tên chuẩn gần nhất theo so khớp chuỗi (bỏ dấu). Chỉ là gợi ý cho người duyệt, không tự áp dụng."""
    key = norm_key(raw_label)
    best, score = None, 0.0
    for name in candidates.values():
        ratio = SequenceMatcher(None, key, norm_key(name)).ratio()
        if ratio > score:
            best, score = name, ratio
    return best if score >= min_ratio else None


def table(conn: sqlite3.Connection, label_type: str | None = None, status: str | None = None) -> pd.DataFrame:
    """Nhãn kèm số tin bị ảnh hưởng, xếp theo số tin giảm dần."""
    frames = []
    for ltype, col in POST_COLUMNS.items():
        if label_type and ltype != label_type:
            continue
        frames.append(
            pd.read_sql_query(
                f"""SELECT l.id, l.label_type, l.source, l.raw_label, l.status, l.master_code, l.mapped_via,
                           l.first_seen_at, COUNT(p.id) AS post_count
                    FROM labels l LEFT JOIN job_posts p ON p.{col} = l.id
                    WHERE l.label_type = ? {"AND l.status = ?" if status else ""}
                    GROUP BY l.id""",
                conn,
                params=(ltype, status) if status else (ltype,),
            )
        )
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df
    names = {t: masters(conn, t) for t in df["label_type"].unique()}
    df["master_name"] = [
        names[t].get(c) if c else None for t, c in zip(df["label_type"], df["master_code"], strict=True)
    ]
    df["suggestion"] = [
        suggest(raw, names[t]) if s == "unmapped" else None
        for raw, t, s in zip(df["raw_label"], df["label_type"], df["status"], strict=True)
    ]
    return df.sort_values(["post_count", "raw_label"], ascending=[False, True], ignore_index=True)


def apply(
    conn: sqlite3.Connection,
    label_id: int,
    action: str,
    master_code: str | None = None,
    reason: str | None = None,
    via: str = "dashboard",
) -> bool:
    """action: 'map' (cần master_code có sẵn) | 'ignore' | 'unmap'. Trả False nếu không có gì thay đổi."""
    row = conn.execute("SELECT label_type, status, master_code FROM labels WHERE id = ?", (label_id,)).fetchone()
    if not row:
        raise LabelError(f"Không có nhãn #{label_id}")
    if action == "map":
        if master_code not in masters(conn, row["label_type"]):
            raise LabelError(f"Mã chuẩn '{master_code}' không có trong danh mục {row['label_type']}")
        new_status, new_code = "mapped", master_code
    elif action == "ignore":
        new_status, new_code = "ignored", None
    elif action == "unmap":
        new_status, new_code = "unmapped", None
    else:
        raise LabelError(f"Hành động không hợp lệ: {action}")
    if (row["status"], row["master_code"]) == (new_status, new_code):
        return False
    with conn:
        conn.execute(
            "UPDATE labels SET status = ?, master_code = ?, mapped_via = ? WHERE id = ?",
            (new_status, new_code, via, label_id),
        )
        conn.execute(
            "INSERT INTO label_history (label_id, at, old_value, new_value, reason, via) VALUES (?, ?, ?, ?, ?, ?)",
            (
                label_id,
                now_iso(),
                _describe(row["status"], row["master_code"]),
                _describe(new_status, new_code),
                reason,
                via,
            ),
        )
    return True


def add_master(conn: sqlite3.Connection, label_type: str, name: str, industry_code: str | None = None) -> str:
    """Thêm tên chuẩn mới (chỉ ngành/nghề). Trả mã mới."""
    if label_type not in EXTENSIBLE:
        raise LabelError(f"Danh mục {label_type} cố định, không thêm được")
    name = " ".join(name.split())
    existing = masters(conn, label_type)
    if not name or norm_key(name) in {norm_key(n) for n in existing.values()}:
        raise LabelError(f"Tên '{name}' trống hoặc đã có trong danh mục")
    prefix = "IND" if label_type == "industry" else "OCC"
    number = max((int(c[len(prefix) :]) for c in existing if c[len(prefix) :].isdigit()), default=0) + 1
    code = f"{prefix}{number:02d}"
    with conn:
        if label_type == "industry":
            conn.execute("INSERT INTO master_industries VALUES (?, ?, ?)", (code, name, number))
        else:
            if industry_code not in masters(conn, "industry"):
                raise LabelError("Nghề mới cần chọn ngành cha có sẵn")
            conn.execute("INSERT INTO master_occupations VALUES (?, ?, ?)", (code, name, industry_code))
    return code


def remap_rules(conn: sqlite3.Connection) -> dict[str, int]:
    """Áp lại quy tắc cố định (bảng tỉnh/quận, bí danh ngành/nhãn, nhóm kinh nghiệm, trùng đúng tên chuẩn) cho nhãn
    còn 'unmapped' VÀ nhãn do chính quy tắc ánh xạ trước đây (`mapped_via = 'rule'`).

    Dùng sau khi sửa seeds (thêm quận/huyện, đổi ngành của một bí danh…): nhãn của quy tắc đi theo quy tắc hiện tại,
    kể cả bỏ ánh xạ khi quy tắc không còn. KHÔNG đụng nhãn ánh xạ/bỏ qua tay (CLI, dashboard).
    """
    from jobmarket.pipeline import IGNORE, _rule_mapping

    changed: dict[str, int] = {}
    rows = conn.execute(
        "SELECT id, label_type, raw_label, status FROM labels WHERE status = 'unmapped' OR mapped_via = 'rule'"
    ).fetchall()
    for row in rows:
        code = _rule_mapping(conn, row["label_type"], row["raw_label"])
        if not code and row["status"] == "unmapped":
            continue
        action, master = ("ignore", None) if code == IGNORE else ("map", code) if code else ("unmap", None)
        if apply(conn, row["id"], action, master, reason="quy tắc cố định (remap)", via="rule"):
            changed[row["label_type"]] = changed.get(row["label_type"], 0) + 1
    return changed


def history(conn: sqlite3.Connection, limit: int = 500) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT h.at, l.label_type, l.source, l.raw_label, h.old_value, h.new_value, h.via, h.reason
           FROM label_history h JOIN labels l ON l.id = h.label_id
           ORDER BY h.id DESC LIMIT ?""",
        conn,
        params=(limit,),
    )


def _describe(status: str, code: str | None) -> str:
    return f"{status}:{code}" if code else status
