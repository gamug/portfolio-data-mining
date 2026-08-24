import sqlite3

import pytest
from conftest import seed_article

from news_nlp import corrections, db

_CATEGORY_SCORES = {
    "earnings_performance": 0.5,
    "mergers_acquisitions": 0.05,
    "leadership_governance": 0.05,
    "legal_regulatory": 0.05,
    "product_innovation": 0.05,
    "capital_shareholder_returns": 0.05,
    "labor_human_capital": 0.05,
    "market_analyst_sentiment": 0.1,
    "partnerships_business_dev": 0.1,
}


def _seed_category(conn: sqlite3.Connection, article_id: int = 1) -> None:
    db.write_category(
        conn,
        article_id,
        label="earnings_performance",
        score=0.5,
        scores=_CATEGORY_SCORES,
        model_name="test-model",
    )
    conn.commit()


def _seed_sentiment(conn: sqlite3.Connection, article_id: int = 1) -> None:
    db.write_sentiment(
        conn,
        article_id,
        label="positive",
        score=0.9,
        positive=0.9,
        negative=0.05,
        neutral=0.05,
        model_name="test-model",
    )
    conn.commit()


def _seed_entity(conn: sqlite3.Connection, article_id: int = 1) -> int:
    db.write_entities(
        conn,
        article_id,
        [{"entity_type": "ORG", "text": "3M", "start_char": 0, "end_char": 2, "score": 0.9}],
        model_name="test-model",
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM article_entities WHERE article_id = ?", (article_id,)
    ).fetchone()
    return int(row["id"])


def test_update_sentiment_changes_label_and_refreshes_timestamp(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_sentiment(conn)

    before = conn.execute(
        "SELECT processed_at FROM article_sentiment WHERE article_id = 1"
    ).fetchone()["processed_at"]

    updated = corrections.update_sentiment(conn, 1, label="negative")
    conn.commit()
    assert updated is not None

    assert updated["label"] == "negative"
    assert updated["processed_at"] != before


def test_update_sentiment_returns_none_for_missing_article(conn: sqlite3.Connection) -> None:
    assert corrections.update_sentiment(conn, 999, label="negative") is None


def test_delete_sentiment_removes_row_and_returns_true(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_sentiment(conn)

    assert corrections.delete_sentiment(conn, 1) is True
    conn.commit()
    assert conn.execute("SELECT * FROM article_sentiment WHERE article_id = 1").fetchone() is None


def test_delete_sentiment_returns_false_when_missing(conn: sqlite3.Connection) -> None:
    assert corrections.delete_sentiment(conn, 999) is False


def test_deleted_sentiment_reappears_in_pending_articles(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, fetch_status="ok", body_text="Body text.")
    conn.commit()
    _seed_sentiment(conn)

    assert db.fetch_pending_articles(conn, "article_sentiment") == []

    corrections.delete_sentiment(conn, 1)
    conn.commit()

    rows = db.fetch_pending_articles(conn, "article_sentiment")
    assert len(rows) == 1
    assert rows[0][0] == 1


def test_update_entity_changes_fields(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    entity_id = _seed_entity(conn)

    updated = corrections.update_entity(conn, entity_id, entity_type="PER", text="Mike")
    assert updated is not None
    conn.commit()

    assert updated["entity_type"] == "PER"
    assert updated["text"] == "Mike"


def test_update_entity_returns_none_for_missing_id(conn: sqlite3.Connection) -> None:
    assert corrections.update_entity(conn, 999, text="x") is None


def test_delete_entity_removes_row(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    entity_id = _seed_entity(conn)

    assert corrections.delete_entity(conn, entity_id) is True
    conn.commit()
    assert (
        conn.execute("SELECT * FROM article_entities WHERE id = ?", (entity_id,)).fetchone() is None
    )


def test_delete_entities_for_article_removes_all_and_returns_count(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1)
    conn.commit()
    db.write_entities(
        conn,
        1,
        [
            {"entity_type": "ORG", "text": "3M", "start_char": 0, "end_char": 2, "score": 0.9},
            {"entity_type": "PER", "text": "Mike", "start_char": 3, "end_char": 7, "score": 0.9},
        ],
        model_name="test-model",
    )
    conn.commit()

    count = corrections.delete_entities_for_article(conn, 1)
    conn.commit()

    assert count == 2
    assert (
        conn.execute("SELECT COUNT(*) FROM article_entities WHERE article_id = 1").fetchone()[0]
        == 0
    )


def test_update_category_changes_label_and_refreshes_timestamp(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    before = conn.execute(
        "SELECT processed_at FROM article_category WHERE article_id = 1"
    ).fetchone()["processed_at"]

    updated = corrections.update_category(conn, 1, label="mergers_acquisitions")
    assert updated is not None
    conn.commit()

    assert updated["label"] == "mergers_acquisitions"
    assert updated["processed_at"] != before


def test_update_category_does_not_touch_raw_distribution_columns(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    corrections.update_category(conn, 1, label="mergers_acquisitions")
    conn.commit()

    row = conn.execute("SELECT * FROM article_category WHERE article_id = 1").fetchone()
    assert row["earnings_performance"] == 0.5  # untouched audit trail


def test_update_category_rejects_unknown_field(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    with pytest.raises(ValueError):
        corrections.update_category(conn, 1, earnings_performance=0.99)


def test_update_category_returns_none_for_missing_article(conn: sqlite3.Connection) -> None:
    assert corrections.update_category(conn, 999, label="mergers_acquisitions") is None


def test_delete_category_removes_row_and_returns_true(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    assert corrections.delete_category(conn, 1) is True
    conn.commit()
    assert conn.execute("SELECT * FROM article_category WHERE article_id = 1").fetchone() is None


def test_delete_category_returns_false_when_missing(conn: sqlite3.Connection) -> None:
    assert corrections.delete_category(conn, 999) is False


def test_deleted_category_reappears_in_pending_articles(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, fetch_status="ok", body_text="Body text.")
    conn.commit()
    _seed_category(conn)

    assert db.fetch_pending_category_articles(conn) == []

    corrections.delete_category(conn, 1)
    conn.commit()

    rows = db.fetch_pending_category_articles(conn)
    assert len(rows) == 1
    assert rows[0][0] == 1
