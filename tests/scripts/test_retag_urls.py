"""Hermetic tests for scripts/retag_urls.py and the T-057 check verdict."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


retag = _load("retag_urls")
validation = _load("ticker_validation_sample")

_V1_DISCOVERED = """
CREATE TABLE discovered_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, domain TEXT NOT NULL,
    company TEXT NOT NULL, ticker TEXT NOT NULL, source TEXT NOT NULL, discovered_at TEXT NOT NULL,
    pub_date TEXT, title TEXT, status TEXT NOT NULL DEFAULT 'pending', fetch_status_code INTEGER,
    UNIQUE (url, ticker))
"""
_V2_ARTICLES = """
CREATE TABLE articles (
    id INTEGER PRIMARY KEY, ticker TEXT, company TEXT, gics_sector TEXT, gics_sub_industry TEXT,
    title TEXT, author TEXT, pub_date TEXT, body_text TEXT, word_count INTEGER, language TEXT,
    source_domain TEXT, extraction_method TEXT, fetch_status TEXT, http_status_code INTEGER,
    fetched_at TEXT, FOREIGN KEY (id) REFERENCES discovered_urls (id))
"""
# id, url, ticker, company, source, status, article status
ROWS = [
    (1, "https://x.com/apple-earnings-beat.html", "AAPL", "Apple Inc.", "sitemap", "fetched", "ok"),
    (2, "https://x.com/turn-on-the-tv.html", "ON", "ON Semiconductor", "sitemap", "fetched", "ok"),
    (3, "https://x.com/in-a-month.html", "A", "Agilent Technologies", "sitemap", "pending", None),
    (4, "https://x.com/feed-item", "MSFT", "Microsoft", "api", "fetched", "ok"),
]


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "urls.db"
    with sqlite3.connect(path) as c:
        c.execute(_V1_DISCOVERED)
        c.execute(_V2_ARTICLES)
        for i, url, ticker, company, source, status, art in ROWS:
            c.execute(
                "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
                " status) VALUES (?, ?, 'x.com', ?, ?, ?, 'now', ?)",
                (i, url, company, ticker, source, status),
            )
            if art:
                c.execute("INSERT INTO articles (id, fetch_status) VALUES (?, ?)", (i, art))
    return path


def _run(db: Path, tmp_path: Path, *extra: str) -> int:
    return retag.main(["--db", str(db), "--out", str(tmp_path / "out"), *extra])


def test_dry_run_changes_nothing_and_reports_the_plan(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = db_file.read_bytes()

    assert _run(db_file, tmp_path) == 0

    assert db_file.read_bytes() == before
    out = capsys.readouterr().out
    assert '"mode": "dry-run"' in out
    removed = list(csv.DictReader((tmp_path / "out/removed_articles.csv").open()))
    assert [r["article_id"] for r in removed] == ["2"]  # `ON` in a URL is not Semiconductor


def test_apply_requires_a_backup_and_the_hand_check(db_file: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--backup"):
        _run(db_file, tmp_path, "--apply")
    with pytest.raises(SystemExit, match="--hand-check-passed"):
        _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "b.db"))


def test_apply_retags_quarantines_and_is_reversible(db_file: Path, tmp_path: Path) -> None:
    backup = tmp_path / "pre.db"
    assert _run(db_file, tmp_path, "--apply", "--backup", str(backup), "--hand-check-passed") == 0

    with sqlite3.connect(db_file) as c:
        classes = dict(c.execute("SELECT id, match_class FROM discovered_urls"))
        status = dict(c.execute("SELECT id, status FROM discovered_urls"))
        arts = {
            r[0]: r[1:]
            for r in c.execute("SELECT id, fetch_status, prev_fetch_status FROM articles")
        }
        feed = c.execute("SELECT match_reason FROM discovered_urls WHERE id = 4").fetchone()[0]
    assert classes[1] == "accept" and classes[4] == "accept" and feed == "feed"
    assert classes[2] != "accept" and classes[3] == "reject"
    assert (
        status[3] == "skipped" and status[1] == "fetched"
    )  # pending reject skipped; others untouched
    assert arts[1] == ("ok", None)
    assert arts[2] == ("quarantined", "ok")
    with sqlite3.connect(backup) as c:  # the backup is the pre-run database, untouched
        assert "match_class" not in {r[1] for r in c.execute("PRAGMA table_info(discovered_urls)")}
        assert c.execute("SELECT fetch_status FROM articles WHERE id = 2").fetchone()[0] == "ok"


def test_a_second_apply_changes_nothing(db_file: Path, tmp_path: Path) -> None:
    _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), "--hand-check-passed")
    with sqlite3.connect(db_file) as c:
        first = c.execute("SELECT * FROM articles ORDER BY id").fetchall()
    _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre2.db"), "--hand-check-passed")
    with sqlite3.connect(db_file) as c:
        assert c.execute("SELECT * FROM articles ORDER BY id").fetchall() == first


def test_backup_never_overwrites(db_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "exists.db"
    target.write_text("keep")
    with pytest.raises(SystemExit, match="already exists"):
        retag.backup(db_file, target)
    assert target.read_text() == "keep"


def _sheet(tmp_path: Path, accept_no: int, reject_yes: int) -> Path:
    path = tmp_path / "sheet.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "match_mode", "label"])
        w.writeheader()
        for i in range(60):
            w.writerow({"id": i, "match_mode": "accept", "label": "no" if i < accept_no else "yes"})
        for i in range(40):
            w.writerow(
                {"id": 100 + i, "match_mode": "reject", "label": "yes" if i < reject_yes else "no"}
            )
    return path


@pytest.mark.parametrize(
    ("accept_no", "reject_yes", "ok"),
    [(5, 1, True), (0, 0, True), (6, 0, False), (0, 2, False)],
)
def test_verdict_thresholds(tmp_path: Path, accept_no: int, reject_yes: int, ok: bool) -> None:
    assert validation.final_verdict(_sheet(tmp_path, accept_no, reject_yes))[0] is ok


def test_verdict_fails_on_unlabeled_rows(tmp_path: Path) -> None:
    path = _sheet(tmp_path, 0, 0)
    path.write_text(path.read_text().replace(",accept,yes", ",accept,", 1))
    assert validation.final_verdict(path)[0] is False


def test_verdict_line_reports_unsure_per_class(tmp_path: Path) -> None:
    path = _sheet(tmp_path, 0, 0)
    path.write_text(path.read_text().replace(",accept,yes", ",accept,unsure", 3))
    ok, text = validation.final_verdict(path)
    assert ok is True  # unsure is neither wrong nor correct ...
    assert "3 unsure" in text  # ... so it is reported


def test_lists_carry_the_class(db_file: Path, tmp_path: Path) -> None:
    _run(db_file, tmp_path)
    rows = list(csv.DictReader((tmp_path / "out/removed_articles.csv").open()))
    assert rows and {r["match_class"] for r in rows} == {"reject"} and rows[0]["match_reason"]
