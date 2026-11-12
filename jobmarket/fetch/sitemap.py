"""Đọc sitemap XML (sitemaps.org) — kênh site tự công bố danh sách URL cho máy tìm kiếm, dùng chung mọi nguồn.

Chỉ lấy `<loc>`; không dùng trình phân tích XML đầy đủ (không cần, và tránh rủi ro thực thể XML từ dữ liệu ngoài).
"""

import html
import re

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def parse(xml: str) -> tuple[bool, list[str]]:
    """→ (là sitemap index — `<loc>` trỏ tới sitemap con, danh sách URL)."""
    return "<sitemapindex" in xml[:2000].lower(), [html.unescape(u) for u in _LOC.findall(xml)]
