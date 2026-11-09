"""Phân loại giống CareerLink (migration 009): danh mục 2 cấp (danh mục → nhóm), mọi nơi làm việc của tin, tên tỉnh."""

from datetime import date

import pandas as pd
import pytest

from jobmarket import labels, stats
from jobmarket.normalize.location import split_location
from jobmarket.pipeline import RunStats, assign_categories, backfill_locations, label_id, save_job, set_locations
from jobmarket.sources import careerlink
from jobmarket.sources.base import RawJob
from jobmarket.store.db import init_db
from tests.test_paged_sources import CL_HTML
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


@pytest.fixture
def conn(tmp_cfg):  # noqa: F811
    c = init_db(tmp_cfg.path("db_file"))
    with c:
        c.execute("INSERT INTO runs (id, source, scope, started_at, app_version) VALUES (1, 'x', '{}', 'x', 't')")
    return c


def _one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def _category(conn, source, raw):
    lid = label_id(conn, "industry", source, raw)
    return tuple(conn.execute("SELECT status, master_code, category_code FROM labels WHERE id = ?", (lid,)).fetchone())


def test_province_name_follows_careerlink(conn):
    assert _one(conn, "SELECT name FROM master_provinces WHERE code = 'HCM'") == "Hồ Chí Minh"


def test_split_location_keeps_every_named_place():
    loc = split_location("Hà Nội, Hồ Chí Minh, Hà Nội")
    assert (loc.first, loc.count, loc.parts) == ("Hà Nội", 3, ["Hà Nội", "Hồ Chí Minh"])
    assert split_location("Hà Nội & 2 nơi khác").parts == ["Hà Nội"]


def test_careerlink_labels_get_their_category_and_group(conn):
    assert _category(conn, "careerlink", "Kế toán / Kiểm toán") == ("mapped", "IND04", "CAT01")
    assert _category(conn, "careerlink", "IT - Phần mềm") == ("mapped", "IND01", "CAT07")  # slug cũ, cùng mã 19
    assert _category(conn, "careerlink", "Tiếng Nhật")[2] is None  # không phải ngành
    assert _category(conn, "careerlink", "Kế toán / Kiểm toán, Thư ký / Hành chánh")[2] is None  # nhãn ghép cũ


def test_new_careerlink_category_is_added_with_careerlink_name(conn):
    status, group, code = _category(conn, "careerlink", "Hàng không")
    assert code and _one(conn, "SELECT name FROM master_categories WHERE code = ?", code) == "Hàng không"
    assert (status, group) == ("unmapped", None)  # chưa có nhóm → chờ ánh xạ, ánh xạ xong danh mục nhận nhóm
    lid = label_id(conn, "industry", "careerlink", "Hàng không")
    labels.apply(conn, lid, "map", "IND10", reason="kiểm thử", via="cli")
    assign_categories(conn)
    assert _one(conn, "SELECT industry_code FROM master_categories WHERE code = ?", code) == "IND10"


def test_other_sources_get_category_only_when_equivalent(conn):
    assert _category(conn, "careerviet", "Hành chính / Thư ký")[2] == "CAT03"
    assert _category(conn, "vieclam24h", "Chăm sóc khách hàng")[2] == "CAT06"
    assert _category(conn, "vietjobs", "tài chính kế toán ngân hàng bảo hiểm")[2] is None  # nhóm rộng: chỉ nhóm
    assert _category(conn, "careerviet", "Luật / Pháp lý")[2] is None  # CareerLink chưa biết danh mục này
    assert _one(conn, "SELECT COUNT(*) FROM master_categories") == 23  # nguồn khác không thêm


def _job(job_id: str, locations: str) -> RawJob:
    return RawJob(
        source_job_id=job_id, url=f"https://x/{job_id}", title=f"Tin {job_id}", company_name="Cty",
        location_raw=locations, industry_label="Kế toán / Kiểm toán", posted_date="2026-11-09",
    )  # fmt: skip


