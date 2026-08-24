import sqlite3

from conftest import seed_article

from news_nlp import db


def seed_sentiment(
    conn: sqlite3.Connection, article_id: int, label: str = "positive", score: float = 0.9
) -> None:
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (?, ?, ?, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')""",
        (article_id, label, score),
    )


def seed_entity(
    conn: sqlite3.Connection, article_id: int, text: str = "3M", score: float = 0.9
) -> None:
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (?, 'ORG', ?, 0, 2, ?, 'test-model', '2023-01-02T00:00:00Z')""",
        (article_id, text, score),
    )


def seed_company_summary(
    conn: sqlite3.Connection,
    article_id: int,
    summary_text: str = "A short summary.",
    num_chunks: int = 1,
) -> None:
    db.write_company_summary(conn, article_id, summary_text, num_chunks, "facebook/bart-large-cnn")


# --- fetch_pending_company_summaries -----------------------------------


def test_fetch_pending_company_summaries_requires_sentiment_and_entities(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1)
    seed_sentiment(conn, 1)
    # no entities seeded for article 1
    seed_article(conn, id=2)
    seed_sentiment(conn, 2)
    seed_entity(conn, 2)
    conn.commit()

    rows = db.fetch_pending_company_summaries(conn)

    assert [r["article_id"] for r in rows] == [2]


def test_fetch_pending_company_summaries_excludes_low_confidence_and_single_digit_entities(
    conn: sqlite3.Connection,
) -> None:
    # NOT GLOB '[0-9]' only matches a single-character digit (e.g. a stray "4"),
    # not multi-digit numbers -- same GLOB pattern as query.sql.
    seed_article(conn, id=1)
    seed_sentiment(conn, 1)
    seed_entity(conn, 1, text="4", score=0.95)  # single digit, filtered by NOT GLOB
    seed_entity(conn, 1, text="3M", score=0.5)  # below 0.8 threshold
    conn.commit()

    rows = db.fetch_pending_company_summaries(conn)

    assert rows == []


def test_fetch_pending_company_summaries_excludes_non_200_http_status(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, http_status_code=404)
    seed_sentiment(conn, 1)
    seed_entity(conn, 1)
    conn.commit()

    assert db.fetch_pending_company_summaries(conn) == []


def test_fetch_pending_company_summaries_excludes_already_summarized(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1)
    seed_sentiment(conn, 1)
    seed_entity(conn, 1)
    seed_company_summary(conn, 1)
    conn.commit()

    assert db.fetch_pending_company_summaries(conn) == []


def test_fetch_pending_company_summaries_respects_limit(conn: sqlite3.Connection) -> None:
    for i in (1, 2):
        seed_article(conn, id=i)
        seed_sentiment(conn, i)
        seed_entity(conn, i)
    conn.commit()

    rows = db.fetch_pending_company_summaries(conn, limit=1)

    assert len(rows) == 1


def test_build_company_summary_input_uses_real_newlines(conn: sqlite3.Connection) -> None:
    seed_article(
        conn,
        id=1,
        company="3M",
        ticker="MMM",
        title="3M beats estimates",
        body_text="full article text",
    )
    seed_sentiment(conn, 1, label="positive", score=0.87)
    seed_entity(conn, 1, text="3M")
    conn.commit()

    row = db.fetch_pending_company_summaries(conn)[0]
    text = db.build_company_summary_input(row)

    assert "\n" in text
    assert "\\n" not in text  # not a literal backslash-n
    assert text.startswith("METADATA:\nTicker-MMM\nCompany-3M")
    assert "NLP FEATURES:\nSentiment-positive Confidence-0.87" in text
    assert "Entities-3M" in text
    assert "TEXT BODY:\nTitle-3M beats estimates\nBody-full article text" in text


# --- write_company_summary ----------------------------------------------


def test_write_company_summary_then_fetch_pending_excludes_it(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1)
    seed_sentiment(conn, 1)
    seed_entity(conn, 1)
    conn.commit()

    assert len(db.fetch_pending_company_summaries(conn)) == 1

    seed_company_summary(conn, 1)
    conn.commit()

    assert db.fetch_pending_company_summaries(conn) == []


# --- fetch_pending_sector_weeks ------------------------------------------


def test_fetch_pending_sector_weeks_includes_closed_week(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, pub_date="2026-08-03T00:00:00Z")  # Monday, closed week
    seed_company_summary(conn, 1)
    conn.commit()

    rows = db.fetch_pending_sector_weeks(conn)

    assert len(rows) == 1
    assert rows[0]["gics_sector"] == "Industrials"
    assert rows[0]["gics_sub_industry"] == "Industrial Conglomerates"
    assert rows[0]["week_start"] == "2026-08-03"
    assert rows[0]["week_end"] == "2026-08-09"


def test_fetch_pending_sector_weeks_excludes_open_week(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, pub_date="2026-08-11T00:00:00Z")  # inside the still-open current week
    seed_company_summary(conn, 1)
    conn.commit()

    assert db.fetch_pending_sector_weeks(conn) == []


def test_fetch_pending_sector_weeks_falls_back_to_fetched_at_when_pub_date_null(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, pub_date=None, fetched_at="2026-08-03T00:00:00Z")
    seed_company_summary(conn, 1)
    conn.commit()

    rows = db.fetch_pending_sector_weeks(conn)

    assert [r["week_start"] for r in rows] == ["2026-08-03"]


def test_fetch_pending_sector_weeks_groups_multiple_companies_in_same_subindustry(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_article(conn, id=2, company="Honeywell", ticker="HON", pub_date="2026-08-04T00:00:00Z")
    seed_company_summary(conn, 1)
    seed_company_summary(conn, 2)
    conn.commit()

    rows = db.fetch_pending_sector_weeks(conn)

    assert len(rows) == 1  # both fall in the same sub-industry/week bucket


def test_fetch_pending_sector_weeks_excludes_already_summarized(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, pub_date="2026-08-03T00:00:00Z")
    seed_company_summary(conn, 1)
    conn.commit()
    db.write_sector_summary(
        conn,
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Existing summary.",
        num_articles=1,
        num_companies=1,
        model_name="facebook/bart-large-cnn",
    )
    conn.commit()

    assert db.fetch_pending_sector_weeks(conn) == []


# --- fetch_company_summaries_for_sector_week / build_sector_summary_input


def test_fetch_company_summaries_for_sector_week_returns_matching_rows(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_article(conn, id=2, company="Honeywell", ticker="HON", pub_date="2026-08-04T00:00:00Z")
    seed_company_summary(conn, 1, summary_text="3M summary.")
    seed_company_summary(conn, 2, summary_text="Honeywell summary.")
    conn.commit()

    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    assert [r["company"] for r in rows] == ["3M", "Honeywell"]


def test_build_sector_summary_input_writes_header_once(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_company_summary(conn, 1, summary_text="3M summary.")
    conn.commit()

    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )
    text = db.build_sector_summary_input("Industrials", "Industrial Conglomerates", rows)

    assert text.count("Sector-Industrials") == 1
    assert "MMM (3M): 3M summary." in text


# --- write_sector_summary / list_sector_summaries -------------------------


def test_write_sector_summary_is_idempotent_on_unique_key(conn: sqlite3.Connection) -> None:
    db.write_sector_summary(
        conn,
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "First version.",
        num_articles=1,
        num_companies=1,
        model_name="facebook/bart-large-cnn",
    )
    conn.commit()
    db.write_sector_summary(
        conn,
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Second version.",
        num_articles=2,
        num_companies=2,
        model_name="facebook/bart-large-cnn",
    )
    conn.commit()

    results = db.list_sector_summaries(conn)

    assert len(results) == 1
    assert results[0]["summary_text"] == "Second version."


def test_list_sector_summaries_filters_by_sector(conn: sqlite3.Connection) -> None:
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

    results = db.list_sector_summaries(conn, sector="Industrials")

    assert len(results) == 1
    assert results[0]["gics_sector"] == "Industrials"
