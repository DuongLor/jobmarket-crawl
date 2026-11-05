"""Bản gốc mỗi tin (đã xoá PII) dạng .json.gz — để chạy lại chuẩn hoá mà không crawl lại."""

import gzip
import hashlib
import json
import re
from pathlib import Path

from jobmarket.pii import scrub
from jobmarket.timeutil import current_month

# Mã tin dùng làm tên file → chỉ chữ/số/gạch (chặn "../" từ dữ liệu nguồn hoặc trang người dùng tự lưu)
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Key mang thông tin liên hệ/định danh người (kể cả lồng sâu, vd employer_info.assigned_staff_username) → bỏ hẳn
CONTACT_KEY = re.compile(
    r"(e-?mail|phone|mobile|^tel$|telephone|username|contact|hotline|fax|zalo|skype)", re.IGNORECASE
)


def sanitize(value, removed: set[str]):
    """Lớp chặn chung cho MỌI nguồn trước khi ghi đĩa: bỏ key liên hệ + xoá email/SĐT/link MXH trong mọi chuỗi."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if CONTACT_KEY.search(str(key)):
                removed.add("contact_field")
                continue
            out[key] = sanitize(item, removed)
        return out
    if isinstance(value, list):
        return [sanitize(item, removed) for item in value]
    if isinstance(value, str):
        text, kinds = scrub(value)
        removed.update(kinds)
        return text
    return value


def _target(raw_dir: Path, source: str, source_job_id: str, month: str) -> tuple[Path, Path]:
    if not SAFE_ID.match(source_job_id or "") or not SAFE_ID.match(source):
        raise ValueError(f"Mã tin không hợp lệ để làm tên file: {source_job_id!r}")
    rel = Path(source) / month / f"{source_job_id}.json.gz"
    target = (raw_dir / rel).resolve()
    if not target.is_relative_to(raw_dir.resolve()):
        raise ValueError(f"Đường dẫn bản gốc ra ngoài raw_dir: {rel}")
    return rel, target


def save(raw_dir: Path, source: str, source_job_id: str, payload: dict) -> tuple[str, str]:
    """Ghi file, trả (đường dẫn tương đối so với raw_dir, sha256). Không ghi đè nếu đã có."""
    rel, target = _target(raw_dir, source, source_job_id, current_month())
    extra: set[str] = set()
    clean = sanitize(payload, extra)
    if extra:
        clean["pii_removed"] = sorted(set(clean.get("pii_removed") or []) | extra)
    body = json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8")
    existing = _read_existing(target)
    if existing is not None:  # không ghi đè → mã băm phải khớp file trên đĩa (bản đã lưu trước), không phải bản mới
        body = existing
    else:  # chưa có, hoặc file hỏng (ghi dở khi tắt máy…) → ghi bản mới
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(body))
    return rel.as_posix(), hashlib.sha256(body).hexdigest()


def _read_existing(target: Path) -> bytes | None:
    if not target.exists():
        return None
    try:
        return gzip.decompress(target.read_bytes())
    except (OSError, EOFError):
        return None


def load(raw_dir: Path, rel_path: str) -> dict:
    return json.loads(gzip.decompress((raw_dir / rel_path).read_bytes()))
