"""03 · Phân tích lương đăng tuyển."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import stats  # noqa: E402

ctx = c.load_context()
c.page_header(ctx, "03", "Phân tích lương đăng tuyển",
              "So sánh mức lương theo kinh nghiệm, cấp bậc, địa phương và nghề. Đơn vị: triệu VND/tháng.")  # fmt: skip

if not c.empty_state(ctx):
    min_n = int(ctx.cfg.stats["min_n_salary"])
    st.info(
        "**Lưu ý:** chỉ tính tin có lương dạng khoảng (lấy trung điểm) hoặc một mức. Không trộn “tới X”, “từ X”, "
        f"thoả thuận. Trung vị ưu tiên hơn trung bình vì lương lệch phải. Nhóm dưới {min_n} tin có lương số hiện "
        f"**n<{min_n}**. Dùng bộ lọc **Ngành** ở thanh bên để xem riêng một ngành."
    )
    columns = {"jobs": "Số tin", "salary_n": "Có lương số (n)", "coverage": "Độ phủ", "mean": "Trung bình",
               "median": "Trung vị", "p25": "P25", "p75": "P75"}  # fmt: skip

    def show(dim: str, label: str, key: str, drop_unclassified: bool = False) -> None:
        if c.dimension_missing(ctx, dim, label.lower()):
            return
        t = stats.salary_by(ctx.df, dim, min_n)
        if drop_unclassified:
            t = t[t[dim] != stats.UNCLASSIFIED_DEFAULT]
        numeric = t[t["median"].map(lambda v: not isinstance(v, str))]
        if not numeric.empty:
            fig = go.Figure(go.Bar(
                x=numeric["median"] / 1e6, y=numeric[dim], orientation="h",
                error_x=dict(type="data", symmetric=False, array=(numeric["p75"] - numeric["median"]) / 1e6,
                             arrayminus=(numeric["median"] - numeric["p25"]) / 1e6),
            ))  # fmt: skip
            fig.update_layout(height=max(260, 34 * len(numeric)), margin=dict(l=0, r=0, t=10, b=0),
                              xaxis_title="Trung vị (thanh) và P25–P75 (vạch), triệu VND/tháng",
                              yaxis=dict(autorange="reversed"))  # fmt: skip
            st.plotly_chart(fig, width="stretch")
        view = c.to_millions(t, ["mean", "median", "p25", "p75"])
        view["coverage"] = [c.fmt_pct(v) for v in view["coverage"]]
        st.dataframe(view.rename(columns={dim: label, **columns}), hide_index=True, width="stretch")
        c.csv_button(t, f"luong_theo_{dim}", key)

    # Chiều mà không nguồn nào cung cấp (hiện: nghề) thì ẩn hẳn tab, không để tab chỉ có ghi chú
    has_occupation = c.coverage(ctx, "occupation") > 0
    names_ = ["Theo kinh nghiệm", "Theo cấp bậc", "Theo địa phương", *(["Theo nghề"] if has_occupation else []),
              "Loại lương"]  # fmt: skip
    tabs = dict(zip(names_, st.tabs(names_), strict=True))
    with tabs["Theo kinh nghiệm"]:
        show("experience", "Kinh nghiệm", "csv_exp")
    with tabs["Theo cấp bậc"]:
        show("level", "Cấp bậc", "csv_lvl")
        st.caption("Cấp bậc lấy từ nguồn khi có; nguồn không có thì chỉ suy từ tiêu đề khi có từ khoá rõ ràng.")
    with tabs["Theo địa phương"]:
        show("province", "Địa phương", "csv_prov")
    if has_occupation:
        with tabs["Theo nghề"]:
            show("occupation", "Nghề", "csv_occ", drop_unclassified=True)
            st.caption(f"Ẩn nhóm chưa phân loại nghề. Nghề ít mẫu hiện n<{min_n}.")
    with tabs["Loại lương"]:
        types = stats.salary_types(ctx.df)
        names = {"range": "Khoảng (từ–đến)", "fixed": "Một mức", "up_to": "Tới X (mức trần)", "from": "Từ X",
                 "negotiable": "Thoả thuận", "not_stated": "Không ghi"}  # fmt: skip
        st.subheader(f"Cơ cấu loại lương · {c.fmt_int(len(ctx.df))} tin duy nhất")
        st.dataframe(types.assign(salary_type=types["salary_type"].map(lambda t: names.get(t, t)),
                                  share=[c.fmt_pct(v) for v in types["share"]]).rename(
            columns={"salary_type": "Loại lương", "jobs": "Số tin", "share": "Tỷ trọng"}),
            hide_index=True, width="stretch")  # fmt: skip
        st.caption("Chỉ “Khoảng” và “Một mức” được tính vào trung bình/trung vị.")
        c.csv_button(types, "co_cau_loai_luong", "csv_types")

c.footer(ctx)
