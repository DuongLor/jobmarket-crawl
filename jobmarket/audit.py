"""Kiểm tra tay ngẫu nhiên (plan v3 §3.2, nghiệm thu 1B): xuất mẫu tin để mở trang thật và đánh dấu Đúng/Sai, rồi
chấm % trường đúng theo nguồn × trường. Không request mạng — người kiểm tự mở link.

Mỗi dòng của file = 1 tin × 1 trường (giá trị gốc từ nguồn + giá trị sau chuẩn hoá) → đánh dấu từng ô, chấm thẳng.
"""

import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from jobmarket.store.db import now_iso

SHEET = "Kiểm tra"
INFO_SHEET = "Thông tin"
MARKS = ("Đúng", "Sai", "Bỏ qua")  # Bỏ qua = trang đã gỡ / không xem được → không tính
FIELDS = ("Ngành", "Tỉnh/thành", "Lương", "Kinh nghiệm", "Ngày đăng", "Hạn nộp")
COLUMNS = ["STT tin", "Nguồn", "Link", "Tiêu đề", "Trường", "Giá trị gốc", "Sau chuẩn hoá", "Đánh dấu", "Ghi chú"]
NONE = "(không có)"
UNMAPPED = "(chưa phân loại)"
# Tin thấy trên nguồn trong N ngày qua và chưa hết hạn → trang thật còn mở được để đối chiếu
RECENT_DAYS = 3

_QUERY = """
SELECT p.id, p.source, p.url, p.title, p.salary_raw, p.salary_type, p.salary_from, p.salary_to, p.salary_currency,
       p.posted_date, p.deadline,
       li.raw_label AS industry_raw, li.status AS industry_status, mi.name AS industry_name,
       ll.raw_label AS location_raw, ll.status AS location_status, mp.name AS province_name, p.location_count,
       le.raw_label AS experience_raw, le.status AS experience_status, me.name AS experience_name,
       (SELECT group_concat(raw_label, ' | ') FROM (
            SELECT l.raw_label FROM job_industry_labels j JOIN labels l ON l.id = j.label_id
            WHERE j.job_id = p.id ORDER BY j.position)) AS industries_raw,
       (SELECT group_concat(name, ' | ') FROM (
            SELECT DISTINCT m.name FROM job_industry_labels j
            JOIN labels l ON l.id = j.label_id AND l.status = 'mapped'
            JOIN master_industries m ON m.code = l.master_code
            WHERE j.job_id = p.id)) AS industries_name,
       (SELECT group_concat(name, ' | ') FROM (
            SELECT DISTINCT c.name || COALESCE(' (' || g.name || ')', '') AS name FROM job_industry_labels j
            JOIN labels l ON l.id = j.label_id
            JOIN master_categories c ON c.code = l.category_code
            LEFT JOIN master_industries g ON g.code = c.industry_code
            WHERE j.job_id = p.id)) AS categories_name,
       p.location_raw AS location_raw_full,
       (SELECT group_concat(name, ' | ') FROM (
            SELECT DISTINCT mp2.name FROM job_locations jl
            JOIN labels l ON l.id = jl.label_id AND l.status = 'mapped'
            JOIN master_provinces mp2 ON mp2.code = l.master_code
            WHERE jl.job_id = p.id ORDER BY jl.position)) AS provinces_all
FROM job_posts p
LEFT JOIN labels li ON li.id = p.industry_label_id
LEFT JOIN master_industries mi ON mi.code = li.master_code AND li.status = 'mapped'
LEFT JOIN labels ll ON ll.id = p.location_label_id
LEFT JOIN master_provinces mp ON mp.code = ll.master_code AND ll.status = 'mapped'
LEFT JOIN labels le ON le.id = p.experience_label_id
LEFT JOIN master_experiences me ON me.code = le.master_code AND le.status = 'mapped'
WHERE p.source = ? AND p.dq_status <> 'reject' AND p.status <> 'expired' AND p.last_seen_at >= ?
ORDER BY p.id
"""


def _money(value: float, currency: str | None) -> str:
    if currency == "VND":
        return f"{value / 1e6:g} triệu"
    return f"{value:g} {currency or ''}".strip()


