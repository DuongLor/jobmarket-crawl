"""Cấp bậc suy từ tiêu đề theo bảng từ khoá CỐ ĐỊNH (PLAN Q5).

Chỉ gắn khi tiêu đề chứa từ khoá rõ ràng; không khớp → None (để trống, không đoán "Nhân viên").
Thứ tự kiểm tra từ cấp cao xuống để "Phó giám đốc kinh doanh" không bị bắt thành cấp thấp hơn.
"""

import re

from jobmarket.normalize.text import norm_key

RULES: list[tuple[str, tuple[str, ...]]] = [
    ("DIRECTOR", ("giam doc", "director", "ceo", "cto", "cfo", "coo", "general manager")),
    ("MANAGER", ("truong phong", "pho phong", "head of", "department manager")),
    ("LEAD", ("truong nhom", "team lead", "team leader", "tech lead", "leader")),
    ("SENIOR", ("senior", "sr")),
    ("JUNIOR", ("junior", "jr", "fresher", "moi tot nghiep")),
    ("INTERN", ("thuc tap sinh", "thuc tap", "intern", "internship")),
]


def level_from_title(title: str | None) -> str | None:
    text = norm_key(title)
    if not text:
        return None
    for code, keywords in RULES:
        if any(re.search(rf"\b{re.escape(k)}\b", text) for k in keywords):
            return code
    return None
