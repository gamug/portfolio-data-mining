"""scripts/loose_name_precision.py: sample the promoted `loose_name` pairs and score the labels (T-062)."""

from __future__ import annotations

import csv
import importlib.util
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


def test_the_sample_is_stratified_deterministic_and_only_promoted_pairs(
    db_file: Path, tmp_path: Path
) -> None:
    out, manual = tmp_path / "s.csv", tmp_path / "m.csv"
    assert (
        mod.main(["sample", "--db", str(db_file), "--out", str(out), "--manual", str(manual)]) == 0
    )
    rows = list(csv.DictReader(out.open()))

    assert [r["stratum"] for r in rows].count("news_corp") == 10
    assert [r["stratum"] for r in rows].count("other") == 20
    assert {r["ticker"] for r in rows} <= {"NWS", "KLAC"}  # the Microsoft rejects are not drawn
    assert all(r["match_mode"] == "review" and r["label"] == "" for r in rows)
    assert {r["stratum_size"] for r in rows if r["stratum"] == "news_corp"} == {"14"}
    assert len(list(csv.DictReader(manual.open()))) == 30  # the labeling sheet has the same rows

    again = tmp_path / "again.csv"
    mod.main(
        ["sample", "--db", str(db_file), "--out", str(again), "--manual", str(tmp_path / "m2.csv")]
    )
    assert again.read_text() == out.read_text()


def test_the_summary_weights_the_strata_and_reports_unlabeled_rows() -> None:
    def row(stratum: str, label: str, size: int) -> dict[str, str]:
        return {"stratum": stratum, "label": label, "stratum_size": str(size)}

    rows = (
        [row("news_corp", "yes", 400)] * 9
        + [row("news_corp", "no", 400)]
        + [row("other", "yes", 40)] * 5
        + [row("other", "no", 40)] * 5
        + [row("other", "unclear", 40), row("other", "", 40)]
    )

    text = mod.score(rows)

    assert (
        "news_corp: 10 sampled of 400; 9 the company, 1 not, 0 unclear, 0 unlabeled; share 90%"
        in text
    )
    assert (
        "other: 12 sampled of 40; 5 the company, 5 not, 1 unclear, 1 unlabeled; share 50%" in text
    )
    assert "overall (weighted by stratum size): 86% are the company" in text
