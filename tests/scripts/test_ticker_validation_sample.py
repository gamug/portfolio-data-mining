"""Hermetic tests for scripts/ticker_validation_sample.py (T-055 validation sample)."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "ticker_validation_sample", _SCRIPTS / "ticker_validation_sample.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "urls.db"
    rows = []
    for i in range(30):  # accept: full name in the slug
        rows.append((f"https://cnbc.com/x/tesla-news-{i}.html", "Tesla, Inc.", "TSLA", "sitemap"))
    for i in range(30):  # reject: lone-letter ticker inside a word
        rows.append(
            (f"https://cnbc.com/x/a-{i}-in-a-month.html", "Agilent Technologies", "A", "sitemap")
        )
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT,"
            " domain TEXT, company TEXT, ticker TEXT, source TEXT)"
        )
        con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, body_text TEXT)")
        for url, company, ticker, source in rows:
            con.execute(
                "INSERT INTO discovered_urls (url, title, domain, company, ticker, source)"
                " VALUES (?, NULL, 'cnbc.com', ?, ?, ?)",
                (url, company, ticker, source),
            )
    return db


def test_draw_strata_exclusion_and_determinism(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first = mod.draw(db, exclude={"1", "2"}, seed=3)
    assert first == mod.draw(db, exclude={"1", "2"}, seed=3)
    assert not {"1", "2"} & {str(r["id"]) for r in first}
    by = {r["stratum"]: r for r in first}
    assert set(by) == {"accept/sitemap", "reject/sitemap"}  # empty strata are skipped
    assert by["accept/sitemap"]["stratum_size"] == 30
    assert sum(r["stratum"] == "accept/sitemap" for r in first) == 25
    assert all(r["label"] == "" for r in first)


def test_main_writes_csv_and_reports_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _db(tmp_path)
    existing = tmp_path / "existing.csv"
    existing.write_text("id\n1\n")
    out = tmp_path / "v.csv"
    args = ["--db", str(db), "--existing", str(existing), "--out", str(out)]
    assert mod.main(args) == 0
    rows = list(csv.DictReader(out.open()))
    assert rows
    assert {r["match_mode"] for r in rows} <= {"accept", "review", "reject"}
    assert mod.main(["--db", str(tmp_path / "no.db"), "--existing", str(existing)]) == 1
    assert "not found" in capsys.readouterr().err