def _save(conn, cfg, *jobs: RawJob) -> None:
    with conn:
        for job in jobs:
            save_job(conn, cfg, "careerlink", careerlink.PROVIDES, job, 1, RunStats())


def _locations(conn, job_id: str) -> list[str]:
    sql = (
        "SELECT v.province_name FROM v_job_locations v JOIN job_posts p ON p.id = v.job_id "
        "WHERE p.source_job_id = ? ORDER BY v.position"
    )
    return [r[0] for r in conn.execute(sql, (job_id,))]


def test_job_keeps_every_location_and_updates_on_newer_sighting(tmp_cfg, conn):  # noqa: F811
    _save(conn, tmp_cfg, _job("1", "Hà Nội, Hồ Chí Minh"))
    assert _locations(conn, "1") == ["Hà Nội", "Hồ Chí Minh"]
    _save(conn, tmp_cfg, _job("1", "Đà Nẵng"))
    assert _locations(conn, "1") == ["Đà Nẵng"]


def test_province_counts_job_in_every_place_and_filter_matches_any(tmp_cfg, conn):  # noqa: F811
    _save(conn, tmp_cfg, _job("1", "Hà Nội, Hồ Chí Minh"), _job("2", "Hồ Chí Minh"))
    df = stats.load_unique(conn)
    t = stats.demand_by(df, "province", date(2026, 11, 9), 30).set_index("province")["total"]
    assert (t["Hồ Chí Minh"], t["Hà Nội"]) == (2, 1)  # tổng 3 > 2 tin: đếm đủ ở mỗi nơi
    hn = stats.apply_filters(df, stats.Filters(provinces=["HN"]))
    assert hn["source_job_id"].tolist() == ["1"]  # nơi thứ hai cũng khớp lọc
    cats = stats.demand_by(df.assign(industry_basis="item"), "category", date(2026, 11, 9), 30)
    assert cats.set_index("category")["total"]["Kế toán / Kiểm toán"] == 2


def test_category_allocation_splits_multi_category_job():
    df = pd.DataFrame(
        {
            "id": [1],
            "category_codes": [(("CAT01", "Kế toán / Kiểm toán", 1), ("CAT02", "Ngân hàng", 2))],
            "category_code": ["CAT01"],
            "category_name": ["Kế toán / Kiểm toán"],
            "category_order": [1],
        }
    )
    out = stats.allocate(df, "category")
    assert out["weight"].tolist() == [0.5, 0.5] and set(out["category_name"]) == {"Kế toán / Kiểm toán", "Ngân hàng"}


def test_backfill_locations_reads_saved_cards(tmp_cfg, conn):  # noqa: F811
    jobs = careerlink.parse_listing(CL_HTML, None)
    _save(conn, tmp_cfg, *jobs)
    with conn:
        conn.execute("DELETE FROM job_locations")
        conn.execute("UPDATE job_posts SET location_raw = NULL")
    counts = backfill_locations(conn, tmp_cfg)
    assert counts["jobs"] == len(jobs) and counts["from_raw"] == len(jobs)
    assert _one(conn, "SELECT COUNT(*) FROM job_posts WHERE location_raw IS NULL") == 0


def test_dashboard_cache_version_changes_on_location_backfill(tmp_cfg, conn):  # noqa: F811
    """Dashboard nạp lại khi backfill-locations/backfill/remap đổi bảng phụ mà không đổi job_posts."""
    import re
    from pathlib import Path

    src = (Path(__file__).parents[1] / "dashboard" / "common.py").read_text(encoding="utf-8")
    sql = re.search(r'DATA_VERSION_SQL = """(.*?)"""', src, re.S).group(1)
    _save(conn, tmp_cfg, _job("1", "Hà Nội"))
    before = conn.execute(sql).fetchone()
    with conn:
        set_locations(conn, 1, "careerlink", "Hồ Chí Minh")
    assert tuple(conn.execute(sql).fetchone()) != tuple(before)
