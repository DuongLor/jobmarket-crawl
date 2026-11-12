"""VietnamWorks: API tìm kiếm JSON (POST) → tin đủ trường, không lưu người liên hệ/email. Mạng giả lập, dữ liệu giả."""

import copy
import dataclasses
import json

import httpx

import jobmarket.__main__ as cli
from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import run_search_api
from jobmarket.sources import vietnamworks as vnw
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


def _job(job_id: int, **over) -> dict:
    job = {
        "jobId": job_id,
        "jobTitle": f"Kế toán tổng hợp {job_id}",
        "jobUrl": f"https://www.vietnamworks.com/ke-toan-tong-hop-{job_id}-jv",
        "companyName": "Công ty Mẫu",
        "isAnonymous": False,
        "salaryMin": 12_000_000,
        "salaryMax": 16_000_000,
        "salaryCurrency": "VND",
        "isSalaryVisible": True,
        "jobLevelVI": "Nhân viên",
        "yearsOfExperience": 2,
        "jobFunction": {"parentNameVI": "Kế Toán", "children": [{"nameVI": "Kế Toán Tổng Hợp"}]},
        "industriesV3": [{"industryV3NameVI": "Sản xuất"}],
        "workingLocations": [{"cityNameVI": "Hồ Chí Minh"}, {"cityNameVI": "Hà Nội"}, {"cityNameVI": "Hồ Chí Minh"}],
        "approvedOn": "2026-11-10T09:00:00+07:00",
        "expiredOn": "2026-12-10T23:59:59+07:00",
        "skills": [{"skillName": "Excel"}, {"skillName": "MISA"}],
        "jobDescription": "<p>- Lập báo cáo thuế</p><p>- Liên hệ 0901234567</p>",
        "jobRequirement": "<p>- Tốt nghiệp đại học</p><p>- Gửi CV về hr@example.com</p>",
        "contactName": "Người Liên Hệ Giả",
        "emailAddress": "hr@example.com",
    }
    return job | over


def _page(jobs: list[dict], pages: int, total: int) -> str:
    meta = {"code": 200, "nbHits": total, "nbPages": pages}
    return json.dumps({"meta": meta, "data": jobs}, ensure_ascii=False)


def test_parse_job_maps_fields_and_drops_contact():
    job = vnw.parse_job(_job(2130001))
    assert (job.source_job_id, job.company_name, job.salary_raw) == ("2130001", "Công ty Mẫu", "12 triệu - 16 triệu")
    assert job.location_raw == "Hồ Chí Minh, Hà Nội" and job.experience_raw == "2 năm"
    assert (job.level_raw, job.industry_label, job.occupation_label) == ("Nhân viên", "Kế Toán", "Kế Toán Tổng Hợp")
    assert (job.posted_date, job.deadline, job.skills_raw) == ("2026-11-10", "2026-12-10", "Excel, MISA")
    assert "Lập báo cáo thuế" in job.description_text and "0901234567" not in job.description_text
    assert "hr@example.com" not in job.requirements_text and job.pii_removed
    stored = json.dumps(job.raw_payload, ensure_ascii=False)
    assert "Người Liên Hệ Giả" not in stored and "hr@example.com" not in stored and "Lập báo cáo" not in stored


def test_salary_experience_and_anonymous_company():
    assert vnw.salary_text({"isSalaryVisible": False, "salaryMin": 5, "salaryMax": 9}) == "Thoả thuận"
    assert vnw.salary_text({"isSalaryVisible": True, "salaryMin": 0, "salaryMax": 0}) == "Thoả thuận"
    usd = {"isSalaryVisible": True, "salaryMin": 1000, "salaryMax": 2000, "salaryCurrency": "USD"}
    assert vnw.salary_text(usd) == "1000 USD - 2000 USD"
    big = {"isSalaryVisible": True, "salaryMin": 14_000_000, "salaryMax": 20_000_000, "salaryCurrency": "USD"}
    assert vnw.salary_text(big) == "14000000 USD - 20000000 USD"  # không dạng mũ "1.4e+07"
    assert vnw.salary_text({"isSalaryVisible": True, "salaryMin": 15_000_000}) == "Từ 15 triệu"
    assert vnw.experience_text(0) is None and vnw.experience_text(3) == "3 năm"  # 0 = nguồn không ghi rõ
    assert vnw.parse_job(_job(2130002, isAnonymous=True)).company_name is None
    assert vnw.parse_job(_job(0)) is None


def _cfg(tmp_cfg, **src):  # noqa: F811
    raw = copy.deepcopy(tmp_cfg.raw)
    raw["sources"]["vietnamworks"].update(enabled=True, min_delay_seconds=0, **src)
    return dataclasses.replace(tmp_cfg, raw=raw)


def _client(pages: dict[int, str], seen: list):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = json.loads(request.content)
        seen.append((request.method, body["page"], body["hitsPerPage"]))
        return httpx.Response(200, text=pages.get(body["page"], _page([], 3, 0)))

    return PoliteClient({"user_agent": "t", "min_delay_seconds": 0, "max_retries": 0, "timeout_seconds": 5},
                        transport=httpx.MockTransport(handler))  # fmt: skip


def test_run_reads_all_pages_and_saves_jobs(tmp_cfg):  # noqa: F811
    cfg = _cfg(tmp_cfg, per_page=2, max_pages=10, stop_after_known_pages=0)
    pages = {0: _page([_job(1), _job(2)], 2, 3), 1: _page([_job(3)], 2, 3)}
    seen: list = []
    conn = init_db(cfg.path("db_file"))
    run_id = run_search_api(conn, cfg, _client(pages, seen), vnw)
    assert seen == [("POST", 0, 2), ("POST", 1, 2)]  # dừng ở trang cuối nguồn báo (nbPages = 2)
    run = conn.execute("SELECT status, posts_new FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert (run["status"], run["posts_new"]) == ("success", 3)
    row = conn.execute("SELECT * FROM job_posts WHERE source_job_id = '1'").fetchone()
    assert row["experience_min_months"] == 24 and "Lập báo cáo thuế" in row["description_text"]


def test_normal_run_stops_after_known_pages_but_full_reads_all(tmp_cfg):  # noqa: F811
    cfg = _cfg(tmp_cfg, per_page=1, max_pages=10, stop_after_known_pages=1)
    pages = {i: _page([_job(i + 1)], 5, 5) for i in range(5)}
    conn = init_db(cfg.path("db_file"))
    run_search_api(conn, cfg, _client(pages, []), vnw)  # lần đầu: mọi tin đều mới → đọc 5 trang
    seen: list = []
    run_search_api(conn, cfg, _client(pages, seen), vnw)
    assert len(seen) == 1  # lần sau: trang đầu toàn tin đã biết → dừng
    seen.clear()
    run_search_api(conn, cli.full_config(cfg, ["vietnamworks"]), _client(pages, seen), vnw)
    assert len(seen) == 5


def test_blocked_403_stops_and_counts_as_block(tmp_cfg):  # noqa: F811
    cfg = _cfg(tmp_cfg)

    def handler(request):
        return httpx.Response(404 if request.url.path == "/robots.txt" else 403)

    client = PoliteClient({"user_agent": "t", "min_delay_seconds": 0, "max_retries": 0, "timeout_seconds": 5},
                          transport=httpx.MockTransport(handler))  # fmt: skip
    conn = init_db(cfg.path("db_file"))
    run_id = run_search_api(conn, cfg, client, vnw)
    row = conn.execute("SELECT status, error_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "failed" and json.loads(row["error_sample"])[0].startswith("Blocked")
