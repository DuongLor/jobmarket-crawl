"""05 · Báo cáo tháng: xem bản nháp, chốt (snapshot không đổi), tải Excel."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import report  # noqa: E402

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ctx = c.load_context()
c.page_header(ctx, "05", "Báo cáo tháng",
              "Bản nháp tính từ dữ liệu hiện tại; khi chốt, số liệu được lưu cố định và xuất Excel.")  # fmt: skip


def show(payload: dict) -> None:
    st.caption(
        f"Kỳ {payload['period']} · Ngày chốt số liệu {payload['as_of']} · Nguồn: {', '.join(payload['sources'])}"
    )
    ind = pd.DataFrame(payload["indicators"])
    money = ind["name"].str.contains("Lương trung vị")
    ratio = ind["name"].str.contains("Tỷ lệ")
    for col in ("current", "previous"):
        ind[col] = [c.fmt_million(v) if m else c.fmt_pct(v) if r else v
                    for v, m, r in zip(ind[col], money, ratio, strict=True)]  # fmt: skip
    ind["change"] = [c.fmt_delta(v) for v in ind["change"]]
    st.dataframe(ind.rename(columns={"name": "Chỉ số", "current": "Kỳ này", "previous": "Kỳ trước",
                                     "change": "Thay đổi"}), hide_index=True, width="stretch")  # fmt: skip
    left, right = st.columns(2)
    with left:
        st.subheader("Top 5 ngành trong kỳ")
        t = pd.DataFrame(payload["top_industries"])
        if not t.empty:
            t = t.assign(
                share=[c.fmt_pct(v) for v in t["share"]],
                delta_prev_month=[c.fmt_delta(v) for v in t["delta_prev_month"]],
            )
        st.dataframe(t.rename(columns={"rank": "Hạng", "industry": "Ngành", "jobs": "Số tin", "share": "Tỷ trọng",
                                       "delta_prev_month": "Δ so tháng trước"}),
                     hide_index=True, width="stretch")  # fmt: skip
    with right:
        st.subheader("Top 5 địa phương trong kỳ")
        t = pd.DataFrame(payload["top_provinces"])
        if not t.empty:
            t = t.assign(share=[c.fmt_pct(v) for v in t["share"]])
        names = {"rank": "Hạng", "province": "Địa phương", "jobs": "Số tin", "share": "Tỷ trọng"}
        st.dataframe(t.rename(columns=names),
                     hide_index=True, width="stretch")  # fmt: skip
    st.subheader("Nhận định tự động")
    st.caption("Câu sinh từ công thức — đọc lại trước khi dùng.")
    st.markdown("\n".join(f"- {line}" for line in payload["insights"]))


@st.dialog("Chốt báo cáo tháng?")
def confirm_publish(period: str) -> None:
    st.write(f"Chốt kỳ **{period}**: số liệu được lưu cố định, ánh xạ nhãn đổi sau đó không làm đổi báo cáo này. "
             "Không chốt lại được cùng kỳ.")  # fmt: skip
    if st.button("Xác nhận chốt", type="primary"):
        try:
            _, path = report.publish(ctx.conn, ctx.cfg, period)
            st.session_state["report_toast"] = f"Đã chốt {period} → {path.name}"
            st.rerun()
        except report.ReportError as exc:
            st.error(str(exc))


if not c.empty_state(ctx):
    if msg := st.session_state.pop("report_toast", None):
        st.toast(msg)
    # Kỳ lấy từ tin rõ ngày đăng của bộ đang xem (khớp report.build) — SQL view còn tính tin tồn sẵn theo ngày
    # thấy lần đầu, sẽ đưa ra kỳ không có tin nào.
    known = ctx.df_all["event_date"].dropna()
    periods = sorted(set(known.dt.strftime("%Y-%m")), reverse=True)
    done = report.published(ctx.conn)
    tab_draft, tab_done = st.tabs(["Bản nháp", f"Đã chốt ({len(done)})"])
    with tab_draft:
        period = None
        if ctx.historical:
            c.snapshot_note("báo cáo theo tháng")
        elif not periods:
            c.no_dates_note("báo cáo tháng")
        else:
            period = st.selectbox("Kỳ báo cáo", periods, key="report_period")
        if period is not None:
            if period in set(done["period"]):
                st.info(
                    f"Kỳ {period} đã chốt — xem ở tab “Đã chốt”. Bản dưới đây là số liệu tính lại theo dữ liệu "
                    "hiện tại."
                )
            try:
                show(report.build(ctx.conn, ctx.cfg, period))
                if period not in set(done["period"]) and st.button("Chốt báo cáo kỳ này", type="primary"):
                    confirm_publish(period)
            except report.ReportError as exc:
                st.warning(str(exc))
    with tab_done:
        if done.empty:
            st.info("Chưa có báo cáo nào được chốt.")
        for row in done.itertuples():
            with st.expander(f"Kỳ {row.period} · chốt lúc {row.created_at}"):
                payload = report.load_published(ctx.conn, row.period)
                show(payload)
                path = Path(row.xlsx_path)
                if not path.exists():  # file mất → dựng lại từ snapshot đã chốt (không tính lại)
                    report.write_excel(payload, path)
                st.download_button(
                    "↓ Tải Excel", path.read_bytes(), file_name=path.name, key=f"dl_{row.period}", mime=XLSX_MIME
                )

c.footer(ctx)
