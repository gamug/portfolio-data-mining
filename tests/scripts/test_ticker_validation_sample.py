"""Hermetic tests for scripts/ticker_validation_sample.py (T-055 validation sample)."""

from __future__ import annotations

import csv
import importlib.util
import os
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
    for i in range(12):  # review (ddg): a non-word ticker in the slug only
        rows.append(
            (
                f"https://finance.yahoo.com/news/cnp-update-{i}.html",
                "CenterPoint Energy",
                "CNP",
                "ddg",
            )
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
    assert set(by) == {"accept/sitemap", "reject/sitemap", "review/ddg"}  # empty strata skipped
    assert sum(r["stratum"] == "review/ddg" for r in first) == 10
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


def _labeled_sheet(db: Path) -> list[dict[str, str]]:
    rows = [{k: str(v) for k, v in r.items()} for r in mod.draw(db, set(), 1, final=True)]
    for r in rows:
        r["label"], r["labeled_by"] = "yes", "hand"
    return rows


def test_refresh_replaces_only_the_rows_whose_class_changed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rows = _labeled_sheet(db)
    victim = next(r for r in rows if r["match_mode"] == "reject")
    with sqlite3.connect(db) as con:  # the matcher now reads this pair as the company
        con.execute(
            "UPDATE discovered_urls SET title = 'Agilent Technologies earnings' WHERE id = ?",
            (victim["id"],),
        )

    new, dropped = mod.refresh(rows, db, exclude=set())

    assert dropped == [victim["id"]]
    at = rows.index(victim)
    assert [r["id"] for r in new[:at] + new[at + 1 :]] == [
        r["id"] for r in rows[:at] + rows[at + 1 :]
    ]
    replaced = new[rows.index(victim)]
    assert replaced["id"] not in {r["id"] for r in rows}
    assert (replaced["match_mode"], replaced["source"]) == ("reject", "sitemap")
    assert replaced["label"] == "" and replaced["labeled_by"] == ""
    assert all(r["label"] == "yes" for r in new if r["id"] != replaced["id"])  # labels kept
    assert mod.refresh(new, db, exclude=set())[1] == []  # now fresh


def test_refresh_never_reuses_an_earlier_sample_id(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rows = _labeled_sheet(db)
    victim = next(r for r in rows if r["match_mode"] == "reject")
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE discovered_urls SET title = 'Agilent Technologies earnings' WHERE id = ?",
            (victim["id"],),
        )
        unused = [
            str(i)
            for (i,) in con.execute("SELECT id FROM discovered_urls WHERE id BETWEEN 31 AND 60")
            if str(i) not in {r["id"] for r in rows}
        ]
    earlier = set(unused[:-1])  # all but one unused reject pair were in an earlier sample

    new, _ = mod.refresh(rows, db, exclude=earlier)

    replaced = new[rows.index(victim)]
    assert replaced["id"] == unused[-1]  # the only fresh pair left in the stratum
    assert not {r["id"] for r in new} & earlier


def _write_sheet(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_a_matcher_change_makes_the_sheet_stale_and_refresh_makes_it_fresh(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rows = _labeled_sheet(db)
    victim = next(r for r in rows if r["match_mode"] == "reject")
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE discovered_urls SET title = 'Agilent Technologies earnings' WHERE id = ?",
            (victim["id"],),
        )
    sheet = tmp_path / "sheet.csv"
    _write_sheet(sheet, rows)

    stale = [p for p in mod._db_problems(rows, db) if "current matcher" in p]
    assert len(stale) == 1

    assert mod._refresh(sheet, db) == 0
    fresh = list(csv.DictReader(sheet.open()))
    assert not [p for p in mod._db_problems(fresh, db) if "current matcher" in p]
    assert sum(1 for r in fresh if not r["label"]) == 1  # only the replacement needs a label


@pytest.mark.skipif(
    "TICKER_SHEET_DB" not in os.environ, reason="set TICKER_SHEET_DB to the real urls.db"
)
def test_the_committed_t062_sheet_is_fresh_for_the_shipped_matcher() -> None:
    """Run as `TICKER_SHEET_DB=/path/urls.db uv run pytest`: a matcher change that leaves the committed
    labeled sheet behind fails here, not only in the maintainer's `--verdict` run."""
    sheet = Path(__file__).resolve().parents[2] / "data" / "ticker_reclass_check_sample.csv"
    rows = list(csv.DictReader(sheet.open()))

    assert not [p for p in mod._db_problems(rows, Path(os.environ["TICKER_SHEET_DB"]))]
