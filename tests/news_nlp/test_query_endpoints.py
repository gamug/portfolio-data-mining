import sqlite3

from conftest import seed_article
from fastapi.testclient import TestClient

from news_nlp import db


def test_get_articles_filters_by_company(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2023-01-01T00:00:00Z")
    seed_article(conn, id=2, company="Apple", ticker="AAPL", pub_date="2023-02-01T00:00:00Z")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    resp = client.get("/articles", params={"company": "3M"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == 1
    assert body[0]["sentiment_label"] == "positive"


def test_get_articles_filters_by_sentiment(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="3M")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (2, 'negative', 0.8, 0.05, 0.8, 0.15, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    resp = client.get("/articles", params={"sentiment": "negative"})
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()] == [2]


def test_get_article_detail_returns_full_record(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed_article(conn, id=1, company="3M")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', '3M', 0, 2, 0.99, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    resp = client.get("/articles/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["sentiment"]["label"] == "positive"
    assert len(body["entities"]) == 1
    assert body["entities"][0]["text"] == "3M"


def test_get_article_detail_includes_summary(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    db.write_company_summary(conn, 1, "A short summary.", 1, "facebook/bart-large-cnn")
    conn.commit()

    resp = client.get("/articles/1")
    assert resp.status_code == 200
    assert resp.json()["summary"]["summary_text"] == "A short summary."


def test_get_article_detail_summary_none_when_unprocessed(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed_article(conn, id=1, company="3M")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    resp = client.get("/articles/1")
    assert resp.status_code == 200
    assert resp.json()["summary"] is None


def test_get_article_detail_404_when_unprocessed(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed_article(conn, id=1, company="3M")
    conn.commit()

    resp = client.get("/articles/1")
    assert resp.status_code == 404


def test_get_article_detail_404_when_missing(client: TestClient) -> None:
    resp = client.get("/articles/999")
    assert resp.status_code == 404


def test_get_sentiment_stats_grouped_by_company(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="Apple")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (2, 'negative', 0.8, 0.05, 0.8, 0.15, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    resp = client.get("/stats/sentiment", params={"group_by": "company"})
    assert resp.status_code == 200
    rows = {r["group_key"]: r for r in resp.json()}
    assert rows["3M"]["positive"] == 1
    assert rows["Apple"]["negative"] == 1


def test_get_entity_stats_top_n(client: TestClient, conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.executemany(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, ?, ?, 0, 2, 0.9, 'test-model', '2023-01-02T00:00:00Z')""",
        [("ORG", "3M"), ("ORG", "3M"), ("PER", "Mike Roman")],
    )
    conn.commit()

    resp = client.get("/stats/entities", params={"top": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["text"] == "3M"
    assert body[0]["count"] == 2


def test_get_sector_summaries_lists_all_by_default(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    db.write_sector_summary(
        conn,
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Industrials summary.",
        num_articles=1,
        num_companies=1,
        model_name="facebook/bart-large-cnn",
    )
    conn.commit()

    resp = client.get("/sectors/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["gics_sector"] == "Industrials"


def test_get_sector_summaries_filters_by_sector(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    db.write_sector_summary(
        conn,
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Industrials summary.",
        num_articles=1,
        num_companies=1,
        model_name="facebook/bart-large-cnn",
    )
    db.write_sector_summary(
        conn,
        "Information Technology",
        "Semiconductors",
        "2026-08-03",
        "2026-08-09",
        "Tech summary.",
        num_articles=1,
        num_companies=1,
        model_name="facebook/bart-large-cnn",
    )
    conn.commit()

    resp = client.get("/sectors/summary", params={"sector": "Information Technology"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["gics_sub_industry"] == "Semiconductors"
