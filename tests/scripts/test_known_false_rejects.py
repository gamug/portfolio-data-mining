"""scripts/known_false_rejects.py: the scan that bounds the T-057 false rejects from below."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from data_mining.matching import classify

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import known_false_rejects as scan_mod  # noqa: E402  (needs the scripts path first)

_SHEET = _ROOT / "data" / "retag_known_false_rejects.csv"


def _row(ticker: str, company: str, url: str) -> dict[str, str]:
    return {"article_id": "1", "ticker": ticker, "company": company, "url": url}


@pytest.mark.parametrize(
    ("company", "url"),
    [
        (
            "KLA Corporation",
            "https://x.com/a/kla-corporation-2023-q4-results",
        ),  # short name + suffix
        ("Southern Company", "https://x.com/2024/southern-company-ceo-says.html"),  # stopword name
        ("Bank of America", "https://x.com/bank-of-americas-ceo-says.html"),  # possessive
        ("Dollar General", "https://x.com/discount-dollar-generals-sales-miss.html"),  # possessive
        (
            "International Flavors & Fragrances",
            "https://x.com/stake-in-international-flavors-fragrances",
        ),
        (
            "Trade Desk (The)",
            "https://x.com/the-trade-desks-q4-earnings?mailingid=1",
        ),  # the, parens
    ],
)
def test_in_url_finds_the_company_name_as_whole_words(company: str, url: str) -> None:
    assert scan_mod.in_url(company, url)


@pytest.mark.parametrize(
    ("company", "url"),
    [
        ("Best Buy", "https://x.com/best-buying-set-up-in-this-metal"),  # inside a longer word
        ("Bank of America", "https://bank-of-america.com/news/other-story"),  # host is not the path
        ("Apple Inc.", "https://x.com/a/pineapple-inc-story"),  # starts inside a word
    ],
)
def test_in_url_ignores_partial_words_and_the_host(company: str, url: str) -> None:
    assert not scan_mod.in_url(company, url)


def test_scan_leaves_out_the_reviewed_noise_tickers() -> None:
    rows = [
        _row("ON", "ON Semiconductor", "https://x.com/tariffs-on-semiconductors.html"),
        _row("KLAC", "KLA Corporation", "https://x.com/kla-corporation-q4"),
    ]

    assert [r["ticker"] for r in scan_mod.scan(rows)] == ["KLAC"]


def test_the_committed_file_is_the_pairs_the_frozen_rule_rejects() -> None:
    with _SHEET.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows
    assert len({r["article_id"] for r in rows}) == len(rows)
    assert not {r["ticker"] for r in rows} & set(scan_mod.NOISE)
    assert all(scan_mod.in_url(r["company"], r["url"]) for r in rows)
    # pins today's behaviour: a matcher fix changes this, then re-class and release the articles
    assert {classify(r["url"], "", r["company"], r["ticker"]).cls for r in rows} == {"reject"}
