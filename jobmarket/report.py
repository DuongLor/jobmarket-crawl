"""Báo cáo tháng (PLAN §B6, tab R5 của mockup) — tính từ stats, chốt thành snapshot không đổi (BR-10).

Báo cáo đã chốt lưu nguyên số liệu (JSON) trong bảng `reports` + file Excel: ánh xạ nhãn đổi sau đó
không làm đổi báo cáo cũ. Muốn số mới → chốt kỳ khác; không ghi đè kỳ đã chốt.
"""

import calendar
import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from jobmarket import stats
from jobmarket.config import Config
from jobmarket.store.db import now_iso

SHEET_NAMES = ["Tóm tắt", "Ngành", "Địa phương", "Xu hướng", "Chất lượng", "Phương pháp"]
METHOD_NOTE = [
    "Đơn vị đếm: số tin tuyển dụng duy nhất (đã bỏ tin lỗi và tin trùng), không phải số người cần tuyển.",
    "Tin mới trong kỳ: theo ngày đăng; thiếu ngày đăng thì dùng ngày hệ thống thấy tin lần đầu.",
    "Lương: mức đăng tuyển quy về VND/tháng, chưa phân biệt Gross/Net. Trung vị chỉ tính tin có lương dạng "
    "khoảng (trung điểm) hoặc một mức; nhóm dưới ngưỡng tin có lương số hiện n<5.",
    "Xu hướng: 3 tháng gần nhất so với 3 tháng liền trước; cần ≥ 6 tháng dữ liệu liên tục.",
    "Báo cáo đã chốt giữ nguyên khi ánh xạ nhãn thay đổi sau đó.",
]


class ReportError(ValueError):
    pass


