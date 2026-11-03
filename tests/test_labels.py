from pathlib import Path

import pytest

from jobmarket import labels, stats
from jobmarket.sources.clean_csv import import_clean_csv
from jobmarket.store.db import init_db

FIXTURE = Path(__file__).parent / "fixtures" / "mockup_jobs.csv"


@pytest.fixture
def conn(tmp_path):
    db = init_db(tmp_path / "labels.db")
    import_clean_csv(db, FIXTURE)
    return db


def _unclassified_industry(conn) -> dict:
    return dict(conn.execute("SELECT * FROM labels WHERE label_type = 'industry' AND status = 'unmapped'").fetchone())


def test_table_counts_posts_and_sorts(conn):
    t = labels.table(conn, label_type="industry")
    assert t["post_count"].is_monotonic_decreasing
    assert t["post_count"].sum() == 374  # mọi tin đều trỏ tới 1 nhãn ngành


def test_mapping_changes_stats_immediately_and_logs(conn):
    before = stats.load_unique(conn)
    n_affected = int((before["industry_name"] == stats.UNCLASSIFIED["industry"]).sum())
    assert n_affected == 17
    # Nhãn tách theo nguồn: "(chưa phân loại)" của TopCV và của VietnamWorks là 2 nhãn riêng
    pending = conn.execute("SELECT id FROM labels WHERE label_type = 'industry' AND status = 'unmapped'").fetchall()
    assert len(pending) == 2

    for (label_id,) in pending:
        assert labels.apply(conn, label_id, "map", "IND01", reason="kiểm thử") is True
    after = stats.load_unique(conn)
    assert (after["industry_name"] == stats.UNCLASSIFIED["industry"]).sum() == 0
    assert (after["industry_code"] == "IND01").sum() == (before["industry_code"] == "IND01").sum() + n_affected

    log = labels.history(conn)
    newest = log.iloc[0]
    assert (newest["old_value"], newest["new_value"], newest["reason"], newest["via"]) == (
        "unmapped", "mapped:IND01", "kiểm thử", "dashboard"
    )  # fmt: skip


def test_ignore_keeps_jobs_counted_as_unclassified(conn):
    label = _unclassified_industry(conn)
    labels.apply(conn, label["id"], "ignore")
    df = stats.load_unique(conn)
    assert len(df) == 348
    assert (df["industry_name"] == stats.UNCLASSIFIED["industry"]).sum() == 17


def test_same_action_twice_is_noop(conn):
    label = _unclassified_industry(conn)
    assert labels.apply(conn, label["id"], "map", "IND02") is True
    assert labels.apply(conn, label["id"], "map", "IND02") is False
    entries = conn.execute("SELECT COUNT(*) FROM label_history WHERE label_id = ?", (label["id"],)).fetchone()[0]
    assert entries == 1


@pytest.mark.parametrize(
    ("action", "code", "message"),
    [
        ("map", "KHONG_CO", "không có trong danh mục"),
        ("map", None, "không có trong danh mục"),
        ("xoa", None, "không hợp lệ"),
    ],
)
def test_invalid_actions(conn, action, code, message):
    label = _unclassified_industry(conn)
    with pytest.raises(labels.LabelError, match=message):
        labels.apply(conn, label["id"], action, code)


def test_add_master(conn):
    code = labels.add_master(conn, "industry", "  Bảo hiểm  ")
    assert code == "IND16" and labels.masters(conn, "industry")[code] == "Bảo hiểm"  # IND01–15 có sẵn trong seed
    with pytest.raises(labels.LabelError, match="đã có"):
        labels.add_master(conn, "industry", "bao hiem")
    occ = labels.add_master(conn, "occupation", "Chuyên viên bảo hiểm", industry_code=code)
    assert labels.masters(conn, "occupation")[occ] == "Chuyên viên bảo hiểm"
    with pytest.raises(labels.LabelError, match="cố định"):
        labels.add_master(conn, "location", "Tỉnh mới")


def test_suggest():
    candidates = {"IND01": "Công nghệ thông tin", "IND03": "Kinh doanh – Bán hàng"}
    assert labels.suggest("Công nghệ Thông tin / Viễn thông", candidates) == "Công nghệ thông tin"
    assert labels.suggest("Nông - Lâm - Ngư nghiệp", candidates) is None


def test_remap_rules_maps_districts_and_prefixes(tmp_path):
    from jobmarket.pipeline import label_id

    db = init_db(tmp_path / "remap.db")
    ids = {raw: label_id(db, "location", "vietjobs", raw) for raw in ("hà đông", "tp vinh", "quận 7", "san francisco")}
    status = dict(db.execute("SELECT raw_label, master_code FROM labels"))
    assert status == {"hà đông": "HN", "tp vinh": "NA", "quận 7": "HCM", "san francisco": None}
    # mô phỏng nhãn cũ nạp trước khi có alias: đưa về unmapped rồi remap
    with db:
        db.execute("UPDATE labels SET status = 'unmapped', master_code = NULL WHERE id = ?", (ids["hà đông"],))
    assert labels.remap_rules(db) == {"location": 1}
    assert labels.remap_rules(db) == {}  # chạy lại không đổi gì


@pytest.mark.parametrize("ambiguous", ["Hoàng Mai", "Sơn Tây", "Tân Phú", "Bình Tân", "Châu Thành", "Tân Uyên"])
def test_ambiguous_district_names_not_mapped(tmp_path, ambiguous):
    from jobmarket.pipeline import label_id

    db = init_db(tmp_path / "amb.db")
    label = label_id(db, "location", "vietjobs", ambiguous)
    assert db.execute("SELECT status FROM labels WHERE id = ?", (label,)).fetchone()[0] == "unmapped"


def test_location_flag_follows_current_label_status(conn):
    """Cờ location_unmapped ghi lúc nạp; sau khi ánh xạ / bỏ qua nhãn, bảng cờ phải đổi theo (không lỗi thời)."""
    flagged = [r[0] for r in conn.execute("SELECT id FROM job_posts WHERE dq_flags LIKE '%location_unmapped%'")]
    assert len(flagged) == 5
    with conn:
        label = conn.execute(
            "INSERT INTO labels (label_type, source, raw_label, norm_key, first_seen_at) "
            "VALUES ('location', 'topcv', 'Tokyo', 'tokyo', '2026-10-01T00:00:00')"
        ).lastrowid
        conn.execute(f"UPDATE job_posts SET location_label_id = ? WHERE id IN ({flagged[0]}, {flagged[1]})", (label,))
    assert dict(stats.dq_flag_counts(conn).values)["location_unmapped"] == 5  # nhãn vẫn chưa ánh xạ
    labels.apply(conn, label, "ignore", reason="kiểm thử")
    after = dict(stats.dq_flag_counts(conn).values)
    assert (after["location_unmapped"], after["location_ignored"]) == (3, 2)
