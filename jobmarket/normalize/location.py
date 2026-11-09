"""Địa điểm của nguồn → tỉnh đầu tiên + số địa điểm (PLAN §B4).

TopCV ghi tên mới kèm "(mới)" và nhiều nơi dạng "Hà Nội & 4 nơi khác" / "A & B";
VietnamWorks ghi 63 tên cũ. Cả hai quy về 34 tỉnh qua bảng province_aliases.
"""

import re
from dataclasses import dataclass

from jobmarket.normalize.text import clean, norm_key

_ADMIN_PREFIX = re.compile(r"^(tp|thanh pho|tx|thi xa|thi tran|quan|huyen|tinh|phuong|xa)\s+")


@dataclass
class Location:
    first: str  # địa điểm đầu tiên, đã làm sạch (dùng làm nhãn)
    count: int  # số nơi nguồn ghi (kể cả "& N nơi khác" không nêu tên)
    parts: list[str]  # mọi nơi có tên, theo thứ tự nguồn (job_locations)


def province_candidates(raw: str) -> list[str]:
    """Các khoá (norm_key) để tra province_aliases cho một nhãn địa điểm, thử lần lượt.

    Cả chuỗi; "hồ chí minh: 17 công lý" / "hà nội (bắc từ liêm" → phần trước ":" "("; "cầu giấy - hà nội" → phần sau
    " - " cuối (tỉnh đứng sau quận). Tách trên chuỗi gốc vì norm_key bỏ dấu câu. Thêm bản bỏ tiền tố hành chính
    ("tp vinh", "huyện củ chi")."""
    parts = (raw, raw.split(":")[0], raw.split("(")[0], raw.rsplit(" - ", 1)[-1])
    keys = [norm_key(p) for p in parts if p.strip()]
    keys += [_ADMIN_PREFIX.sub("", k) for k in keys]
    return list(dict.fromkeys(k for k in keys if k))


def split_location(raw: str | None) -> Location | None:
    text = clean(raw)
    if not text:
        return None
    text = re.sub(r"\(\s*mới\s*\)", "", text, flags=re.IGNORECASE)
    more = re.search(r"&\s*(\d+)\s*nơi khác", text, flags=re.IGNORECASE)
    parts = [p.strip(" ,") for p in re.split(r"&|,|;", re.sub(r"&\s*\d+\s*nơi khác", "", text)) if p.strip(" ,")]
    if not parts:
        return None
    count = len(parts) + (int(more.group(1)) if more else 0)
    named = list(dict.fromkeys(clean(p) for p in parts if clean(p)))
    return Location(first=clean(parts[0]), count=count, parts=named)
