import os
from enum import Enum

from common import config


def check_repository():
    """Ensure every configured output/input directory exists. Idempotent."""
    for path in config.general['paths'].values():
        os.makedirs(path, exist_ok=True)


def init_repository() -> None:
    """
    Idempotent startup routine, safe to call multiple times.

    Ensures every configured output/input directory exists. This replaces
    the import-time side effects that used to live in `src/__init__.py`.
    Call it explicitly once at app startup (e.g. from a FastAPI lifespan)
    -- never rely on it running implicitly via import.

    Used to also seed input/s&p500.csv from a committed s&p500/s&p500.csv --
    removed once common.portfolio.load_universe() switched to fetching the
    tracked universe live from Wikipedia (see common/portfolio.py).
    """
    check_repository()


class FileEnumFactory:
    def __init__(self, base_path: str):
        self.base_path = base_path

    def get_files(self, exclude_keyword: str = "farmed"):
        return [
            f for f in os.listdir(self.base_path)
            if exclude_keyword not in f
        ]

    def build_enum(self, name: str = "File"):
        files = self.get_files()
        return Enum(name, {c: c for c in files})
