"""Khoá trùng = công ty + tiêu đề + tỉnh, đã chuẩn hoá (BR-03). Không xoá tin trùng."""

import hashlib
import re

from jobmarket.normalize.text import norm_key

_COMPANY_FORMS = re.compile(
    r"\b(cong ty|cty|tnhh|trach nhiem huu han|mot thanh vien|mtv|co phan|cp|jsc|"
    r"co ltd|company limited|limited|ltd|corporation|corp|tap doan|chi nhanh|vietnam|viet nam)\b"
)


def company_core(name: str | None) -> str:
    return re.sub(r"\s+", " ", _COMPANY_FORMS.sub(" ", norm_key(name))).strip()


def dedup_key(company: str | None, title: str | None, location_key: str | None) -> str:
    raw = "|".join((company_core(company), norm_key(title), location_key or ""))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()
