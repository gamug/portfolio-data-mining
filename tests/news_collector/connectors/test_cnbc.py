"""Tests for CNBC connector."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from news_collector.connectors.cnbc import CNBCConnector
from news_collector.models import DateRange


@pytest.fixture
def connector() -> CNBCConnector:
    return CNBCConnector(client=MagicMock())


def test_is_article_url_valid(connector: CNBCConnector) -> None:
    assert connector.is_article_url(
        "https://www.cnbc.com/2024/01/15/apple-earnings-beat-expectations.html"
    )


def test_is_article_url_rejects_root(connector: CNBCConnector) -> None:
    assert not connector.is_article_url("https://www.cnbc.com/")


def test_is_article_url_rejects_tag_page(connector: CNBCConnector) -> None:
    assert not connector.is_article_url("https://www.cnbc.com/technology/")


def test_url_matches_company_ticker(connector: CNBCConnector) -> None:
    assert connector.url_matches_company(
        "https://www.cnbc.com/2024/01/15/aapl-earnings.html",
        "",
        "Apple Inc.",
        "AAPL",
    )


def test_url_matches_company_title(connector: CNBCConnector) -> None:
    assert connector.url_matches_company(
        "https://www.cnbc.com/2024/01/15/tech-earnings.html",
        "Apple beats earnings estimates",
        "Apple Inc.",
        "AAPL",
    )


def test_url_matches_company_negative(connector: CNBCConnector) -> None:
    assert not connector.url_matches_company(
        "https://www.cnbc.com/2024/01/15/microsoft-results.html",
        "Microsoft revenue grows",
        "Apple Inc.",
        "AAPL",
    )


def test_build_ddg_queries_includes_site_filter(connector: CNBCConnector) -> None:
    dr = DateRange(start=date(2022, 1, 1), end=date(2023, 12, 31))
    queries = connector.build_ddg_queries("Apple Inc.", "AAPL", dr)
    assert all("cnbc.com" in q for q in queries)
    assert any("AAPL" in q for q in queries)


def test_build_ddg_queries_year_range(connector: CNBCConnector) -> None:
    dr = DateRange(start=date(2022, 1, 1), end=date(2024, 12, 31))
    queries = connector.build_ddg_queries("Apple Inc.", "AAPL", dr)
    years = [str(y) for y in range(2022, 2025)]
    for year in years:
        assert any(year in q for q in queries)
