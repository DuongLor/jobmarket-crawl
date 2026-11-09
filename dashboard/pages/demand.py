"""02 · Nhu cầu tuyển dụng theo ngành / địa phương / thời gian."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import stats  # noqa: E402

ctx = c.load_context()
c.page_header(ctx, "02", "Nhu cầu tuyển dụng", "Phân bổ tin tuyển dụng theo ngành, địa phương và thời gian.")

if not c.empty_state(ctx):
    days = int(ctx.cfg.stats["window_days"])
    o = stats.overview(ctx.df, ctx.as_of, days, c.seen_days(ctx))
    timed = not ctx.historical and c.has_dates(ctx)  # có tin rõ ngày đăng → chỉ số theo cửa sổ thời gian có nghĩa
    comparable = timed and c.window_comparable(ctx, days)  # kỳ trước nằm trọn sau ngày bắt đầu thu thập
    cols = st.columns(5)
    cols[0].metric("Tổng tin duy nhất", c.fmt_int(o["unique_jobs"]))
    cols[1].metric("Tin đang tuyển", "—" if ctx.historical else c.fmt_int(o["active"]),
                   help=None if ctx.historical else c.active_help(o))  # fmt: skip
    cols[2].metric(f"Tin đăng/làm mới {days} ngày", c.fmt_int(o["new_window"]) if timed else "—")
    cols[3].metric(f"Δ so {days} ngày trước", c.fmt_delta(o["new_delta"]) if comparable else "—",
                   help=None if comparable else f"So được khi đã thu thập đủ {2 * days} ngày — kỳ trước ngày bắt "
                   "đầu thu thập luôn thiếu tin.")  # fmt: skip
    cols[4].metric("Chưa phân loại ngành", c.fmt_int(ctx.df["industry_code"].isna().sum()))

    labels = {"total": "Tổng tin", "share": "Tỷ trọng", "active": "Đang tuyển", "new": f"Đăng/làm mới {days} ngày",
              "prev": f"{days} ngày trước", "delta": f"Δ% {days} ngày", "rank": "Hạng"}  # fmt: skip

    def show(dim: str, column: str, title: str, note: str, key: str, missing_label: str) -> None:
        st.subheader(title)
        if c.dimension_missing(ctx, dim, missing_label):
            return
        t = stats.demand_by(ctx.df, dim, ctx.as_of, days, c.seen_days(ctx))
        if ctx.historical:  # ảnh chụp: không có đang tuyển / tin mới theo cửa sổ thời gian
            t = t.drop(columns=["active", "new", "prev", "delta"])
        elif not timed:  # chưa tin nào rõ ngày đăng → cột tin mới toàn 0 gây hiểu nhầm
            t = t.drop(columns=["new", "prev", "delta"])
        elif not comparable:  # kỳ trước chưa thu thập đủ → bỏ so sánh, giữ số tin mới
            t = t.drop(columns=["prev", "delta"])
        st.caption(note)
        view = t.assign(share=[c.fmt_pct(v) for v in t["share"]], rank=t["rank"].astype("Int64"))
        if "delta" in view:
            view["delta"] = [c.fmt_delta(v) for v in view["delta"]]
        st.dataframe(view.rename(columns={dim: column, **labels}), hide_index=True, width="stretch")
        c.csv_button(t, f"nhu_cau_{dim}", key)

    tab_ind, tab_cat, tab_loc, tab_time, tab_heat = st.tabs(
        ["Theo nhóm ngành", "Theo danh mục", "Theo địa phương", "Theo thời gian", "Bản đồ nhiệt"]
    )
    with tab_ind:
        show("industry", "Ngành", "Phân bổ theo ngành", "Tổng các nhóm bằng số tin tính cơ cấu, gồm cả “(chưa phân "
             "loại)”. " + c.industry_share_note(ctx), "csv_ind", "ngành")  # fmt: skip
    with tab_cat:
        show("category", "Danh mục", "Phân bổ theo danh mục (giống CareerLink)", "Danh mục là cấp chi tiết dưới "
             "nhóm ngành, đặt tên theo CareerLink; nguồn khác chỉ có danh mục khi tương đương rõ, còn lại vào “(chưa "
             "phân loại)”. Tin nhiều danh mục chia đều cho mỗi danh mục. " + c.industry_share_note(ctx),
             "csv_cat", "danh mục")  # fmt: skip
    with tab_loc:
        show("province", "Địa phương", "Phân bổ theo địa phương", "Tin ghi nhiều nơi làm việc được tính ở "
             "MỖI nơi (như CareerLink) → tổng các tỉnh lớn hơn số tin. Tỉnh quy về 34 đơn vị hành chính sau sáp "
             "nhập 2025.", "csv_loc", "địa phương")  # fmt: skip
    with tab_time:
        if ctx.historical:
            c.snapshot_note("tin đăng/làm mới theo tháng")
        elif not timed:
            st.subheader("Tin đăng/làm mới theo tháng")
            c.no_dates_note("tin đăng/làm mới theo tháng")
        else:
            st.subheader("Tin đăng/làm mới theo tháng")
            st.caption("Theo ngày đăng (CareerLink/CareerViet: ngày làm mới); tin thiếu ngày thì dùng ngày thấy lần "
                       "đầu. Chỉ từ tháng bắt đầu thu thập; tin tồn sẵn không tính. MoM của tháng bắt đầu giữa chừng "
                       "và tháng kế tiếp để trống.")  # fmt: skip
            m = stats.monthly(ctx.df, ctx.as_of.replace(day=1), months=12)
            fig = px.line(m, x="month", y="total", markers=True, labels={"month": "", "total": "Tin đăng/làm mới"})
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")
            st.dataframe(m.assign(mom=[c.fmt_delta(v) for v in m["mom"]]).rename(
            columns={"month": "Tháng", "total": "Tổng tin đăng/làm mới", "mom": "MoM", **c.SOURCE_NAMES}),
            hide_index=True, width="stretch")  # fmt: skip
            c.csv_button(m, "tin_moi_theo_thang", "csv_time")
            st.warning("**Thiên lệch giai đoạn đầu:** trước ngày bắt đầu thu thập chỉ còn thấy tin chưa hết hạn. "
                   "Không dùng các tháng đầu để kết luận xu hướng dài hạn.")  # fmt: skip
    with tab_heat:
        st.subheader("Bản đồ nhiệt ngành × địa phương")
        if not c.dimension_missing(ctx, "industry", "ngành"):
            h = stats.heatmap(ctx.df[ctx.df["industry_code"].notna()])
            top_cols = h.drop(columns="Tổng").sum().sort_values(ascending=False).head(12).index
            fig = px.imshow(h[top_cols], text_auto=True, aspect="auto", color_continuous_scale="Blues",
                            labels={"x": "", "y": "", "color": "Số tin"})  # fmt: skip
            fig.update_layout(height=max(320, 32 * len(h)), margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")
            st.caption("Hiện 12 địa phương nhiều tin nhất, chỉ tin đã phân loại ngành; bảng CSV có đủ. "
                       + c.industry_share_note(ctx))  # fmt: skip
            c.csv_button(h.reset_index(), "ban_do_nhiet_nganh_tinh", "csv_heat")

c.footer(ctx)
