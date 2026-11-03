"""Địa điểm của nguồn → tỉnh đầu tiên + số địa điểm (PLAN §B4).

TopCV ghi tên mới kèm "(mới)" và nhiều nơi dạng "Hà Nội & 4 nơi khác" / "A & B";
VietnamWorks ghi 63 tên cũ. Cả hai quy về 34 tỉnh qua bảng province_aliases.
"""

import re
from dataclasses import dataclass

from jobmarket.normalize.text import clean


@dataclass
class Location:
    first: str  # địa điểm đầu tiên, đã làm sạch (dùng làm nhãn)
    count: int


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
    return Location(first=clean(parts[0]), count=count)
