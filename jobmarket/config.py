import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Trỏ sang DB khác mà không sửa config (vd dashboard trên dữ liệu mẫu data/demo.db)
DB_ENV = "JOBMARKET_DB"


@dataclass(frozen=True)
class Config:
    raw: dict
    root: Path

    def path(self, key: str) -> Path:
        if key == "db_file" and os.environ.get(DB_ENV):
            return (self.root / os.environ[DB_ENV]).resolve()
        return self.root / self.raw["paths"][key]

    @property
    def quality(self) -> dict:
        return self.raw["quality"]

    @property
    def crawl(self) -> dict:
        return self.raw["crawl"]

    @property
    def normalize(self) -> dict:
        return self.raw["normalize"]

    @property
    def stats(self) -> dict:
        return self.raw["stats"]

    def source(self, name: str) -> dict:
        return self.raw["sources"][name]


def load_config(path: Path | None = None) -> Config:
    path = path or PROJECT_ROOT / "config.toml"
    with path.open("rb") as f:
        return Config(raw=tomllib.load(f), root=path.parent)
