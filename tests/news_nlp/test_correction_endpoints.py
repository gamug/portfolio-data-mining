import sqlite3

from conftest import seed_article
from fastapi.testclient import TestClient

from news_nlp import db


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


def test_patch_sentiment_updates_label(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_sentiment(conn)

    resp = client.patch("/articles/1/sentiment", json={"label": "negative"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "negative"


def test_patch_sentiment_404_when_missing(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()

    resp = client.patch("/articles/1/sentiment", json={"label": "negative"})
    assert resp.status_code == 404


def test_patch_sentiment_422_for_invalid_label(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_sentiment(conn)

    resp = client.patch("/articles/1/sentiment", json={"label": "bullish"})
    assert resp.status_code == 422


def test_delete_sentiment_204_and_removes_row(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_sentiment(conn)

    resp = client.delete("/articles/1/sentiment")
    assert resp.status_code == 204
    assert conn.execute("SELECT * FROM article_sentiment WHERE article_id = 1").fetchone() is None


def test_delete_sentiment_404_when_missing(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()

    resp = client.delete("/articles/1/sentiment")
    assert resp.status_code == 404


def test_patch_entity_updates_fields(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    entity_id = _seed_entity(conn)

    resp = client.patch(f"/entities/{entity_id}", json={"entity_type": "PER", "text": "Mike"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_type"] == "PER"
    assert body["text"] == "Mike"


def test_patch_entity_404_when_missing(client: TestClient) -> None:
    resp = client.patch("/entities/999", json={"text": "x"})
    assert resp.status_code == 404


def test_delete_entity_204(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    entity_id = _seed_entity(conn)

    resp = client.delete(f"/entities/{entity_id}")
    assert resp.status_code == 204
    assert (
        conn.execute("SELECT * FROM article_entities WHERE id = ?", (entity_id,)).fetchone() is None
    )


def test_patch_category_updates_label(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    resp = client.patch("/articles/1/category", json={"label": "mergers_acquisitions"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "mergers_acquisitions"


def test_patch_category_404_when_missing(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()

    resp = client.patch("/articles/1/category", json={"label": "mergers_acquisitions"})
    assert resp.status_code == 404


def test_patch_category_422_for_invalid_label(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    resp = client.patch("/articles/1/category", json={"label": "not_a_real_category"})
    assert resp.status_code == 422


def test_delete_category_204_and_removes_row(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()
    _seed_category(conn)

    resp = client.delete("/articles/1/category")
    assert resp.status_code == 204
    assert conn.execute("SELECT * FROM article_category WHERE article_id = 1").fetchone() is None


def test_delete_category_404_when_missing(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    conn.commit()

    resp = client.delete("/articles/1/category")
    assert resp.status_code == 404


def test_get_category_stats(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.commit()
    _seed_category(conn)

    resp = client.get("/stats/categories")
    assert resp.status_code == 200
    body = resp.json()
    assert {"label": "earnings_performance", "count": 1} in body


def test_delete_article_entities_bulk(client: TestClient, conn: sqlite3.Connection) -> None:
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

    resp = client.delete("/articles/1/entities")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}
