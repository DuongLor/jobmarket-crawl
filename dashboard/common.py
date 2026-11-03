"""Phần dùng chung của dashboard: kết nối DB, bộ lọc chung, định dạng, chân trang (PLAN §B6, BR-09)."""

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import stats  # noqa: E402
from jobmarket.config import Config, load_config  # noqa: E402
from jobmarket.store.db import init_db  # noqa: E402

PERIODS = {
    "30 ngày gần nhất": 30,
    "90 ngày gần nhất": 90,
    "6 tháng gần nhất": 183,
    "12 tháng gần nhất": 365,
    "Toàn bộ": None,
}
DEFAULT_PERIOD = "6 tháng gần nhất"
SOURCE_NAMES = {"topcv": "TopCV", "vietnamworks": "VietnamWorks", "careerlink": "CareerLink", "vietjobs": "VietJobs"}
# Bộ dữ liệu lịch sử (ảnh chụp một lần, không có ngày đăng từng tin) — KHÔNG trộn với dữ liệu crawl hằng ngày
HISTORICAL_SOURCES = {"vietjobs"}
DATASETS = {"current": "Thị trường hiện tại (crawl hằng ngày)", "history": "Lịch sử 07–10/2025 (VietJobs)"}
LOW_COVERAGE = 0.2
UNIT_NOTE = "Đơn vị: số tin tuyển dụng duy nhất, không phải số người cần tuyển."


@dataclass
class Context:
    cfg: Config
    conn: object
    df_all: pd.DataFrame
    df: pd.DataFrame
    as_of: date | None
    filters: stats.Filters
    masters: dict[str, dict[str, str]]
    dataset: str = "current"

    @property
    def historical(self) -> bool:
        return self.dataset == "history"


DATA_VERSION_SQL = """SELECT (SELECT MAX(id) FROM job_posts), (SELECT MAX(last_seen_at) FROM job_posts),
    (SELECT COUNT(*) FROM job_posts WHERE status = 'expired'), (SELECT COUNT(*) FROM label_history),
    (SELECT COUNT(*) FROM master_industries) + (SELECT COUNT(*) FROM master_occupations)"""


@st.cache_data(show_spinner="Đang nạp dữ liệu…", max_entries=4)
def _load_unique_cached(db_file: str, version: tuple, max_range_ratio: float, backlog_grace_days: int) -> pd.DataFrame:
    """Nạp lại chỉ khi dữ liệu đổi: tin mới/thấy lại, tin hết hạn, đổi ánh xạ (luôn ghi label_history),
    thêm tên chuẩn — hoặc khi đổi tham số hiệu chỉnh trong config."""
    return stats.load_unique(
        init_db(Path(db_file)), max_range_ratio=max_range_ratio, backlog_grace_days=backlog_grace_days
    )


def available_datasets(sources) -> list[str]:
    sources = set(sources)
    found = []
    if sources - HISTORICAL_SOURCES:
        found.append("current")
    if sources & HISTORICAL_SOURCES:
        found.append("history")
    return found or ["current"]


