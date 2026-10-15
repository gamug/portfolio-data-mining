"""Hermetic tests for scripts/score_ticker_rules.py (T-054 evidence for T-055)."""

from __future__ import annotations

import csv
import importlib.util
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "score_ticker_rules", _SCRIPTS / "score_ticker_rules.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
from ticker_tagging_sample import FIELDS  # noqa: E402

# (id, ticker, company, url, title, body, status, label)
CASES = [
    (1, "AAPL", "Apple Inc.", "https://x/apple-earnings.html", "Apple earnings", "", "ok", "yes"),
    (
        2,
        "ON",
        "ON Semiconductor",
        "https://x/turn-on-the-tv.html",
        "Turn on the TV",
        "",
        "ok",
        "no",
    ),
    (
        3,
        "XOM",
        "Exxon Mobil",
        "https://x/oil.html",
        "Oil rises",
        "Exxon Mobil said. Exxon Mobil cut.",
        "ok",
        "yes",
    ),
    (4, "BZ", "Bank Zeta", "https://x/n.html", "A bank said today", "", "failed", "no"),
    (5, "TSLA", "Tesla, Inc.", "https://x/ev.html", "TSLA jumps on deliveries", "", "ok", "yes"),
]


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    db, sample = tmp_path / "urls.db", tmp_path / "s.csv"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute(
            "CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, body_text TEXT, fetch_status TEXT)"
        )
        for i, _t, _c, _u, title, body, status, _l in CASES:
            con.execute("INSERT INTO discovered_urls VALUES (?, ?)", (i, title))
            con.execute("INSERT INTO articles VALUES (?, ?, ?, ?)", (i, title, body, status))
    with sample.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for i, t, c, u, _title, _b, _s, lab in CASES:
            writer.writerow(
                {"id": i, "url": u, "ticker": t, "company": c, "label": lab}
                | {k: "" for k in FIELDS if k not in ("id", "url", "ticker", "company", "label")}
                | {"stratum": "d/s/both", "stratum_size": "10"}
            )
    return db, sample


def test_rules_keep_and_drop(tmp_path: Path) -> None:
    db, sample = _fixture(tmp_path)
    data = mod.load(db, sample)
    kept = {r["id"]: mod.rule_plus_title_signals(r, t, b) for r, _s, t, b in data}
    assert kept == {"1": True, "2": False, "3": True, "4": False, "5": True}
    assert not mod.rule_name_or_explicit(data[2][0], data[2][2], data[2][3])  # needs the body rule
    assert mod.rule_plus_body_name(data[2][0], data[2][2], data[2][3])


def test_score_counts_and_ok_filter(tmp_path: Path) -> None:
    db, sample = _fixture(tmp_path)
    data = mod.load(db, sample)
    s = mod.score(data, mod.rule_plus_title_signals)
    assert (s["kept_correct"], s["kept_wrong"], s["lost_correct"], s["dropped_wrong"]) == (
        3,
        0,
        0,
        2,
    )
    assert s["precision"] == 1.0
    assert mod.score(data, mod.rule_current, only_ok=True)["kept_wrong"] == 1  # id 2; id 4 failed


def test_main_exclude_and_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, sample = _fixture(tmp_path)
    assert mod.main(["--db", str(db), "--sample", str(sample), "--exclude-ids", "1"]) == 0
    out = capsys.readouterr().out
    assert "rows scored: 4; excluded (unclear or unlabeled): 0" in out
    assert "current (accepts all)" in out
    assert mod.main(["--db", str(tmp_path / "no.db"), "--sample", str(sample)]) == 1


def _row(ticker: str, company: str, url: str = "https://x/a.html") -> dict[str, str]:
    return {"ticker": ticker, "company": company, "url": url}


