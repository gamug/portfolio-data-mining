"""Matching contract of all seven connectors (T-056a): one shared rule, no per-connector drift."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from news_collector.connectors.base import BaseConnector
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.connectors.ft import FTConnector
from news_collector.connectors.investing import InvestingConnector
from news_collector.connectors.nasdaq import NasdaqConnector
from news_collector.connectors.seeking_alpha import SeekingAlphaConnector
from news_collector.connectors.stocktwits import StockTwitsConnector
from news_collector.connectors.yahoo_finance import YahooFinanceConnector

CONNECTORS = [
    CNBCConnector,
    FTConnector,
    InvestingConnector,
    NasdaqConnector,
    SeekingAlphaConnector,
    StockTwitsConnector,
    YahooFinanceConnector,
]
# The four connectors whose old rule accepted `ticker in url` (raw substring).
SHORTCUT = {NasdaqConnector, SeekingAlphaConnector, StockTwitsConnector, YahooFinanceConnector}


@pytest.fixture(params=CONNECTORS, ids=lambda c: c.__name__)
def connector(request: pytest.FixtureRequest) -> BaseConnector:
    return request.param(client=MagicMock())  # type: ignore[no-any-return]


def host(connector: BaseConnector) -> str:
    return f"https://www.{connector.config.domain}"


# (path, title, company, ticker, expected class)
CASES = [
    (
        "/articles/aapl-beats-earnings-2024-01-15",
        "Apple beats estimates",
        "Apple Inc.",
        "AAPL",
        "accept",
    ),
    ("/news/apple-inc-q1-results", "", "Apple Inc.", "AAPL", "accept"),
    ("/news/microsoft-results", "Microsoft revenue grows", "Apple Inc.", "AAPL", "reject"),
    # the documented failure modes
    ("/news/a-1-in-a-month", "", "Agilent Technologies", "A", "reject"),
    ("/news/trusts-trump-on-trade", "", "ON Semiconductor", "ON", "reject"),
    ("/news/bank-rates-rise", "Bank rates rise", "Bank of America", "BAC", "reject"),
    ("/news/trade-war-ends", "Trade war ends", "Trade Desk", "TTD", "reject"),
    ("/news/first-quarter-gdp", "First quarter GDP", "First Solar", "FSLR", "reject"),
    ("/news/bad-news-driving-the-euro", "", "News Corp (Class A)", "NWSA", "reject"),
    # a ticker is never a raw substring
    ("/news/season-reason-treason", "", "ON Semiconductor", "ON", "reject"),
    ("/news/pineaapl-update", "", "Apple Inc.", "AAPL", "reject"),
    # weaker evidence is kept for review, not excluded
    ("/news/cnp-update", "", "CenterPoint Energy", "CNP", "review"),
    ("/news/a.html", "Egypt PM says talks resume", "Philip Morris International", "PM", "review"),
    # regression: still matches
    ("/news/a.html", "GM lays off workers", "General Motors", "GM", "accept"),
    ("/news/a.html", "Is Google's prize a worry?", "Alphabet Inc.", "GOOGL", "accept"),
    ("/news/s-and-p-global-inc-q1", "", "S&P Global", "SPGI", "accept"),
]


@pytest.mark.parametrize(("path", "title", "company", "ticker", "expected"), CASES)
def test_class_and_url_matches_company(
    connector: BaseConnector, path: str, title: str, company: str, ticker: str, expected: str
) -> None:
    url = host(connector) + path
    assert connector.classify_match(url, title, company, ticker) == expected
    assert connector.url_matches_company(url, title, company, ticker) is (expected != "reject")
    assert connector.explain_match(url, title, company, ticker).cls == expected


def test_the_substring_shortcut_is_gone(connector: BaseConnector) -> None:
    """`ticker.lower() in url.lower()` used to accept these in four connectors."""
    url = host(connector) + "/articles/the-market-today-in-a-month"
    assert not connector.url_matches_company(url, "", "Agilent Technologies", "A")
    assert not connector.url_matches_company(url, "", "ON Semiconductor", "ON")
    assert not connector.url_matches_company(
        host(connector) + "/x/pineaapl", "", "Apple Inc.", "AAPL"
    )


def test_the_host_is_not_matched(connector: BaseConnector) -> None:
    domain_word = connector.config.domain.split(".")[0]  # e.g. "nasdaq", "cnbc", "yahoo"
    company = f"{domain_word.title()}, Inc."
    url = host(connector) + "/articles/unrelated-story"
    assert connector.classify_match(url, "Unrelated story", company, "ZZZZ") == "reject"


def test_all_seven_share_one_rule() -> None:
    assert set(CONNECTORS) == {c for c in BaseConnector.__subclasses__()}
    for cls in CONNECTORS:
        assert "classify_match" not in vars(cls)  # none re-implements the rule
