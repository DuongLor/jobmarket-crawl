"""Bản gốc mỗi tin (đã xoá PII) dạng .json.gz — để chạy lại chuẩn hoá mà không crawl lại."""

import gzip
import hashlib
import json
from pathlib import Path

from jobmarket.timeutil import current_month


def save(raw_dir: Path, source: str, source_job_id: str, payload: dict) -> tuple[str, str]:
    """Ghi file, trả (đường dẫn tương đối so với raw_dir, sha256). Không ghi đè nếu đã có."""
    month = current_month()
    rel = Path(source) / month / f"{source_job_id}.json.gz"
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    target = raw_dir / rel
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(body))
    return rel.as_posix(), digest


def load(raw_dir: Path, rel_path: str) -> dict:
    return json.loads(gzip.decompress((raw_dir / rel_path).read_bytes()))
