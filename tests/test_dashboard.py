"""Dashboard chạy thật (Streamlit AppTest) trên dữ liệu mẫu: không lỗi, hiện đúng số của mockup."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from jobmarket.sources.clean_csv import import_clean_csv
from jobmarket.store.db import init_db

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["overview", "jobs", "demand", "salary", "trends", "reports", "quality"]


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    db_file = tmp_path / "demo.db"
    import_clean_csv(init_db(db_file), ROOT / "tests" / "fixtures" / "mockup_jobs.csv")
    monkeypatch.setenv("JOBMARKET_DB", str(db_file))
    return db_file


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    db_file = tmp_path / "empty.db"
    init_db(db_file)
    monkeypatch.setenv("JOBMARKET_DB", str(db_file))
    return db_file


def run(page: str) -> AppTest:
    return AppTest.from_file(str(ROOT / "dashboard" / "pages" / f"{page}.py"), default_timeout=60).run()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_error(demo_db, page):
    at = run(page)
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("page", PAGES)
def test_page_shows_empty_state_without_data(empty_db, page):
    at = run(page)
    assert not at.exception, [e.value for e in at.exception]
    assert any("Chưa có dữ liệu" in i.value for i in at.info)


def test_overview_numbers_match_mockup(demo_db):
    metrics = {m.label: m.value for m in run("overview").metric}
    assert metrics["Tin duy nhất"] == "348"
    assert metrics["Đang tuyển"] == "64"
    assert metrics["Tin mới 30 ngày"] == "59"
    assert metrics["Số công ty"] == "71"
    assert metrics["Lương trung vị"] == "14,5 triệu"
    assert metrics["Tin có lương số"] == "57,8%"


def test_footer_states_unit_and_salary_note(demo_db):
    captions = " ".join(c.value for c in run("demand").caption)
    assert "số tin tuyển dụng duy nhất" in captions and "chưa phân biệt Gross/Net" in captions
    assert "Ngày chốt số liệu: 31/10/2026" in captions


def test_filter_from_session_state(demo_db):
    at = AppTest.from_file(str(ROOT / "dashboard" / "pages" / "overview.py"), default_timeout=60)
    at.session_state["f_period"] = "Toàn bộ"
    at.session_state["f_provinces"] = ["HN"]
    at.run()
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["Tin duy nhất"]) < 348


def test_app_entrypoint_runs(demo_db):
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]


def test_jobs_page_lists_and_searches(demo_db):
    at = AppTest.from_file(str(ROOT / "dashboard" / "pages" / "jobs.py"), default_timeout=60)
    at.session_state["f_period"] = "Toàn bộ"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("348 tin" in cap.value for cap in at.caption)
    table = at.dataframe[0].value
    assert {"Tiêu đề", "Công ty", "Lương", "Link"} <= set(table.columns)
    at.text_input(key="jobs_query").input("lap trinh vien").run()  # gõ không dấu vẫn tìm được
    assert not at.exception
    found = at.dataframe[0].value
    assert 0 < len(found) < 348 and found["Tiêu đề"].str.contains("Lập trình viên", case=False).all()


def test_hidden_by_period_notice(demo_db):
    at = AppTest.from_file(str(ROOT / "dashboard" / "pages" / "overview.py"), default_timeout=60)
    at.session_state["f_period"] = "30 ngày gần nhất"
    at.run()
    assert any("nằm ngoài khoảng thời gian" in i.value for i in at.info)


def test_salary_text_vectorized():
    import importlib.util
    import sys

    import pandas as pd

    sys.path.insert(0, str(ROOT / "dashboard"))
    spec = importlib.util.spec_from_file_location("jobs_page_fmt", ROOT / "dashboard" / "pages" / "jobs.py")
    src = (ROOT / "dashboard" / "pages" / "jobs.py").read_text(encoding="utf-8")
    ns: dict = {"pd": pd}
    exec(src[src.index("def salary_text") : src.index("if not c.empty_state")], ns)  # chỉ lấy hàm, không chạy trang
    df = pd.DataFrame({
        "salary_type": ["range", "fixed", "up_to", "from", "negotiable", "not_stated", "range"],
        "salary_from": [15e6, 8.5e6, None, 10e6, None, None, None],
        "salary_to": [20e6, 8.5e6, 30e6, None, None, None, None],
    })  # fmt: skip
    assert ns["salary_text"](df).tolist() == [
        "15 – 20 triệu", "8,5 triệu", "Tới 30 triệu", "Từ 10 triệu", "Thoả thuận", "Không ghi", "Không ghi",
    ]  # fmt: skip
    assert spec is not None


@pytest.fixture
def mixed_db(tmp_path, monkeypatch, tmp_cfg):  # noqa: F811
    """Dữ liệu crawl (mockup, 348 tin duy nhất) + bộ lịch sử VietJobs (5 tin mẫu)."""
    from jobmarket.sources.vietjobs import import_vietjobs

    db_file = tmp_path / "mixed.db"
    conn = init_db(db_file)
    import_clean_csv(conn, ROOT / "tests" / "fixtures" / "mockup_jobs.csv")
    import_vietjobs(conn, tmp_cfg, ROOT / "tests" / "fixtures" / "vietjobs_sample.csv")
    monkeypatch.setenv("JOBMARKET_DB", str(db_file))
    return conn


def _overview(dataset=None, period="Toàn bộ"):
    at = AppTest.from_file(str(ROOT / "dashboard" / "pages" / "overview.py"), default_timeout=60)
    at.session_state["f_period"] = period
    if dataset:
        at.session_state["f_dataset"] = dataset
    return at.run()


def test_datasets_are_not_mixed(mixed_db):
    current = _overview()
    assert not current.exception
    assert {m.label: m.value for m in current.metric}["Tin duy nhất"] == "348"  # mặc định: chỉ dữ liệu crawl
    history = _overview("history")
    assert not history.exception, [e.value for e in history.exception]
    metrics = {m.label: m.value for m in history.metric}
    assert metrics["Tin duy nhất"] == "5"
    assert metrics["Đang tuyển"] == "—" and metrics["Tin mới 30 ngày"] == "—"  # ảnh chụp: không có chỉ số thời gian
    assert any("ảnh chụp 07–10/2025" in i.value for i in history.info)


def test_missing_dimension_shows_note_instead_of_unclassified_chart(mixed_db):
    from jobmarket import labels

    for (label_id,) in mixed_db.execute("SELECT id FROM labels WHERE label_type = 'industry'").fetchall():
        labels.apply(mixed_db, label_id, "ignore", reason="kiểm thử")
    at = _overview()
    assert not at.exception
    assert any("không cung cấp **ngành**" in i.value for i in at.info)


from tests.test_pipeline import tmp_cfg  # noqa: E402, F401  (fixture dùng chung)
