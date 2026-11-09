"""Ánh xạ địa điểm theo quy tắc (2026-11-09): "tỉnh: địa chỉ", "tỉnh (quận", "quận - tỉnh", quận/huyện của tỉnh cũ."""

import pytest

from jobmarket.pipeline import _rule_mapping
from jobmarket.store.db import init_db
from tests.test_pipeline import tmp_cfg  # noqa: F401  (fixture dùng chung)


@pytest.fixture
def conn(tmp_cfg):  # noqa: F811
    return init_db(tmp_cfg.path("db_file"))


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("hồ chí minh: 17 công lý", "HCM"),
        ("hà nội (bắc từ liêm", "HN"),
        ("cầu giấy - hà nội", "HN"),
        ("tp vinh - nghệ an", "NA"),
        ("hồ chí minh: the rivus - phước thiện", "HCM"),
        ("quận phú nhuận", "HCM"),
        ("đắc nông", "LD"),  # viết sai, Đắk Nông sáp nhập vào Lâm Đồng
        ("duy tiên", "NB"),  # Hà Nam cũ
        ("phường phủ lý", "NB"),
        ("tx cai lậy", "DT"),  # Tiền Giang cũ
        ("Hồ Chí Minh", "HCM"),
    ],
)
def test_location_rules(conn, raw, code):
    assert _rule_mapping(conn, "location", raw) == code


@pytest.mark.parametrize(
    "raw", ["hà nội và hcm", "cần thơ/lâm đồng", "Toàn quốc", "tân phú", "bình tân", "hoàng mai", "seattle"]
)
def test_ambiguous_or_foreign_locations_stay_unmapped(conn, raw):
    assert _rule_mapping(conn, "location", raw) is None  # nhiều tỉnh / trùng tên nhiều tỉnh / nước ngoài: không đoán


def test_careerlink_categories_mapped(conn):
    assert _rule_mapping(conn, "industry", "Dược / Sinh học") == "IND08"
    assert _rule_mapping(conn, "industry", "Thư ký / Hành chánh") == "IND11"
    assert _rule_mapping(conn, "industry", "Tư vấn dịch vụ khách hàng") == "IND03"
