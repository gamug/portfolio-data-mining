"""Hermetic tests for scripts/ticker_tagging_sample.py (T-054)."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))  # the script imports its sibling audit script
_spec = importlib.util.spec_from_file_location(
    "ticker_tagging_sample", _SCRIPTS / "ticker_tagging_sample.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "urls.db"
    rows = []
    for i in range(30):
        rows.append(
            (
                f"https://cnbc.com/x/a-{i}-in-a-month.html",
                "cnbc.com",
                "Agilent Technologies",
                "A",
                "sitemap",
            )
        )
    for i in range(12):
        rows.append(
            (
                f"https://cnbc.com/x/agilent-technologies-{i}.html",
                "cnbc.com",
                "Agilent Technologies",
                "A",
                "sitemap",
            )
        )
    rows.append(
        (
            "https://cnbc.com/x/agilent-technologies-results.html",
            "cnbc.com",
            "Agilent Technologies",
            "A",
            "ddg",
        )
    )
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, domain TEXT,"
            " company TEXT, ticker TEXT, source TEXT)"
        )
        con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)")
        for url, domain, company, ticker, source in rows:
            con.execute(
                "INSERT INTO discovered_urls (url, title, domain, company, ticker, source) VALUES (?,?,?,?,?,?)",
                (url, None, domain, company, ticker, source),
            )
    return db


def test_normalized_name_and_full_name_presence() -> None:
    assert mod.normalized_name("Alphabet Inc. (Class C)") == "alphabet"
    assert mod.full_name_present(
        "Agilent Technologies", "https://x/agilent-technologies-q1.html", None
    )
    assert not mod.full_name_present("Agilent Technologies", "https://x/agilent-q1.html", None)
    assert not mod.full_name_present("3M", "https://x/3m-profit.html", None)  # too short


def test_stratum_splits_word_tickers() -> None:
    assert mod.stratum_of("d", "s", "ticker_only", "A") == "d/s/ticker_only/word_ticker"
    assert mod.stratum_of("d", "s", "ticker_only", "AAPL") == "d/s/ticker_only/other_ticker"
    assert mod.stratum_of("d", "s", "both", "A") == "d/s/both"


def test_draw_is_deterministic_and_reports_population(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first = mod.draw(db, seed=1)
    assert first == mod.draw(db, seed=1)
    by_stratum = {r["stratum"]: r for r in first}
    word = by_stratum["cnbc.com/sitemap/ticker_only/word_ticker"]
    assert word["stratum_size"] == 30
    assert sum(r["stratum"] == word["stratum"] for r in first) == mod.SMALL_STRATUM_N
    assert len({r["id"] for r in first}) == len(first)
    assert all(r["label"] == "" for r in first)
    assert mod.KNOWN_CORRECT in by_stratum


def test_wilson_bounds() -> None:
    assert mod.wilson(0, 0) == (0.0, 0.0)
    lo, hi = mod.wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8


def _write(path: Path, labels: list[tuple[str, str, int, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=mod.FIELDS)
        writer.writeheader()
        for i, (stratum, mode, size, label) in enumerate(labels):
            writer.writerow(
                {"id": i, "url": f"u{i}", "ticker": "T", "company": "C", "domain": "d.com", "source": "s",
                 "match_mode": mode, "stratum": stratum, "stratum_size": size, "label": label, "labeled_by": "hand"}
            )  # fmt: skip


def test_report_weights_strata_and_excludes_unclear(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    # big stratum all wrong, small stratum all right: weighted rate follows the big one.
    rows = [("d/s/ticker_only", "ticker_only", 900, "no")] * 5 + [
        ("d/s/both", "both", 100, "yes")
    ] * 5
    rows += [("d/s/ticker_only", "ticker_only", 900, "unclear")]
    rows += [(mod.KNOWN_CORRECT, "both", 50, "yes")] * 4 + [(mod.KNOWN_CORRECT, "both", 50, "no")]
    _write(path, rows)
    text = mod.report(path)
    assert "unclear rows excluded from every rate: 1" in text
    assert "ticker_only" in text
    assert "known-correct candidates labeled yes: 4/5" in text
    mode_rates = {
        name: rate
        for name, _, _, rate, _ in mod._rate_rows(
            [
                r
                for r in csv.DictReader(path.open())
                if r["label"] != "unclear" and r["stratum"] != mod.KNOWN_CORRECT
            ],
            "match_mode",
        )
    }
    assert mode_rates == {"both": 0.0, "ticker_only": 1.0}


def test_report_rejects_unlabeled(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    _write(path, [("d/s/both", "both", 10, "")])
    with pytest.raises(ValueError, match="no yes/no/unclear label"):
        mod.report(path)


def test_main_sample_and_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out" / "sample.csv"
    assert mod.main(["sample", "--db", str(_db(tmp_path)), "--out", str(out)]) == 0
    assert out.is_file()
    assert mod.main(["sample", "--db", str(tmp_path / "nope.db")]) == 1
    assert mod.main(["report", "--sample", str(tmp_path / "nope.csv")]) == 1
    assert "not found" in capsys.readouterr().err