@pytest.mark.parametrize(
    ("row", "title", "expected"),
    [
        (_row("GM", "General Motors"), "GM lays off 1,000 workers", "accept"),  # short upper-case
        (_row("A", "Agilent Technologies"), "Trust a month on", "reject"),  # lone letter
        (_row("ON", "ON Semiconductor"), "Why traders bet ON growth", "reject"),  # common word
        (_row("MMM", "3M"), "3M trims forecast", "accept"),  # digit name
        (_row("TTD", "Trade Desk"), "Trade war ends", "reject"),  # stopword first word
        (_row("TTD", "Trade Desk"), "The Trade Desk raises outlook", "accept"),  # full name
        (
            _row("HON", "Honeywell International"),
            "Honeywell stock soars",
            "review",
        ),  # first word only
        (_row("AAPL", "Apple Inc."), "Stocks (NASDAQ: AAPL) rise", "accept"),  # explicit form
        # real failures found on the validation sample (T-055)
        (
            _row("NWSA", "News Corp (Class A)", "https://c/bad-news-driving-the-euro.html"),
            "Bad News",
            "reject",
        ),
        (
            _row("PM", "Philip Morris International"),
            "UPDATE 2-Egypt PM says IMF talks resume",
            "review",
        ),
        (
            _row("SPGI", "S&P Global", "https://s/s-and-p-global-inc-2022-q1-results"),
            "S&P Global Inc. 2022 Q1",
            "accept",
        ),
        (
            _row(
                "IFF",
                "International Flavors & Fragrances",
                "https://s/international-flavors-and-fragrances-inc-2024",
            ),
            "x",
            "accept",
        ),
        (
            _row("PG", "Procter & Gamble"),
            "Activist firm Trian cuts holdings in P&G, Mondelez",
            "accept",
        ),  # alias
        (
            _row("GOOGL", "Alphabet Inc. (Class A)"),
            "Is Google's Prize Acquisition Cause For Alarm?",
            "accept",
        ),  # alias
        (
            _row("ICE", "Intercontinental Exchange", "https://c/immigrants-ice-report.html"),
            "Immigrants convicted",
            "review",
        ),  # a word-like ticker only: judged downstream
    ],
)
def test_design_class(row: dict[str, str], title: str, expected: str) -> None:
    assert mod.design_class(row, title) == expected


def test_design_report_weights_by_stratum(tmp_path: Path) -> None:
    db, sample = _fixture(tmp_path)
    text = mod.design_report(mod.load(db, sample))
    assert "design v1" in text
    assert "known-correct (labeled yes) classed accept: 0/0" in text


def test_with_host_reproduces_the_old_leak_and_diff_counts_it(tmp_path: Path) -> None:
    row = _row("NDAQ", "Nasdaq, Inc.", "https://www.nasdaq.com/articles/apple-beats-estimates")
    assert mod.design_class(row, "Apple beats", with_host=True) == "accept"
    assert mod.design_class(row, "Apple beats") == "reject"
    db = tmp_path / "urls.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, company TEXT, ticker TEXT, source TEXT)"
        )
        con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute(
            "INSERT INTO discovered_urls VALUES (1, ?, 'Apple beats', 'Nasdaq, Inc.', 'NDAQ', 'ddg')",
            (row["url"],),
        )
        con.execute(
            "INSERT INTO discovered_urls VALUES (2, 'https://c/x.html', 'Egypt PM says', 'Philip Morris International', 'PM', 'ddg')"
        )
    moves, by_ticker = mod.class_diff(db, with_host=True)
    assert moves == {("accept", "reject"): 1}
    assert by_ticker == {"NDAQ": 1}
    moves, by_ticker = mod.class_diff(db, abbreviations=mod.BASELINE_ABBREVIATIONS)
    assert moves == {("accept", "review"): 1}
    assert "class changes vs x: 1 rows" in mod.render_diff("x", moves, by_ticker)


@pytest.mark.parametrize(("ticker", "alias"), [(t, a) for t, al in mod.ALIASES.items() for a in al])
def test_every_alias_accepts_a_title_that_names_it(ticker: str, alias: str) -> None:
    row = _row(ticker, "Zzzz Holdings Corporation")  # the full name cannot match
    assert mod.design_class(row, f"Big news about {alias.title()} today") == "accept"
    assert mod.design_class(row, "Big news today", aliases={}) == "reject"


