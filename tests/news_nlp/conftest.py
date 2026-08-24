import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from news_nlp import db as db_module

ARTICLES_SCHEMA = """
CREATE TABLE articles (
    id INTEGER PRIMARY KEY,
    ticker TEXT,
    company TEXT,
    gics_sector TEXT,
    gics_sub_industry TEXT,
    title TEXT,
    author TEXT,
    pub_date TEXT,
    fetched_at TEXT,
    body_text TEXT,
    word_count INTEGER,
    source_domain TEXT,
    fetch_status TEXT,
    http_status_code INTEGER
)
"""


def seed_article(
    conn: sqlite3.Connection,
    id: int,
    company: str = "3M",
    ticker: str = "MMM",
    title: str = "Test Title",
    pub_date: str | None = "2023-01-15T00:00:00Z",
    body_text: str = "Body text.",
    word_count: int = 2,
    source_domain: str = "example.com",
    fetch_status: str = "ok",
    gics_sector: str = "Industrials",
    gics_sub_industry: str = "Industrial Conglomerates",
    fetched_at: str = "2023-01-15T00:00:00Z",
    http_status_code: int = 200,
) -> None:
    conn.execute(
        """INSERT INTO articles
           (id, ticker, company, gics_sector, gics_sub_industry, title, author, pub_date,
            fetched_at, body_text, word_count, source_domain, fetch_status, http_status_code)
           VALUES (?, ?, ?, ?, ?, ?, 'Author', ?, ?, ?, ?, ?, ?, ?)""",
        (
            id,
            ticker,
            company,
            gics_sector,
            gics_sub_industry,
            title,
            pub_date,
            fetched_at,
            body_text,
            word_count,
            source_domain,
            fetch_status,
            http_status_code,
        ),
    )


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)
    conn.executescript(ARTICLES_SCHEMA)
    conn.commit()
    conn.close()

    conn = db_module.connect(path)
    db_module.init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def conn(test_db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db_module.connect(test_db_path)
    yield c
    c.close()


@pytest.fixture
def client(test_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    # Deliberately deferred: importing apps.news_nlp_api pulls in news_nlp.pipeline,
    # which imports torch/transformers -- fine for the tests that use this fixture,
    # but every other test in the suite would otherwise pay that import cost too.
    from apps.news_nlp_api import app, pipeline_status  # noqa: PLC0415

    pipeline_status.reset()
    yield TestClient(app)
    pipeline_status.reset()
