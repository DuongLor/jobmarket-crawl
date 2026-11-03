"""Tầng 2 CareerLink: bổ sung từ trang chi tiết (JSON-LD). Mạng giả lập, không gọi ra ngoài."""

from pathlib import Path

import httpx
import pytest

from jobmarket import stats
from jobmarket.fetch.client import PoliteClient
from jobmarket.pipeline import enrich_details, run_paged
from jobmarket.sources import careerlink
from jobmarket.store.db import init_db
from tests.test_pipeline import CHALLENGE, tmp_cfg  # noqa: F401  (fixture dùng chung)

FIX = Path(__file__).parent / "fixtures" / "careerlink"
LISTING = (FIX / "listing_sample.html").read_text(encoding="utf-8")
DETAIL = (FIX / "detail_sample.html").read_text(encoding="utf-8")
EMPTY = "<html><body>trống</body></html>"


def test_parse_detail_fields():
    d = careerlink.parse_detail(DETAIL)
    assert d["industry"] == "Khách sạn"
    assert d["experience"] == "no requirements"
    assert (d["posted_date"], d["deadline"], d["employment_type"]) == ("2026-11-03", "2026-12-03", "FULL_TIME")
    assert "chấm công" in d["description_text"]
    assert "example.com" not in d["description_text"] and "0912" not in d["description_text"]  # đã lọc PII
    assert set(d["pii_removed"]) == {"email", "phone"}
    assert "description" not in d["jsonld"]


def test_parse_detail_without_jsonld():
    assert careerlink.parse_detail("<html><body>không có JSON-LD</body></html>") is None


def site(detail_status=200, detail_html=DETAIL, block_detail=False):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path.startswith("/tim-viec-lam/"):
            if block_detail:
                return httpx.Response(403, text=CHALLENGE)
            return httpx.Response(detail_status, text=detail_html)
        if int(request.url.params.get("page", 1)) > 1:
            return httpx.Response(200, text=EMPTY)
        return httpx.Response(200, text=LISTING)

    return handler, calls


def client(cfg, handler):
    return PoliteClient(cfg.crawl, transport=httpx.MockTransport(handler))


@pytest.fixture
def loaded(tmp_cfg):  # noqa: F811
    conn = init_db(tmp_cfg.path("db_file"))
    handler, _ = site()
    run_paged(conn, tmp_cfg, client(tmp_cfg, handler), careerlink, max_pages=1)  # detail_max_per_run mặc định 0
    return conn


def test_enrich_fills_industry_experience_dates(loaded, tmp_cfg):  # noqa: F811
    conn = loaded
    assert conn.execute("SELECT COUNT(*) FROM job_posts WHERE detail_fetched_at IS NULL").fetchone()[0] == 6
    handler, calls = site()
    counts = enrich_details(conn, tmp_cfg, client(tmp_cfg, handler), careerlink, limit=2)
    assert counts == {"enriched": 2, "removed": 0, "no_jsonld": 0}
    assert len([u for u in calls if "/tim-viec-lam/" in u]) == 2  # đúng ngân sách
    df = stats.load_unique(conn)
    done = df[df["status"] == "active"]
    assert len(done) == 2
    row = conn.execute(
        "SELECT p.*, li.raw_label ind, le.master_code exp FROM job_posts p "
        "JOIN labels li ON li.id = p.industry_label_id JOIN labels le ON le.id = p.experience_label_id "
        "WHERE p.detail_fetched_at IS NOT NULL LIMIT 1"
    ).fetchone()
    assert (row["ind"], row["exp"]) == ("Khách sạn", "NONE")  # "no requirements" → chưa có kinh nghiệm
    assert (row["posted_date"], row["deadline"], row["employment_type"]) == ("2026-11-03", "2026-12-03", "FULL_TIME")
    assert conn.execute("SELECT COUNT(*) FROM job_posts WHERE detail_fetched_at IS NULL").fetchone()[0] == 4


