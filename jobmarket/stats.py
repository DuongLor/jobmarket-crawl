"""Mọi công thức thống kê ở MỘT chỗ — dashboard và báo cáo chỉ gọi vào đây.

Mã công thức (M01…, S01…, T01…, Q01…) theo tab Formulas của docs/mockup.xlsx.
Mọi số liệu đọc từ view v_jobs_unique (BR-04); ngưỡng đọc từ config [stats] (BR-11).
"""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from jobmarket.dedup import company_core
from jobmarket.normalize.text import norm_key

UNCLASSIFIED = {"industry": "(chưa phân loại)", "province": "(chưa xác định)"}
UNCLASSIFIED_DEFAULT = "(chưa phân loại)"
SMALL_SAMPLE = "n<5"

# dimension → (cột mã trong view, bảng danh mục, cột thứ tự)
DIMENSIONS = {
    "industry": ("industry_code", "master_industries", "sort_order"),
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
BACKLOG_GRACE_DAYS = 0


def load_unique(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    max_range_ratio: float | None = MAX_RANGE_RATIO,
    backlog_grace_days: int = BACKLOG_GRACE_DAYS,
) -> pd.DataFrame:
    """Tin duy nhất kèm tên chuẩn; mã chưa ánh xạ → tên nhóm "(chưa phân loại)".

    Hai hiệu chỉnh để thống kê đúng (cấu hình [stats] max_range_ratio, backlog_grace_days):
    - Tin TỒN SẴN khi bắt đầu thu thập (chưa có ngày đăng, thấy lần đầu trong ngày đầu tiên nguồn được thu
      thập): ngày đăng thật không biết → event_date = NaT, không tính vào tin mới / theo tháng / xu hướng.
      Tin xuất hiện sau đó: ngày thấy lần đầu ≈ ngày đăng (thu thập hằng ngày).
    - Khoảng lương rộng bất thường (đầu trên > max_range_ratio × đầu dưới, thường "lương cứng + hoa hồng lên tới"):
      trung điểm không đại diện → không đưa vào thống kê lương (như dạng "tới X").
    """
    df = pd.read_sql_query("SELECT * FROM v_jobs_unique ORDER BY first_seen_at, id", conn)
    for col in ("event_date", "posted_date", "deadline"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    seen_day = pd.to_datetime(df["first_seen_at"], errors="coerce").dt.normalize()
    source_start = seen_day.groupby(df["source"]).transform("min")
    backlog = df["posted_date"].isna() & (seen_day <= source_start + pd.Timedelta(days=int(backlog_grace_days)))
    df["date_known"] = ~backlog
    df.loc[backlog, "event_date"] = pd.NaT
    df["salary_excluded"] = None
    if max_range_ratio:
        wide = (df["salary_type"] == "range") & (df["salary_to"] > df["salary_from"] * float(max_range_ratio))
        df.loc[wide, "salary_mid"] = None
        df.loc[wide, "salary_excluded"] = "wide_range"
    for dim, (code_col, table, order_col) in DIMENSIONS.items():
        order = order_col or "rowid"
        rows = conn.execute(f"SELECT code, name, {order} FROM {table}").fetchall()
        df[f"{dim}_name"] = (
            df[code_col].map({r[0]: r[1] for r in rows}).fillna(UNCLASSIFIED.get(dim, UNCLASSIFIED_DEFAULT))
        )
        # Thứ tự danh mục: phá hoà khi xếp hạng, sắp bảng theo thứ tự chuẩn; chưa phân loại xếp cuối
        df[f"{dim}_order"] = df[code_col].map({r[0]: r[2] for r in rows}).fillna(10_000)
    df["company_key"] = df["company_name"].fillna("").map(company_identity)
    return apply_filters(df, filters) if filters else df


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
    for values, col in (
        (f.sources, "source"),
        (f.industries, "industry_code"),
        (f.provinces, "province_code"),
        (f.experiences, "experience_code"),
        (f.levels, "level_code"),
    ):
        if values:
            mask &= df[col].isin(values)
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
    còn thấy tin trong `seen_days` ngày tính tới ngày chốt.
    """
    not_expired = df["deadline"].isna() | (df["deadline"] >= pd.Timestamp(as_of))
    recently_seen = pd.to_datetime(df["last_seen_at"], errors="coerce") >= pd.Timestamp(as_of) - timedelta(
        days=seen_days
    )
    return ((df["status"] == "active") & not_expired) | ((df["status"] == "unknown") & recently_seen & not_expired)


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
    """M01, M02, M05 theo 1 chiều. Nhóm chưa phân loại vẫn tính vào tổng, không xếp hạng."""
    name = f"{dim}_name"
    cur, prev = _windows(as_of, window_days)
    work = df.assign(
        active=is_active(df, as_of, seen_days), new=_in(df["event_date"], cur), prev=_in(df["event_date"], prev)
    )
    out = (
        work.groupby([name, f"{dim}_order"])
        .agg(total=("id", "size"), active=("active", "sum"), new=("new", "sum"), prev=("prev", "sum"))
        .reset_index()
    )
    out["share"] = out["total"] / out["total"].sum() if len(df) else 0
    out["delta"] = [_delta(n, p) for n, p in zip(out["new"], out["prev"], strict=True)]
    unclassified = out[name].isin(set(UNCLASSIFIED.values()) | {UNCLASSIFIED_DEFAULT})
    # Hạng duy nhất: nhiều tin hơn đứng trước; bằng nhau thì theo thứ tự danh mục (khớp mockup)
    ranked = out[~unclassified].sort_values(["total", f"{dim}_order"], ascending=[False, True])
    ranked["rank"] = range(1, len(ranked) + 1)
    rest = out[unclassified].assign(rank=pd.NA)
    result = pd.concat([ranked, rest], ignore_index=True).drop(columns=f"{dim}_order")
    return result.rename(columns={name: dim})


def monthly(df: pd.DataFrame, end_month: date, months: int = 6) -> pd.DataFrame:
    """M04: tin mới theo tháng (theo event_date), tách nguồn, kèm MoM."""
    periods = pd.period_range(end=pd.Period(end_month, "M"), periods=months, freq="M")
    month = df["event_date"].dt.to_period("M")
    table = pd.crosstab(month, df["source"]).reindex(periods, fill_value=0).astype(int)
    table.insert(0, "total", table.sum(axis=1).astype(int))
    table["mom"] = table["total"].pct_change().replace([float("inf")], pd.NA)
    table.index = table.index.astype(str)
    return table.rename_axis("month").reset_index()


def heatmap(df: pd.DataFrame, rows: str = "industry", cols: str = "province") -> pd.DataFrame:
    """M06: số tin theo cặp (ngành, tỉnh)."""
    table = pd.crosstab(df[f"{rows}_name"], df[f"{cols}_name"])
    table["Tổng"] = table.sum(axis=1)
    return table.sort_values("Tổng", ascending=False)


def salary_by(df: pd.DataFrame, dim: str, min_n: int) -> pd.DataFrame:
    """S01–S04: TB, trung vị, P25–P75 trên salary_mid (range → trung điểm, fixed → chính nó).

    Nhóm có < min_n tin có lương số → chỉ số lương = "n<5" (BR-07). Luôn kèm số tin + độ phủ.
    Phân vị nội suy tuyến tính (= PERCENTILE/PERCENTILE.INC của Excel trong workbook).
    """
    name = f"{dim}_name"
    rows = []
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
    data = df[~df[name].isin(set(UNCLASSIFIED.values()) | {UNCLASSIFIED_DEFAULT})]
    table = pd.crosstab(data[name], data["event_date"].dt.to_period("M")).reindex(columns=periods, fill_value=0)
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
    """Số tháng có dữ liệu liên tục tính tới tháng gần nhất (điều kiện mở màn Xu hướng: ≥ 6)."""
    if df.empty:
        return 0
    present = set(df["event_date"].dt.to_period("M").dropna())
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
        total = _quality_totals(conn)
        return pd.concat([per_source, total], ignore_index=True)
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


def _quality_totals(conn: sqlite3.Connection) -> pd.DataFrame:
    row = conn.execute(
        """SELECT (SELECT COUNT(*) FROM job_posts), (SELECT SUM(dq_status = 'reject') FROM job_posts),
                  COUNT(*), AVG(province_code IS NOT NULL), AVG(industry_code IS NOT NULL),
                  AVG(occupation_code IS NOT NULL), AVG(experience_code IS NOT NULL), AVG(salary_mid IS NOT NULL)
           FROM v_jobs_unique"""
    ).fetchone()
    cols = ["records", "rejected", "unique_jobs", "pct_location", "pct_industry", "pct_occupation",
            "pct_experience", "pct_salary"]  # fmt: skip
    out = pd.DataFrame([dict(zip(cols, row, strict=True))]).assign(source="(Tất cả)")
    out["pct_reject"] = out["rejected"] / out["records"] if row[0] else None
    return out


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
