import re
import unicodedata

_SPACES = re.compile(r"\s+")


def clean(value: str | None) -> str:
    """Chuẩn hoá Unicode (NFC) và gộp khoảng trắng; giữ nguyên dấu và hoa/thường."""
    if not value:
        return ""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", value)).strip()


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return without_marks.replace("đ", "d").replace("Đ", "D")


def norm_key(value: str | None) -> str:
    """Khoá so khớp: chữ thường, bỏ dấu, chỉ giữ chữ-số, gộp khoảng trắng."""
    text = strip_accents(clean(value)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()
