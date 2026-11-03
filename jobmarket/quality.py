"""Cờ chất lượng ok / warn / reject (PLAN §B4, BR-02)."""

REQUIRED = ("source_job_id", "title", "company_name")


def assess(fields: dict, flags: list[str], provides: set[str]) -> tuple[str, list[str]]:
    """`provides` = các trường nguồn có cung cấp; chỉ báo thiếu với trường nguồn có.

    Vd TopCV (chỉ trang danh sách) không có ngày đăng → không gắn `posted_missing` cho mọi tin.
    """
    flags = list(dict.fromkeys(flags))
    # Chỉ bắt buộc trường nguồn CÓ cung cấp (vd bộ dữ liệu VietJobs không có tên công ty → không reject cả bộ)
    missing = [f for f in REQUIRED if f in provides and not fields.get(f)]
    if missing:
        return "reject", flags + [f"missing_{f.removesuffix('_name')}" for f in missing]
    if "posted_date" in provides and not fields.get("posted_date"):
        flags.append("posted_missing")
    return ("warn" if flags else "ok"), flags
