"""scripts/loose_name_precision.py: sample the promoted `loose_name` pairs and score the labels (T-062)."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from data_mining.db import connect
from data_mining.schema import apply_schema, run_migrations

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "loose_name_precision", _SCRIPTS / "loose_name_precision.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "urls.db"
    db = connect(str(path))
    apply_schema(db)
    run_migrations(db)
    rows = [(f"https://x.com/news-corp-story-{i}", "News Corp", "NWS") for i in range(14)]
    rows += [
        (f"https://x.com/news-corp-story-{i}", "News Corp", "NWSA") for i in range(14)
    ]  # same URLs
    rows += [(f"https://x.com/kla-corporation-q{i}", "KLA Corporation", "KLAC") for i in range(25)]
    rows += [
        (f"https://x.com/nothing-here-{i}", "Microsoft", "MSFT") for i in range(5)
    ]  # stays reject
    for i, (url, company, ticker) in enumerate(rows, 1):
        db.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (?, ?, 'x.com', ?, ?, 'sitemap', 'now',"
            " 'pending', 'reject', 'none')",
            (i, url, company, ticker),
        )
    db.commit()
    db.close()
    return path


def test_the_sample_is_stratified_by_distinct_url_deterministic_and_only_promoted_pairs(
    db_file: Path, tmp_path: Path
) -> None:
    out, manual = tmp_path / "s.csv", tmp_path / "m.csv"
    assert (
        mod.main(["sample", "--db", str(db_file), "--out", str(out), "--manual", str(manual)]) == 0
    )
    rows = list(csv.DictReader(out.open()))

    assert [r["stratum"] for r in rows].count("news_corp") == 10
    assert [r["stratum"] for r in rows].count("other") == 20
    assert len({r["url"] for r in rows}) == len(rows)  # NWS and NWSA are one URL, drawn once
    assert {r["ticker"] for r in rows} <= {
        "NWS",
        "NWSA",
        "KLAC",
    }  # the Microsoft rejects are not drawn
    assert all(r["match_mode"] == "review" and r["label"] == "" for r in rows)
    assert {r["stratum_size"] for r in rows if r["stratum"] == "news_corp"} == {
        "14"
    }  # URLs, not pairs
    assert len(list(csv.DictReader(manual.open()))) == 30  # the labeling sheet has the same rows

    again = tmp_path / "again.csv"
    mod.main(
        ["sample", "--db", str(db_file), "--out", str(again), "--manual", str(tmp_path / "m2.csv")]
    )
    assert again.read_text() == out.read_text()


def _row(stratum: str, label: str, size: int, id_: str = "1") -> dict[str, str]:
    return {"id": id_, "stratum": stratum, "label": label, "stratum_size": str(size)}


def test_the_summary_leads_with_a_lower_bound_and_reports_unclear_and_unlabeled_rows() -> None:
    rows = (
        [_row("news_corp", "yes", 400, f"n{i}") for i in range(9)]
        + [_row("news_corp", "no", 400, "n9")]
        + [_row("other", "yes", 40, f"o{i}") for i in range(5)]
        + [_row("other", "no", 40, f"p{i}") for i in range(5)]
        + [_row("other", "unclear", 40, "u"), _row("other", "", 40, "b")]
    )

    text = mod.score(rows)

    assert (
        "news_corp: 10 distinct URLs sampled of 400; 9 the company, 1 not, 0 unclear, 0 unlabeled; share 90%"
        in text
    )
    assert (
        "other: 12 distinct URLs sampled of 40; 5 the company, 5 not, 1 unclear, 1 unlabeled; share 50%"
        in text
    )
    overall = text.splitlines()[-2]
    assert overall.startswith("overall: 95% lower bound ") and "point estimate 86%" in overall
    low = int(overall.split("lower bound ")[1].split("%")[0])
    assert low < 86  # the bound is below the point estimate, and it comes first
    assert text.splitlines()[-1] == "1 unclear row(s) are not counted as the company"


def test_what_if_re_scores_with_a_contested_label_changed() -> None:
    rows = [_row("other", "yes", 10, f"y{i}") for i in range(4)] + [_row("other", "no", 10, "c")]

    as_labeled = mod.score(rows)
    changed = mod.score(rows, {"c": "yes"})

    assert "4 the company, 1 not" in as_labeled
    assert "5 the company, 0 not" in changed and "share 100%" in changed


def test_refresh_keeps_the_labeled_rows_that_are_still_promoted_and_replaces_the_others(
    db_file: Path, tmp_path: Path
) -> None:
    out, manual = tmp_path / "s.csv", tmp_path / "m.csv"
    mod.main(["sample", "--db", str(db_file), "--out", str(out), "--manual", str(manual)])
    rows = list(csv.DictReader(out.open()))
    for r in rows:
        r["label"], r["labeled_by"] = "yes", "hand"
    mod._write_sample(out, rows)
    victim = next(r for r in rows if r["stratum"] == "other")
    with sqlite3.connect(db_file) as con:  # the matcher stopped promoting it (now an accept)
        con.execute(
            "UPDATE discovered_urls SET match_class = 'accept' WHERE id = ?", (victim["id"],)
        )

    assert (
        mod.main(["refresh", "--db", str(db_file), "--sample", str(out), "--manual", str(manual)])
        == 0
    )
    new = list(csv.DictReader(out.open()))

    at = [r["id"] for r in rows].index(victim["id"])
    assert [r["id"] for i, r in enumerate(new) if i != at] == [
        r["id"] for i, r in enumerate(rows) if i != at
    ]
    assert (
        new[at]["id"] != victim["id"] and new[at]["stratum"] == "other" and new[at]["label"] == ""
    )
    assert sum(1 for r in new if r["label"] == "yes") == 29
    assert len({r["url"] for r in new}) == 30
    assert len(list(csv.DictReader(manual.open()))) == 30
