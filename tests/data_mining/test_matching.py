"""Tests for `data_mining.matching` (the T-055 three-class rule)."""

from __future__ import annotations

import random

import pytest

from data_mining import matching
from data_mining.matching import ALIASES, classify


def cls(url: str, title: str, company: str, ticker: str) -> str:
    return classify(url, title, company, ticker).cls


@pytest.mark.parametrize(
    ("url", "title", "company", "ticker", "expected", "reason"),
    [
        # accept: full name, alias, explicit ticker form, upper-case ticker in the title
        ("https://x.com/2024/01/apple-earnings.html", "", "Apple Inc.", "AAPL", "accept", "name"),
        ("https://x.com/a.html", "Apple beats estimates", "Apple Inc.", "AAPL", "accept", "name"),
        (
            "https://x.com/a.html",
            "Is Google's prize a worry?",
            "Alphabet Inc.",
            "GOOGL",
            "accept",
            "alias",
        ),
        (
            "https://x.com/a.html",
            "Cuts in P&G, Mondelez",
            "Procter & Gamble",
            "PG",
            "accept",
            "alias",
        ),
        (
            "https://x.com/a.html",
            "Shares (NASDAQ: AAPL) rise",
            "Zzz Corp",
            "AAPL",
            "accept",
            "explicit_ticker",
        ),
        ("https://x.com/a.html", "$TSLA jumps", "Zzz Corp", "TSLA", "accept", "explicit_ticker"),
        (
            "https://x.com/a.html",
            "GM lays off 1,000",
            "General Motors",
            "GM",
            "accept",
            "upper_ticker",
        ),
        ("https://x.com/3m-profit.html", "", "3M", "MMM", "accept", "name"),
        ("https://x.com/s-and-p-global-inc-q1", "", "S&P Global", "SPGI", "accept", "name"),
        (
            "https://x.com/international-flavors-and-fragrances-inc",
            "",
            "International Flavors & Fragrances",
            "IFF",
            "accept",
            "name",
        ),
        # review: weaker signs only
        (
            "https://x.com/ford-q2-sales.html",
            "",
            "Ford Motor Company",
            "F",
            "review",
            "weak_first_word",
        ),
        ("https://x.com/cnp-update.html", "", "CenterPoint Energy", "CNP", "review", "weak_ticker"),
        (
            "https://x.com/a.html",
            "Egypt PM says talks resume",
            "Philip Morris International",
            "PM",
            "review",
            "abbreviation",
        ),
        # reject: ordinary-word tickers, generic first words, substrings, the host
        ("https://x.com/a-1-in-a-month.html", "", "Agilent Technologies", "A", "reject", "none"),
        (
            "https://x.com/trusts-trump-on-trade.html",
            "",
            "ON Semiconductor",
            "ON",
            "reject",
            "none",
        ),
        ("https://x.com/a.html", "Bank rates rise", "Bank of America", "BAC", "reject", "none"),
        ("https://x.com/a.html", "Trade war ends", "Trade Desk", "TTD", "reject", "none"),
        (
            "https://x.com/bad-news-driving-the-euro.html",
            "",
            "News Corp (Class A)",
            "NWSA",
            "reject",
            "none",
        ),
        (
            "https://www.nasdaq.com/articles/apple-beats",
            "Apple beats",
            "Nasdaq, Inc.",
            "NDAQ",
            "reject",
            "none",
        ),
        ("https://x.com/a.html", "Oil rises", "Microsoft", "MSFT", "reject", "none"),
    ],
)
def test_classify(
    url: str, title: str, company: str, ticker: str, expected: str, reason: str
) -> None:
    got = classify(url, title, company, ticker)
    assert (got.cls, got.reason) == (expected, reason)


def test_ticker_is_never_a_raw_substring() -> None:
    # `AAPL` inside a longer token, and `A`/`ON` inside words, never count
    assert cls("https://x.com/pineaapl-news.html", "", "Apple Inc.", "AAPL") == "reject"
    assert cls("https://x.com/season-reason.html", "", "ON Semiconductor", "ON") == "reject"


def test_host_is_not_matched_but_with_host_reproduces_the_old_leak() -> None:
    args = ("https://www.nasdaq.com/articles/apple-beats", "Apple beats", "Nasdaq, Inc.", "NDAQ")
    assert classify(*args).cls == "reject"
    assert classify(*args, with_host=True).cls == "accept"


