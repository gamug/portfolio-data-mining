import sqlite3
from pathlib import Path

from conftest import seed_article

from news_nlp import db


def test_db_path_points_to_project_root_data_dir() -> None:
    # parents[0]=tests/news_nlp, [1]=tests, [2]=repo root -- one level
    # deeper than the original news-nlp repo's tests/test_db_module.py
    # (tests/ directly at repo root there) since this suite is namespaced
    # under tests/news_nlp/ alongside the other migrated modules' tests.
    project_root = Path(__file__).resolve().parents[2]
    assert project_root / "data" / "urls.db" == db.DB_PATH


def test_fetch_pending_articles_unpacks_as_two_tuple(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, body_text="Body text.")
    conn.commit()

    rows = db.fetch_pending_articles(conn, "article_sentiment")
    assert len(rows) == 1
    article_id, body_text = rows[0]
    assert article_id == 1
    assert body_text == "Body text."


def test_fetch_pending_category_articles_unpacks_as_three_tuple(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, title="Test Title", body_text="Body text.")
    conn.commit()

    rows = db.fetch_pending_category_articles(conn)
    assert len(rows) == 1
    article_id, title, body_text = rows[0]
    assert article_id == 1
    assert title == "Test Title"
    assert body_text == "Body text."


def test_fetch_pending_category_articles_excludes_already_categorized(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, body_text="Body text.")
    conn.commit()
    db.write_category(
        conn,
        1,
        label="other",
        score=0.2,
        scores={
            "earnings_performance": 0.1,
            "mergers_acquisitions": 0.1,
            "leadership_governance": 0.1,
            "legal_regulatory": 0.1,
            "product_innovation": 0.1,
            "capital_shareholder_returns": 0.2,
            "labor_human_capital": 0.1,
            "market_analyst_sentiment": 0.1,
            "partnerships_business_dev": 0.1,
        },
        model_name="test-model",
    )
    conn.commit()

    assert db.fetch_pending_category_articles(conn) == []
