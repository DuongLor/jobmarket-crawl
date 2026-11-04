"""01 · Tổng quan thị trường tuyển dụng."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import stats  # noqa: E402

ctx = c.load_context()
c.page_header(ctx, "01", "Tổng quan thị trường tuyển dụng",
              "Bức tranh nhu cầu tuyển dụng và mức lương đăng tuyển từ các nguồn đã thu thập.")  # fmt: skip

if not c.empty_state(ctx):
    s = ctx.cfg.stats
    o = stats.overview(ctx.df, ctx.as_of, int(s["window_days"]), c.seen_days(ctx))
    # Mọi chỉ số tính trên bộ dữ liệu đang xem (không lấy chất lượng của cả DB)
    cov = {dim: c.coverage(ctx, dim) for dim in ("industry", "occupation", "province", "experience")}
    q_view = {f"pct_{'location' if d == 'province' else d}": v for d, v in cov.items()}
    badge = stats.confidence(o["salary_n"], q_view, ctx.cfg.quality)
    st.markdown(f"**Độ tin cậy: {badge}** · {c.UNIT_NOTE}")

    cols = st.columns(6)
    cols[0].metric("Tin duy nhất", c.fmt_int(o["unique_jobs"]),
                   help="Đã bỏ tin lỗi và tin trùng. Mọi chỉ số tính trên tập này.")  # fmt: skip
    if ctx.historical:
        cols[1].metric("Đang tuyển", "—", help="Bộ lịch sử: mọi tin đã hết hạn.")
        cols[2].metric(
            f"Tin đăng/làm mới {s['window_days']} ngày", "—", help="Bộ lịch sử là ảnh chụp, không có ngày đăng."
        )
    else:
        cols[1].metric("Đang tuyển", c.fmt_int(o["active"]), help=c.active_help(o))
        if c.has_dates(ctx):
            comparable = c.window_comparable(ctx, int(s["window_days"]))
            cols[2].metric(f"Tin đăng/làm mới {s['window_days']} ngày", c.fmt_int(o["new_window"]),
                           delta=c.fmt_delta(o["new_delta"]) if comparable and o["new_delta"] is not None else None,
                           help=(f"So với {s['window_days']} ngày liền trước ({c.fmt_int(o['prev_window'])} tin)."
                                 if comparable else "Chưa so với kỳ trước: kỳ trước nằm trước ngày bắt đầu thu thập "
                                 "nên thiếu tin (chỉ còn thấy tin chưa hết hạn)."))  # fmt: skip
        else:
            cols[2].metric(f"Tin đăng/làm mới {s['window_days']} ngày", "—",
                           help="Chưa có tin nào rõ ngày đăng (đều là tin có sẵn khi bắt đầu thu thập).")  # fmt: skip
    no_company = int(ctx.df["company_name"].isna().sum())
    company_help = "Số công ty khác nhau có tin trong tập đang xem."
    if no_company:
        company_help = f"Chỉ đếm tin có tên công ty; {c.fmt_int(no_company)} tin không có" + (
            " (vd bộ dữ liệu VietJobs không cung cấp tên công ty)."
        )
    cols[3].metric("Số công ty", c.fmt_int(o["companies"]) if o["companies"] else "—", help=company_help)
    cols[4].metric("Lương trung vị", c.fmt_million(o["salary_median"]),
                   help=f"n = {c.fmt_int(o['salary_n'])} tin có lương số")  # fmt: skip
    cols[5].metric("Tin có lương số", c.fmt_pct(o["salary_coverage"]),
                   help=f"{c.fmt_int(o['salary_n'])} / {c.fmt_int(o['unique_jobs'])}")  # fmt: skip

    left, right = st.columns(2)
    with left:
        st.subheader("Nhu cầu theo ngành")
        if not c.dimension_missing(ctx, "industry", "ngành"):
            ind = stats.demand_by(ctx.df, "industry", ctx.as_of, int(s["window_days"]), c.seen_days(ctx))
            top = ind[ind["industry"] != stats.UNCLASSIFIED["industry"]].head(8)
            fig = px.bar(
                top.iloc[::-1], x="total", y="industry", orientation="h", labels={"total": "Số tin", "industry": ""}
            )
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")
            unclassified = int(ind.loc[ind["industry"] == stats.UNCLASSIFIED["industry"], "total"].sum())
            st.caption(f"Top 8 ngành · {c.fmt_int(unclassified)} tin chưa phân loại ngành (không vẽ). "
                       + c.industry_share_note(ctx))  # fmt: skip
    with right:
        if ctx.historical:
            st.subheader("Tin theo nguồn")
            c.snapshot_note("tin đăng/làm mới theo tháng")
        elif not c.has_dates(ctx):
            st.subheader("Tin đăng/làm mới theo tháng")
            c.no_dates_note("tin đăng/làm mới theo tháng")
        else:
            st.subheader("Tin đăng/làm mới theo tháng")
            month_end = ctx.as_of.replace(day=1)
            m = stats.monthly(ctx.df, month_end)
            by_source = m.melt(id_vars="month", value_vars=[x for x in m.columns if x not in ("month", "total", "mom")],
                               var_name="Nguồn", value_name="Số tin")  # fmt: skip
            by_source["Nguồn"] = by_source["Nguồn"].map(lambda x: c.SOURCE_NAMES.get(x, x))
            fig = px.bar(by_source, x="month", y="Số tin", color="Nguồn", labels={"month": ""})
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), barmode="stack")
            st.plotly_chart(fig, width="stretch")
            if (m["total"] > 0).sum() <= 1:
                st.caption("Mới có dữ liệu 1 tháng — biểu đồ có ý nghĩa sau vài tháng thu thập hằng đêm.")

    left, mid, right = st.columns(3)
    with left:
        st.subheader("Top địa phương")
        prov = stats.demand_by(ctx.df, "province", ctx.as_of, int(s["window_days"]), c.seen_days(ctx)).head(5)
        st.dataframe(prov[["province", "total"]].rename(columns={"province": "Địa phương", "total": "Số tin"}),
                     hide_index=True, width="stretch")  # fmt: skip
    with mid:
        # Nguồn không có kinh nghiệm (CareerLink chưa enrich) → so theo cấp bậc thay vì để ô trống
        dim, label = ("experience", "Kinh nghiệm")
        if c.coverage(ctx, "experience") < c.LOW_COVERAGE and c.coverage(ctx, "level") >= c.LOW_COVERAGE:
            dim, label = ("level", "Cấp bậc")
        st.subheader(f"Lương trung vị theo {label.lower()}")
        if not c.dimension_missing(ctx, dim, label.lower()):
            sal = stats.salary_by(ctx.df, dim, int(s["min_n_salary"]))
            sal = sal[sal[dim] != stats.UNCLASSIFIED_DEFAULT]
            shown = sal.assign(**{"Trung vị": [c.fmt_million(v) for v in sal["median"]]})
            st.dataframe(shown[[dim, "Trung vị", "salary_n"]].rename(columns={dim: label, "salary_n": "n"}),
                         hide_index=True, width="stretch")  # fmt: skip
            fallback = " Nguồn này không có yêu cầu kinh nghiệm nên so theo cấp bậc." if dim == "level" else ""
            st.caption(f"Trung vị hiện khi n ≥ {s['min_n_salary']}.{fallback}")
    with right:
        st.subheader("Xu hướng ngành")
        if ctx.historical:
            c.snapshot_note("xu hướng theo thời gian")
        else:
            months = stats.months_of_data(ctx.df_all)
            if months < 6:
                st.info(c.months_note(months))
            else:
                t = stats.trend(ctx.df, ctx.as_of.replace(day=1), int(s["min_vol_trend"]), float(s["trend_threshold"]))
                st.dataframe(t[["industry", "trend"]].rename(columns={"industry": "Ngành", "trend": "Xu hướng"}),
                             hide_index=True, width="stretch")  # fmt: skip
        st.subheader("Chất lượng (bộ đang xem)")
        q1, q2, q3 = st.columns(3)
        q1.metric("Phủ ngành", c.fmt_pct(cov["industry"]))
        q2.metric("Phủ kinh nghiệm", c.fmt_pct(cov["experience"]))
        q3.metric("Phủ tỉnh", c.fmt_pct(cov["province"]))

c.footer(ctx)
