"""07 · Chất lượng dữ liệu & ánh xạ nhãn."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket import labels, stats  # noqa: E402

TYPE_NAMES = {"industry": "Ngành", "occupation": "Nghề", "location": "Địa điểm", "experience": "Kinh nghiệm",
              "level": "Cấp bậc"}  # fmt: skip
ACTIONS = {"keep": "— giữ nguyên —", "map": "Ánh xạ vào tên chuẩn", "ignore": "Bỏ qua (ignored)",
           "unmap": "Đưa về chưa ánh xạ"}  # fmt: skip

ctx = c.load_context()
c.page_header(ctx, "07", "Chất lượng dữ liệu & ánh xạ nhãn",
              "Theo dõi độ phủ dữ liệu, xử lý nhãn nguồn chưa ánh xạ và xem lịch sử thay đổi.")  # fmt: skip

if not c.empty_state(ctx):
    q = stats.quality(ctx.conn, include_total=True)
    total = q.set_index("source").loc["(Tất cả)"]
    pending = labels.table(ctx.conn, status="unmapped")
    st.caption("Trang quản trị tính trên **toàn bộ DB, mọi nguồn** — không theo bộ dữ liệu/bộ lọc ở thanh bên.")
    rejected = int(total["rejected"] or 0)
    cols = st.columns(5)
    cols[0].metric("Tổng bản ghi", c.fmt_int(total["records"]))
    cols[1].metric("Tin duy nhất", c.fmt_int(total["unique_jobs"]))
    cols[2].metric("Reject", c.fmt_int(rejected))
    cols[3].metric("Bản ghi trùng", c.fmt_int(total["records"] - total["unique_jobs"] - rejected))
    cols[4].metric("Nhãn chờ ánh xạ", c.fmt_int(len(pending)))

    tab_cov, tab_queue, tab_log = st.tabs(["Chỉ số chất lượng", "Hàng chờ ánh xạ", "Nhật ký thay đổi"])

    with tab_cov:
        th = ctx.cfg.quality
        st.subheader("Độ phủ và ngưỡng kiểm soát")
        checks = {"pct_reject": ("≤", th["max_reject"]), "pct_location": ("≥", th["min_location"]),
                  "pct_industry": ("≥", th["min_industry"]), "pct_occupation": ("≥", th["min_occupation"]),
                  "pct_experience": ("≥", th["min_experience"])}  # fmt: skip

        def verdict(row) -> str:
            fails = [k for k, (op, v) in checks.items()
                     if pd.notna(row[k]) and ((row[k] > v) if op == "≤" else (row[k] < v))]  # fmt: skip
            return "Đạt" if not fails else "Chưa đạt: " + ", ".join(k.removeprefix("pct_") for k in fails)

        view = q.assign(verdict=q.apply(verdict, axis=1))
        for col in [*checks, "pct_salary"]:
            view[col] = [c.fmt_pct(v) for v in view[col]]
        names = {"source": "Nguồn", "records": "Tổng bản ghi", "unique_jobs": "Tin duy nhất",
                 "pct_salary": "% có lương số (theo dõi)", "verdict": "Đánh giá",
                 **{k: f"% {k.removeprefix('pct_')} {op} {v * 100:.0f}%" for k, (op, v) in checks.items()}}  # fmt: skip
        st.dataframe(view[list(names)].rename(columns=names), hide_index=True, width="stretch")
        c.csv_button(q, "chat_luong_theo_nguon", "csv_quality")

        left, right = st.columns(2)
        with left:
            st.subheader("Cờ chất lượng")
            st.caption("Bản ghi warn vẫn được tính; chỉ reject bị loại khỏi thống kê. Cờ về địa điểm/kinh nghiệm "
                       "tính theo trạng thái nhãn hiện tại; `location_ignored` = địa điểm nước ngoài, bỏ qua có "
                       "chủ ý.")  # fmt: skip
            st.dataframe(stats.dq_flag_counts(ctx.conn).rename(columns={"flag": "Cờ", "records": "Số bản ghi"}),
                         hide_index=True, width="stretch")  # fmt: skip
        with right:
            st.subheader("Các lần chạy gần nhất")
            runs = pd.read_sql_query(
                "SELECT id, source, started_at, status, pages_fetched, posts_new, posts_updated, errors_count, "
                "error_sample FROM runs ORDER BY id DESC LIMIT 10",
                ctx.conn,
            )
            st.dataframe(runs, hide_index=True, width="stretch")

    with tab_queue:
        st.subheader("Hàng chờ duyệt nhãn")
        st.caption("Mỗi dòng là một nhãn của nguồn; xử lý nhãn nhiều tin trước. Đổi ánh xạ là số liệu đổi ngay, "
                   "không cần thu thập lại. Gợi ý chỉ dựa trên so khớp chữ — kiểm tra trước khi lưu.")  # fmt: skip
        f1, f2 = st.columns(2)
        ltype = f1.selectbox("Loại nhãn", list(TYPE_NAMES), format_func=TYPE_NAMES.get, key="q_type")
        status = f2.selectbox("Trạng thái", ["unmapped", "mapped", "ignored", "(tất cả)"], key="q_status")
        table = labels.table(ctx.conn, label_type=ltype, status=None if status == "(tất cả)" else status)
        master = labels.masters(ctx.conn, ltype)
        if table.empty:
            st.success("Không có nhãn nào ở trạng thái này.")
        else:
            name_to_code = {v: k for k, v in master.items()}
            editor = pd.DataFrame({
                "id": table["id"],
                "Nguồn": table["source"],
                "Nhãn gốc": table["raw_label"],
                "Phát hiện": table["first_seen_at"].str[:10],
                "Số tin": table["post_count"],
                "Trạng thái": table["status"],
                "Hiện tại": table["master_name"],
                "Gợi ý": table["suggestion"],
                "Hành động": ACTIONS["keep"],
                "Tên chuẩn đích": table["suggestion"],
                "Lý do": "",
            })  # fmt: skip
            edited = st.data_editor(
                editor,
                hide_index=True,
                width="stretch",
                disabled=["id", "Nguồn", "Nhãn gốc", "Phát hiện", "Số tin", "Trạng thái", "Hiện tại", "Gợi ý"],
                column_config={
                    "id": None,
                    "Hành động": st.column_config.SelectboxColumn(options=list(ACTIONS.values()), required=True),
                    "Tên chuẩn đích": st.column_config.SelectboxColumn(options=list(master.values())),
                },
                key=f"editor_{ltype}_{status}",
            )
            if st.button("Lưu thay đổi", type="primary"):
                action_codes = {v: k for k, v in ACTIONS.items()}
                changed, errors = 0, []
                for row in edited.itertuples(index=False):
                    action = action_codes[row[8]]
                    if action == "keep":
                        continue
                    try:
                        code = name_to_code.get(row[9]) if action == "map" else None
                        if action == "map" and not code:
                            raise labels.LabelError("chưa chọn tên chuẩn đích")
                        changed += labels.apply(ctx.conn, int(row[0]), action, code, reason=row[10] or None)
                    except labels.LabelError as exc:
                        errors.append(f"“{row[2]}”: {exc}")
                for err in errors:
                    st.error(err)
                if changed:
                    st.toast(f"Đã lưu {changed} thay đổi — số liệu đã cập nhật.")
                    st.rerun()
                elif not errors:
                    st.info("Chưa chọn hành động nào khác “giữ nguyên”.")
            c.csv_button(table, f"nhan_{ltype}_{status}", "csv_labels")

        if ltype in labels.EXTENSIBLE:
            with st.expander(f"Thêm tên chuẩn {TYPE_NAMES[ltype].lower()} mới"):
                new_name = st.text_input("Tên chuẩn mới", key="new_master_name")
                parent = None
                if ltype == "occupation":
                    industries = labels.masters(ctx.conn, "industry")
                    parent = st.selectbox("Thuộc ngành", list(industries), format_func=industries.get)
                if st.button("Thêm vào danh mục"):
                    try:
                        code = labels.add_master(ctx.conn, ltype, new_name, parent)
                        st.toast(f"Đã thêm {code}: {new_name}")
                        st.rerun()
                    except labels.LabelError as exc:
                        st.error(str(exc))

    with tab_log:
        st.subheader("Nhật ký thay đổi ánh xạ")
        log = labels.history(ctx.conn)
        st.dataframe(log.rename(columns={"at": "Thời điểm", "label_type": "Loại", "source": "Nguồn",
                                         "raw_label": "Nhãn gốc", "old_value": "Cũ", "new_value": "Mới",
                                         "via": "Kênh", "reason": "Lý do"}),
                     hide_index=True, width="stretch")  # fmt: skip
        c.csv_button(log, "nhat_ky_anh_xa", "csv_log")

c.footer(ctx)
