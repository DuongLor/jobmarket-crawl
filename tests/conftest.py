import dataclasses
from pathlib import Path

import pytest

from jobmarket.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_db_override(monkeypatch):
    """Biến JOBMARKET_DB của người dùng không được làm test ghi vào DB thật."""
    monkeypatch.delenv("JOBMARKET_DB", raising=False)


@pytest.fixture(autouse=True)
def _no_real_mysql(monkeypatch):
    """Test không bao giờ ghi vào MySQL thật của người dùng (test riêng tự giả lập kết nối)."""

    def refuse(_cfg):
        raise AssertionError("test không được kết nối MySQL thật")

    monkeypatch.setattr("jobmarket.mysql_mirror.connect", refuse)


@pytest.fixture
def cfg():
    c = load_config()
    return dataclasses.replace(c, raw={**c.raw, "mysql": {**c.raw.get("mysql", {}), "enabled": False}})


@pytest.fixture
def norm_cfg(cfg):
    return cfg.normalize


@pytest.fixture
def topcv_listing_html() -> str:
    return (FIXTURES / "topcv" / "listing_sample.html").read_text(encoding="utf-8")
