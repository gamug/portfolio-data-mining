"""Hermetic tests for scripts/retag_urls.py and the T-057 check verdict."""

from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from data_mining.db import connect
from data_mining.quarantine import quarantine_articles
from data_mining.schema import apply_schema, run_migrations

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
validation = importlib.import_module("ticker_validation_sample")  # the module retag imports

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


@pytest.fixture(autouse=True)
def _small_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixtures hold ids 1-3, not 100 pairs: shrink the required shape (1 accept, 2 reject) and
    forget the committed earlier samples, whose ids would otherwise collide with these tiny ids."""
    monkeypatch.setattr(validation, "FINAL_COUNTS", {"accept": 1, "reject": 2})
    monkeypatch.setattr(validation, "EARLIER_SAMPLES", ())


def _real_sheet(tmp_path: Path, rows: list[tuple[str, str, str]] | None = None) -> Path:
    """A labeled T-057 sheet for ids 1-3 of the fixtures (apple is accept; the other two reject)."""
    rows = rows or [("1", "accept", "yes"), ("2", "reject", "no"), ("3", "reject", "no")]
    path = tmp_path / "handcheck.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "match_mode", "label"])
        w.writerows(rows)
    return path


def _hc(tmp_path: Path) -> list[str]:
    """`--hand-check` with a sheet that is the T-057 one and passes."""
    return ["--hand-check", str(_real_sheet(tmp_path))]


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
    with pytest.raises(SystemExit, match="--hand-check"):
        _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "b.db"))


def test_apply_retags_quarantines_and_is_reversible(db_file: Path, tmp_path: Path) -> None:
    backup = tmp_path / "pre.db"
    assert _run(db_file, tmp_path, "--apply", "--backup", str(backup), *_hc(tmp_path)) == 0

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
    _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), *_hc(tmp_path))
    with sqlite3.connect(db_file) as c:
        first = c.execute("SELECT * FROM articles ORDER BY id").fetchall()
    _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre2.db"), *_hc(tmp_path))
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


def test_verdict_line_reports_unclear_per_class(tmp_path: Path) -> None:
    path = _sheet(tmp_path, 0, 0)
    path.write_text(path.read_text().replace(",accept,yes", ",accept,unclear", 3))
    ok, text = validation.final_verdict(path)
    assert ok is True  # unclear is neither wrong nor correct ...
    assert "3 unclear" in text  # ... so it is reported


def test_lists_carry_the_class(db_file: Path, tmp_path: Path) -> None:
    _run(db_file, tmp_path)
    rows = list(csv.DictReader((tmp_path / "out/removed_articles.csv").open()))
    assert rows and {r["match_class"] for r in rows} == {"reject"} and rows[0]["match_reason"]


def _mixed_db(path: Path) -> None:
    """A schema-3 database: a legacy row, a row already classed by the T-056c strategies, a quarantined article."""
    db = connect(str(path))
    apply_schema(db)
    run_migrations(db)
    rows = [  # id, url, ticker, company, reason, class, status, article status
        (
            1,
            "https://x.com/apple-earnings-beat.html",
            "AAPL",
            "Apple Inc.",
            None,
            "accept",
            "fetched",
            "ok",
        ),
        (
            2,
            "https://x.com/some-story.html",
            "MSFT",
            "Microsoft",
            "weak_ticker",
            "review",
            "fetched",
            "ok",
        ),
        (
            3,
            "https://x.com/other-story.html",
            "GOOG",
            "Alphabet",
            "weak_ticker",
            "review",
            "fetched",
            "thin_content",
        ),
    ]
    for i, url, ticker, company, reason, cls, status, art in rows:
        db.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (?, ?, 'x.com', ?, ?, 'ddg', 'now', ?, ?, ?)",
            (i, url, company, ticker, status, cls, reason),
        )
        db.execute("INSERT INTO articles (id, fetch_status) VALUES (?, ?)", (i, art))
    db.commit()
    quarantine_articles(db, 3)  # already quarantined before the run
    db.close()


def test_apply_on_a_database_that_already_holds_classed_rows(tmp_path: Path) -> None:
    path = tmp_path / "mixed.db"
    _mixed_db(path)

    assert (
        _run(path, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), *_hc(tmp_path)) == 0
    )

    with sqlite3.connect(path) as c:
        d = {
            r[0]: r[1:]
            for r in c.execute("SELECT id, match_class, match_reason FROM discovered_urls")
        }
        a = {
            r[0]: r[1:]
            for r in c.execute("SELECT id, fetch_status, prev_fetch_status FROM articles")
        }
    assert (
        d[1][0] == "accept" and d[1][1] is not None
    )  # the legacy row was re-tagged (it had no reason)
    assert d[2] == ("review", "weak_ticker") and d[3] == (
        "review",
        "weak_ticker",
    )  # classed rows kept
    assert a[1] == ("ok", None)
    assert a[2] == ("quarantined", "ok")
    assert a[3] == ("quarantined", "thin_content")  # the original previous status survives


def test_lists_and_summary_carry_the_previous_status(db_file: Path, tmp_path: Path) -> None:
    _run(db_file, tmp_path)  # dry run
    rows = list(csv.DictReader((tmp_path / "out/removed_articles.csv").open()))
    assert rows[0]["prev_fetch_status"] == "ok"
    summary = json.loads((tmp_path / "out/retag_summary.json").read_text())
    assert summary["articles_to_quarantine_by_prev_status"]["reject"] == {"ok": 1}


def test_apply_lists_carry_the_previous_status_too(db_file: Path, tmp_path: Path) -> None:
    _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), *_hc(tmp_path))
    rows = list(csv.DictReader((tmp_path / "out/removed_articles.csv").open()))
    assert [r["prev_fetch_status"] for r in rows] == ["ok"]


def test_apply_fails_loudly_when_the_result_does_not_match_the_plan(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        retag, "quarantine_articles", lambda db: 0
    )  # the quarantine silently does nothing

    code = _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), *_hc(tmp_path))

    assert code == 1
    assert "VERIFICATION FAILED" in capsys.readouterr().err
    summary = json.loads((tmp_path / "out/retag_summary.json").read_text())
    assert summary["verified"] is False and summary["problems"]
    assert not (tmp_path / "out/removed_articles.csv").exists()  # no hand-off lists from a bad run


def test_verify_flags_a_quarantined_accept_article(db_file: Path, tmp_path: Path) -> None:
    _run(db_file, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), *_hc(tmp_path))
    summary = json.loads((tmp_path / "out/retag_summary.json").read_text())
    assert retag.verify(db_file, summary) == []
    with sqlite3.connect(db_file) as c:
        c.execute("UPDATE articles SET fetch_status = 'quarantined' WHERE id = 1")
    assert any("accept article" in p for p in retag.verify(db_file, summary))


def _apply(db: Path, tmp_path: Path, sheet: Path | None = None) -> int:
    hc = ["--hand-check", str(sheet)] if sheet else _hc(tmp_path)
    return _run(db, tmp_path, "--apply", "--backup", str(tmp_path / "pre.db"), *hc)


def test_a_failing_hand_check_stops_apply_without_touching_the_database(
    db_file: Path, tmp_path: Path
) -> None:
    before = db_file.read_bytes()

    with pytest.raises(SystemExit, match="back to T-055"):
        _apply(db_file, tmp_path, _sheet(tmp_path, 6, 0))  # 6 wrong `accept` pairs

    assert db_file.read_bytes() == before
    assert not (tmp_path / "pre.db").exists()  # not even the backup


def test_an_unlabeled_hand_check_stops_apply(db_file: Path, tmp_path: Path) -> None:
    sheet = _sheet(tmp_path, 0, 0)
    sheet.write_text(sheet.read_text().replace(",accept,yes", ",accept,", 1))
    with pytest.raises(SystemExit, match="back to T-055"):
        _apply(db_file, tmp_path, sheet)
    with pytest.raises(SystemExit, match="not found"):
        _apply(db_file, tmp_path, tmp_path / "missing.csv")


def test_apply_records_the_hand_check_verdict(db_file: Path, tmp_path: Path) -> None:
    assert _apply(db_file, tmp_path) == 0
    summary = json.loads((tmp_path / "out/retag_summary.json").read_text())
    assert summary["hand_check"]["sheet"].endswith("handcheck.csv")
    assert "PASS" in summary["hand_check"]["verdict"]


def test_apply_aborts_before_writing_when_the_corpus_moved(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_backup = retag.backup

    def backup_then_a_collector_insert(src: Path, dest: Path) -> None:
        real_backup(src, dest)
        with sqlite3.connect(src) as c:  # the collector writes between the scan and the apply
            c.execute(
                "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at)"
                " VALUES ('https://x.com/new', 'x.com', 'Apple Inc.', 'AAPL', 'ddg', 'now')"
            )

    monkeypatch.setattr(retag, "backup", backup_then_a_collector_insert)

    with pytest.raises(SystemExit, match="changed since the plan"):
        _apply(db_file, tmp_path)

    with sqlite3.connect(db_file) as c:  # nothing was migrated or re-tagged
        assert "match_class" not in {r[1] for r in c.execute("PRAGMA table_info(discovered_urls)")}


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([("1", "accept", "yes"), ("2", "reject", "no")], "needs exactly 2"),  # one reject missing
        ([("1", "accept", "yes"), ("2", "reject", "no"), ("2", "reject", "no")], "duplicated ids"),
        (
            [("1", "accept", "yes"), ("2", "reject", "no"), ("9", "reject", "no")],
            "not in the database",
        ),
        ([("1", "reject", "no"), ("2", "reject", "no"), ("3", "reject", "no")], "needs exactly 1"),
        (
            [
                ("1", "accept", "yes"),
                ("2", "accept", "yes"),
                ("3", "reject", "no"),
                ("4", "reject", "no"),
            ],
            "needs exactly",
        ),
    ],
)
def test_a_sheet_that_is_not_the_t057_check_stops_apply(
    db_file: Path, tmp_path: Path, rows: list[tuple[str, str, str]], message: str
) -> None:
    before = db_file.read_bytes()

    with pytest.raises(SystemExit, match=message):
        _apply(db_file, tmp_path, _real_sheet(tmp_path, rows))

    assert db_file.read_bytes() == before
    assert not (tmp_path / "pre.db").exists()


def test_a_sheet_with_a_stale_class_is_refused(db_file: Path, tmp_path: Path) -> None:
    """Row 2 is reject today; a sheet that says accept was drawn under another rule."""
    rows = [("1", "accept", "yes"), ("3", "reject", "no"), ("2", "accept", "no")]
    with pytest.raises(SystemExit, match="not what the current matcher gives"):
        _apply(db_file, tmp_path, _real_sheet(tmp_path, rows))


def test_a_sheet_reusing_an_earlier_sample_is_refused(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = tmp_path / "earlier.csv"
    earlier.write_text("id\n3\n")
    monkeypatch.setattr(validation, "EARLIER_SAMPLES", (earlier,))
    with pytest.raises(SystemExit, match="not fresh"):
        _apply(db_file, tmp_path)


def test_the_committed_samples_are_what_the_check_excludes() -> None:
    importlib.reload(validation)  # undo the autouse shrink: the real defaults
    names = {p.name for p in validation.EARLIER_SAMPLES}
    assert names == {"ticker_tagging_sample.csv", "ticker_validation_sample.csv"}
    assert validation.FINAL_COUNTS == {"accept": 60, "reject": 40}


def test_verdict_with_a_db_also_checks_the_sheet(db_file: Path, tmp_path: Path) -> None:
    good = _real_sheet(tmp_path)
    assert validation.main(["--verdict", str(good), "--db", str(db_file)]) == 0
    bad = _real_sheet(tmp_path, [("1", "accept", "yes"), ("2", "reject", "no")])
    assert validation.main(["--verdict", str(bad), "--db", str(db_file)]) == 1
