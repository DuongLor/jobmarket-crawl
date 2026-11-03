"""Thống kê trên 374 dòng mẫu (tab Data_Jobs) phải ra ĐÚNG số in trong docs/mockup.xlsx + docs/ui-lowfi.html.

Ngày chốt 2026-10-31, tháng báo cáo 2026-10 (tab Params). Bảng theo địa phương không so: mockup dùng tên
tỉnh cũ (Bình Dương…), dự án quy về 34 tỉnh mới (PLAN Q8).
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from jobmarket import stats
from jobmarket.sources.clean_csv import import_clean_csv
from jobmarket.store.db import init_db

AS_OF = date(2026, 10, 31)
REPORT_MONTH = date(2026, 10, 1)
M = 1_000_000


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = init_db(tmp_path_factory.mktemp("mockup") / "mockup.db")
    _, inserted = import_clean_csv(db, Path(__file__).parent / "fixtures" / "mockup_jobs.csv")
    assert inserted == 374
    return db


@pytest.fixture(scope="module")
def df(conn):
    return stats.load_unique(conn)


def test_dashboard_cards(df):
    o = stats.overview(df, AS_OF, window_days=30)
    assert o["unique_jobs"] == 348
    assert o["active"] == 64
    assert o["new_window"] == 59 and o["prev_window"] == 55
    assert o["new_delta"] == pytest.approx(0.0727272727)
    assert o["companies"] == 71
    assert o["salary_median"] == pytest.approx(14.5 * M)
    assert o["salary_n"] == 201
    assert o["salary_coverage"] == pytest.approx(0.5775862069)


def test_r1_demand_by_industry(df):
    t = stats.demand_by(df, "industry", AS_OF, 30).set_index("industry")
    assert t["total"].sum() == 348
    cntt = t.loc["Công nghệ thông tin"]
    assert (cntt["total"], cntt["active"], cntt["new"], cntt["prev"], cntt["rank"]) == (75, 22, 21, 9, 1)
    assert cntt["share"] == pytest.approx(0.2155172414)
    assert cntt["delta"] == pytest.approx(1.333333333)
    assert t.loc["Tài chính – Kế toán", "rank"] == 9 and t.loc["Y tế – Dược", "rank"] == 10
    unclassified = t.loc["(chưa phân loại)"]
    assert unclassified["total"] == 17 and unclassified["rank"] is pd_na()


def pd_na():
    import pandas as pd

    return pd.NA


def test_r1_monthly(df):
    t = stats.monthly(df, REPORT_MONTH).set_index("month")
    assert t["total"].tolist() == [52, 58, 64, 60, 52, 62]
    assert t["topcv"].tolist() == [30, 26, 23, 34, 29, 30]
    assert t["vietnamworks"].tolist() == [22, 32, 41, 26, 23, 32]
    assert t.loc["2026-10", "mom"] == pytest.approx(0.1923076923)


def test_r1_heatmap_row_total(df):
    t = stats.heatmap(df)
    assert t.loc["Công nghệ thông tin", "Tổng"] == 75
    assert t.loc["Công nghệ thông tin", "Hà Nội"] == 28


@pytest.mark.parametrize(
    ("group", "jobs", "n", "mean", "median", "p25", "p75"),
    [
        ("Chưa có kinh nghiệm", 59, 35, 9.213571429, 8.5, 5.9375, 12),
        ("Dưới 1 năm", 27, 13, 9.192307692, 9, 7, 10),
        ("1 – dưới 2 năm", 69, 43, 12.91686047, 12, 10.75, 15),
        ("2 – dưới 3 năm", 77, 42, 16.62619048, 16, 13.5, 21),
        ("3 – dưới 5 năm", 75, 39, 24.38397436, 23.5, 19.5, 29.5),
        ("Từ 5 năm trở lên", 32, 21, 38.70357143, 38, 29.5, 47),
    ],
)
def test_r2_salary_by_experience(df, group, jobs, n, mean, median, p25, p75):
    row = stats.salary_by(df, "experience", min_n=5).set_index("experience").loc[group]
    assert (row["jobs"], row["salary_n"]) == (jobs, n)
    for key, expected in (("mean", mean), ("median", median), ("p25", p25), ("p75", p75)):
        assert row[key] == pytest.approx(expected * M), key


def test_r2_salary_order_and_small_sample(df):
    t = stats.salary_by(df, "level", min_n=5)
    assert t["level"].tolist()[:7] == [
        "Thực tập sinh", "Nhân viên mới (Junior)", "Nhân viên", "Nhân viên cấp cao (Senior)",
        "Trưởng nhóm", "Quản lý / Trưởng phòng", "Giám đốc",
    ]  # fmt: skip
    director = t.set_index("level").loc["Giám đốc"]
    assert (director["jobs"], director["salary_n"]) == (7, 4)
    assert director["coverage"] == pytest.approx(0.5714285714)
    assert {director[k] for k in ("mean", "median", "p25", "p75")} == {stats.SMALL_SAMPLE}
    intern = t.set_index("level").loc["Thực tập sinh"]
    assert intern["median"] == pytest.approx(5 * M) and intern["p75"] == pytest.approx(6.09375 * M)


def test_r4_trend(df):
    t = stats.trend(df, REPORT_MONTH, min_vol=10, threshold=0.10).set_index("industry")
    cntt = t.loc["Công nghệ thông tin"]
    assert cntt[["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]].tolist() == [
        3,
        11,
        12,
        19,
        9,
        21,
    ]
    assert (cntt["recent_3m"], cntt["prev_3m"], cntt["trend"]) == (49, 26, "↑ Tăng")
    assert cntt["delta"] == pytest.approx(0.8846153846)
    assert t.loc["Sản xuất – Cơ khí", "trend"] == "↓ Giảm"
    assert t.loc["Marketing – Truyền thông", "trend"] == "→ Ổn định"
    assert t.loc["Dịch vụ – Nhà hàng – Khách sạn", "trend"] == "→ Ổn định"  # Δ = −10% không vượt ngưỡng
    assert t.loc["Logistics – Xuất nhập khẩu", "trend"] == "Chưa đủ dữ liệu"  # 3T trước = 7 < 10
    assert "(chưa phân loại)" not in t.index
    assert t[["recent_3m", "prev_3m"]].sum().tolist() == [166, 165]


def test_months_of_data(df):
    assert stats.months_of_data(df) == 6


def test_months_of_data_without_known_dates(df):
    """Toàn tin tồn sẵn (event_date = NaT) → 0 tháng, không crash (từng làm sập Tổng quan/Xu hướng)."""
    assert stats.months_of_data(df.assign(event_date=pd.NaT)) == 0


def test_monthly_counts_are_integers(df):
    m = stats.monthly(df.assign(event_date=pd.NaT), date(2026, 10, 1))
    assert (m["total"] == 0).all() and m["total"].dtype.kind == "i"


def test_quality_by_source(conn):
    q = stats.quality(conn).set_index("source")
    topcv = q.loc["topcv"]
    assert (topcv["records"], topcv["unique_jobs"]) == (185, 172)
    expected = {
        "pct_reject": 0.01621621622,
        "pct_location": 0.9825581395,
        "pct_industry": 0.9534883721,
        "pct_occupation": 0.8953488372,
        "pct_experience": 0.9709302326,
        "pct_salary": 0.5988372093,
    }
    for key, value in expected.items():
        assert topcv[key] == pytest.approx(value), key
    assert (q.loc["vietnamworks", "records"], q.loc["vietnamworks", "unique_jobs"]) == (189, 176)


def test_dq_flag_counts(conn):
    counts = dict(stats.dq_flag_counts(conn).values)
    assert counts == {
        "location_unmapped": 5,
        "experience_unparsed": 9,
        "salary_invalid": 2,
        "salary_ambiguous": 3,
        "posted_missing": 4,
        "missing_company": 6,
    }


def test_salary_types(df):
    t = stats.salary_types(df).set_index("salary_type")
    assert t["jobs"].sum() == 348


def test_filters(conn):
    only_hn = stats.load_unique(conn, stats.Filters(provinces=["HN"], sources=["topcv"]))
    assert len(only_hn) > 0
    assert set(only_hn["province_code"]) == {"HN"} and set(only_hn["source"]) == {"topcv"}
    october = stats.load_unique(conn, stats.Filters(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)))
    assert len(october) == 62


def test_unknown_status_active_if_recently_seen():
    import pandas as pd

    df = pd.DataFrame(
        {
            "status": ["unknown", "unknown", "active", "expired"],
            "deadline": pd.to_datetime([None, None, None, None]),
            "last_seen_at": [
                "2026-10-30T02:00:00",
                "2026-10-01T02:00:00",
                "2026-01-01T00:00:00",
                "2026-10-31T00:00:00",
            ],
        }
    )
    assert stats.is_active(df, AS_OF).tolist() == [True, False, True, False]


def test_backlog_without_posted_date_not_counted_as_new(tmp_path):
    """Tin chưa có ngày đăng, thấy lần đầu trong ngày đầu thu thập = tồn sẵn → không phải "tin mới"."""
    import pandas as pd

    db = init_db(tmp_path / "b.db")
    with db:
        run = db.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES ('x', '{}', 't', 't')"
        ).lastrowid
        rows = [  # (id, first_seen, posted)
            ("1", "2026-11-03T02:00:00", None),  # ngày đầu, chưa có ngày đăng → tồn sẵn
            ("2", "2026-11-03T03:00:00", "2026-11-03"),  # ngày đầu nhưng biết ngày đăng → tin mới
            ("3", "2026-11-04T02:00:00", None),  # xuất hiện sau ngày đầu → ngày thấy ≈ ngày đăng
        ]
        for jid, seen, posted in rows:
            db.execute(
                "INSERT INTO job_posts (source, source_job_id, title, dq_status, dedup_key, "
                "first_seen_at, last_seen_at, "
                "first_run_id, last_run_id, normalizer_version, posted_date) "
                "VALUES ('x', ?, ?, 'ok', ?, ?, ?, ?, ?, 1, ?)",
                (jid, f"T{jid}", jid, seen, seen, run, run, posted),
            )
    df = stats.load_unique(db).set_index("source_job_id")
    assert df["date_known"].to_dict() == {"1": False, "2": True, "3": True}
    assert pd.isna(df.loc["1", "event_date"])
    o = stats.overview(df.reset_index(), date(2026, 11, 5), 30)
    assert (o["unique_jobs"], o["new_window"]) == (3, 2)


def test_wide_salary_range_excluded(tmp_path):
    db = init_db(tmp_path / "w.db")
    with db:
        run = db.execute(
            "INSERT INTO runs (source, scope, started_at, app_version) VALUES ('x', '{}', 't', 't')"
        ).lastrowid
        for jid, lo, hi in (("ok", 10e6, 20e6), ("wide", 10e6, 200e6)):
            db.execute(
                "INSERT INTO job_posts (source, source_job_id, title, dq_status, dedup_key, "
                "first_seen_at, last_seen_at, "
                "first_run_id, last_run_id, normalizer_version, salary_type, salary_from, salary_to) "
                "VALUES ('x', ?, ?, 'ok', ?, '2026-11-03T00:00:00', '2026-11-03T00:00:00', ?, ?, 1, 'range', ?, ?)",
                (jid, jid, jid, run, run, lo, hi),
            )
    df = stats.load_unique(db).set_index("source_job_id")
    assert df.loc["ok", "salary_mid"] == 15e6
    assert pd_isna(df.loc["wide", "salary_mid"]) and df.loc["wide", "salary_excluded"] == "wide_range"
    assert stats.load_unique(db, max_range_ratio=0).set_index("source_job_id").loc["wide", "salary_mid"] == 105e6


def pd_isna(v):
    import pandas as pd

    return pd.isna(v)


def test_company_identity_merges_same_legal_form_only():
    k = stats.company_identity
    assert k("Công Ty Cổ Phần Bán Lẻ Kỹ Thuật Số FPT") == k("Công Ty CP Bán Lẻ Kỹ Thuật Số FPT")
    assert k("Công ty TNHH Phân phối Sao Mai") != k("Công ty CP Phân phối Sao Mai")
    assert k("") == ""
