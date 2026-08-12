import sqlite3

import pytest
from fastapi.testclient import TestClient

from news_nlp import db as db_module

ARTICLES_SCHEMA = """
CREATE TABLE articles (
    id INTEGER PRIMARY KEY,
    ticker TEXT,
    company TEXT,
    title TEXT,
    author TEXT,
    pub_date TEXT,
    body_text TEXT,
    word_count INTEGER,
    source_domain TEXT,
    fetch_status TEXT
)
"""


def seed_article(conn, id, company="3M", ticker="MMM", title="Test Title",
                  pub_date="2023-01-15T00:00:00Z", body_text="Body text.",
                  word_count=2, source_domain="example.com", fetch_status="ok"):
    conn.execute(
        """INSERT INTO articles
           (id, ticker, company, title, author, pub_date, body_text, word_count, source_domain, fetch_status)
           VALUES (?, ?, ?, ?, 'Author', ?, ?, ?, ?, ?)""",
        (id, ticker, company, title, pub_date, body_text, word_count, source_domain, fetch_status),
    )


@pytest.fixture
def test_db_path(tmp_path):
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
def conn(test_db_path):
    c = db_module.connect(test_db_path)
    yield c
    c.close()


@pytest.fixture
def client(test_db_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    from apps.news_nlp_api import app, pipeline_status
    pipeline_status.reset()
    yield TestClient(app)
    pipeline_status.reset()
