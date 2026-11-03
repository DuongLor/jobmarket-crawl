"""Dashboard thị trường tuyển dụng — chạy: python -m jobmarket dashboard [--demo]"""

from pathlib import Path

import streamlit as st
from common import init_db, load_config, method_dialog, render_filters

PAGES = Path(__file__).parent / "pages"

st.set_page_config(page_title="Thị trường tuyển dụng", page_icon="📊", layout="wide")

cfg = load_config()
conn = init_db(cfg.path("db_file"))
unmapped = conn.execute("SELECT COUNT(*) FROM labels WHERE status = 'unmapped'").fetchone()[0]
sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM job_posts ORDER BY 1")]
masters = {
    dim: dict(conn.execute(f"SELECT code, name FROM {table} ORDER BY {order}").fetchall())
    for dim, table, order in (
        ("industry", "master_industries", "sort_order"),
        ("province", "master_provinces", "rowid"),
        ("experience", "master_experiences", "sort_order"),
        ("level", "master_levels", "rank_order"),
    )
}
conn.close()

nav = st.navigation(
    {
        "Thị trường": [
            st.Page(PAGES / "overview.py", title="Tổng quan", icon="📊", default=True),
            st.Page(PAGES / "jobs.py", title="Danh sách tin", icon="🔎"),
            st.Page(PAGES / "demand.py", title="Nhu cầu tuyển dụng", icon="📋"),
            st.Page(PAGES / "salary.py", title="Phân tích lương", icon="💰"),
            st.Page(PAGES / "trends.py", title="Xu hướng", icon="📈"),
            st.Page(PAGES / "reports.py", title="Báo cáo tháng", icon="🗂️"),
        ],
        "Quản trị dữ liệu": [
            st.Page(
                PAGES / "quality.py",
                title=f"Chất lượng & ánh xạ ({unmapped} chờ)" if unmapped else "Chất lượng & ánh xạ",
                icon="🧭",
            ),
        ],
    }
)

render_filters(masters, sources)
if st.sidebar.button("ⓘ Phương pháp & giới hạn", width="stretch"):
    method_dialog()
st.sidebar.caption(f"DB: `{cfg.path('db_file').name}`")

nav.run()
