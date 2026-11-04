import pytest

from jobmarket.normalize.salary import parse_salary

M = 1_000_000


@pytest.mark.parametrize(
    ("raw", "kind", "lo", "hi"),
    [
        ("15 - 20 triệu", "range", 15 * M, 20 * M),
        ("12 - 18 triệu", "range", 12 * M, 18 * M),
        ("6.8 - 10 triệu", "range", 6.8 * M, 10 * M),
        ("6,8 - 10 triệu", "range", 6.8 * M, 10 * M),
        ("70 - 200 triệu", "range", 70 * M, 200 * M),
        ("20 - 15 triệu", "range", 15 * M, 20 * M),
        ("10 đến 15 triệu", "range", 10 * M, 15 * M),
        ("15tr - 20tr", "range", 15 * M, 20 * M),
        ("15M - 20M", "range", 15 * M, 20 * M),
        ("15 triệu", "fixed", 15 * M, 15 * M),
        ("15 - 15 triệu", "fixed", 15 * M, 15 * M),
        ("15.000.000 - 20.000.000 VNĐ", "range", 15 * M, 20 * M),
        ("8.000.000 VND", "fixed", 8 * M, 8 * M),
        ("Tới 30 triệu", "up_to", None, 30 * M),
        ("Lên đến 25 triệu", "up_to", None, 25 * M),
        ("Up to $2,000", "up_to", None, 2000 * 25500),
        ("Từ 60 triệu", "from", 60 * M, None),
        ("Trên 10 triệu", "from", 10 * M, None),
        ("1,000 - 3,000 USD", "range", 1000 * 25500, 3000 * 25500),
        ("$800 - $1,200", "range", 800 * 25500, 1200 * 25500),
        ("120 - 240 triệu/năm", "range", 10 * M, 20 * M),
        ("5 - 7 triệu/tuần", "range", 5 * M * 4.33, 7 * M * 4.33),
        ("100 - 150 nghìn/giờ", "range", 100_000 * 208, 150_000 * 208),
    ],
)
def test_parse_numeric(raw, kind, lo, hi, norm_cfg):
    s = parse_salary(raw, norm_cfg)
    assert s.type == kind
    assert s.from_vnd == pytest.approx(lo) if lo is not None else s.from_vnd is None
    assert s.to_vnd == pytest.approx(hi) if hi is not None else s.to_vnd is None
    assert s.flags == []


@pytest.mark.parametrize("raw", ["Thoả thuận", "Thỏa thuận", "Thương lượng", "Cạnh tranh", "Negotiable", "Competitive"])
def test_negotiable(raw, norm_cfg):
    s = parse_salary(raw, norm_cfg)
    assert (s.type, s.from_vnd, s.to_vnd) == ("negotiable", None, None)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_not_stated(raw, norm_cfg):
    assert parse_salary(raw, norm_cfg).type == "not_stated"


def test_vnd_without_unit_is_ambiguous_not_guessed(norm_cfg):
    s = parse_salary("15 - 20", norm_cfg)
    assert "salary_ambiguous" in s.flags
    assert s.from_vnd is None and s.to_vnd is None


def test_text_without_number_is_ambiguous(norm_cfg):
    assert "salary_ambiguous" in parse_salary("Lương hấp dẫn triệu", norm_cfg).flags


@pytest.mark.parametrize("raw", ["0.1 - 0.5 triệu", "600 - 900 triệu"])
def test_out_of_valid_range_is_invalid(raw, norm_cfg):
    s = parse_salary(raw, norm_cfg)
    assert "salary_invalid" in s.flags
    assert s.from_vnd is None and s.to_vnd is None
    assert s.type == "range"


@pytest.mark.parametrize("raw", ["2 triệu - 5 triệu", "2.5 - 3.5 triệu", "1.0 triệu"])
def test_intern_and_part_time_pay_is_valid(raw, norm_cfg):
    """Lương thực tập/part-time 1–3 triệu là dữ liệu thật (CareerLink, VietJobs) — ngưỡng chỉ bắt lỗi đơn vị."""
    s = parse_salary(raw, norm_cfg)
    assert "salary_invalid" not in s.flags and s.from_vnd is not None


@pytest.mark.parametrize(
    ("raw", "tax"), [("20 - 30 triệu (Gross)", "gross"), ("20 triệu NET", "net"), ("20 triệu", "unknown")]
)
def test_tax(raw, tax, norm_cfg):
    assert parse_salary(raw, norm_cfg).tax == tax


def test_renormalize_salaries_updates_stored_rows(tmp_path):
    """Đổi ngưỡng lương → `renormalize` sửa tin đã lưu, giữ cờ khác, không đụng tin reject."""
    import dataclasses

    from jobmarket.config import load_config
    from jobmarket.pipeline import renormalize_salaries
    from jobmarket.store.db import init_db

    cfg = load_config()
    conn = init_db(tmp_path / "s.db")
    with conn:
        conn.execute("INSERT INTO runs (id, source, scope, started_at, app_version) VALUES (1, 'x', '{}', 'd', 't')")
        for i, (raw, status, flags) in enumerate(
            [("2 triệu - 5 triệu", "warn", "salary_invalid,posted_missing"), ("20 triệu", "reject", "missing_title")]
        ):
            conn.execute(
                "INSERT INTO job_posts (source, source_job_id, title, salary_raw, salary_type, status, dq_status, "
                "dq_flags, dedup_key, first_seen_at, last_seen_at, first_run_id, last_run_id, normalizer_version) "
                "VALUES ('x', ?, 't', ?, 'range', 'unknown', ?, ?, ?, 'd', 'd', 1, 1, 'v')",
                (str(i), raw, status, flags, f"k{i}"),
            )
    cfg = dataclasses.replace(cfg, raw={**cfg.raw, "normalize": {**cfg.normalize, "salary_min_valid": 1_000_000}})
    assert renormalize_salaries(conn, cfg, "x") == {"checked": 2, "changed": 2}
    rows = conn.execute("SELECT salary_from, salary_to, dq_status, dq_flags FROM job_posts ORDER BY id").fetchall()
    assert tuple(rows[0]) == (2_000_000, 5_000_000, "warn", "posted_missing")
    assert tuple(rows[1])[2:] == ("reject", "missing_title")
