from jobmarket.sources import topcv


def test_category_pages(topcv_listing_html):
    pages = topcv.category_pages(topcv_listing_html)
    assert len(pages) == 25
    assert pages[0].url == "https://www.topcv.vn/tim-viec-lam-kinh-doanh-ban-hang-cr1"
    assert pages[0].industry_label == "Kinh doanh/Bán hàng"
    assert all("?page=" not in p.url for p in pages)


def test_parse_listing_fields(topcv_listing_html):
    jobs = topcv.parse_listing(topcv_listing_html, "Công nghệ Thông tin")
    assert len(jobs) == 8
    first = jobs[0]
    assert first.source_job_id == "2342204"
    # TopCV dùng 2 dạng URL: /viec-lam/<slug>/<id>.html và /brand/<cty>/tuyen-dung/<slug>-j<id>.html
    assert first.url.startswith("https://www.topcv.vn/") and "2342204" in first.url
    assert "?" not in first.url
    assert first.company_name == "Công ty Cổ phần MISA"
    assert first.salary_raw == "15 - 20 triệu"
    assert first.location_raw == "Hà Nội"
    assert first.experience_raw == "Không yêu cầu"
    assert first.industry_label == "Công nghệ Thông tin"
    assert first.posted_date is None and first.level_raw is None  # trang danh sách không có


def test_parse_listing_variants(topcv_listing_html):
    jobs = {j.source_job_id: j for j in topcv.parse_listing(topcv_listing_html, None)}
    assert jobs["2143663"].salary_raw == "1,000 - 3,000 USD"
    assert jobs["2323656"].location_raw == "Hà Nội & 4 nơi khác"
    assert jobs["2304851"].location_raw == "Hải Phòng (mới)"


def test_raw_payload_is_card_only(topcv_listing_html):
    job = topcv.parse_listing(topcv_listing_html, None)[0]
    assert set(job.raw_payload) == {"listing_card_html", "listing_industry"}
    assert "job-item-search-result" in job.raw_payload["listing_card_html"]