def test_enrich_marks_removed_on_404(loaded, tmp_cfg):  # noqa: F811
    handler, _ = site(detail_status=404, detail_html="not found")
    counts = enrich_details(loaded, tmp_cfg, client(tmp_cfg, handler), careerlink, limit=1)
    assert counts["removed"] == 1
    assert loaded.execute("SELECT COUNT(*) FROM job_posts WHERE status = 'removed'").fetchone()[0] == 1


def test_enrich_stops_when_blocked(loaded, tmp_cfg):  # noqa: F811
    from jobmarket.fetch.client import Blocked

    handler, calls = site(block_detail=True)
    with pytest.raises(Blocked):
        enrich_details(loaded, tmp_cfg, client(tmp_cfg, handler), careerlink, limit=5)
    assert len([u for u in calls if "/tim-viec-lam/" in u]) == 1  # dừng ngay, không thử lại
    assert loaded.execute("SELECT COUNT(*) FROM job_posts WHERE detail_fetched_at IS NOT NULL").fetchone()[0] == 0


def test_run_paged_does_detail_phase_with_budget(tmp_cfg):  # noqa: F811
    import dataclasses

    raw = {**tmp_cfg.raw, "sources": {**tmp_cfg.raw["sources"]}}
    raw["sources"]["careerlink"] = {**raw["sources"]["careerlink"], "detail_max_per_run": 3}
    cfg = dataclasses.replace(tmp_cfg, raw=raw)
    conn = init_db(cfg.path("db_file"))
    handler, calls = site()
    run_id = run_paged(conn, cfg, client(cfg, handler), careerlink, max_pages=1)
    run = conn.execute("SELECT status, scope FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "success" and '"enriched": 3' in run["scope"]
    assert len([u for u in calls if "/tim-viec-lam/" in u]) == 3


ROBOT_CHECK = "<html><body><h2>Xác nhận bạn không phải robot</h2>Chúng tôi nhận thấy có điều bất thường.</body></html>"


def _sequence_site(pages: list[str]):
    """Trang chi tiết trả lần lượt theo danh sách `pages` (mỗi request lấy 1 phần tử)."""
    served = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        served.append(str(request.url))
        return httpx.Response(200, text=pages[min(len(served), len(pages)) - 1])

    return handler, served


def _fetched(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM job_posts WHERE detail_fetched_at IS NOT NULL").fetchone()[0]


def test_robot_check_page_with_http_200_is_blocked(loaded, tmp_cfg):  # noqa: F811
    """CareerLink 2026-11-03: trang chi tiết trả 200 kèm hộp "Xác nhận bạn không phải robot" → dừng, không vượt."""
    from jobmarket.fetch.client import Blocked

    handler, served = _sequence_site([ROBOT_CHECK])
    with pytest.raises(Blocked):
        enrich_details(loaded, tmp_cfg, client(tmp_cfg, handler), careerlink, limit=5)
    assert len(served) == 1 and _fetched(loaded) == 0


def test_pages_without_jsonld_in_a_row_stop_and_stay_pending(loaded, tmp_cfg):  # noqa: F811
    """Trang rỗng liên tiếp = nghi bị hạn chế ngầm → dừng, KHÔNG đánh dấu đã đọc (lần sau đọc lại)."""
    from jobmarket.fetch.client import Blocked

    handler, served = _sequence_site([EMPTY, EMPTY, EMPTY, DETAIL])
    with pytest.raises(Blocked):
        enrich_details(loaded, tmp_cfg, client(tmp_cfg, handler), careerlink, limit=5)
    assert len(served) == 3 and _fetched(loaded) == 0


def test_single_page_without_jsonld_is_marked_once_source_proves_healthy(loaded, tmp_cfg):  # noqa: F811
    handler, _ = _sequence_site([EMPTY, DETAIL])
    counts = enrich_details(loaded, tmp_cfg, client(tmp_cfg, handler), careerlink, limit=2)
    assert (counts["no_jsonld"], counts["enriched"]) == (1, 1)
    assert _fetched(loaded) == 2
