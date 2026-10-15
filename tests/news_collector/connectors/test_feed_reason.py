"""`api` feed rows keep `accept` by construction and say so (`match_reason = 'feed'`)."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import MagicMock

import pytest

from news_collector.connectors.stocktwits import StockTwitsConnector
from news_collector.connectors.yahoo_finance import YahooFinanceConnector
from news_collector.models import DateRange

_RANGE = DateRange(start=date(2024, 1, 1), end=date(2024, 1, 31))


def test_stocktwits_api_rows_are_accept_with_reason_feed() -> None:
    connector = StockTwitsConnector(client=MagicMock())
    messages = [
        {"id": 1, "created_at": "2024-01-10T12:00:00Z", "user": {"username": "u"}, "body": "x"}
    ]

    rows, _ = connector._messages_to_urls(messages, "AAPL", _RANGE)

    assert [(r.source, r.match_class, r.match_reason) for r in rows] == [("api", "accept", "feed")]


def test_yahoo_api_rows_are_accept_with_reason_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = YahooFinanceConnector(client=MagicMock())
    items = [{"link": "https://finance.yahoo.com/news/a.html", "title": "t"}]
    monkeypatch.setattr(
        "news_collector.connectors.yahoo_finance._sync_yfinance_news", lambda t: items
    )

    rows = asyncio.run(connector.fetch_via_yfinance("AAPL", _RANGE))

    assert [(r.source, r.match_class, r.match_reason) for r in rows] == [("api", "accept", "feed")]
