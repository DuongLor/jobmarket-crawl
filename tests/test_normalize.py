import pytest

from jobmarket.dedup import company_core, dedup_key
from jobmarket.normalize.experience import experience_code, experience_code_from_years
from jobmarket.normalize.level import level_from_title
from jobmarket.normalize.location import split_location
from jobmarket.normalize.text import norm_key
from jobmarket.pii import scrub
from jobmarket.quality import assess


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("Không yêu cầu", "NONE"),
        ("Không yêu cầu kinh nghiệm", "NONE"),
        ("Chưa có kinh nghiệm", "NONE"),
        ("Dưới 1 năm", "LT_1Y"),
        ("6 tháng", "LT_1Y"),
        ("1 năm", "Y1_2"),
        ("Trên 1 năm", "Y1_2"),
        ("2 năm", "Y2_3"),
        ("2 - 4 năm", "Y2_3"),
        ("3 năm", "Y3_5"),
        ("4 năm", "Y3_5"),
        ("5 năm", "Y5P"),
        ("Trên 5 năm", "Y5P"),
        ("10 năm", "Y5P"),
    ],
)
def test_experience(raw, code):
    assert experience_code(raw) == code


@pytest.mark.parametrize("raw", ["Có kinh nghiệm", "Kinh nghiệm thực tế", "Dưới 2 năm", "", None])
def test_experience_unreadable_is_not_guessed(raw):
    assert experience_code(raw) is None


@pytest.mark.parametrize(("years", "code"), [(0, "NONE"), (1, "Y1_2"), (2.5, "Y2_3"), (7, "Y5P"), (None, None)])
def test_experience_from_years(years, code):
    assert experience_code_from_years(years) == code


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("Senior Data Engineer (ONSITE VPBANK)", "SENIOR"),
        ("Thực Tập Sinh Marketing", "INTERN"),
        ("Intern Frontend Developer", "INTERN"),
        ("Junior PHP Developer", "JUNIOR"),
        ("Fresher Java", "JUNIOR"),
        ("Trưởng Nhóm Kinh Doanh", "LEAD"),
        ("Tech Lead .NET", "LEAD"),
        ("Trưởng Phòng Nhân Sự", "MANAGER"),
        ("Phó Giám Đốc Kinh Doanh", "DIRECTOR"),
        ("Giám đốc Senior Partnership", "DIRECTOR"),
        ("Chuyên Viên Kinh Doanh", None),
        ("Account Manager", None),
        ("Kế toán tổng hợp", None),
    ],
)
def test_level_from_title(title, code):
    assert level_from_title(title) == code


@pytest.mark.parametrize(
    ("raw", "first", "count"),
    [
        ("Hà Nội", "Hà Nội", 1),
        ("Hồ Chí Minh (mới)", "Hồ Chí Minh", 1),
        ("Hà Nội & 4 nơi khác", "Hà Nội", 5),
        ("Hồ Chí Minh (mới) & Khánh Hòa (mới)", "Hồ Chí Minh", 2),
        ("Bình Dương, Đồng Nai", "Bình Dương", 2),
    ],
)
def test_split_location(raw, first, count):
    loc = split_location(raw)
    assert (loc.first, loc.count) == (first, count)


def test_split_location_empty():
    assert split_location("") is None


def test_norm_key():
    assert norm_key("  Đà Nẵng (mới) ") == "da nang moi"
    assert norm_key("TP.HCM") == "tp hcm"


def test_dedup_key_ignores_company_form_case_and_accents():
    a = dedup_key("CÔNG TY TNHH Phần mềm Sao Mai", "Senior Lập trình viên Backend", "HN")
    b = dedup_key("Công ty Cổ phần phần mềm sao mai", "senior lap trinh vien backend", "HN")
    assert a == b
    assert a != dedup_key("Công ty TNHH Phần mềm Sao Mai", "Senior Lập trình viên Backend", "HCM")
    assert company_core("Công ty TNHH MTV Đầu tư ABC") == "dau tu abc"


def test_pii_scrub():
    text, removed = scrub(
        "Liên hệ: hr.team@abc.com.vn, 0912 345 678 hoặc +84 912.345.678, zalo https://zalo.me/0912345678"
    )
    assert "abc.com.vn" not in text and "0912" not in text and "zalo.me" not in text
    assert set(removed) == {"email", "phone", "social"}


@pytest.mark.parametrize(
    "text", ["0912 345 678", "0912.345.678", "0912345678", "+84 912 345 678", "+84912345678", "024 3826 1234"]
)
def test_pii_phone_formats(text):
    scrubbed, removed = scrub(f"Gọi {text} ngay")
    assert removed == ["phone"] and text not in scrubbed


@pytest.mark.parametrize(
    "text",
    [
        '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2"/>',
        '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0"/>',
        "Lương 0.5 - 1.2 tỷ/năm, mã tin 3665468, năm 2026",
    ],
)
def test_pii_does_not_touch_svg_or_numbers(text):
    assert scrub(text) == (text, [])


def test_pii_scrub_keeps_salary_numbers():
    text, removed = scrub("Lương 15.000.000 - 20.000.000 VND, mã tin 2345128")
    assert removed == [] and "15.000.000" in text


CRAWLED = {"source_job_id", "title", "company_name"}  # nguồn crawl có đủ 3 trường bắt buộc


@pytest.mark.parametrize(
    ("fields", "flags", "provides", "expected"),
    [
        ({"source_job_id": "1", "title": "A", "company_name": "B"}, [], CRAWLED, ("ok", [])),
        (
            {"source_job_id": "1", "title": "A", "company_name": "B"},
            ["salary_invalid"],
            CRAWLED,
            ("warn", ["salary_invalid"]),
        ),
        (
            {"source_job_id": "1", "title": "A", "company_name": ""},
            [],
            CRAWLED,
            ("reject", ["missing_company"]),
        ),
        (
            {"source_job_id": None, "title": "A", "company_name": "B"},
            [],
            CRAWLED,
            ("reject", ["missing_source_job_id"]),
        ),
        (
            {"source_job_id": "1", "title": "A", "company_name": "B"},
            [],
            CRAWLED | {"posted_date"},
            ("warn", ["posted_missing"]),
        ),
        # Nguồn không cung cấp tên công ty (bộ dữ liệu VietJobs) → không reject vì thiếu công ty
        ({"source_job_id": "h", "title": "A"}, [], {"source_job_id", "title"}, ("ok", [])),
        ({"source_job_id": "h", "title": ""}, [], {"source_job_id", "title"}, ("reject", ["missing_title"])),
    ],
)
def test_quality(fields, flags, provides, expected):
    assert assess(fields, flags, provides) == expected


@pytest.mark.parametrize(
    "text", ["Liên hệ 0912 345 678.", "Gọi 0912.345.678, gặp chị Lan", "(0912345678)", "SĐT:0912345678."]
)
def test_pii_phone_at_sentence_end_or_punctuation(text):
    scrubbed, removed = scrub(text)
    assert removed == ["phone"] and "0912" not in scrubbed