def salary_text(r: dict) -> str:
    kind, cur = r["salary_type"], r["salary_currency"]
    lo, hi = r["salary_from"], r["salary_to"]
    if kind == "range" and lo is not None and hi is not None:
        return f"{_money(lo, cur)} – {_money(hi, cur)}/tháng"
    if kind == "fixed" and lo is not None:
        return f"{_money(lo, cur)}/tháng"
    if kind == "from" and lo is not None:
        return f"từ {_money(lo, cur)}/tháng"
    if kind == "up_to" and hi is not None:
        return f"đến {_money(hi, cur)}/tháng"
    return {"negotiable": "Thoả thuận", "not_stated": "Không ghi lương"}.get(kind, NONE)


def _mapped(raw: str | None, status: str | None, name: str | None) -> str:
    if raw is None:
        return NONE
    if status == "ignored":
        return "(bỏ qua — không phải giá trị hợp lệ)"
    return name or UNMAPPED


def field_rows(r: dict) -> list[tuple[str, str, str]]:
    """(trường, giá trị gốc, sau chuẩn hoá) của 1 tin."""
    # Tin ghi nhiều nơi: hiện ĐỦ (thống kê tính tin ở mỗi nơi); đúng khi mọi nơi khớp trang thật
    location = r.get("provinces_all") or _mapped(r["location_raw"], r["location_status"], r["province_name"])
    shown = len(r["provinces_all"].split(" | ")) if r.get("provinces_all") else 1  # không có danh sách → 1 nơi
    if (r["location_count"] or 0) > shown:
        location += f" (nguồn ghi {r['location_count']} nơi, có nơi chưa xác định được tỉnh)"
    location_raw = r.get("location_raw_full") or r["location_raw"]
    # Tin nhiều mục ngành (nguồn không có mục chính, thống kê chia đều) → hiện đủ: đúng khi mỗi mục khớp trang thật.
    # Có danh mục (kiểu CareerLink) → "Danh mục (Nhóm)"; chưa có → nhóm ngành.
    industry_raw = r.get("industries_raw") or r["industry_raw"]
    industry = (
        r.get("categories_name")
        or r.get("industries_name")
        or _mapped(r["industry_raw"], r["industry_status"], r["industry_name"])
    )
    return [
        ("Ngành", industry_raw or NONE, industry),
        ("Tỉnh/thành", location_raw or NONE, location),
        ("Lương", r["salary_raw"] or NONE, salary_text(r)),
        (
            "Kinh nghiệm",
            r["experience_raw"] or NONE,
            _mapped(r["experience_raw"], r["experience_status"], r["experience_name"]),
        ),
        ("Ngày đăng", r["posted_date"] or NONE, r["posted_date"] or NONE),
        ("Hạn nộp", r["deadline"] or NONE, r["deadline"] or NONE),
    ]


def sample(conn: sqlite3.Connection, sources: list[str], n: int, seed: int) -> tuple[pd.DataFrame, dict]:
    """Chọn ngẫu nhiên n tin còn mở mỗi nguồn (seed cố định → chọn lại được đúng mẫu)."""
    since = (datetime.fromisoformat(now_iso()) - timedelta(days=RECENT_DAYS)).isoformat(timespec="seconds")
    prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows, pool = [], {}
        rng = random.Random(seed)
        job_no = 0
        for source in sources:
            candidates = [dict(r) for r in conn.execute(_QUERY, (source, since))]
            pool[source] = len(candidates)
            for r in rng.sample(candidates, min(n, len(candidates))):
                job_no += 1
                for field, raw, norm in field_rows(r):
                    rows.append([job_no, source, r["url"], r["title"], field, raw, norm, "", ""])
    finally:
        conn.row_factory = prev
    return pd.DataFrame(rows, columns=COLUMNS), {"since": since, "pool": pool}


