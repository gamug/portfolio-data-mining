"""scripts/sitemap_preselect_check.py: the token-index preselection loses no pair (T-068)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from data_mining import matching as M
from news_collector.models import SitemapEntry

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "sitemap_preselect_check", _SCRIPTS / "sitemap_preselect_check.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

MIN_PAIRS = 100
COMPANIES = [
    ("KLAC", "KLA Corporation"),
    ("AAPL", "Apple Inc."),
    ("BAC", "Bank of America Corporation"),
    ("BRK.B", "Berkshire Hathaway"),
    ("ON", "ON Semiconductor"),
    ("A", "Agilent Technologies"),
    ("IT", "Gartner"),
    ("NWS", "News Corp (Class B)"),
    ("T", "AT&T Inc."),
    ("PM", "Philip Morris International"),
    ("KO", "Coca-Cola Company"),
    ("PEP", "PepsiCo"),
    ("MMM", "3M"),
    ("GOOGL", "Alphabet Inc. (Class A)"),
    ("AMZN", "Amazon.com"),
]


def _entry(
    slug: str, title: str | None = None, lastmod: date | None = date(2024, 5, 1)
) -> SitemapEntry:
    return SitemapEntry(
        url=f"https://www.cnbc.com/2024/05/01/{slug}.html", lastmod=lastmod, title=title
    )


def _base() -> list[SitemapEntry]:
    words = [
        "stock",
        "market",
        "shares",
        "jump",
        "fed",
        "rates",
        "oil",
        "gold",
        "tech",
        "bank",
        "ceo",
    ]
    return [_entry(f"{words[i % 11]}-{words[(i * 3) % 11]}-story-{i}") for i in range(200)]


def test_title_fixture_keeps_the_title_evidence() -> None:
    raw, companies = mod.title_fixture()
    result = mod.compare(raw, companies)["KLAC"]
    assert result["candidates"] == 1  # only the titled entry: the title index found it
    assert list(result["full"].values()) == [("accept", "explicit_ticker", "2026-10-19")]
    assert not mod.differing({"KLAC": result})


def test_equal_on_a_synthetic_corpus_with_titles() -> None:
    corpus = mod.synthetic_corpus(_base(), COMPANIES, n=1500, seed=3)
    result = mod.compare(corpus, COMPANIES)
    assert sum(len(r["full"]) for r in result.values()) > MIN_PAIRS  # not vacuous
    assert mod.differing(result) == []


@pytest.mark.parametrize("family", ["name", "loose_name", "weak_ticker", "title"])
def test_the_check_fails_when_an_anchor_family_is_removed(family: str) -> None:
    corpus = mod.synthetic_corpus(_base(), COMPANIES, n=3000, seed=3)
    weak = mod.compare(corpus, COMPANIES, skip=frozenset({family}))
    assert mod.differing(weak) != []


@pytest.mark.parametrize(
    ("ticker", "company", "slug", "title"),
    [
        ("BAC", "Bank of America Corporation", "bank-of-americas-ceo-speaks", None),  # possessive
        ("T", "AT&T Inc.", "neutral-slug", "AT&T results beat"),  # `&` read as `and`
        (
            "BRK.B",
            "Berkshire Hathaway",
            "neutral-slug",
            "(NYSE: BRK.B) rises",
        ),  # a dot in the ticker
        ("NWS", "News Corp (Class B)", "news-corporation-shares-pop", None),  # a stop-word name
        ("PEP", "PepsiCo", "pepsi-earnings-preview", None),  # brand of a camel-case name
        ("MMM", "3M", "3m-raises-outlook", None),  # a name with a digit
        ("ON", "ON Semiconductor", "neutral-slug", "NYSE: ON jumps"),  # an ordinary-word ticker
        ("GOOGL", "Alphabet Inc. (Class A)", "google-cloud-growth", None),  # an alias
        ("KLAC", "KLA Corporation", "neutral-slug", "$KLAC breaks out"),  # a cashtag
        ("KLAC", "KLA Corporation", "kla-corporation-is-a-great-company", None),
    ],
)
def test_the_anchors_cover_the_hard_forms(
    ticker: str, company: str, slug: str, title: str | None
) -> None:
    entry = _entry(slug, title)
    rows = [(entry.url, "2024-05-01", title)]
    expected = M.classify(entry.url, title or "", company, ticker)
    assert expected.cls != "reject"  # the case really is a pair of the matcher
    assert 0 in mod.TokenIndex(rows).candidates(company, ticker)


def test_pub_date_is_the_cached_lastmod_and_the_first_duplicate_wins() -> None:
    raw = [
        _entry("kla-corporation-q4", lastmod=date(2024, 5, 1)),
        _entry("kla-corporation-q4", lastmod=date(2024, 6, 1)),  # the same URL, a later lastmod
        _entry("kla-corporation-q3", lastmod=None),  # no lastmod: kept, pub_date None
    ]
    result = mod.compare(raw, [("KLAC", "KLA Corporation")])["KLAC"]
    assert (
        result["full"]["https://www.cnbc.com/2024/05/01/kla-corporation-q4.html"][2] == "2024-05-01"
    )
    assert result["pre"] == result["full"]
    assert result["full"]["https://www.cnbc.com/2024/05/01/kla-corporation-q3.html"][2] is None
    assert mod.duplicate_lastmods(raw) == (1, 1, 0)


def test_a_run_with_an_end_date_equals_the_full_scan_with_that_end() -> None:
    raw = [
        _entry("kla-corporation-q1", lastmod=date(2023, 3, 1)),
        _entry("kla-corporation-q2", lastmod=date(2024, 3, 1)),  # after the end: not a pair
        _entry("kla-corporation-q3", lastmod=None),  # no lastmod: kept, as today
    ]
    result = mod.compare(raw, [("KLAC", "KLA Corporation")], end=date(2023, 12, 31))["KLAC"]
    assert sorted(u.rsplit("-", 1)[1] for u in result["full"]) == ["q1.html", "q3.html"]  # not q2
    assert result["pre"] == result["full"]
    assert mod.differing({"KLAC": result}) == []


def test_the_cache_keeps_articles_only_and_normalizes() -> None:
    connector = mod.CNBCConnector(None)  # type: ignore[arg-type]
    raw = [
        SitemapEntry(
            url="https://www.cnbc.com/2024/05/01/a-story.html/?utm_source=x",
            lastmod=date(2024, 5, 1),
        ),
        SitemapEntry(
            url="https://www.cnbc.com/markets/", lastmod=date(2024, 5, 1)
        ),  # not an article
    ]
    rows = mod.cache_rows(raw, connector)
    assert [r[0] for r in rows] == ["https://www.cnbc.com/2024/05/01/a-story.html"]


def test_out_of_range_entries_are_left_out() -> None:
    raw = [_entry("kla-corporation-old", lastmod=date(2020, 1, 1)), _entry("kla-corporation-new")]
    assert [e.url.rsplit("/", 1)[1] for e in mod.in_range(raw, date(2022, 1, 1))] == [
        "kla-corporation-new.html"
    ]


def test_stored_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    entries = tmp_path / "entries.jsonl"
    entries.write_text(
        '["https://www.cnbc.com/2024/05/01/kla-corporation-q4.html", "2024-05-01", null]\n'
        '["https://www.cnbc.com/2020/05/01/old-story.html", "2020-05-01", null]\n'
    )
    db_path = tmp_path / "urls.db"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, domain TEXT, source TEXT, discovered_at TEXT, match_class TEXT)"
    )
    db.executemany(
        "INSERT INTO discovered_urls (url, domain, source, discovered_at, match_class) VALUES (?, 'cnbc.com', 'sitemap', '2026-08-10T00:00:00', ?)",
        [
            ("https://www.cnbc.com/2024/05/01/kla-corporation-q4.html", "accept"),
            ("https://www.cnbc.com/2020/05/01/old-story.html", "review"),
            ("https://www.cnbc.com/2023/01/01/left-the-sitemap.html", "accept"),
            ("https://www.cnbc.com/2023/01/01/left-the-sitemap-2.html", "reject"),
        ],
    )
    db.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, fetch_status TEXT)")
    db.execute("INSERT INTO articles VALUES (1, 'ok')")  # the 2024 row has an `ok` article
    db.commit()
    db.close()
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    mod.cmd_stored(argparse.Namespace(entries=str(entries), db=str(db_path)))
    out = capsys.readouterr().out
    assert "stored and gone 2" in out
    assert "in range today: 1" in out
    assert "left the sitemap: 1" in out
    assert "crawl article urls not stored: 0" in out
    assert "(with an `ok` article) with a URL year before 2022: 0 of 1" in out
    assert "(all) with a URL year before 2022: 1 of 3" in out
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_timing_is_a_positive_number_of_microseconds() -> None:
    rows = [(e.url, "2024-05-01", None) for e in _base()]
    assert mod.time_matcher(rows, COMPANIES, per_ticker=20, tickers=3) > 0


def test_check_takes_an_end_date_and_refuses_a_malformed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[argparse.Namespace] = []
    monkeypatch.setattr(mod, "cmd_check", seen.append)
    monkeypatch.setattr(sys, "argv", ["x", "check", "--entries", "e", "--end", "2025-06-30"])
    with pytest.raises(SystemExit):  # `main` exits with the command's status
        mod.main()
    assert seen[0].end == date(2025, 6, 30)
    monkeypatch.setattr(sys, "argv", ["x", "check", "--entries", "e", "--end", "30/06/2025"])
    with pytest.raises(SystemExit) as bad:
        mod.main()
    assert bad.value.code == 2  # argparse: invalid date