def test_coke_is_not_a_coca_cola_alias() -> None:
    row = _row("KO", "Coca-Cola Company (The)", "https://c/coke-prices-climb.html")
    assert mod.design_class(row, "Coke prices climb for steelmakers") == "reject"


def test_corpus_sizes_and_render(tmp_path: Path) -> None:
    db = tmp_path / "urls.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT,"
            " company TEXT, ticker TEXT, source TEXT)"
        )
        con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute(
            "INSERT INTO discovered_urls VALUES (1, 'https://c/a.html', 'Apple Inc. soars', 'Apple Inc.', 'AAPL', 'sitemap')"
        )
        con.execute(
            "INSERT INTO discovered_urls VALUES (2, 'https://c/b.html', 'Oil rises', 'Apple Inc.', 'AAPL', 'ddg')"
        )
    assert mod.corpus_sizes(db) == {("accept", "sitemap"): 1, ("reject", "ddg"): 1}
    assert mod.corpus_sizes(db, discovery=True) == {("reject", "sitemap"): 1, ("reject", "ddg"): 1}
    text = mod.render_sizes("x", mod.corpus_sizes(db))
    assert "2 rows" in text
    assert "accept " in text


def test_single_word_name_hits_counts_only_one_word_name_accepts(tmp_path: Path) -> None:
    db = tmp_path / "urls.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT,"
            " company TEXT, ticker TEXT, source TEXT)"
        )
        con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)")
        rows = [
            (1, "https://c/visa-bulletin.html", "", "Visa Inc.", "V"),
            (2, "https://c/visa-fees.html", "", "Visa Inc.", "V"),
            (3, "https://c/other.html", "Oil rises", "Visa Inc.", "V"),
            (4, "https://c/procter-and-gamble.html", "", "Procter & Gamble", "PG"),
        ]
        con.executemany("INSERT INTO discovered_urls VALUES (?, ?, ?, ?, ?, 'ddg')", rows)
    hits = mod.single_word_name_hits(db)
    assert hits == {("V", "visa"): 2}
    text = mod.render_name_hits(hits)
    assert "2 rows, 1 companies" in text
    assert "re-tag mode" in text
    assert "visa" in text


def test_render_name_hits_lists_only_the_top_entries() -> None:
    text = mod.render_name_hits(Counter({("V", "visa"): 5, ("A", "agilent"): 2}), top=1)
    assert "top 1" in text
    assert "visa" in text
    assert "agilent" not in text


def test_top_below_one_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        mod.main(["--names", "--top", "0"])
    assert "--top must be at least 1" in capsys.readouterr().err


# Re-tag-mode, in-sample figures (the rule was tuned on both samples): a tripwire so a rule edit
# cannot move the numbers PLAN reports without someone updating them. The titles
# come from the local corpus, so this skips where `urls.db` is absent.
_SAMPLE_FIGURES = {
    "ticker_tagging_sample.csv": (
        "accept   share  28.8%  wrong in class  10.9%",
        "known-correct (labeled yes) classed accept: 17/17",
    ),
    "ticker_validation_sample.csv": (
        "accept   share  20.2%  wrong in class   0.0%",
        "known-correct (labeled yes) classed accept: 0/0",
    ),
}


@pytest.mark.parametrize("sample", sorted(_SAMPLE_FIGURES))
def test_committed_samples_reproduce_the_plan_figures(sample: str) -> None:
    db = Path(os.environ.get("URLS_DB_FOR_TESTS", "/workspaces/thesis/data/urls.db"))
    if not db.is_file():
        pytest.skip("urls.db not available")
    data = mod.load(db, Path("data") / sample)
    text = mod.design_report(data)
    assert all(line in text for line in _SAMPLE_FIGURES[sample])
