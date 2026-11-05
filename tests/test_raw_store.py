"""Bản gốc: lớp chặn PII chung cho mọi nguồn + tên file an toàn + cập nhật hạn nộp khi tin đã có."""

import gzip
import json

import pytest

from jobmarket.pipeline import RunStats, save_job
from jobmarket.sources.base import RawJob
from jobmarket.store import raw
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


def test_sanitize_drops_nested_contact_keys_and_scrubs_strings():
    removed: set[str] = set()
    out = raw.sanitize(
        {
            "item": {
                "employer_info": {"name": "Công ty A", "assigned_staff_username": "abc@x.vn", "phone": "0912345678"},
                "note": "Gọi 0912 345 678 hoặc mail hr@x.com.vn",
                "salary_min": 15_000_000,
            }
        },
        removed,
    )
    assert out["item"]["employer_info"] == {"name": "Công ty A"}
    assert "@" not in out["item"]["note"] and "0912" not in out["item"]["note"]
    assert out["item"]["salary_min"] == 15_000_000
    assert {"contact_field", "email", "phone"} <= removed


def test_save_writes_sanitized_file(tmp_path):
    rel, _ = raw.save(tmp_path, "src", "123", {"item": {"contact_email": "a@b.com", "title": "Kế toán"}})
    body = json.loads(gzip.decompress((tmp_path / rel).read_bytes()))
    assert body["item"] == {"title": "Kế toán"} and "contact_field" in body["pii_removed"]


@pytest.mark.parametrize("bad", ["../../escaped", "a/b", "..", "x" * 65, ""])
def test_save_rejects_unsafe_job_id(tmp_path, bad):
    with pytest.raises(ValueError):
        raw.save(tmp_path / "raw", "src", bad, {})
    assert not any(tmp_path.rglob("*.json.gz"))


def start_run(conn, source: str) -> int:
    return conn.execute(
        "INSERT INTO runs (source, scope, started_at, app_version) VALUES (?, '{}', '2026-01-01T00:00:00', 'test')",
        (source,),
    ).lastrowid


def _job(job_id: str, deadline: str | None) -> RawJob:
    return RawJob(source_job_id=job_id, url=None, title="Kế toán", company_name="Công ty A", deadline=deadline)


def test_unsafe_id_from_source_is_rejected_not_crashing(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    stats = RunStats()
    save_job(conn, tmp_cfg, "src", set(), _job("../../x", None), start_run(conn, "src"), stats)
    assert stats.posts_rejected == 1 and conn.execute("SELECT COUNT(*) FROM job_posts").fetchone()[0] == 0


def test_extended_deadline_is_updated_and_reopens(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    run = start_run(conn, "src")
    save_job(conn, tmp_cfg, "src", set(), _job("1", "2026-01-01"), run, RunStats(), seen_at="2026-01-02T08:00:00")
    conn.execute("UPDATE job_posts SET status = 'expired'")
    save_job(conn, tmp_cfg, "src", set(), _job("1", "2099-12-31"), run, RunStats(), seen_at="2026-01-05T08:00:00")
    assert tuple(conn.execute("SELECT deadline, status FROM job_posts").fetchone()) == ("2099-12-31", "unknown")
    # File lưu tay CŨ hơn lần thấy cuối không được ghi đè hạn nộp mới
    save_job(conn, tmp_cfg, "src", set(), _job("1", "2026-02-01"), run, RunStats(), seen_at="2026-01-03T08:00:00")
    assert conn.execute("SELECT deadline FROM job_posts").fetchone()[0] == "2099-12-31"


def test_active_job_not_seen_again_expires(tmp_cfg):  # noqa: F811
    from jobmarket.pipeline import mark_expired

    conn = init_db(tmp_cfg.path("db_file"))
    run = start_run(conn, "src")
    save_job(conn, tmp_cfg, "src", set(), _job("1", None), run, RunStats(), seen_at="2026-01-02T08:00:00")
    conn.execute("UPDATE job_posts SET status = 'active'")
    assert mark_expired(conn, "src", before="2026-02-01T00:00:00") == 1
    assert conn.execute("SELECT status FROM job_posts").fetchone()[0] == "expired"


def test_existing_file_is_kept_and_hash_matches_disk(tmp_path):
    import hashlib

    rel, first = raw.save(tmp_path, "careerviet", "35B09EDD", {"v": 1})
    rel2, second = raw.save(tmp_path, "careerviet", "35B09EDD", {"v": 2})
    on_disk = gzip.decompress((tmp_path / rel).read_bytes())
    assert rel == rel2 and first == second == hashlib.sha256(on_disk).hexdigest()
    assert raw.load(tmp_path, rel)["v"] == 1


def test_corrupt_existing_file_is_rewritten(tmp_path):
    rel, _ = raw.save(tmp_path, "careerviet", "35B09EDD", {"v": 1})
    (tmp_path / rel).write_bytes(gzip.compress(b'{"v": 1}')[:10])  # gzip cắt cụt
    raw.save(tmp_path, "careerviet", "35B09EDD", {"v": 2})
    assert raw.load(tmp_path, rel)["v"] == 2
