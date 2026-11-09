"""Mọi công thức thống kê ở MỘT chỗ — dashboard và báo cáo chỉ gọi vào đây.

Mã công thức (M01…, S01…, T01…, Q01…) theo tab Formulas của docs/mockup.xlsx.
Mọi số liệu đọc từ view v_jobs_unique (BR-04); ngưỡng đọc từ config [stats] (BR-11).
"""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from jobmarket.dedup import company_core
from jobmarket.normalize.text import norm_key

UNCLASSIFIED = {"industry": "(chưa phân loại)", "category": "(chưa phân loại)", "province": "(chưa xác định)"}
UNCLASSIFIED_DEFAULT = "(chưa phân loại)"
SMALL_SAMPLE = "n<5"

# dimension → (cột mã trong view, bảng danh mục, cột thứ tự)
DIMENSIONS = {
    "industry": ("industry_code", "master_industries", "sort_order"),
    # Danh mục kiểu CareerLink (2 cấp: danh mục thuộc 1 nhóm ngành); category_code = danh mục đầu của tin
    "category": ("category_code", "master_categories", "sort_order"),
    "occupation": ("occupation_code", "master_occupations", None),
    "province": ("province_code", "master_provinces", None),
    "experience": ("experience_code", "master_experiences", "sort_order"),
    "level": ("level_code", "master_levels", "rank_order"),
}


@dataclass
class Filters:
    """Bộ lọc dùng chung (PLAN §B6). Danh sách rỗng = không lọc."""

    date_from: date | None = None
    date_to: date | None = None
    sources: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    provinces: list[str] = field(default_factory=list)
    experiences: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)


_LEGAL_FORMS = (
    ("cp", re.compile(r"\b(co phan|cp|jsc|joint stock)\b")),
    ("tnhh", re.compile(r"\b(tnhh|trach nhiem huu han|mtv|llc|co ltd|company limited|limited|ltd)\b")),
)


def company_identity(name: str) -> str:
    """Khoá đếm công ty: cùng LOẠI HÌNH pháp lý + cùng tên lõi.

    "Công ty CP X" = "Công ty Cổ phần X" (chỉ khác viết tắt) → 1 công ty; nhưng "Công ty TNHH X" ≠ "Công ty CP X"
    (khác loại hình → có thể là 2 pháp nhân) → không gộp.
    """
    if not name or not name.strip():
        return ""
    key = norm_key(name)
    form = next((f for f, pattern in _LEGAL_FORMS if pattern.search(key)), "")
    return f"{form}|{company_core(name) or key}"


MAX_RANGE_RATIO = 5.0
# Lương tháng đại diện cao hơn mức này gần như luôn là nhập sai/đơn vị khác (năm, tổng gói) → không vào thống kê
MAX_MONTHLY_SALARY = 150_000_000
BACKLOG_GRACE_DAYS = 0


