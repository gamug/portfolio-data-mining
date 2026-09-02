"""Tests for FinnhubNewsFetcher (src/pricing/news.py) -- company/general
news fetching, sentiment, rolling-window backfill, and CSV export.

No test hits the network: finnhub.Client is replaced with a MagicMock after
construction. sleep_between_calls=0 throughout so tests don't sleep.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import finnhub
import pytest

from pricing.news import FinnhubNewsFetcher


@pytest.fixture
def fetcher() -> FinnhubNewsFetcher:
    f = FinnhubNewsFetcher(api_key="test-key", sleep_between_calls=0)
    f.client = MagicMock()
    return f


def make_raw_article(**overrides: object) -> dict:
    article = {
        "datetime": 1704067200,  # 2024-01-01T00:00:00Z
        "headline": "Company beats earnings",
        "summary": "Details here.",
        "source": "Reuters",
        "url": "https://example.com/a",
        "category": "company",
    }
    article.update(overrides)
    return article


def make_finnhub_api_exception() -> finnhub.exceptions.FinnhubAPIException:
    response = MagicMock()
    response.json.return_value = {"error": "rate limited"}
    response.status_code = 429
    return finnhub.exceptions.FinnhubAPIException(response)


# ---------------------------------------------------------------------
# fetch_ticker_news
# ---------------------------------------------------------------------


def test_fetch_ticker_news_returns_raw_articles(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.company_news.return_value = [make_raw_article()]

    result = fetcher.fetch_ticker_news("AAPL", "2024-01-01", "2024-01-07")

    fetcher.client.company_news.assert_called_once_with("AAPL", _from="2024-01-01", to="2024-01-07")
    assert result == [make_raw_article()]


def test_fetch_ticker_news_finnhub_api_exception_returns_empty_list(
    fetcher: FinnhubNewsFetcher,
) -> None:
    fetcher.client.company_news.side_effect = make_finnhub_api_exception()

    result = fetcher.fetch_ticker_news("AAPL", "2024-01-01", "2024-01-07")

    assert result == []


def test_fetch_ticker_news_generic_exception_returns_empty_list(
    fetcher: FinnhubNewsFetcher,
) -> None:
    fetcher.client.company_news.side_effect = Exception("boom")

    result = fetcher.fetch_ticker_news("AAPL", "2024-01-01", "2024-01-07")

    assert result == []


# ---------------------------------------------------------------------
# fetch_many
# ---------------------------------------------------------------------


def test_fetch_many_normalizes_and_accumulates_across_tickers(
    fetcher: FinnhubNewsFetcher,
) -> None:
    fetcher.client.company_news.side_effect = [
        [make_raw_article(headline="AAPL news")],
        [make_raw_article(headline="MSFT news")],
    ]

    result = fetcher.fetch_many(["AAPL", "MSFT"], "2024-01-01", "2024-01-07", verbose=False)

    assert [r["headline"] for r in result] == ["AAPL news", "MSFT news"]
    assert [r["ticker"] for r in result] == ["AAPL", "MSFT"]
    assert fetcher.articles == result


def test_fetch_many_handles_no_articles_for_a_ticker(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.company_news.return_value = []

    result = fetcher.fetch_many(["AAPL"], "2024-01-01", "2024-01-07", verbose=False)

    assert result == []
    assert fetcher.articles == []


# ---------------------------------------------------------------------
# fetch_general_news
# ---------------------------------------------------------------------


def test_fetch_general_news_normalizes_with_no_ticker(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.general_news.return_value = [make_raw_article()]

    result = fetcher.fetch_general_news(category="general", min_id=5)

    fetcher.client.general_news.assert_called_once_with("general", min_id=5)
    assert result[0]["ticker"] is None
    assert result[0]["headline"] == "Company beats earnings"


def test_fetch_general_news_exception_returns_empty_list(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.general_news.side_effect = Exception("boom")

    result = fetcher.fetch_general_news()

    assert result == []


# ---------------------------------------------------------------------
# fetch_news_sentiment
# ---------------------------------------------------------------------


def test_fetch_news_sentiment_success(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.news_sentiment.return_value = {
        "symbol": "AAPL",
        "buzz": {"articlesInLastWeek": 10},
    }

    result = fetcher.fetch_news_sentiment("AAPL")

    assert result == {
        "success": True,
        "data": {"symbol": "AAPL", "buzz": {"articlesInLastWeek": 10}},
    }


def test_fetch_news_sentiment_missing_symbol_is_an_error(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.news_sentiment.return_value = {}

    result = fetcher.fetch_news_sentiment("AAPL")

    assert result["success"] is False
    assert "paid Finnhub plan" in result["error"]


def test_fetch_news_sentiment_exception_returns_error_dict(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.news_sentiment.side_effect = Exception("boom")

    result = fetcher.fetch_news_sentiment("AAPL")

    assert result["success"] is False
    assert "boom" in result["error"]


# ---------------------------------------------------------------------
# fetch_rolling_range
# ---------------------------------------------------------------------


def test_fetch_rolling_range_chunks_into_windows(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.client.company_news.return_value = []

    fetcher.fetch_rolling_range(["AAPL"], "2024-01-01", "2024-01-15", window_days=7, verbose=False)

    # 15 days / 7-day windows -> [1-7], [8-14], [15-15] = 3 windows = 3 calls
    calls = fetcher.client.company_news.call_args_list
    assert len(calls) == 3
    assert calls[0].kwargs == {"_from": "2024-01-01", "to": "2024-01-07"}
    assert calls[1].kwargs == {"_from": "2024-01-08", "to": "2024-01-14"}
    assert calls[2].kwargs == {"_from": "2024-01-15", "to": "2024-01-15"}


def test_fetch_rolling_range_raises_when_start_after_end(fetcher: FinnhubNewsFetcher) -> None:
    with pytest.raises(ValueError, match="start_date must be before end_date"):
        fetcher.fetch_rolling_range(["AAPL"], "2024-02-01", "2024-01-01", verbose=False)


def test_fetch_rolling_range_writes_checkpoint_csv_per_window(
    fetcher: FinnhubNewsFetcher, tmp_path: Path
) -> None:
    checkpoint = str(tmp_path / "checkpoint.csv")
    fetcher.client.company_news.side_effect = [
        [make_raw_article(headline="week1")],
        [make_raw_article(headline="week2")],
    ]

    fetcher.fetch_rolling_range(
        ["AAPL"],
        "2024-01-01",
        "2024-01-14",
        window_days=7,
        verbose=False,
        checkpoint_csv=checkpoint,
    )

    with open(checkpoint, encoding="utf-8") as f:
        content = f.read()
    assert content.count("week1") == 1
    assert content.count("week2") == 1
    assert content.count("ticker,datetime,headline") == 1  # header written only once


def test_fetch_rolling_range_skips_csv_write_for_empty_windows(
    fetcher: FinnhubNewsFetcher, tmp_path: Path
) -> None:
    checkpoint = str(tmp_path / "checkpoint.csv")
    fetcher.client.company_news.return_value = []

    fetcher.fetch_rolling_range(
        ["AAPL"], "2024-01-01", "2024-01-07", verbose=False, checkpoint_csv=checkpoint
    )

    assert not os.path.isfile(checkpoint)


# ---------------------------------------------------------------------
# _normalize_article
# ---------------------------------------------------------------------


def test_normalize_article_maps_fields() -> None:
    result = FinnhubNewsFetcher._normalize_article("AAPL", make_raw_article())

    assert result["ticker"] == "AAPL"
    assert (
        result["datetime"]
        == datetime.fromtimestamp(1704067200, tz=UTC).replace(tzinfo=None).isoformat()
    )
    assert result["headline"] == "Company beats earnings"
    assert result["url"] == "https://example.com/a"


def test_normalize_article_defaults_missing_fields() -> None:
    result = FinnhubNewsFetcher._normalize_article(None, {})

    assert result["ticker"] is None
    assert result["headline"] == ""
    assert result["summary"] == ""
    assert result["source"] == ""
    assert result["url"] == ""
    assert result["category"] == ""


# ---------------------------------------------------------------------
# clear / to_csv / to_list
# ---------------------------------------------------------------------


def test_clear_resets_articles(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.articles = [make_raw_article()]

    fetcher.clear()

    assert fetcher.articles == []


def test_to_list_returns_accumulated_articles(fetcher: FinnhubNewsFetcher) -> None:
    fetcher.articles = [{"ticker": "AAPL"}]

    assert fetcher.to_list() == [{"ticker": "AAPL"}]


def test_to_csv_writes_all_accumulated_articles(
    fetcher: FinnhubNewsFetcher, tmp_path: Path
) -> None:
    out = str(tmp_path / "out.csv")
    fetcher.articles = [
        FinnhubNewsFetcher._normalize_article("AAPL", make_raw_article(headline="h1")),
        FinnhubNewsFetcher._normalize_article("MSFT", make_raw_article(headline="h2")),
    ]

    fetcher.to_csv(out)

    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert content.count("h1") == 1
    assert content.count("h2") == 1


def test_to_csv_with_no_articles_does_not_create_file(
    fetcher: FinnhubNewsFetcher, tmp_path: Path
) -> None:
    out = str(tmp_path / "out.csv")

    fetcher.to_csv(out)

    assert not os.path.isfile(out)
