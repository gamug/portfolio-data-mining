import sqlite3
from datetime import date

from conftest import seed_article

from news_nlp import db
from news_nlp.categories import CATEGORY_SLUGS, OTHER_LABEL


def seed_sentiment(
    conn: sqlite3.Connection, article_id: int, label: str = "positive", score: float = 0.9
) -> None:
    conn.execute(
        """INSERT INTO article_sentiment (article_id, label, score, positive, negative, neutral, model_name, processed_at)
           VALUES (?, ?, ?, 0.9, 0.05, 0.05, 'test-model', '2023-01-02T00:00:00Z')""",
        (article_id, label, score),
    )


def seed_category(
    conn: sqlite3.Connection,
    article_id: int,
    label: str = "earnings_performance",
    score: float = 0.9,
) -> None:
    scores = dict.fromkeys(CATEGORY_SLUGS, 0.05)
    if label != OTHER_LABEL:
        scores[label] = score
    db.write_category(
        conn, article_id, label=label, score=score, scores=scores, model_name="test-model"
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
    # Today, not a hardcoded date -- the "current week" is only still open
    # relative to whenever this test actually runs.
    today = date.today().isoformat()
    seed_article(conn, id=1, pub_date=f"{today}T00:00:00Z")  # inside the still-open current week
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


def test_fetch_pending_sector_weeks_regenerates_legacy_format_version_rows(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, pub_date="2026-08-03T00:00:00Z")
    seed_company_summary(conn, 1)
    conn.commit()
    db.write_sector_summary(
        conn,
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Old broken summary.",
        num_articles=1,
        num_companies=1,
        model_name="sshleifer/distilbart-cnn-12-6",
        format_version=0,  # a pre-fix row
    )
    conn.commit()

    rows = db.fetch_pending_sector_weeks(conn)

    assert len(rows) == 1  # legacy row treated as stale/pending, not already-summarized


# --- fetch_company_summaries_for_sector_week --------------------------------


def _seed_two_company_two_category_group(conn: sqlite3.Connection) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_article(conn, id=2, company="Honeywell", ticker="HON", pub_date="2026-08-04T00:00:00Z")
    seed_sentiment(conn, 1, label="positive")
    seed_sentiment(conn, 2, label="negative")
    seed_category(conn, 1, label="earnings_performance")
    seed_category(conn, 2, label="legal_regulatory")
    seed_company_summary(conn, 1, summary_text="3M beat earnings estimates.")
    seed_company_summary(conn, 2, summary_text="Honeywell faces a regulatory probe.")


def test_fetch_company_summaries_for_sector_week_returns_matching_rows(
    conn: sqlite3.Connection,
) -> None:
    _seed_two_company_two_category_group(conn)
    conn.commit()

    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    assert {r["company"] for r in rows} == {"3M", "Honeywell"}
    assert {r["category_label"] for r in rows} == {"earnings_performance", "legal_regulatory"}
    assert {r["sentiment_label"] for r in rows} == {"positive", "negative"}


def test_fetch_company_summaries_for_sector_week_excludes_uncategorized_articles(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_sentiment(conn, 1)
    # no category seeded for article 1
    seed_company_summary(conn, 1, summary_text="3M summary.")
    conn.commit()

    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    assert rows == []


# --- fetch_sector_week_entity_stats -----------------------------------------


def test_fetch_sector_week_entity_stats_counts_qualifying_entities(
    conn: sqlite3.Connection,
) -> None:
    seed_article(conn, id=1, company="3M", ticker="MMM", pub_date="2026-08-03T00:00:00Z")
    seed_sentiment(conn, 1)
    seed_category(conn, 1)
    seed_company_summary(conn, 1)
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', 'Acme Corp', 0, 9, 0.9, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', 'Acme Corp', 20, 29, 0.85, 'test-model', '2023-01-02T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO article_entities (article_id, entity_type, text, start_char, end_char, score, model_name, processed_at)
           VALUES (1, 'ORG', '4', 40, 41, 0.95, 'test-model', '2023-01-02T00:00:00Z')"""
    )  # single-digit, excluded
    conn.commit()

    stats = db.fetch_sector_week_entity_stats(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    assert stats == [{"text": "Acme Corp", "entity_type": "ORG", "count": 2}]


# --- compose_sector_summary / build_sector_intro_seed -----------------------


def test_compose_sector_summary_keeps_each_companys_text_under_its_own_line(
    conn: sqlite3.Connection,
) -> None:
    _seed_two_company_two_category_group(conn)
    conn.commit()
    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    text = db.compose_sector_summary(
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Intro sentence.",
        rows,
        entity_stats=[],
    )

    mmm_line = next(line for line in text.splitlines() if line.startswith("- MMM"))
    hon_line = next(line for line in text.splitlines() if line.startswith("- HON"))
    assert "3M beat earnings estimates." in mmm_line
    assert "Honeywell faces a regulatory probe." not in mmm_line
    assert "Honeywell faces a regulatory probe." in hon_line
    assert "3M beat earnings estimates." not in hon_line


def test_compose_sector_summary_separates_categories_into_their_own_sections(
    conn: sqlite3.Connection,
) -> None:
    _seed_two_company_two_category_group(conn)
    conn.commit()
    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    text = db.compose_sector_summary(
        "Industrials",
        "Industrial Conglomerates",
        "2026-08-03",
        "2026-08-09",
        "Intro sentence.",
        rows,
        entity_stats=[],
    )

    earnings_idx = text.index("EARNINGS & FINANCIAL PERFORMANCE")
    legal_idx = text.index("LEGAL & REGULATORY")
    mmm_idx = text.index("- MMM")
    hon_idx = text.index("- HON")
    assert earnings_idx < mmm_idx < legal_idx  # 3M's bullet sits under its own category section
    assert legal_idx < hon_idx


def test_build_sector_intro_seed_never_mentions_a_ticker_or_company_name(
    conn: sqlite3.Connection,
) -> None:
    _seed_two_company_two_category_group(conn)
    conn.commit()
    rows = db.fetch_company_summaries_for_sector_week(
        conn, "Industrials", "Industrial Conglomerates", "2026-08-03"
    )

    seed = db.build_sector_intro_seed(
        "Industrials", "Industrial Conglomerates", "2026-08-03", "2026-08-09", rows
    )

    for forbidden in (
        "MMM",
        "HON",
        "3M",
        "Honeywell",
        "beat earnings estimates",
        "regulatory probe",
    ):
        assert forbidden not in seed


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
