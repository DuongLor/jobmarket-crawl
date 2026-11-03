"""Báo cáo tháng: khớp tab R5 của mockup, chốt là cố định, không chốt lại."""

from pathlib import Path

import openpyxl
import pytest

from jobmarket import labels, report
from jobmarket.sources.clean_csv import import_clean_csv
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)

FIXTURE = Path(__file__).parent / "fixtures" / "mockup_jobs.csv"


@pytest.fixture
def conn(tmp_cfg):  # noqa: F811
    db = init_db(tmp_cfg.path("db_file"))
    import_clean_csv(db, FIXTURE)
    return db


def test_indicators_match_mockup_r5(conn, tmp_cfg):  # noqa: F811
    p = report.build(conn, tmp_cfg, "2026-10")
    ind = {i["name"]: i for i in p["indicators"]}
    assert (ind["Tin tuyển dụng mới"]["current"], ind["Tin tuyển dụng mới"]["previous"]) == (62, 52)
    assert ind["Tin tuyển dụng mới"]["change"] == pytest.approx(0.1923076923)
    assert (ind["Công ty mới xuất hiện"]["current"], ind["Công ty mới xuất hiện"]["previous"]) == (1, 2)
    assert (ind["Số tin có lương số (n)"]["current"], ind["Số tin có lương số (n)"]["previous"]) == (28, 30)
    assert ind["Lương trung vị (VND/tháng)"]["current"] == pytest.approx(12_500_000)
    assert ind["Lương trung vị (VND/tháng)"]["previous"] == pytest.approx(14_750_000)
    assert ind["Tỷ lệ tin có lương số"]["current"] == pytest.approx(0.4516129032)
    top = p["top_industries"][0]
    assert (top["industry"], top["jobs"]) == ("Công nghệ thông tin", 21)
    assert top["share"] == pytest.approx(0.3387096774) and top["delta_prev_month"] == pytest.approx(1.333333333)
    assert any("Công nghệ thông tin (+88,5%)" in line for line in p["insights"])


def test_publish_is_frozen_and_not_overwritten(conn, tmp_cfg):  # noqa: F811
    payload, path = report.publish(conn, tmp_cfg, "2026-10")
    assert path.exists()
    assert openpyxl.load_workbook(path).sheetnames == report.SHEET_NAMES
    # đổi ánh xạ sau khi chốt: báo cáo đã chốt KHÔNG đổi, bản nháp thì đổi
    cntt_labels = [
        r[0] for r in conn.execute("SELECT id FROM labels WHERE label_type = 'industry' AND master_code = 'IND01'")
    ]
    for label_id in cntt_labels:
        labels.apply(conn, label_id, "ignore", reason="kiểm thử")
    assert report.load_published(conn, "2026-10")["top_industries"][0]["industry"] == "Công nghệ thông tin"
    assert report.build(conn, tmp_cfg, "2026-10")["top_industries"][0]["industry"] != "Công nghệ thông tin"
    with pytest.raises(report.ReportError, match="đã chốt"):
        report.publish(conn, tmp_cfg, "2026-10")


def test_empty_period_raises(conn, tmp_cfg):  # noqa: F811
    with pytest.raises(report.ReportError, match="Không có tin"):
        report.build(conn, tmp_cfg, "2019-01")


def test_periods_available(conn):
    assert report.periods_available(conn)[:2] == ["2026-10", "2026-09"]