def test_ordinary_word_names_and_tickers_never_accept_alone() -> None:
    for ticker in matching.WORD_TICKERS:
        assert cls("https://x.com/a.html", f"{ticker} is up", "Zzz Corp", ticker) != "accept"
    assert cls("https://x.com/a.html", "Latest News", "News Corp", "NWS") != "accept"


@pytest.mark.parametrize(("ticker", "alias"), [(t, a) for t, al in ALIASES.items() for a in al])
def test_every_alias_accepts(ticker: str, alias: str) -> None:
    assert (
        cls("https://x.com/a.html", f"About {alias.title()} today", "Zzzz Holdings", ticker)
        == "accept"
    )
    assert (
        classify(
            "https://x.com/a.html", f"About {alias} today", "Zzzz Holdings", ticker, aliases={}
        ).cls
        == "reject"
    )


def test_coke_is_not_a_coca_cola_alias() -> None:
    assert (
        cls(
            "https://x.com/coke-prices-climb.html",
            "Coke prices climb",
            "Coca-Cola Company (The)",
            "KO",
        )
        == "reject"
    )


def test_the_rule_is_monotone_more_title_never_lowers_the_class() -> None:
    rank = {"reject": 0, "review": 1, "accept": 2}
    pieces = [
        "Apple",
        "beats",
        "GM",
        "news",
        "PM",
        "(NASDAQ: AAPL)",
        "Google",
        "Honeywell",
        "ON",
        "a",
        "Trade",
    ]
    pairs = [
        ("https://x.com/a.html", "Apple Inc.", "AAPL"),
        ("https://x.com/honeywell-q2.html", "Honeywell International", "HON"),
        ("https://x.com/a.html", "General Motors", "GM"),
        ("https://x.com/a.html", "Philip Morris International", "PM"),
        ("https://x.com/a.html", "Alphabet Inc.", "GOOGL"),
    ]
    rng = random.Random(7)  # noqa: S311 - reproducible test data
    for url, company, ticker in pairs:
        for _ in range(60):
            words = [rng.choice(pieces) for _ in range(rng.randint(0, 6))]
            short = " ".join(words)
            longer = " ".join([*words, rng.choice(pieces), rng.choice(pieces)])
            assert rank[cls(url, longer, company, ticker)] >= rank[cls(url, short, company, ticker)]


@pytest.mark.parametrize(
    ("title", "company", "ticker", "expected"),
    [
        ("Options (A) and (B) explained", "Agilent Technologies", "A", "reject"),
        ("Switch (ON) the lights", "ON Semiconductor", "ON", "reject"),
        ("Plan B (C) results", "Citigroup", "C", "reject"),
        ("Fed (PM) says", "Philip Morris International", "PM", "review"),
        ("Agilent (NYSE: A) rises", "Agilent Technologies", "A", "accept"),
        ("Agilent (NYSE American: A) rises", "Agilent Technologies", "A", "accept"),
        ("Rally in $ON today", "ON Semiconductor", "ON", "review"),
    ],
)
def test_bare_parenthesised_ticker_is_not_explicit(
    title: str, company: str, ticker: str, expected: str
) -> None:
    assert cls("https://x.com/news/story", title, company, ticker) == expected


@pytest.mark.parametrize(
    ("title", "company", "ticker", "expected"),
    [
        ("Meet the NYSE: A-list stocks", "Agilent Technologies", "A", "reject"),
        ("Agilent (NYSE: A-list) rises", "Agilent Technologies", "A", "review"),
        ("Shares of NYSE: A rise", "Agilent Technologies", "A", "accept"),
        ("Apple (NASDAQ: AAPL).", "Zzz Corp", "AAPL", "accept"),
        ("Calls on $TSLA, buyers", "Zzz Corp", "TSLA", "accept"),
        ("Fed $PM", "Philip Morris International", "PM", "review"),
        ("Rally in $ON today", "ON Semiconductor", "ON", "review"),
        ("Rally in $A today", "Agilent Technologies", "A", "review"),
        ("Price is $ALL in", "Allstate", "ALL", "review"),
        ("Shares (NYSE: ON) rally", "ON Semiconductor", "ON", "accept"),
        ("Shares Nasdaq: ON rise", "ON Semiconductor", "ON", "accept"),
        ("Agilent NYSE American: A rises", "Agilent Technologies", "A", "accept"),
        ("Fund NYSEARCA: PM", "Philip Morris International", "PM", "accept"),
        ("Fund NYSE MKT: PM", "Philip Morris International", "PM", "accept"),
        ("Agilent (NYSE MKT: A) rises", "Agilent Technologies", "A", "accept"),
    ],
)
def test_explicit_forms_edges_and_cashtags_of_common_tickers(
    title: str, company: str, ticker: str, expected: str
) -> None:
    assert cls("https://x.com/news/story", title, company, ticker) == expected


