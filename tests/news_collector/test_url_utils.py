"""Tests for URL normalization utilities."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from news_collector.utils.url import extract_domain, is_valid_http_url, normalize_url

# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Strips tracking params
        (
            "https://www.cnbc.com/2024/01/15/apple-earnings.html?utm_source=twitter&utm_medium=social",
            "https://www.cnbc.com/2024/01/15/apple-earnings.html",
        ),
        # Normalizes to lowercase host
        (
            "HTTPS://WWW.CNBC.COM/2024/01/15/apple-earnings.html",
            "https://www.cnbc.com/2024/01/15/apple-earnings.html",
        ),
        # Removes trailing slash
        (
            "https://www.nasdaq.com/articles/apple-beats-2024-01-01/",
            "https://www.nasdaq.com/articles/apple-beats-2024-01-01",
        ),
        # Removes fragment
        (
            "https://ft.com/content/abc-123#paywall-section",
            "https://ft.com/content/abc-123",
        ),
        # Keeps meaningful query params
        (
            "https://example.com/article?page=2&id=123",
            "https://example.com/article?id=123&page=2",
        ),
        # Bare path stays as /
        (
            "https://cnbc.com/",
            "https://cnbc.com/",
        ),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "url, expected_domain",
    [
        ("https://www.cnbc.com/article", "cnbc.com"),
        ("https://finance.yahoo.com/news/item", "finance.yahoo.com"),
        ("https://ft.com/content/abc", "ft.com"),
        ("https://www.investing.com/news/story-123", "investing.com"),
    ],
)
def test_extract_domain(url: str, expected_domain: str) -> None:
    assert extract_domain(url) == expected_domain


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://cnbc.com/article", True),
        ("http://example.com", True),
        ("ftp://example.com", False),
        ("not-a-url", False),
        ("", False),
    ],
)
def test_is_valid_http_url(url: str, expected: bool) -> None:
    assert is_valid_http_url(url) == expected


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@given(url=st.from_regex(r"https://[a-z]{3,10}\.[a-z]{2,4}/[a-z0-9/-]{1,50}", fullmatch=True))
@settings(max_examples=200)
def test_normalize_url_idempotent(url: str) -> None:
    """normalize_url applied twice must equal applied once."""
    once = normalize_url(url)
    twice = normalize_url(once)
    assert once == twice


@given(url=st.from_regex(r"https://[a-z]{3,10}\.[a-z]{2,4}/[a-z0-9/-]{1,50}", fullmatch=True))
@settings(max_examples=100)
def test_normalized_url_is_valid(url: str) -> None:
    """normalize_url output must always be a valid HTTP URL."""
    result = normalize_url(url)
    assert is_valid_http_url(result)