def _month_bounds(period: str) -> tuple[date, date]:
    year, month = (int(x) for x in period.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _prev_period(period: str) -> str:
    start, _ = _month_bounds(period)
    prev = (pd.Timestamp(start) - pd.offsets.MonthBegin(1)).date()
    return prev.strftime("%Y-%m")


def _month_slice(df: pd.DataFrame, period: str) -> pd.DataFrame:
    return df[df["event_date"].dt.strftime("%Y-%m") == period]


def _change(cur, prev):
    return None if cur is None or not prev else cur / prev - 1


def _native(value):
    """Kiểu pandas/numpy → kiểu JSON (int/float/None)."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _month_indicators(df: pd.DataFrame, period: str, first_seen_month: pd.Series) -> dict:
    part = _month_slice(df, period)
    salaries = part["salary_mid"].dropna()
    return {
        "new_jobs": len(part),
        "new_companies": int((first_seen_month == period).sum()),
        "salary_n": len(salaries),
        "salary_median": float(salaries.median()) if len(salaries) else None,
        "salary_coverage": len(salaries) / len(part) if len(part) else None,
    }


def build(conn: sqlite3.Connection, cfg: Config, period: str) -> dict:
    """Số liệu báo cáo của tháng `period` (YYYY-MM), chưa chốt."""
    start, end = _month_bounds(period)
    df = stats.load_unique(
        conn,
        max_range_ratio=float(cfg.stats.get("max_range_ratio", stats.MAX_RANGE_RATIO)),
        backlog_grace_days=int(cfg.stats.get("backlog_grace_days", stats.BACKLOG_GRACE_DAYS)),
        max_monthly_salary=float(cfg.stats.get("max_monthly_salary", stats.MAX_MONTHLY_SALARY)),
    )
    df = df[df["event_date"] <= pd.Timestamp(end)]
    if _month_slice(df, period).empty:
        raise ReportError(f"Không có tin nào trong kỳ {period}")
    s = cfg.stats
    prev = _prev_period(period)
    with_company = df[df["company_key"] != ""]
    first_seen_month = with_company.groupby("company_key")["event_date"].min().dt.strftime("%Y-%m")
    cur_ind, prev_ind = (_month_indicators(df, p, first_seen_month) for p in (period, prev))

    indicators = [
        {"name": name, "current": cur_ind[key], "previous": prev_ind[key],
         "change": _change(cur_ind[key], prev_ind[key]) if key != "salary_coverage" else None}
        for key, name in (
            ("new_jobs", "Tin tuyển dụng mới"),
            ("new_companies", "Công ty mới xuất hiện"),
            ("salary_n", "Số tin có lương số (n)"),
            ("salary_median", "Lương trung vị (VND/tháng)"),
            ("salary_coverage", "Tỷ lệ tin có lương số"),
        )
    ]  # fmt: skip

    month_df, prev_df = _month_slice(df, period), _month_slice(df, prev)
    prev_counts = prev_df["industry_name"].value_counts()
    industries = month_df[month_df["industry_code"].notna()]["industry_name"].value_counts().head(5)
    top_industries = [
        {"rank": i + 1, "industry": name, "jobs": int(n), "share": n / len(month_df),
         "delta_prev_month": _change(int(n), int(prev_counts.get(name, 0)))}
        for i, (name, n) in enumerate(industries.items())
    ]  # fmt: skip
    provinces = month_df[month_df["province_code"].notna()]["province_name"].value_counts().head(5)
    top_provinces = [
        {"rank": i + 1, "province": name, "jobs": int(n), "share": n / len(month_df)}
        for i, (name, n) in enumerate(provinces.items())
    ]

    trend_rows: list[dict] = []
    months = stats.months_of_data(df)
    if months >= 6:
        t = stats.trend(df, start, int(s["min_vol_trend"]), float(s["trend_threshold"]))
        trend_rows = [
            {k: _native(v) for k, v in row.items()}
            for row in t[["industry", "recent_3m", "prev_3m", "delta", "trend"]].to_dict("records")
        ]

    quality = stats.quality(conn, include_total=True)
    payload = {
        "period": period,
        "as_of": end.isoformat(),
        "generated_at": now_iso(),
        "indicators": indicators,
        "top_industries": top_industries,
        "top_provinces": top_provinces,
        "trend": trend_rows,
        "months_of_data": months,
        "quality": [{k: _native(v) for k, v in r.items()} for r in quality.to_dict("records")],
        "sources": sorted(df["source"].unique().tolist()),
    }
    payload["insights"] = insights(payload, int(s["min_n_salary"]))
    return json.loads(json.dumps(payload, default=_native))


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%".replace(".", ",")


def insights(p: dict, min_n: int) -> list[str]:
    """Nhận định tự động bằng câu mẫu từ số liệu — cần đọc lại trước khi dùng."""
    ind = {i["name"]: i for i in p["indicators"]}
    month = f"{p['period'][5:]}/{p['period'][:4]}"
    new = ind["Tin tuyển dụng mới"]
    lines = [
        f"Tháng {month} ghi nhận {new['current']} tin mới ({_pct(new['change'])} so với tháng trước), "
        f"{ind['Công ty mới xuất hiện']['current']} công ty lần đầu xuất hiện."
    ]
    if p["top_industries"]:
        top = p["top_industries"][0]
        lines.append(f"Ngành nhiều tin nhất: {top['industry']} ({top['jobs']} tin, "
                     f"{top['share'] * 100:.1f}% tổng tin trong tháng).".replace(".", ",", 1))  # fmt: skip
    rising = [t for t in p["trend"] if t["trend"] == "↑ Tăng"]
    if rising:
        best = max(rising, key=lambda t: t["delta"])
        lines.append(
            f"Ngành tăng mạnh nhất (3 tháng so với 3 tháng trước): {best['industry']} ({_pct(best['delta'])})."
        )
    elif p["months_of_data"] < 6:
        lines.append(f"Chưa đủ dữ liệu kết luận xu hướng ({p['months_of_data']}/6 tháng liên tục).")
    salary = ind["Lương trung vị (VND/tháng)"]
    n = ind["Số tin có lương số (n)"]["current"]
    if salary["current"] is not None and n >= min_n:
        lines.append(f"Lương trung vị của tin có lương số: {salary['current'] / 1e6:.1f} triệu VND/tháng (n={n}); "
                     "chỉ tính tin có lương dạng khoảng hoặc một mức.".replace(".", ",", 1))  # fmt: skip
    if p["top_provinces"]:
        lines.append("Địa phương dẫn đầu: " + ", ".join(x["province"] for x in p["top_provinces"][:3]) + ".")
    return lines


def write_excel(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = pd.DataFrame(
        [["Kỳ báo cáo", payload["period"]], ["Ngày chốt số liệu", payload["as_of"]],
         ["Nguồn", ", ".join(payload["sources"])], ["Sinh lúc", payload["generated_at"]]],
        columns=["", "Giá trị"],
    )  # fmt: skip
    ind = pd.DataFrame(payload["indicators"]).rename(
        columns={"name": "Chỉ số", "current": "Kỳ này", "previous": "Kỳ trước", "change": "Thay đổi"}
    )
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        head.to_excel(xw, sheet_name=SHEET_NAMES[0], index=False)
        ind.to_excel(xw, sheet_name=SHEET_NAMES[0], index=False, startrow=len(head) + 2)
        pd.DataFrame({"Nhận định tự động (đọc lại trước khi dùng)": payload["insights"]}).to_excel(
            xw, sheet_name=SHEET_NAMES[0], index=False, startrow=len(head) + len(ind) + 5
        )
        pd.DataFrame(payload["top_industries"]).rename(
            columns={"rank": "Hạng", "industry": "Ngành", "jobs": "Số tin", "share": "Tỷ trọng",
                     "delta_prev_month": "Δ so tháng trước"}
        ).to_excel(xw, sheet_name=SHEET_NAMES[1], index=False)  # fmt: skip
        pd.DataFrame(payload["top_provinces"]).rename(
            columns={"rank": "Hạng", "province": "Địa phương", "jobs": "Số tin", "share": "Tỷ trọng"}
        ).to_excel(xw, sheet_name=SHEET_NAMES[2], index=False)
        trend = pd.DataFrame(payload["trend"]) if payload["trend"] else pd.DataFrame(
            {"Ghi chú": [f"Chưa đủ dữ liệu ({payload['months_of_data']}/6 tháng liên tục)."]})  # fmt: skip
        trend.to_excel(xw, sheet_name=SHEET_NAMES[3], index=False)
        pd.DataFrame(payload["quality"]).to_excel(xw, sheet_name=SHEET_NAMES[4], index=False)
        pd.DataFrame({"Phương pháp & giới hạn": METHOD_NOTE}).to_excel(xw, sheet_name=SHEET_NAMES[5], index=False)
    return path


def published(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT period, as_of_date, created_at, xlsx_path FROM reports ORDER BY period DESC", conn)


def load_published(conn: sqlite3.Connection, period: str) -> dict | None:
    row = conn.execute("SELECT payload FROM reports WHERE period = ?", (period,)).fetchone()
    return json.loads(row[0]) if row else None


def publish(conn: sqlite3.Connection, cfg: Config, period: str) -> tuple[dict, Path]:
    """Chốt báo cáo: lưu snapshot + Excel. Kỳ đã chốt → ReportError (không ghi đè, BR-10)."""
    if load_published(conn, period):
        raise ReportError(f"Kỳ {period} đã chốt — báo cáo đã chốt không bị ghi đè")
    payload = build(conn, cfg, period)
    path = write_excel(payload, cfg.path("data_dir") / "reports" / f"bao_cao_{period}.xlsx")
    with conn:
        conn.execute(
            "INSERT INTO reports (period, as_of_date, payload, xlsx_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (period, payload["as_of"], json.dumps(payload, ensure_ascii=False), str(path), now_iso()),
        )
    return payload, path


def periods_available(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', event_date) p FROM v_jobs_unique "
        "WHERE event_date IS NOT NULL ORDER BY p DESC"
    ).fetchall()
    return [r[0] for r in rows]