@pytest.mark.parametrize(
    ("url", "company", "ticker"),
    [
        ("https://x.com/kla-corporation-2022-q4-results", "KLA Corporation", "KLAC"),
        ("https://x.com/2024/southern-company-ceo-says.html", "Southern Company", "SO"),
        ("https://x.com/murdochs-news-corp-to-sell-foxtel", "News Corp (Class B)", "NWS"),
        (
            "https://x.com/stake-in-international-flavors-fragrances",
            "International Flavors & Fragrances",
            "IFF",
        ),
        ("https://x.com/pepsi-why-not-to-buy-this-soft-drink-maker", "PepsiCo", "PEP"),
        ("https://x.com/semiconductor-bank-of-americas-top-chip-stocks", "Bank of America", "BAC"),
        ("https://x.com/news-corps-dual-class-structure", "News Corp (Class B)", "NWS"),
        ("https://x.com/dollar-generals-sales-miss", "Dollar General", "DG"),
    ],
)
def test_a_full_name_the_url_spells_loosely_is_review_never_reject(
    url: str, company: str, ticker: str
) -> None:
    assert classify(url, "", company, ticker) == matching.Match("review", "loose_name")


@pytest.mark.parametrize(
    ("url", "company", "ticker"),
    [
        ("https://x.com/southern-states-weather", "Southern Company", "SO"),  # lone stopword
        ("https://x.com/latest-news-today", "News Corp (Class A)", "NWSA"),  # lone ordinary word
        ("https://x.com/kla-news", "KLA Corporation", "KLAC"),  # name split by other words
        ("https://x.com/key-to-a-good-retirement", "KeyCorp", "KEY"),  # brand too short
        ("https://x.com/cis-outlook-2024", "Cisco", "CSCO"),  # no capital tail to cut
        ("https://x.com/sys-admin-tips", "Sysco", "SYY"),
    ],
)
def test_a_loose_name_needs_the_whole_name(url: str, company: str, ticker: str) -> None:
    assert cls(url, "", company, ticker) == "reject"


@pytest.mark.parametrize(
    ("url", "company", "ticker"),
    [
        (
            "https://x.com/pros-best-buys-in-health-care",
            "Best Buy",
            "BBY",
        ),  # a plural, not the company
        ("https://x.com/cramer-on-semiconductors-risk", "ON Semiconductor", "ON"),
        ("https://x.com/best-apples-for-pies", "Apple Inc.", "AAPL"),
        ("https://x.com/student-visas-explained", "Visa Inc.", "V"),
        ("https://x.com/tariffs-on-semiconductors-hit-asia", "ON Semiconductor", "ON"),
    ],
)
def test_a_trailing_s_in_the_url_is_not_read_as_the_company(
    url: str, company: str, ticker: str
) -> None:
    assert matching.Match("review", "loose_name") != classify(url, "", company, ticker)


# The 11 names of the real universe (506 company names in `urls.db`) whose last word ends in "co" or
# "corp": the camel-case rule must cut exactly two of them. A name added to the universe later is not
# in this list: re-scan the company names of `urls.db` for a capital `Co`/`Corp` tail when it changes.
CO_ENDING_NAMES = [
    "Costco",
    "Cisco",
    "Fifth Third Bancorp",
    "Invesco",
    "KeyCorp",
    "Kimco Realty",
    "Labcorp",
    "Masco",
    "PepsiCo",
    "Sysco",
    "U.S. Bancorp",
]


def test_the_camel_case_tail_cuts_only_pepsico_and_keycorp() -> None:
    cut = [n for n in CO_ENDING_NAMES if matching._CAMEL_TAIL.search(n)]

    assert cut == ["KeyCorp", "PepsiCo"]
