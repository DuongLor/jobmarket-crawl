"""CareerViet: thẻ tin trang danh sách — mẫu rút gọn từ trang thật 2026-11-04."""

from pathlib import Path

from jobmarket.sources import careerviet as cv

HTML = (Path(__file__).parent / "fixtures" / "careerviet" / "listing_sample.html").read_text(encoding="utf-8")


def test_parse_listing_fields():
    jobs = {j.source_job_id: j for j in cv.parse_listing(HTML, None)}
    assert set(jobs) == {"35C8CE8E", "35C8CE7D", "35C8CE68"}
    j = jobs["35C8CE7D"]
    assert j.title == "Beauty Consultant (English speaker)/ Tư Vấn Viên Tiếng Anh - Rohto Aohal Clinic"
    assert j.company_name == "CÔNG TY TNHH ROHTO-MENTHOLATUM (VIỆT NAM)"
    assert j.salary_raw == "12 - 14 Triệu VND"  # bỏ tiền tố "Lương:"
    assert j.location_raw == "Hồ Chí Minh, Hà Nội"  # nhiều tỉnh tách đúng, không dính chữ
    assert (j.posted_date, j.deadline) == ("2026-11-04", "2026-11-30")
    assert j.industry_label == "Dịch vụ khách hàng"
    assert j.url.startswith("https://careerviet.vn/vi/tim-viec-lam/") and j.url.endswith(".35C8CE7D.html")
    assert jobs["35C8CE8E"].salary_raw == "Cạnh tranh"


def test_page_url():
    first = "https://careerviet.vn/viec-lam/tat-ca-viec-lam-vi.html"
    assert cv.page_url(first, 1) == first
    assert cv.page_url(first, 3) == "https://careerviet.vn/viec-lam/tat-ca-viec-lam-trang-3-vi.html"
    assert cv.page_url("https://careerviet.vn/viec-lam/tat-ca-viec-lam-trang-3-vi.html", 1) == first


def test_last_page_from_embedded_page_count():
    assert cv.last_page('...\\"itemCount\\":18485,\\"pageCount\\":370,\\"hasNextPage\\":true...', "") == 370
    assert cv.last_page("<html></html>", "") is None