def load_unique(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    max_range_ratio: float | None = MAX_RANGE_RATIO,
    backlog_grace_days: int = BACKLOG_GRACE_DAYS,
    max_monthly_salary: float | None = MAX_MONTHLY_SALARY,
) -> pd.DataFrame:
    """Tin duy nhất kèm tên chuẩn; mã chưa ánh xạ → tên nhóm "(chưa phân loại)".

    Hai hiệu chỉnh để thống kê đúng (cấu hình [stats] max_range_ratio, backlog_grace_days):
    - Tin TỒN SẴN khi bắt đầu thu thập (chưa có ngày đăng, thấy lần đầu trong ngày đầu tiên nguồn được thu
      thập): ngày đăng thật không biết → event_date = NaT, không tính vào tin mới / theo tháng / xu hướng.
      Tin xuất hiện sau đó: ngày thấy lần đầu ≈ ngày đăng (thu thập hằng ngày).
    - Khoảng lương rộng bất thường (đầu trên > max_range_ratio × đầu dưới, thường "lương cứng + hoa hồng lên tới"):
      trung điểm không đại diện → không đưa vào thống kê lương (như dạng "tới X").
    - Lương đại diện > max_monthly_salary/tháng (vd "500 triệu", "200 – 500 triệu"): nhập sai/đơn vị khác → loại.
    """
    df = pd.read_sql_query("SELECT * FROM v_jobs_unique ORDER BY first_seen_at, id", conn)
    for col in ("event_date", "posted_date", "deadline"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    seen_day = pd.to_datetime(df["first_seen_at"], errors="coerce").dt.normalize()
    source_start = seen_day.groupby(df["source"]).transform("min")
    df["collection_start"] = source_start
    backlog = df["posted_date"].isna() & (seen_day <= source_start + pd.Timedelta(days=int(backlog_grace_days)))
    # Ngày đăng/làm mới TRƯỚC ngày bắt đầu thu thập nguồn: hệ thống chỉ thấy tin còn sống tới lúc đó (tin đã hết
    # hạn trước đó không bao giờ thấy) → không phải dòng tin của tháng ấy. Tính vào tháng cũ sẽ làm tháng trước
    # thiếu có hệ thống → MoM/xu hướng luôn "tăng" giả (đo 2026-11-04: +5.095% sau 2 ngày thu thập).
    # Dung sai 1 ngày = độ trễ bình thường giữa lúc đăng và lần chạy kế tiếp thấy tin.
    backlog |= df["event_date"].notna() & (df["event_date"] < source_start - pd.Timedelta(days=1))
    df["date_known"] = ~backlog
    df.loc[backlog, "event_date"] = pd.NaT
    df["status_known"] = _status_known(conn, df)
    df["industry_basis"] = _industry_basis(conn, df)
    df["salary_excluded"] = None
    if max_range_ratio:
        wide = (df["salary_type"] == "range") & (df["salary_to"] > df["salary_from"] * float(max_range_ratio))
        df.loc[wide, "salary_mid"] = None
        df.loc[wide, "salary_excluded"] = "wide_range"
    if max_monthly_salary:
        high = df["salary_mid"] > float(max_monthly_salary)
        df.loc[high, "salary_mid"] = None
        df.loc[high, "salary_excluded"] = "too_high"
    df["category_codes"] = _multi_codes(
        conn,
        df,
        """SELECT j.job_id, c.code, c.name, c.sort_order FROM job_industry_labels j
           JOIN labels l ON l.id = j.label_id AND l.status <> 'ignored'
           JOIN master_categories c ON c.code = l.category_code
           ORDER BY j.job_id, j.position""",
    )
    df["category_code"] = df["category_codes"].map(lambda t: t[0][0] if t else None)
    df["province_codes"] = _multi_codes(
        conn,
        df,
        """SELECT jl.job_id, p.code, p.name, NULL FROM job_locations jl
           JOIN labels l ON l.id = jl.label_id AND l.status = 'mapped'
           JOIN master_provinces p ON p.code = l.master_code
           ORDER BY jl.job_id, jl.position""",
    )
    for dim, (code_col, table, order_col) in DIMENSIONS.items():
        order = order_col or "rowid"
        rows = conn.execute(f"SELECT code, name, {order} FROM {table}").fetchall()
        df[f"{dim}_name"] = (
            df[code_col].map({r[0]: r[1] for r in rows}).fillna(UNCLASSIFIED.get(dim, UNCLASSIFIED_DEFAULT))
        )
        # Thứ tự danh mục: phá hoà khi xếp hạng, sắp bảng theo thứ tự chuẩn; chưa phân loại xếp cuối
        df[f"{dim}_order"] = df[code_col].map({r[0]: r[2] for r in rows}).fillna(10_000)
    df["company_key"] = df["company_name"].fillna("").map(company_identity)
    df["industry_codes"] = _industry_codes(conn, df)
    return apply_filters(df, filters) if filters else df


def _multi_codes(conn: sqlite3.Connection, df: pd.DataFrame, query: str) -> pd.Series:
    """Giá trị KHÁC NHAU (mã, tên, thứ tự) của một chiều nhiều giá trị cho từng tin, theo thứ tự nguồn."""
    if df.empty:
        return pd.Series(dtype=object)
    codes: dict[int, dict[str, tuple]] = {}
    for job_id, code, name, order in conn.execute(query):
        codes.setdefault(job_id, {}).setdefault(code, (code, name, order if order is not None else 10_000))
    return df["id"].map(lambda i: tuple(codes.get(i, {}).values()))


def _industry_codes(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.Series:
    """Mã ngành chuẩn KHÁC NHAU của mọi mục ngành của tin (bảng job_industry_labels, theo thứ tự nguồn); tin một
    ngành có 1 phần tử; tin không có bảng mục ngành → tuple rỗng (dùng industry_code)."""
    return _multi_codes(
        conn,
        df,
        """SELECT j.job_id, m.code, m.name, m.sort_order FROM job_industry_labels j
           JOIN labels l ON l.id = j.label_id AND l.status = 'mapped'
           JOIN master_industries m ON m.code = l.master_code
           ORDER BY j.job_id, j.position""",
    )


# Chiều nhiều giá trị → (cột bộ giá trị, chia đều?, số giá trị tối thiểu để tách dòng).
# Ngành/danh mục: tin k mục tính 1/k mỗi mục (tổng = số tin); tin 1 ngành giữ mã ngành của view.
# Tỉnh: tin ghi nhiều nơi tính ĐỦ 1 ở mỗi nơi (như CareerLink lọc theo tỉnh) → tổng > số tin.
MULTI_DIMENSIONS = {
    "industry": ("industry_codes", True, 2),
    "category": ("category_codes", True, 1),
    "province": ("province_codes", False, 1),
}

# Chiều suy từ trang danh mục đang duyệt → chỉ tính tin có phân loại so sánh được (industry_comparable)
LISTING_BASED_DIMS = {"industry", "category"}


def allocate(df: pd.DataFrame, dim: str) -> pd.DataFrame:
    """Mỗi tin → một dòng cho mỗi giá trị của chiều `dim` kèm trọng số (giữ trọng số đã có khi gọi nối tiếp)."""
    df = df.assign(weight=df.get("weight", 1.0))
    if dim not in MULTI_DIMENSIONS or MULTI_DIMENSIONS[dim][0] not in df or df.empty:
        return df
    col, share, min_k = MULTI_DIMENSIONS[dim]
    k = df[col].map(len)
    has = k >= min_k  # tin chưa có giá trị nào (chưa phân loại / dữ liệu cũ) giữ mã hiện có
    if not has.any():
        return df
    spread = df[has].assign(_alloc=df.loc[has, col].map(list))
    if share:
        spread["weight"] = spread["weight"] / k[has]
    spread = spread.explode("_alloc")
    values = spread.pop("_alloc").tolist()
    for i, suffix in enumerate(("code", "name", "order")):
        spread[f"{dim}_{suffix}"] = [t[i] for t in values]
    return pd.concat([df[~has], spread], ignore_index=True)


# Nguồn mà ngành của tin lấy từ TRANG DANH MỤC đang duyệt (không phải từ chính tin): tỷ trọng ngành phản ánh danh
# mục nào đã được duyệt, không phản ánh thị trường (CareerLink 2026-11-04: 4/15 ngành có tin, 49% chưa phân loại).
# Tin đã đọc trang chi tiết (ngành từ JSON-LD của tin) thì ngành gắn theo tin.
LISTING_INDUSTRY_SOURCES = {"careerlink", "jobsgo"}


def category_sweep_progress(conn: sqlite3.Connection, source: str) -> tuple[int, int]:
    """(số danh mục ngành đã quét trọn ≥ 1 vòng, tổng số danh mục ngành đã biết) của nguồn.

    Danh mục cùng mã (đổi slug) tính là một; bỏ danh mục không phải ngành (nhãn 'ignored', vd "Tiếng Nhật")."""
    ignored = {
        r[0]
        for r in conn.execute(
            "SELECT raw_label FROM labels WHERE source = ? AND label_type = 'industry' AND status = 'ignored'",
            (source,),
        )
    }
    swept: dict[str, bool] = {}
    for url, label, done in conn.execute(
        "SELECT url, label, last_full_sweep_started_at IS NOT NULL FROM crawl_listings "
        "WHERE source = ? AND label IS NOT NULL",
        (source,),
    ):
        if label in ignored:
            continue
        match = re.search(r"/(\d+)$", url)
        key = match.group(1) if match else url
        swept[key] = swept.get(key, False) or bool(done)
    return sum(swept.values()), len(swept)


def _industry_basis(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.Series:
    """'item' = ngành ghi trên chính tin (trang chi tiết); 'category' = các danh mục chứa tin, nguồn ĐÃ quét trọn mọi
    danh mục ngành ≥ 1 vòng (mục ngành của tin đủ theo các danh mục đã biết → tính tỷ trọng, chia đều); 'listing' =
    danh mục chưa quét hết (tỷ trọng sẽ lệch về danh mục quét trước → không tính)."""
    if df.empty:
        return pd.Series(dtype=object)
    detail = dict(conn.execute("SELECT id, detail_fetched_at IS NOT NULL FROM job_posts").fetchall())
    from_listing = df["source"].isin(LISTING_INDUSTRY_SOURCES) & ~df["id"].map(detail).fillna(0).astype(bool)
    complete = {s for s in LISTING_INDUSTRY_SOURCES if (p := category_sweep_progress(conn, s))[1] and p[0] == p[1]}
    basis = pd.Series("item", index=df.index, dtype=object)
    basis[from_listing] = np.where(df.loc[from_listing, "source"].isin(complete), "category", "listing")
    return basis


def _status_known(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.Series:
    """Trạng thái còn tuyển/hết hạn xác định được: có trạng thái từ trang chi tiết, có hạn nộp, hoặc nguồn đã quét
    trọn ít nhất một vòng (tin không thấy lại đã bị đánh hết hạn). Trước vòng quét trọn đầu tiên, tin 'unknown'
    không hạn nộp có thể đã đóng từ lâu → "chưa xác định", không cộng vào "đang tuyển"."""
    if df.empty:
        return pd.Series(dtype=bool)
    # Chỉ vòng quét trọn của danh sách GỐC (start_url — cũng là mốc `mark_expired` dùng); một danh mục nhỏ quét xong
    # không có nghĩa tin của cả nguồn đã được xét hết hạn.
    swept = {
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT c.source FROM crawl_listings c
               JOIN runs r ON r.source = c.source AND c.url = json_extract(r.scope, '$.start_url')
               WHERE c.last_full_sweep_started_at IS NOT NULL"""
        ).fetchall()
    }
    return (df["status"] != "unknown") | df["deadline"].notna() | df["source"].isin(swept)


def industry_comparable(df: pd.DataFrame) -> pd.DataFrame:
    """Tập dùng cho cơ cấu/tỷ trọng/xu hướng NGÀNH: tin có ngành gắn theo tin, hoặc theo danh mục đã quét trọn."""
    return df[df["industry_basis"] != "listing"] if "industry_basis" in df else df


def as_of_date(conn: sqlite3.Connection) -> date | None:
    """Ngày chốt số liệu = ngày gần nhất hệ thống thấy tin (không phải hôm nay: dữ liệu có thể cũ)."""
    value = conn.execute("SELECT MAX(date(last_seen_at)) FROM job_posts").fetchone()[0]
    return date.fromisoformat(value) if value else None


def confidence(n: int, quality_row: dict | None, thresholds: dict) -> str:
    """Huy hiệu độ tin cậy (PLAN §B6): Cao / Vừa / Thấp."""
    coverage_ok = quality_row is not None and all(
        (quality_row.get(f"pct_{k}") or 0) >= thresholds[f"min_{k}"]
        for k in ("location", "industry", "occupation", "experience")
    )
    if n >= thresholds["confidence_high_n"] and coverage_ok:
        return "Cao"
    if n >= thresholds["confidence_mid_n"]:
        return "Vừa"
    return "Thấp"


def apply_filters(df: pd.DataFrame, f: Filters) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    # Tin chưa rõ ngày đăng (tồn sẵn khi bắt đầu thu thập) vẫn thuộc hiện trạng thị trường → giữ lại cho thống kê
    # phân bố/lương; các chỉ số theo thời gian (tin mới, theo tháng, xu hướng) tự bỏ qua vì event_date trống.
    unknown = df["event_date"].isna()
    if f.date_from:
        mask &= unknown | (df["event_date"] >= pd.Timestamp(f.date_from))
    if f.date_to:
        mask &= unknown | (df["event_date"] <= pd.Timestamp(f.date_to))
    for values, col, multi in (
        (f.sources, "source", None),
        (f.industries, "industry_code", "industry_codes"),
        (f.categories, "category_code", "category_codes"),
        (f.provinces, "province_code", "province_codes"),
        (f.experiences, "experience_code", None),
        (f.levels, "level_code", None),
    ):
        if not values:
            continue
        hit = df[col].isin(values)
        # Chiều nhiều giá trị: tin khớp nếu BẤT KỲ giá trị nào khớp (tin Hà Nội + HCM có trong lọc "Hồ Chí Minh")
        if multi and multi in df:
            codes = df[multi].explode().dropna().str[0]
            hit |= df.index.isin(codes.index[codes.isin(values)])
        mask &= hit
    return df[mask]


def _windows(as_of: date, days: int) -> tuple[tuple[pd.Timestamp, pd.Timestamp], tuple[pd.Timestamp, pd.Timestamp]]:
    """M05: (as_of − days, as_of] và (as_of − 2·days, as_of − days]."""
    end = pd.Timestamp(as_of)
    mid = end - timedelta(days=days)
    return (mid, end), (mid - timedelta(days=days), mid)


def _in(series: pd.Series, window: tuple[pd.Timestamp, pd.Timestamp]) -> pd.Series:
    return (series > window[0]) & (series <= window[1])


ACTIVE_SEEN_DAYS = 7


def is_active(df: pd.DataFrame, as_of: date, seen_days: int = ACTIVE_SEEN_DAYS) -> pd.Series:
    """M03: status='active' và chưa quá hạn tại ngày chốt.

    Nguồn chỉ có trang danh sách không cho biết trạng thái (status='unknown'): coi là đang tuyển nếu hệ thống
    còn thấy tin trong `seen_days` ngày tính tới ngày chốt. `seen_days` ≤ 0 = bỏ điều kiện này, tin 'unknown' chưa
    bị đánh dấu hết hạn là còn tuyển (dùng khi việc hết hạn do vòng quét trọn quyết định — pipeline.mark_expired).
    """
    not_expired = df["deadline"].isna() | (df["deadline"] >= pd.Timestamp(as_of))
    if seen_days <= 0:
        recently_seen = pd.Series(True, index=df.index)
    else:
        recently_seen = pd.to_datetime(df["last_seen_at"], errors="coerce") >= pd.Timestamp(as_of) - timedelta(
            days=seen_days
        )
    known = df["status_known"] if "status_known" in df else pd.Series(True, index=df.index)
    active = ((df["status"] == "active") & not_expired) | ((df["status"] == "unknown") & recently_seen & not_expired)
    return active & known


def status_undetermined(df: pd.DataFrame) -> pd.Series:
    """Tin chưa xác định được còn tuyển hay không (xem `_status_known`)."""
    if "status_known" not in df:
        return pd.Series(False, index=df.index)
    return ~df["status_known"].astype(bool)


def _delta(cur: float, prev: float) -> float | None:
    return None if not prev else cur / prev - 1


def overview(df: pd.DataFrame, as_of: date, window_days: int, seen_days: int = ACTIVE_SEEN_DAYS) -> dict:
    """Thẻ chỉ số màn Tổng quan."""
    cur, prev = _windows(as_of, window_days)
    new, before = int(_in(df["event_date"], cur).sum()), int(_in(df["event_date"], prev).sum())
    salaries = df["salary_mid"].dropna()
    return {
        "unique_jobs": len(df),
        "active": int(is_active(df, as_of, seen_days).sum()),
        "status_undetermined": int(status_undetermined(df).sum()),
        "new_window": new,
        "prev_window": before,
        "new_delta": _delta(new, before),
        "companies": int(df.loc[df["company_key"] != "", "company_key"].nunique()),
        "salary_median": float(salaries.median()) if len(salaries) else None,
        "salary_n": len(salaries),
        "salary_coverage": len(salaries) / len(df) if len(df) else None,
    }


def demand_by(
    df: pd.DataFrame, dim: str, as_of: date, window_days: int, seen_days: int = ACTIVE_SEEN_DAYS
) -> pd.DataFrame:
    """M01, M02, M05 theo 1 chiều. Nhóm chưa phân loại vẫn tính vào tổng, không xếp hạng.

    Chiều ngành: chỉ tin có ngành gắn theo tin (`industry_comparable`) — ngành suy từ danh mục đang duyệt làm lệch
    tỷ trọng."""
    if dim in LISTING_BASED_DIMS:
        df = industry_comparable(df)
    name = f"{dim}_name"
    cur, prev = _windows(as_of, window_days)
    work = df.assign(
        active=is_active(df, as_of, seen_days), new=_in(df["event_date"], cur), prev=_in(df["event_date"], prev)
    )
    # Ngành/danh mục: tin nhiều mục chia đều 1/k (tổng = số tin); tỉnh: tin nhiều nơi tính đủ ở mỗi nơi
    work = allocate(work, dim)
    for col in ("active", "new", "prev"):
        work[col] = work[col].astype(float) * work["weight"]
    out = (
        work.groupby([name, f"{dim}_order"])
        .agg(total=("weight", "sum"), active=("active", "sum"), new=("new", "sum"), prev=("prev", "sum"))
        .reset_index()
    )
    out["share"] = out["total"] / out["total"].sum() if len(df) else 0
    for col in ("total", "active", "new", "prev"):
        out[col] = _tidy_counts(out[col])
    out["delta"] = [_delta(n, p) for n, p in zip(out["new"], out["prev"], strict=True)]
    unclassified = out[name].isin(set(UNCLASSIFIED.values()) | {UNCLASSIFIED_DEFAULT})
    # Hạng duy nhất: nhiều tin hơn đứng trước; bằng nhau thì theo thứ tự danh mục (khớp mockup)
    ranked = out[~unclassified].sort_values(["total", f"{dim}_order"], ascending=[False, True])
    ranked["rank"] = range(1, len(ranked) + 1)
    rest = out[unclassified].assign(rank=pd.NA)
    result = pd.concat([ranked, rest], ignore_index=True).drop(columns=f"{dim}_order")
    return result.rename(columns={name: dim})


def _tidy_counts(values: pd.Series) -> pd.Series:
    """Số đếm có trọng số: nguyên hết → int; có phần lẻ (tin nhiều ngành) → 1 chữ số thập phân, làm tròn theo phần
    dư lớn nhất để TỔNG vẫn đúng (3 × 1/3 → 0,4 + 0,3 + 0,3 = 1, không phải 0,9)."""
    if ((values.round(6) % 1) == 0).all():
        return values.round().astype(int)
    tenths = values.to_numpy(dtype=float) * 10
    floor = np.floor(tenths + 1e-9)
    short = int(round(tenths.sum() - floor.sum()))
    floor[np.argsort(-(tenths - floor), kind="stable")[:short]] += 1
    return pd.Series(floor / 10, index=values.index)


def _starts_month(start: pd.Timestamp) -> bool:
    """Thu thập bắt đầu ngày `start` có coi là phủ trọn tháng đó không: bắt đầu trong 3 ngày đầu tháng (tin đăng ngày
    1–2 vẫn thấy được nhờ dung sai 1 ngày của `load_unique` + tin còn hạn). Ngày 4 trở đi → tháng thiếu một phần."""
    return start.day <= 3


def _source_starts(df: pd.DataFrame) -> list[pd.Timestamp]:
    """Ngày bắt đầu thu thập của từng nguồn có trong `df`."""
    if "collection_start" not in df:
        return []
    return [pd.Timestamp(t) for t in df["collection_start"].dropna().unique()]


def _partial_months(df: pd.DataFrame) -> set[pd.Period]:
    """Tháng có nguồn bắt đầu thu thập giữa chừng (tháng đó nguồn ấy chỉ có một phần)."""
    return {t.to_period("M") for t in _source_starts(df) if not _starts_month(t)}


def monthly(df: pd.DataFrame, end_month: date, months: int = 6) -> pd.DataFrame:
    """M04: tin mới theo tháng (theo event_date), tách nguồn, kèm MoM.

    Chỉ các tháng từ khi bắt đầu thu thập (tháng trước đó không quan sát được, không phải "0 tin"). Tháng bắt đầu
    thu thập giữa chừng chỉ có một phần → MoM của nó và của tháng kế tiếp để trống."""
    end = pd.Period(end_month, "M")
    periods = pd.period_range(end=end, periods=months, freq="M")
    starts = _source_starts(df)
    if starts:
        start_month = min(starts).to_period("M")
        periods = periods[periods >= start_month] if start_month <= end else periods[:0]
    month = df["event_date"].dt.to_period("M")
    table = pd.crosstab(month, df["source"]).reindex(periods, fill_value=0).astype(int)
    table.insert(0, "total", table.sum(axis=1).astype(int))
    table["mom"] = table["total"].pct_change().replace([float("inf")], pd.NA)
    # MoM trống: tháng đầu tiên, tháng có nguồn bắt đầu giữa chừng và tháng ngay sau (so với tháng thiếu một phần)
    if starts:
        blank = {min(starts).to_period("M")} | {m + i for m in _partial_months(df) for i in (0, 1)}
        table.loc[table.index.isin(list(blank)), "mom"] = pd.NA
    table.index = table.index.astype(str)
    return table.rename_axis("month").reset_index()


def heatmap(df: pd.DataFrame, rows: str = "industry", cols: str = "province") -> pd.DataFrame:
    """M06: số tin theo cặp (ngành, tỉnh)."""
    if LISTING_BASED_DIMS & {rows, cols}:
        df = industry_comparable(df)
    df = allocate(allocate(df.drop(columns="weight", errors="ignore"), rows), cols)
    table = pd.crosstab(df[f"{rows}_name"], df[f"{cols}_name"], values=df["weight"], aggfunc="sum").fillna(0)
    table = table.apply(_tidy_counts)
    table["Tổng"] = _tidy_counts(table.sum(axis=1))
    return table.sort_values("Tổng", ascending=False)


def salary_by(df: pd.DataFrame, dim: str, min_n: int) -> pd.DataFrame:
    """S01–S04: TB, trung vị, P25–P75 trên salary_mid (range → trung điểm, fixed → chính nó).

    Nhóm có < min_n tin có lương số → chỉ số lương = "n<5" (BR-07). Luôn kèm số tin + độ phủ.
    Phân vị nội suy tuyến tính (= PERCENTILE/PERCENTILE.INC của Excel trong workbook).
    """
    name = f"{dim}_name"
    rows = []
    # Danh mục/tỉnh: tin vào lương của MỖI danh mục/nơi nó thuộc (cùng cách đếm với màn nhu cầu)
    if dim in MULTI_DIMENSIONS:
        df = allocate(df, dim)
    ordered = df.sort_values([f"{dim}_order", name], kind="stable")
    for group, part in ordered.groupby(name, sort=False):
        s = part["salary_mid"].dropna()
        enough = len(s) >= min_n
        rows.append(
            {
                dim: group,
                "jobs": len(part),
                "salary_n": len(s),
                "coverage": len(s) / len(part),
                "mean": s.mean() if enough else SMALL_SAMPLE,
                "median": s.median() if enough else SMALL_SAMPLE,
                "p25": s.quantile(0.25) if enough else SMALL_SAMPLE,
                "p75": s.quantile(0.75) if enough else SMALL_SAMPLE,
            }
        )
    return pd.DataFrame(rows)


def salary_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cơ cấu loại lương trên toàn tập tin duy nhất."""
    out = df["salary_type"].fillna("not_stated").value_counts().rename_axis("salary_type").reset_index(name="jobs")
    out["share"] = out["jobs"] / len(df) if len(df) else 0
    return out


def trend(df: pd.DataFrame, end_month: date, min_vol: int, threshold: float, dim: str = "industry") -> pd.DataFrame:
    """T01–T02: Σ 3 tháng gần nhất vs Σ 3 tháng liền trước; nhóm chưa phân loại không xét."""
    periods = pd.period_range(end=pd.Period(end_month, "M"), periods=6, freq="M")
    name = f"{dim}_name"
    if dim in LISTING_BASED_DIMS:
        df = industry_comparable(df)
    df = allocate(df, dim)
    data = df[~df[name].isin(set(UNCLASSIFIED.values()) | {UNCLASSIFIED_DEFAULT})]
    table = (
        pd.crosstab(data[name], data["event_date"].dt.to_period("M"), values=data["weight"], aggfunc="sum")
        .reindex(columns=periods, fill_value=0)
        .fillna(0)
    )
    table = table.apply(_tidy_counts)
    table.columns = table.columns.astype(str)
    recent, before = table.iloc[:, 3:].sum(axis=1), table.iloc[:, :3].sum(axis=1)
    table["recent_3m"], table["prev_3m"] = recent, before
    table["delta"] = [_delta(r, p) for r, p in zip(recent, before, strict=True)]
    table["trend"] = [trend_label(r, p, min_vol, threshold) for r, p in zip(recent, before, strict=True)]
    return table.rename_axis(dim).reset_index()


def trend_label(recent: float, prev: float, min_vol: int, threshold: float) -> str:
    if prev < min_vol:
        return "Chưa đủ dữ liệu"
    change = recent / prev - 1
    if change > threshold:
        return "↑ Tăng"
    if change < -threshold:
        return "↓ Giảm"
    return "→ Ổn định"


def months_of_data(df: pd.DataFrame) -> int:
    """Số tháng TRỌN có dữ liệu liên tục tính tới tháng gần nhất (điều kiện mở màn Xu hướng: ≥ 6).

    Tháng có nguồn bắt đầu thu thập giữa chừng không tính (phạm vi thu thập tháng đó không trọn)."""
    if df.empty:
        return 0
    present = set(df["event_date"].dt.to_period("M").dropna()) - _partial_months(df)
    if not present:  # mọi tin chưa rõ ngày đăng (tin tồn sẵn, bộ ảnh chụp)
        return 0
    month, count = max(present), 0
    while month in present:
        count, month = count + 1, month - 1
    return count


def quality(conn: sqlite3.Connection, include_total: bool = False) -> pd.DataFrame:
    """Q01–Q02 theo nguồn: tổng bản ghi, tin duy nhất, % reject, % phủ từng trường, % có lương số."""
    if include_total:
        per_source = quality(conn)
        return pd.concat([per_source, _quality_totals(per_source)], ignore_index=True)
    totals = pd.read_sql_query(
        "SELECT source, COUNT(*) AS records, SUM(dq_status = 'reject') AS rejected FROM job_posts GROUP BY source",
        conn,
    )
    unique = pd.read_sql_query(
        """SELECT source, COUNT(*) AS unique_jobs,
                  AVG(province_code IS NOT NULL) AS pct_location, AVG(industry_code IS NOT NULL) AS pct_industry,
                  AVG(occupation_code IS NOT NULL) AS pct_occupation,
                  AVG(experience_code IS NOT NULL) AS pct_experience, AVG(salary_mid IS NOT NULL) AS pct_salary
           FROM v_jobs_unique GROUP BY source""",
        conn,
    )
    out = totals.merge(unique, on="source", how="left")
    out["pct_reject"] = out["rejected"] / out["records"]
    return out


def _quality_totals(per_source: pd.DataFrame) -> pd.DataFrame:
    """Dòng "(Tất cả)" gộp từ bảng theo nguồn (không đọc lại v_jobs_unique): % phủ = TB có trọng số tin duy nhất."""
    unique = per_source["unique_jobs"].fillna(0)
    n = unique.sum()
    row = {"source": "(Tất cả)", "records": per_source["records"].sum(), "rejected": per_source["rejected"].sum(),
           "unique_jobs": n}  # fmt: skip
    for col in ("pct_location", "pct_industry", "pct_occupation", "pct_experience", "pct_salary"):
        row[col] = (per_source[col].fillna(0) * unique).sum() / n if n else None
    row["pct_reject"] = row["rejected"] / row["records"] if row["records"] else None
    return pd.DataFrame([row])


LABEL_FLAGS = {"location_unmapped", "experience_unparsed"}


def dq_flag_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    """Cờ chất lượng. Cờ phụ thuộc ánh xạ nhãn tính lại theo trạng thái nhãn hiện tại — cờ ghi lúc nạp
    sẽ lỗi thời ngay khi nhãn được ánh xạ sau đó (remap / duyệt trên dashboard)."""
    flags = pd.read_sql_query("SELECT dq_flags FROM job_posts WHERE dq_flags <> ''", conn)["dq_flags"]
    exploded = flags.str.split(",").explode().str.strip()
    counts = exploded[(exploded != "") & ~exploded.isin(LABEL_FLAGS)].value_counts()
    # Giữ cờ đã ghi lúc nạp, chỉ gỡ khi nhãn của tin nay đã mapped (hoặc ignored → tách riêng location_ignored)
    live = conn.execute(
        """SELECT SUM(loc AND COALESCE(lo.status, 'unmapped') = 'unmapped'),
                  SUM(loc AND lo.status = 'ignored'),
                  SUM(exp AND COALESCE(le.status, 'unmapped') = 'unmapped')
           FROM (SELECT location_label_id, experience_label_id,
                        instr(dq_flags, 'location_unmapped') > 0 AS loc,
                        instr(dq_flags, 'experience_unparsed') > 0 AS exp
                 FROM job_posts) p
           LEFT JOIN labels lo ON lo.id = p.location_label_id
           LEFT JOIN labels le ON le.id = p.experience_label_id"""
    ).fetchone()
    for name, value in zip(("location_unmapped", "location_ignored", "experience_unparsed"), live, strict=True):
        if value:
            counts[name] = int(value)
    counts = counts.sort_values(ascending=False)
    return counts.rename_axis("flag").reset_index(name="records")
