from pathlib import Path

import pytest

from jobmarket.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_db_override(monkeypatch):
    """Biến JOBMARKET_DB của người dùng không được làm test ghi vào DB thật."""
    monkeypatch.delenv("JOBMARKET_DB", raising=False)


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def norm_cfg(cfg):
    return cfg.normalize


@pytest.fixture
def topcv_listing_html() -> str:
    return (FIXTURES / "topcv" / "listing_sample.html").read_text(encoding="utf-8")
