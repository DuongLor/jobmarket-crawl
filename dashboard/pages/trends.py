"""04 · Xu hướng ngành: 3 tháng gần nhất so với 3 tháng liền trước."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import stats  # noqa: E402

ctx = c.load_context()
c.page_header(ctx, "04", "Xu hướng ngành", "Ngành tăng/giảm: tổng tin 3 tháng gần nhất so với 3 tháng liền trước.")

if not c.empty_state(ctx):
    s = ctx.cfg.stats
    months = stats.months_of_data(ctx.df_all)
    if ctx.historical:
        c.snapshot_note("xu hướng theo thời gian")
    elif c.dimension_missing(ctx, "industry", "ngành"):
        pass
    elif not months:
        c.no_dates_note("xu hướng")
    elif months < 6:
        st.info(
            f"**Chưa đủ điều kiện kết luận xu hướng.** Cần ≥ 6 tháng dữ liệu liên tục cùng phạm vi thu thập; "
            f"hiện có {months}/6 tháng."
        )
        st.progress(months / 6)
    else:
        month_end = ctx.as_of.replace(day=1)
        t = stats.trend(ctx.df, month_end, int(s["min_vol_trend"]), float(s["trend_threshold"]))
        st.caption(
            f"Lệch > {float(s['trend_threshold']) * 100:.0f}% → tăng/giảm · 3 tháng trước < {s['min_vol_trend']} tin "
            "→ “Chưa đủ dữ liệu” · không xét nhóm chưa phân loại."
        )
        view = t.assign(delta=[c.fmt_delta(v) for v in t["delta"]])
        st.dataframe(view.rename(columns={"industry": "Ngành", "recent_3m": "3T gần nhất", "prev_3m": "3T trước đó",
                                          "delta": "Δ%", "trend": "Xu hướng"}),
                     hide_index=True, width="stretch")  # fmt: skip
        c.csv_button(t, "xu_huong_nganh", "csv_trend")
    st.caption("Phân tích kỹ năng xuất hiện thường xuyên: làm sau (cần từ điển kỹ năng).")

c.footer(ctx)