def export(conn: sqlite3.Connection, sources: list[str], n: int, seed: int | None, out_dir: Path) -> Path:
    seed = seed if seed is not None else random.SystemRandom().randrange(1, 10**6)
    df, meta = sample(conn, sources, n, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(now_iso()).strftime("%Y-%m-%d_%H%M")
    path = out_dir / f"audit_{stamp}_seed{seed}.xlsx"
    info = pd.DataFrame(
        [
            ("Tạo lúc", now_iso()),
            ("Seed", seed),
            ("Số tin/nguồn", n),
            ("Chọn từ", f"tin chưa hết hạn, thấy trên nguồn từ {meta['since']}"),
            *((f"Số tin đủ điều kiện — {s}", c) for s, c in meta["pool"].items()),
            (
                "Cách đánh dấu",
                "Mở Link, so cột 'Sau chuẩn hoá' với trang thật → chọn Đúng / Sai / Bỏ qua (trang đã gỡ)",
            ),
            ("Chấm điểm", f".venv/Scripts/python -m jobmarket audit --score {path.name}"),
        ],
        columns=["Mục", "Giá trị"],
    )
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name=SHEET, index=False)
        info.to_excel(xw, sheet_name=INFO_SHEET, index=False)
        _format(xw.book[SHEET], len(df))
    return path


def _format(ws, n_rows: int) -> None:
    from openpyxl.worksheet.datavalidation import DataValidation

    widths = {"A": 8, "B": 12, "C": 40, "D": 45, "E": 13, "F": 35, "G": 35, "H": 11, "I": 30}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    if n_rows:
        dv = DataValidation(type="list", formula1='"' + ",".join(MARKS) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"H2:H{n_rows + 1}")
        for row in range(2, n_rows + 2):
            link = ws[f"C{row}"]
            if link.value:
                link.hyperlink = link.value


def score(path: Path) -> dict:
    """% trường đúng theo nguồn × trường (chỉ tính ô Đúng/Sai; Bỏ qua và ô trống không tính)."""
    df = pd.read_excel(path, sheet_name=SHEET, dtype=str).fillna("")
    df["Đánh dấu"] = df["Đánh dấu"].str.strip()
    unknown = sorted(set(df["Đánh dấu"]) - set(MARKS) - {""})
    if unknown:
        raise ValueError(f"Giá trị đánh dấu không hợp lệ: {unknown} (chỉ dùng {', '.join(MARKS)})")
    judged = df[df["Đánh dấu"].isin(("Đúng", "Sai"))]
    result: dict = {"file": path.name, "unmarked": int((df["Đánh dấu"] == "").sum()), "sources": {}}
    for source, g in judged.groupby("Nguồn", sort=True):
        fields = {
            f: {"correct": int((h["Đánh dấu"] == "Đúng").sum()), "n": len(h)}
            for f, h in g.groupby("Trường", sort=False)
        }
        result["sources"][source] = {
            "correct": int((g["Đánh dấu"] == "Đúng").sum()),
            "n": len(g),
            "fields": fields,
            "wrong": g[g["Đánh dấu"] == "Sai"][["STT tin", "Trường", "Sau chuẩn hoá", "Ghi chú"]].values.tolist(),
        }
    return result


def log_markdown(result: dict, target: float = 0.95) -> str:
    lines = [f"## {now_iso()[:16].replace('T', ' ')} — `{result['file']}`", ""]
    if result["unmarked"]:
        lines += [f"⚠️ Còn {result['unmarked']} ô chưa đánh dấu (không tính).", ""]
    header = "| Nguồn | " + " | ".join(FIELDS) + f" | Tổng | Đạt ≥ {target:.0%} |"
    lines += [header, "|" + "---|" * (len(FIELDS) + 3)]
    for source, s in result["sources"].items():
        cells = []
        for f in FIELDS:
            v = s["fields"].get(f)
            cells.append(f"{v['correct']}/{v['n']}" if v else "—")
        rate = s["correct"] / s["n"]
        lines.append(f"| {source} | " + " | ".join(cells) + f" | {rate:.1%} | {'✅' if rate >= target else '❌'} |")
    wrong = [(src, w) for src, s in result["sources"].items() for w in s["wrong"]]
    if wrong:
        lines += ["", "Sai:"]
        lines += [f"- {src} · tin {w[0]} · {w[1]}: `{w[2]}`" + (f" — {w[3]}" if w[3] else "") for src, w in wrong]
    return "\n".join(lines) + "\n"