def dataset_scope(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    historical = df["source"].isin(HISTORICAL_SOURCES)
    return df[historical] if dataset == "history" else df[~historical]


def load_context() -> Context:
    cfg = load_config()
    conn = init_db(cfg.path("db_file"))
    version = tuple(conn.execute(DATA_VERSION_SQL).fetchone())
    everything = _load_unique_cached(
        str(cfg.path("db_file")),
        version,
        float(cfg.stats.get("max_range_ratio", stats.MAX_RANGE_RATIO)),
        int(cfg.stats.get("backlog_grace_days", stats.BACKLOG_GRACE_DAYS)),
    )
    datasets = available_datasets(everything["source"].unique()) if not everything.empty else ["current"]
    dataset = st.session_state.get("f_dataset", datasets[0])
    if dataset not in datasets:
        dataset = datasets[0]
    # Mọi màn chỉ thấy bộ dữ liệu đang chọn → không trộn ảnh chụp 2025 với dữ liệu crawl hằng ngày
    df_all = dataset_scope(everything, dataset)
    seen = pd.to_datetime(df_all["last_seen_at"], errors="coerce").max() if not df_all.empty else None
    as_of = seen.date() if seen is not None and not pd.isna(seen) else stats.as_of_date(conn)
    masters = {
        dim: dict(conn.execute(f"SELECT code, name FROM {table} ORDER BY {order or 'rowid'}").fetchall())
        for dim, (_, table, order) in stats.DIMENSIONS.items()
    }
    filters = current_filters(as_of, historical=dataset == "history")
    return Context(cfg, conn, df_all, stats.apply_filters(df_all, filters), as_of, filters, masters, dataset)


def seen_days(ctx: "Context") -> int:
    return int(ctx.cfg.stats.get("active_seen_days", stats.ACTIVE_SEEN_DAYS))


def current_filters(as_of: date | None, historical: bool = False) -> stats.Filters:
    s = st.session_state
    # Bộ lịch sử là ảnh chụp một lần (mọi tin cùng ngày) → bỏ lọc thời gian
    days = None if historical else PERIODS.get(s.get("f_period", DEFAULT_PERIOD))
    return stats.Filters(
        date_from=(as_of - timedelta(days=days)) if (days and as_of) else None,
        date_to=as_of,
        sources=list(s.get("f_sources", [])),
        industries=list(s.get("f_industries", [])),
        provinces=list(s.get("f_provinces", [])),
        experiences=list(s.get("f_experiences", [])),
        levels=list(s.get("f_levels", [])),
    )


def _reset_filters() -> None:
    st.session_state["f_period"] = DEFAULT_PERIOD
    for key in ("f_sources", "f_industries", "f_provinces", "f_experiences", "f_levels"):
        st.session_state[key] = []


def render_filters(ctx_masters: dict[str, dict[str, str]], sources: list[str]) -> None:
    """Bộ lọc dùng chung ở sidebar — giữ nguyên khi chuyển màn (gọi từ app.py)."""
    sb = st.sidebar
    datasets = available_datasets(sources)
    if st.session_state.get("f_dataset") not in datasets:
        st.session_state["f_dataset"] = datasets[0]
    sb.radio("Bộ dữ liệu", datasets, key="f_dataset", format_func=DATASETS.get,
             help="Hai bộ khác bản chất (thời gian, trường có sẵn) nên xem riêng, không cộng gộp.")  # fmt: skip
    historical = st.session_state["f_dataset"] == "history"
    scoped = [s for s in sources if (s in HISTORICAL_SOURCES) == historical]
    sb.markdown("**Bộ lọc phân tích**")
    sb.selectbox("Khoảng thời gian", list(PERIODS), key="f_period", index=list(PERIODS).index(DEFAULT_PERIOD),
                 disabled=historical,
                 help="Bộ lịch sử là ảnh chụp một lần — không lọc theo thời gian." if historical else None)  # fmt: skip
    if st.session_state.get("f_sources"):
        st.session_state["f_sources"] = [s for s in st.session_state["f_sources"] if s in scoped]
    sb.multiselect("Nguồn", scoped, key="f_sources", format_func=lambda s: SOURCE_NAMES.get(s, s),
                   placeholder="Tất cả nguồn")  # fmt: skip
    for key, dim, label in (
        ("f_industries", "industry", "Ngành"),
        ("f_provinces", "province", "Địa phương"),
        ("f_experiences", "experience", "Kinh nghiệm"),
        ("f_levels", "level", "Cấp bậc"),
    ):
        options = ctx_masters[dim]
        sb.multiselect(label, list(options), key=key, format_func=lambda c, o=options: o.get(c, c),
                       placeholder=f"Tất cả {label.lower()}")  # fmt: skip
    sb.button("Đặt lại bộ lọc", on_click=_reset_filters, width="stretch")


def filter_summary(ctx: Context) -> str:
    f = ctx.filters
    parts = [DATASETS[ctx.dataset]]
    if not ctx.historical:
        parts.append(st.session_state.get("f_period", DEFAULT_PERIOD))
    for values, dim in (
        (f.industries, "industry"), (f.provinces, "province"), (f.experiences, "experience"), (f.levels, "level")
    ):  # fmt: skip
        if values:
            parts.append(", ".join(ctx.masters[dim].get(v, v) for v in values))
    if f.sources:
        parts.append(", ".join(SOURCE_NAMES.get(s, s) for s in f.sources))
    return " · ".join(parts)


def page_header(ctx: Context, code: str, title: str, subtitle: str) -> None:
    st.caption(f"{code} · Dữ liệu chốt: {fmt_date(ctx.as_of)} · Đang xem: {filter_summary(ctx)}")
    st.title(title)
    st.write(subtitle)
    if not ctx.historical and len(ctx.df):
        unknown = int((~ctx.df["date_known"]).sum())
        wide = int((ctx.df["salary_excluded"] == "wide_range").sum())
        notes = []
        if unknown:
            notes.append(
                f"**{fmt_int(unknown)} tin chưa rõ ngày đăng** (đã có sẵn khi bắt đầu thu thập) — vẫn tính vào phân "
                "bố ngành/địa phương/lương, nhưng **không** tính là tin mới hay vào xu hướng; ngày đăng được bổ sung "
                "dần từ trang chi tiết."
            )
        if wide:
            notes.append(
                f"**{fmt_int(wide)} tin** có khoảng lương rộng bất thường (vd lương cứng + hoa hồng) "
                "không đưa vào thống kê lương."
            )
        if notes:
            st.caption(" · ".join(notes))
    hidden = hidden_by_period(ctx)
    if hidden:
        st.info(
            f"Có **{fmt_int(hidden)} tin** nằm ngoài khoảng thời gian đang xem. "
            + "Chọn **Khoảng thời gian → Toàn bộ** ở thanh bên để xem tất cả."
        )


def coverage(ctx: Context, dim: str) -> float:
    code_col = stats.DIMENSIONS[dim][0]
    return float(ctx.df[code_col].notna().mean()) if len(ctx.df) else 0.0


def dimension_missing(ctx: Context, dim: str, label: str) -> bool:
    """True (và ghi chú) nếu nguồn trong bộ dữ liệu gần như không cung cấp chiều này.

    Thay vì vẽ biểu đồ 100% "(chưa phân loại)".
    """
    cov = coverage(ctx, dim)
    if cov >= LOW_COVERAGE:
        return False
    srcs = ", ".join(SOURCE_NAMES.get(s, s) for s in sorted(ctx.df["source"].unique()))
    st.info(f"Nguồn đang xem ({srcs}) không cung cấp **{label}** (chỉ {fmt_pct(cov)} tin có) — chưa phân tích được "
            "theo chiều này. Xem bộ dữ liệu khác ở thanh bên, hoặc chờ thêm dữ liệu.")  # fmt: skip
    return True


def snapshot_note(what: str) -> None:
    st.info(f"Bộ lịch sử VietJobs là **ảnh chụp 07–10/2025** (không có ngày đăng từng tin) — không tính được {what}.")


def has_dates(ctx: Context) -> bool:
    """Bộ đang xem có tin nào biết ngày đăng không — không có thì chỉ số theo thời gian là "—", không phải 0."""
    return bool(len(ctx.df)) and bool(ctx.df["date_known"].any())


def window_comparable(ctx: Context, days: int) -> bool:
    """So cửa sổ N ngày với N ngày liền trước chỉ có nghĩa khi cửa sổ trước nằm trọn sau ngày bắt đầu thu thập.

    Trước đó nguồn chỉ còn hiện tin chưa hết hạn → kỳ trước luôn thiếu, Δ% bị thổi phồng (vd +14.000%).
    """
    if ctx.df_all.empty:
        return False
    started = pd.to_datetime(ctx.df_all["first_seen_at"]).min().date()
    return ctx.as_of - timedelta(days=2 * days - 1) >= started


def no_dates_note(what: str) -> None:
    st.info(
        f"Chưa có tin nào rõ ngày đăng nên chưa tính được {what}. Tin có sẵn lúc bắt đầu thu thập không biết ngày "
        "đăng; số liệu theo thời gian bắt đầu từ các tin xuất hiện ở những lần chạy sau, hoặc khi `enrich` bổ sung "
        "ngày đăng từ trang chi tiết."
    )


def hidden_by_period(ctx: Context) -> int:
    """Số tin khớp các bộ lọc khác nhưng bị loại chỉ vì khoảng thời gian."""
    if ctx.filters.date_from is None or ctx.df_all.empty:
        return 0
    from dataclasses import replace

    unbounded = stats.apply_filters(ctx.df_all, replace(ctx.filters, date_from=None, date_to=None))
    return max(0, len(unbounded) - len(ctx.df))


def empty_state(ctx: Context) -> bool:
    """True nếu không có dữ liệu để hiển thị (và đã vẽ thông báo)."""
    if ctx.df_all.empty:
        st.info(
            "Chưa có dữ liệu. Nạp trang TopCV bạn tự lưu: `python -m jobmarket import-html`, "
            "hoặc xem thử bằng dữ liệu mẫu: `python -m jobmarket demo` rồi `python -m jobmarket dashboard --demo`."
        )
        return True
    if ctx.df.empty:
        st.warning("Không có tin nào khớp bộ lọc hiện tại. Thử nới khoảng thời gian hoặc bấm “Đặt lại bộ lọc”.")
        return True
    return False


def footer(ctx: Context) -> None:
    sources = sorted(ctx.df_all["source"].unique()) if not ctx.df_all.empty else []
    st.divider()
    st.caption(
        f"Nguồn: {', '.join(SOURCE_NAMES.get(s, s) for s in sources) or '—'} · "
        f"Ngày chốt số liệu: {fmt_date(ctx.as_of)} · {UNIT_NOTE} "
        "Lương là mức đăng tuyển, quy về VND/tháng, chưa phân biệt Gross/Net."
    )


def csv_button(df: pd.DataFrame, name: str, key: str) -> None:
    st.download_button(
        "↓ Xuất CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{name}.csv",
        mime="text/csv",
        key=key,
    )


def fmt_int(value) -> str:
    """12345 → "12.345" (dấu chấm phân tách hàng nghìn, chỉ định dạng con số)."""
    return f"{int(value):,}".replace(",", ".")


def fmt_date(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "—"


def fmt_million(value) -> str:
    if value is None or isinstance(value, str) or pd.isna(value):
        return value if isinstance(value, str) else "—"
    return f"{value / 1_000_000:,.1f}".replace(",", " ").replace(".", ",") + " triệu"


def fmt_pct(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value * 100:.1f}%".replace(".", ",")


def fmt_delta(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return ("+" if value > 0 else "") + fmt_pct(value)


def to_millions(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Cột lương → chuỗi triệu VND "9,21" (giữ nguyên 'n<5'); cột toàn chuỗi để bảng không lẫn kiểu."""
    out = df.copy()
    for col in cols:
        out[col] = [
            v if isinstance(v, str) else "—" if pd.isna(v) else f"{v / 1_000_000:.2f}".replace(".", ",")
            for v in out[col]
        ]
    return out


@st.dialog("Phương pháp & giới hạn dữ liệu", width="large")
def method_dialog() -> None:
    st.markdown(
        """
- **Tin duy nhất**: bỏ tin lỗi (reject: thiếu mã tin, tiêu đề hoặc công ty) và tin trùng
  (cùng công ty + tiêu đề + tỉnh, giữ tin thấy sớm nhất). Mọi số liệu tính trên tập này.
- **Tin mới (dòng chảy)** đếm theo ngày đăng; thiếu ngày đăng thì dùng ngày hệ thống thấy tin lần đầu.
  CareerLink: ngày trên thẻ tin là ngày **đăng/cập nhật** (tin đăng lại được làm mới ngày) — trang chi tiết có
  `datePosted` thì ưu tiên. Δ so kỳ trước chỉ hiện khi kỳ trước nằm trọn sau ngày bắt đầu thu thập.
  **Đang tuyển (tồn)** = trạng thái đang tuyển và chưa quá hạn tại ngày chốt.
- **Lương đại diện**: khoảng → trung điểm; một mức → chính nó. “Tới X”, “từ X”, thoả thuận, không ghi
  → không đưa vào trung bình/trung vị. Nhóm dưới 5 tin có lương số hiện **n<5**.
- **Xu hướng**: 3 tháng gần nhất so với 3 tháng liền trước; lệch quá 10% là tăng/giảm;
  3 tháng trước dưới 10 tin → “Chưa đủ dữ liệu”. Cần ≥ 6 tháng dữ liệu liên tục.
- **Thiên lệch giai đoạn đầu**: trước ngày bắt đầu thu thập chỉ thấy tin chưa hết hạn → các tháng đầu bị thiếu.
- **Tin tồn sẵn**: tin đã có khi bắt đầu thu thập mà chưa biết ngày đăng → vẫn tính vào phân bố ngành/địa phương/
  lương, **không** tính là tin mới / theo tháng / xu hướng. Ngày đăng thật được bổ sung dần từ trang chi tiết.
- **Khoảng lương rộng bất thường** (đầu trên > 5 lần đầu dưới, kiểu "lương cứng + hoa hồng lên tới") → không đưa
  vào trung bình/trung vị (ngưỡng `max_range_ratio`).
- **Công ty**: gộp tên chỉ khác cách viết loại hình (CP = Cổ phần = JSC); khác loại hình (TNHH ≠ CP) không gộp.
- **Hai bộ dữ liệu** (crawl hằng ngày / ảnh chụp VietJobs 2025) xem riêng, không cộng gộp.
- **TopCV** chỉ lấy từ trang danh sách (mẫu, không phải toàn bộ thị trường): không có cấp bậc, hạn nộp, mô tả.
- Ngưỡng (5 tin, 10 tin, 10%) chỉnh trong `config.toml`.
"""
    )
