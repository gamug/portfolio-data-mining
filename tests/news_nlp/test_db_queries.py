import sqlite3

from conftest import seed_article

from news_nlp import db

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


def test_list_articles_filters_by_company(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="Apple")
    conn.commit()

    result = db.list_articles(conn, company="3M")
    assert [a["id"] for a in result] == [1]


def test_list_articles_includes_entity_count(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', '3M', 0, 2, 0.9, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    result = db.list_articles(conn)
    assert result[0]["entity_count"] == 1


def test_get_article_detail_returns_none_for_missing_article(conn: sqlite3.Connection) -> None:
    assert db.get_article_detail(conn, 999) is None


def test_get_article_detail_sentiment_none_when_unprocessed(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.commit()

    detail = db.get_article_detail(conn, 1)
    assert detail is not None
    assert detail["sentiment"] is None
    assert detail["entities"] == []
    assert detail["category"] is None


def test_get_article_detail_includes_category(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.commit()
    db.write_category(
        conn,
        1,
        label="earnings_performance",
        score=0.5,
        scores=_CATEGORY_SCORES,
        model_name="test-model",
    )
    conn.commit()

    detail = db.get_article_detail(conn, 1)
    assert detail is not None
    assert detail["category"]["label"] == "earnings_performance"
    assert detail["category"]["score"] == 0.5
    assert detail["category"]["earnings_performance"] == 0.5


def test_get_article_detail_includes_sentiment_and_entities(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', '3M', 0, 2, 0.9, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    detail = db.get_article_detail(conn, 1)
    assert detail is not None
    assert detail["sentiment"]["label"] == "positive"
    assert len(detail["entities"]) == 1
    assert detail["entities"][0]["text"] == "3M"


def test_sentiment_stats_overall_totals(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="3M")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (2, 'positive', 0.7, 0.7, 0.2, 0.1, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    result = db.sentiment_stats(conn)
    assert len(result) == 1
    assert result[0]["positive"] == 2
    assert result[0]["total"] == 2


def test_sentiment_stats_grouped_by_year(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M", pub_date="2022-06-01T00:00:00Z")
    seed_article(conn, id=2, company="3M", pub_date="2023-06-01T00:00:00Z")
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (1, 'positive', 0.9, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (2, 'negative', 0.8, 0.05, 0.8, 0.15, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.commit()

    result = db.sentiment_stats(conn, group_by="year")
    by_year = {r["group_key"]: r for r in result}
    assert by_year["2022"]["positive"] == 1
    assert by_year["2023"]["negative"] == 1


def test_list_articles_filters_by_category(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="Apple")
    conn.commit()
    db.write_category(
        conn,
        1,
        label="earnings_performance",
        score=0.5,
        scores=_CATEGORY_SCORES,
        model_name="test-model",
    )
    db.write_category(
        conn, 2, label="other", score=0.2, scores=_CATEGORY_SCORES, model_name="test-model"
    )
    conn.commit()

    result = db.list_articles(conn, category="earnings_performance")
    assert [a["id"] for a in result] == [1]


def test_category_stats_counts_per_label(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="3M")
    seed_article(conn, id=3, company="Apple")
    conn.commit()
    db.write_category(
        conn,
        1,
        label="earnings_performance",
        score=0.5,
        scores=_CATEGORY_SCORES,
        model_name="test-model",
    )
    db.write_category(
        conn,
        2,
        label="earnings_performance",
        score=0.6,
        scores=_CATEGORY_SCORES,
        model_name="test-model",
    )
    db.write_category(
        conn, 3, label="other", score=0.2, scores=_CATEGORY_SCORES, model_name="test-model"
    )
    conn.commit()

    result = db.category_stats(conn)
    by_label = {r["label"]: r["count"] for r in result}
    assert by_label["earnings_performance"] == 2
    assert by_label["other"] == 1


def test_category_stats_filters_by_company(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    seed_article(conn, id=2, company="Apple")
    conn.commit()
    db.write_category(
        conn,
        1,
        label="earnings_performance",
        score=0.5,
        scores=_CATEGORY_SCORES,
        model_name="test-model",
    )
    db.write_category(
        conn, 2, label="other", score=0.2, scores=_CATEGORY_SCORES, model_name="test-model"
    )
    conn.commit()

    result = db.category_stats(conn, company="3M")
    assert len(result) == 1
    assert result[0]["label"] == "earnings_performance"


def test_entity_stats_orders_by_count_desc(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M")
    conn.executemany(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, ?, ?, 0, 2, 0.9, 'test-model', '2023-01-02T00:00:00Z')""",
        [("ORG", "3M"), ("ORG", "3M"), ("PER", "Mike Roman")],
    )
    conn.commit()

    result = db.entity_stats(conn, top=5)
    assert result[0] == {"text": "3M", "entity_type": "ORG", "count": 2}
    assert result[1]["text"] == "Mike Roman"
