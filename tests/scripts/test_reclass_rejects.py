"""scripts/reclass_rejects.py: promote the rejected pairs the current matcher no longer rejects (T-062)."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from data_mining.db import connect
from data_mining.quarantine import quarantine_articles
from data_mining.schema import apply_schema, run_migrations

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("reclass_rejects", _SCRIPTS / "reclass_rejects.py")
assert _spec is not None
assert _spec.loader is not None
reclass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reclass)

# id, url, ticker, company, source, status
ROWS = [
    (
        1,
        "https://x.com/kla-corporation-q4-results",
        "KLAC",
        "KLA Corporation",
        "sitemap",
        "fetched",
    ),
    (2, "https://x.com/turn-on-the-tv.html", "ON", "ON Semiconductor", "sitemap", "fetched"),
    (3, "https://x.com/bank-of-americas-picks", "BAC", "Bank of America", "sitemap", "skipped"),
    (4, "https://x.com/feed-item", "MSFT", "Microsoft", "api", "fetched"),
]


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "urls.db"
    db = connect(str(path))
    apply_schema(db)
    run_migrations(db)
    for i, url, ticker, company, source, status in ROWS:
        db.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (?, ?, 'x.com', ?, ?, ?, 'now', ?, 'reject', 'none')",
            (i, url, company, ticker, source, status),
        )
        if status == "fetched":
            db.execute("INSERT INTO articles (id, fetch_status) VALUES (?, 'ok')", (i,))
    db.commit()
    quarantine_articles(db)
    db.close()
    return path


def _rows(path: Path) -> dict[int, tuple[str, str, str]]:
    with sqlite3.connect(path) as con:
        return {
            r[0]: r[1:]
            for r in con.execute(
                "SELECT id, match_class, match_reason, status FROM discovered_urls"
            )
        }


def test_dry_run_reports_the_promotions_and_writes_nothing(
    db_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _rows(db_file)

    assert reclass.main(["--db", str(db_file)]) == 0
    assert '"promotions": 2' in capsys.readouterr().out
    assert _rows(db_file) == before


def test_apply_promotes_to_review_and_keeps_the_article_quarantined(
    db_file: Path, tmp_path: Path
) -> None:
    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")]) == 0

    rows = _rows(db_file)
    assert rows[1] == ("review", "loose_name", "fetched")
    assert rows[2] == ("reject", "none", "fetched")  # not about the company: stays rejected
    assert rows[3] == ("review", "loose_name", "pending")  # skipped only because of the reject
    assert rows[4] == ("reject", "none", "fetched")  # a feed row is never re-classed
    with sqlite3.connect(db_file) as con:
        status = con.execute("SELECT fetch_status FROM articles WHERE id = 1").fetchone()[0]
    assert status == "quarantined"
    assert (tmp_path / "b.db").is_file()


def test_a_second_apply_finds_nothing(db_file: Path, tmp_path: Path) -> None:
    reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    before = _rows(db_file)

    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "c.db")]) == 0
    assert _rows(db_file) == before


def test_apply_needs_a_backup_that_does_not_exist(db_file: Path, tmp_path: Path) -> None:
    existing = tmp_path / "b.db"
    existing.write_text("keep")

    assert reclass.main(["--db", str(db_file), "--apply"]) == 1
    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(existing)]) == 1
    assert existing.read_text() == "keep"
    assert _rows(db_file)[1][0] == "reject"


def test_the_t057_sheet_is_not_its_own_earlier_sample() -> None:
    """T-062 draws a new sheet that excludes the T-057 one, which must still pass its own check."""
    validation = importlib.import_module("ticker_validation_sample")
    sheet = Path(validation.EARLIER_SAMPLES[-1])

    assert sheet.name == "ticker_retag_check_sample.csv"
    ids = {i for i in validation._read_ids(sheet)}
    assert ids  # the sheet exists and is excluded from a fresh draw
