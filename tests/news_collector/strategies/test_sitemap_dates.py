"""Sitemap `lastmod` parsing and the date-range filter (T-075).

`_parse_date` used to return None for every value, so no entry was ever filtered by the run's date
range and no row got a `pub_date`.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest
from defusedxml import ElementTree as DefusedET

from news_collector.models import DateRange
from news_collector.strategies.sitemap import (
    _parse_date,
    _parse_sitemap_index,
    _parse_sitemap_leaf,
)

_RANGE = DateRange(start=date(2022, 1, 1), end=date(2026, 8, 10))
_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-18T04:20:10Z", date(2026, 10, 18)),  # what cnbc publishes
        ("2026-10-18T04:20:10+00:00", date(2026, 10, 18)),
        ("2026-10-18T23:59:59-05:00", date(2026, 10, 18)),  # the written date, not converted to UTC
        ("2026-10-18T04:20:10", date(2026, 10, 18)),
        ("2026-10-18", date(2026, 10, 18)),
        ("  2026-10-18  ", date(2026, 10, 18)),
        ("2026-10", date(2026, 10, 1)),
        ("", None),
        ("not a date", None),
        ("2026-13-45", None),
    ],
)
def test_parse_date(text: str, expected: date | None) -> None:
    assert _parse_date(text) == expected


def _leaf(*entries: tuple[str, str | None]) -> object:
    urls = "".join(
        f"<url><loc>{loc}</loc>{f'<lastmod>{lm}</lastmod>' if lm else ''}</url>"
        for loc, lm in entries
    )
    return DefusedET.fromstring(f"<urlset {_NS}>{urls}</urlset>")


def test_leaf_filters_by_the_date_range_and_sets_lastmod() -> None:
    root = _leaf(
        ("https://x.com/old", "2012-07-10T01:02:03Z"),
        ("https://x.com/before", "2021-12-31T23:59:59Z"),
        ("https://x.com/first-day", "2022-01-01T00:00:00Z"),
        ("https://x.com/last-day", "2026-08-10T12:00:00Z"),
        ("https://x.com/after", "2026-10-18T04:20:10Z"),
    )

    entries = _parse_sitemap_leaf(root, _RANGE)  # type: ignore[arg-type]

    assert [(e.url, e.lastmod) for e in entries] == [
        ("https://x.com/first-day", date(2022, 1, 1)),
        ("https://x.com/last-day", date(2026, 8, 10)),
    ]


def test_leaf_keeps_an_entry_without_lastmod() -> None:
    entries = _parse_sitemap_leaf(_leaf(("https://x.com/undated", None)), _RANGE)  # type: ignore[arg-type]

    assert [(e.url, e.lastmod) for e in entries] == [("https://x.com/undated", None)]


def test_leaf_keeps_an_unparseable_lastmod_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    root = _leaf(("https://x.com/odd", "yesterday"), ("https://x.com/old", "2012-07-10"))

    with caplog.at_level(logging.WARNING):
        entries = _parse_sitemap_leaf(root, _RANGE)  # type: ignore[arg-type]

    assert [e.url for e in entries] == ["https://x.com/odd"]
    assert "1 sitemap entries with an unparseable lastmod were kept" in caplog.text


def test_index_skips_children_older_than_the_range_start() -> None:
    root = DefusedET.fromstring(
        f"<sitemapindex {_NS}>"
        "<sitemap><loc>https://x.com/old.xml</loc><lastmod>2019-05-01T00:00:00Z</lastmod></sitemap>"
        "<sitemap><loc>https://x.com/new.xml</loc><lastmod>2026-10-18T00:00:00Z</lastmod></sitemap>"
        "<sitemap><loc>https://x.com/undated.xml</loc></sitemap>"
        "</sitemapindex>"
    )

    assert _parse_sitemap_index(root, _RANGE) == [  # type: ignore[arg-type]
        "https://x.com/new.xml",
        "https://x.com/undated.xml",
    ]
