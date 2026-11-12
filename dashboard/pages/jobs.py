"""Danh sách tin: xem từng tin (tiêu đề, công ty, lương…), tìm kiếm, mở tin gốc, xem mô tả."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common as c  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from jobmarket.normalize.text import norm_key  # noqa: E402

PAGE_SIZE = 200

ctx = c.load_context()
c.page_header(ctx, "06", "Danh sách tin tuyển dụng",
              "Từng tin sau khi làm sạch (tin duy nhất). Bấm một dòng để xem mô tả; cột Link mở tin gốc.")  # fmt: skip


def names(codes: tuple, fallback: str) -> str:
    """Tin nhiều giá trị (nơi làm việc, danh mục) → hiện đủ tên, như trang nguồn; chưa có → `fallback`."""
    return ", ".join(t[1] for t in codes) if codes else fallback


def salary_text(df: pd.DataFrame) -> pd.Series:
    """Chuỗi lương hiển thị, tính cả cột một lượt (không lặp từng dòng — 35k tin)."""

    def m(col: str) -> pd.Series:
        return (df[col] / 1e6).map(lambda v: f"{v:g}".replace(".", ","), na_action="ignore")

    lo, hi, kind = m("salary_from"), m("salary_to"), df["salary_type"]
    out = pd.Series("Không ghi", index=df.index)
    out[kind == "negotiable"] = "Thoả thuận"
    for mask, text in (
        ((kind == "range") & lo.notna() & hi.notna(), lo + " – " + hi + " triệu"),
        ((kind == "fixed") & lo.notna(), lo + " triệu"),
        ((kind == "up_to") & hi.notna(), "Tới " + hi + " triệu"),
        ((kind == "from") & lo.notna(), "Từ " + lo + " triệu"),
    ):
        out[mask] = text[mask]
    return out


if not c.empty_state(ctx):
    query = st.text_input(
        "Tìm theo tiêu đề hoặc công ty", key="jobs_query", placeholder="vd: ke toan, kỹ sư điện, MISA…"
    )
    df = ctx.df.sort_values(["first_seen_at", "id"], ascending=False)
    if query.strip():
        key = norm_key(query)
        haystack = (df["title"].fillna("") + " " + df["company_name"].fillna("")).map(norm_key)
        df = df[haystack.str.contains(key, regex=False)]

    st.caption(f"{c.fmt_int(len(df))} tin" + (f" khớp “{query}”" if query.strip() else ""))
    if df.empty:
        st.info("Không có tin nào khớp. Thử từ khoá khác hoặc nới bộ lọc ở thanh bên.")
    else:
        view = pd.DataFrame({
            "id": df["id"].to_numpy(),
            "Tiêu đề": df["title"].to_numpy(),
            "Công ty": df["company_name"].fillna("—").to_numpy(),
            # Tin ghi nhiều nơi / nhiều danh mục: hiện đủ (như trang nguồn), không chỉ mục đầu
            "Địa phương": [names(codes, name)
                           for codes, name in zip(df["province_codes"], df["province_name"], strict=True)],
            "Lương": salary_text(df).to_numpy(),
            "Kinh nghiệm": df["experience_name"].to_numpy(),
            "Cấp bậc": df["level_name"].to_numpy(),
            "Danh mục": [names(codes, "—") for codes in df["category_codes"]],
            "Nhóm ngành": df["industry_name"].to_numpy(),
            "Nguồn": df["source"].map(lambda s: c.SOURCE_NAMES.get(s, s)).to_numpy(),
            "Thấy lần đầu": df["first_seen_at"].str[:10].to_numpy(),
            "Link": df["url"].to_numpy(),
        })  # fmt: skip
        pages = max(1, -(-len(view) // PAGE_SIZE))
        page = st.number_input(f"Trang (mỗi trang {PAGE_SIZE} tin, tổng {pages} trang)", 1, pages, 1, key="jobs_page")
        chunk = view.iloc[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        event = st.dataframe(
            chunk,
            hide_index=True,
            width="stretch",
            height=520,
            on_select="rerun",
            selection_mode="single-row",
            column_config={"id": None, "Link": st.column_config.LinkColumn("Link", display_text="Mở tin")},
            key=f"jobs_table_{page}",
        )
        c.csv_button(view.drop(columns="id"), "danh_sach_tin", "csv_jobs")

        rows = event.selection.rows if event and event.selection else []
        if rows:
            job_id = int(chunk.iloc[rows[0]]["id"])
            job = ctx.conn.execute(
                "SELECT title, url, skills_raw, salary_raw, description_text, requirements_text, experience_raw, "
                "experience_min_months, experience_max_months, dq_flags FROM job_posts WHERE id = ?",
                (job_id,),
            ).fetchone()
            st.subheader(job["title"] or "—")
            if job["url"]:
                st.link_button("Mở tin gốc", job["url"])
            st.write(
                f"**Lương (nguyên văn):** {job['salary_raw'] or '—'} · **Kỹ năng/tag:** {job['skills_raw'] or '—'}"
            )
            st.write(
                f"**Kinh nghiệm:** {job['experience_raw'] or '—'}"
                f" ({c.months_range(job['experience_min_months'], job['experience_max_months'])})"
            )
            if job["dq_flags"]:
                st.caption(f"Cờ chất lượng: {job['dq_flags']}")
            if job["description_text"]:
                with st.expander("Mô tả công việc", expanded=True):
                    st.text(job["description_text"])
            if job["requirements_text"]:
                with st.expander("Yêu cầu công việc", expanded=True):
                    st.text(job["requirements_text"])
            if not (job["description_text"] or job["requirements_text"]):
                st.caption("Chưa có mô tả/yêu cầu: tin chưa được đọc trang chi tiết, hoặc nguồn không cho đọc.")
        else:
            st.caption("Bấm vào một dòng để xem chi tiết tin.")

c.footer(ctx)
