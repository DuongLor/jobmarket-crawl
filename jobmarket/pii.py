"""Xoá thông tin cá nhân TRƯỚC khi lưu bản gốc hay ghi log (PLAN §A2)."""

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Số VN theo nhóm: 0912 345 678 · 0912.345.678 · 0912345678 · +84 912 345 678 · 024 3826 1234.
# Không cho dấu cách xen giữa TỪNG chữ số (tránh xoá nhầm toạ độ SVG "0 2.3 1.5 4.05…", số thập phân).
# Biên: không dính chữ số hoặc "chữ số + dấu chấm" (số thập phân), nhưng dấu chấm CUỐI CÂU thì vẫn xoá
# ("… hoặc 0912 345 678." — trước 2026-11-04 bị bỏ sót vì cấm mọi dấu chấm phía sau).
PHONE = re.compile(r"(?<!\d)(?<!\d\.)(?:\+84[\s.\-]?|0)\d{2,3}[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}(?!\d)(?!\.\d)")
# Tổng đài 1900/1800 (8–10 chữ số): 1900 1234 · 1900.54.55.66 · 18006789
HOTLINE = re.compile(r"(?<![\d.])1[89]00(?:\d{4,6}|[\s.\-]?\d{2,4}(?:[\s.\-]\d{2,3}){0,2})(?!\d)(?!\.\d)")
ZALO_FB = re.compile(r"https?://(?:zalo\.me|(?:www\.|m\.)?facebook\.com|fb\.com)/[^\s\"'<>]+", re.IGNORECASE)

REDACTED = "[đã xoá]"


def scrub(text: str | None) -> tuple[str, list[str]]:
    """Trả (văn bản đã xoá, danh sách loại đã xoá)."""
    if not text:
        return text or "", []
    removed = []
    for kind, pattern in (("email", EMAIL), ("social", ZALO_FB), ("phone", PHONE), ("phone", HOTLINE)):
        text, n = pattern.subn(REDACTED, text)
        if n and kind not in removed:
            removed.append(kind)
    return text, removed
