"""Làm sạch PII trong bản gốc ĐÃ LƯU (data/raw) bằng đúng lớp chặn đang dùng khi ghi (`raw.sanitize`).

Dùng khi phát hiện bản gốc cũ lọt dữ liệu cá nhân (2026-11-04: email nhân viên trong vieclam24h). Ghi đè tại chỗ —
không giữ bản sao (giữ bản sao = giữ lại chính dữ liệu cá nhân cần xoá) — và cập nhật `job_posts.raw_sha256`.

    .venv/Scripts/python scripts/scrub_raw.py            # chạy thử: chỉ đếm
    .venv/Scripts/python scripts/scrub_raw.py --apply    # ghi đè
"""

import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobmarket.config import load_config  # noqa: E402
from jobmarket.store.db import connect  # noqa: E402
from jobmarket.store.raw import sanitize  # noqa: E402


def main(apply: bool) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    raw_dir = cfg.path("raw_dir")
    conn = connect(cfg.path("db_file"))
    changed: dict[str, int] = {}
    for path in sorted(raw_dir.rglob("*.json.gz")):
        payload = json.loads(gzip.decompress(path.read_bytes()))
        removed: set[str] = set()
        clean = sanitize(payload, removed)
        if not removed:
            continue
        clean["pii_removed"] = sorted(set(clean.get("pii_removed") or []) | removed)
        source = path.relative_to(raw_dir).parts[0]
        changed[source] = changed.get(source, 0) + 1
        if apply:
            body = json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8")
            path.write_bytes(gzip.compress(body))
            conn.execute(
                "UPDATE job_posts SET raw_sha256 = ? WHERE raw_path = ?",
                (hashlib.sha256(body).hexdigest(), path.relative_to(raw_dir).as_posix()),
            )
    if apply:
        conn.commit()
    print(("Đã làm sạch" if apply else "Cần làm sạch (chạy thử)"), changed or "0 file")


if __name__ == "__main__":
    main("--apply" in sys.argv)
