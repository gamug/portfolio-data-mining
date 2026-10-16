"""scripts/reclass_rejects.py: promote the rejected pairs the current matcher no longer rejects (T-062)."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from data_mining.db import connect
from data_mining.matching import Match
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


@pytest.fixture(autouse=True)
def _audit_list_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reclass, "DEFAULT_LIST", str(tmp_path / "audit" / "list.csv"))


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


def _insert_row(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (99, 'https://x.com/z', 'x.com', 'Z', 'Z',"
            " 'sitemap', 'now', 'pending', 'reject', 'none')"
        )


def test_apply_writes_nothing_when_the_database_moved_since_the_plan(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The baseline is the counts taken with the plan, so a row added while the plan or the backup
    ran (a collector left running) stops the apply; the old check compared two reads microseconds apart."""
    real_backup = reclass._backup

    def backup_then_a_collector_writes(
        db_path: Path, backup: Path, expected: tuple[object, ...]
    ) -> str:
        sha = real_backup(db_path, backup, expected)
        _insert_row(db_path)
        return sha

    monkeypatch.setattr(reclass, "_backup", backup_then_a_collector_writes)
    before = _rows(db_file)

    with pytest.raises(SystemExit, match="changed since the plan"):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    assert _rows(db_file) == {**before, 99: ("reject", "none", "pending")}


def test_apply_refuses_a_busy_database_before_it_copies_anything(
    db_file: Path, tmp_path: Path
) -> None:
    holder = sqlite3.connect(db_file, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SystemExit, match="busy"):
            reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert not (tmp_path / "b.db").exists()
    assert _rows(db_file)[1][0] == "reject"


def test_apply_refuses_when_the_disk_cannot_hold_the_copy(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reclass.shutil, "disk_usage", lambda _p: type("U", (), {"free": 10})())

    with pytest.raises(SystemExit, match="not enough disk"):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    assert not (tmp_path / "b.db").exists()
    assert _rows(db_file)[1][0] == "reject"


def test_a_copy_that_does_not_match_the_plan_is_removed_and_nothing_is_written(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / "b.db"

    real = reclass._fingerprint
    calls = {"n": 0}

    def counts_then_a_bad_copy(con: sqlite3.Connection) -> tuple[object, ...]:
        calls["n"] += 1
        if calls["n"] == 2:  # the read of the finished copy
            return ((0, 0),)
        return real(con)

    monkeypatch.setattr(reclass, "_fingerprint", counts_then_a_bad_copy)

    with pytest.raises(SystemExit, match="does not match the plan"):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(backup)])

    assert not backup.exists()
    assert _rows(db_file)[1][0] == "reject"


def test_a_crash_between_the_commit_and_the_sync_is_finished_by_a_rerun_without_a_backup(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(_db: object) -> tuple[int, int]:
        raise RuntimeError("killed")

    monkeypatch.setattr(reclass, "sync_quarantine", crash)
    with pytest.raises(RuntimeError):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    assert _rows(db_file)[1][:2] == ("review", "loose_name")  # promotions are in

    monkeypatch.undo()
    assert (
        reclass.main(["--db", str(db_file), "--apply"]) == 0
    )  # nothing to promote: no backup needed
    with sqlite3.connect(db_file) as con:
        status = con.execute("SELECT fetch_status FROM articles WHERE id = 1").fetchone()[0]
    assert status == "quarantined"


def test_apply_with_something_to_promote_needs_a_backup(db_file: Path) -> None:
    assert reclass.main(["--db", str(db_file), "--apply"]) == 1
    assert _rows(db_file)[1][0] == "reject"


def test_the_dry_run_reports_status_tickers_and_the_sync_it_will_run(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reclass.main(["--db", str(db_file)])
    report = json.loads(capsys.readouterr().out)

    assert report["by_status"] == {"fetched": 1, "skipped": 1}
    assert report["skipped_back_to_pending"] == 1
    assert report["promoted_with_a_quarantined_article"] == 1
    assert report["top_tickers"] == {"KLAC": 1, "BAC": 1}
    assert report["api_rejects_left_alone"] == 1
    assert report["predicted_sync"] == {"quarantined": 0, "released": 0}

    reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    applied = capsys.readouterr().out.strip().splitlines()[-1]
    assert applied == "promoted 2 rows; quarantined 0, released 0"


def test_the_t057_sheet_is_not_its_own_earlier_sample() -> None:
    """T-062 draws a new sheet that excludes the T-057 one, which must still pass its own check."""
    validation = importlib.import_module("ticker_validation_sample")
    sheet = Path(validation.EARLIER_SAMPLES[-1])

    assert sheet.name == "ticker_retag_check_sample.csv"
    ids = {i for i in validation._read_ids(sheet)}
    assert ids  # the sheet exists and is excluded from a fresh draw


def test_the_predicted_sync_counts_only_articles_that_exist_and_are_held_back(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rows promoted to `accept`: id 1 has a quarantined article (released), id 3 has no article,
    id 5 has an `ok` article that is not quarantined (neither is released); the prediction is what
    the apply then prints."""
    with sqlite3.connect(db_file) as con:
        con.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (5, 'https://x.com/kla-other', 'x.com',"
            " 'KLA Corporation', 'KLAC', 'sitemap', 'now', 'fetched', 'reject', 'none')"
        )
        con.execute("INSERT INTO articles (id, fetch_status) VALUES (5, 'ok')")
    monkeypatch.setattr(
        reclass,
        "classify",
        lambda url, *_a, **_k: Match(
            "accept" if "x.com/kla" in url or "bank" in url else "reject", "t"
        ),
    )

    reclass.main(["--db", str(db_file)])
    predicted = json.loads(capsys.readouterr().out)["predicted_sync"]
    assert predicted == {"quarantined": 0, "released": 1}

    reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "promoted 3 rows; quarantined 0, released 1"
    )


def _articles(path: Path) -> dict[int, str]:
    with sqlite3.connect(path) as con:
        return dict(con.execute("SELECT id, fetch_status FROM articles"))


def test_apply_stops_when_a_status_flipped_after_the_plan_even_if_no_row_was_added(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crawler changing an existing row leaves every row count as it was; the fingerprint sees it."""
    real_backup = reclass._backup

    def backup_then_the_crawler_flips_a_status(
        db_path: Path, backup: Path, expected: tuple[object, ...]
    ) -> str:
        sha = real_backup(db_path, backup, expected)
        with sqlite3.connect(db_path) as con:
            con.execute("UPDATE discovered_urls SET status = 'failed' WHERE id = 1")
        return sha

    monkeypatch.setattr(reclass, "_backup", backup_then_the_crawler_flips_a_status)

    with pytest.raises(SystemExit, match="changed since the plan"):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    assert _rows(db_file)[1] == ("reject", "none", "failed")  # promoted nothing


def test_apply_saves_the_audit_list_and_never_overwrites_one(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = tmp_path / "audit.csv"
    args = [
        "--db",
        str(db_file),
        "--apply",
        "--backup",
        str(tmp_path / "b.db"),
        "--list",
        str(listing),
    ]
    assert reclass.main(args) == 0
    rows = {r["id"]: r for r in csv.DictReader(listing.open())}

    assert set(rows) == {"1", "3"}
    assert rows["1"]["old_class"] == "reject" and rows["1"]["new_class"] == "review"
    assert (rows["1"]["old_status"], rows["1"]["new_status"]) == ("fetched", "fetched")
    assert (rows["3"]["old_status"], rows["3"]["new_status"]) == ("skipped", "pending")
    assert rows["1"]["article_id"] == "1" and rows["3"]["article_id"] == ""
    assert rows["1"]["article_fetch_status"] == "quarantined"
    out = capsys.readouterr().out
    assert f"audit list {listing} (2 rows)" in out
    assert "sha256 " + hashlib.sha256((tmp_path / "b.db").read_bytes()).hexdigest() in out


def test_an_existing_audit_list_or_an_unwritable_one_stops_before_any_write(
    db_file: Path, tmp_path: Path
) -> None:
    kept = tmp_path / "kept.csv"
    kept.write_text("custody")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    before = _rows(db_file)

    with pytest.raises(SystemExit, match="exists"):
        reclass.main(
            [
                "--db",
                str(db_file),
                "--apply",
                "--backup",
                str(tmp_path / "b.db"),
                "--list",
                str(kept),
            ]
        )
    with pytest.raises(SystemExit, match="cannot write"):
        reclass.main(
            [
                "--db",
                str(db_file),
                "--apply",
                "--backup",
                str(tmp_path / "c.db"),
                "--list",
                str(blocker / "x.csv"),
            ]
        )

    assert kept.read_text() == "custody"
    assert _rows(db_file) == before


def test_revert_puts_the_rows_back_and_leaves_a_row_changed_since_alone(
    db_file: Path, tmp_path: Path
) -> None:
    listing = tmp_path / "audit.csv"
    original, original_articles = _rows(db_file), _articles(db_file)
    reclass.main(
        [
            "--db",
            str(db_file),
            "--apply",
            "--backup",
            str(tmp_path / "b.db"),
            "--list",
            str(listing),
        ]
    )
    assert _rows(db_file) != original

    assert (
        reclass.main(
            ["--db", str(db_file), "--revert", str(listing), "--backup", str(tmp_path / "r.db")]
        )
        == 0
    )

    assert _rows(db_file) == original  # class, reason and status (skipped again) as before
    assert _articles(db_file) == original_articles

    reclass.main(
        [
            "--db",
            str(db_file),
            "--apply",
            "--backup",
            str(tmp_path / "b2.db"),
            "--list",
            str(tmp_path / "second.csv"),
        ]
    )
    with sqlite3.connect(db_file) as con:  # a person re-classed row 1 meanwhile
        con.execute(
            "UPDATE discovered_urls SET match_class = 'accept', match_reason = 'hand' WHERE id = 1"
        )
    reclass.main(
        [
            "--db",
            str(db_file),
            "--revert",
            str(tmp_path / "second.csv"),
            "--backup",
            str(tmp_path / "r2.db"),
        ]
    )

    assert _rows(db_file)[1][:2] == ("accept", "hand")  # not what the run wrote: untouched
    assert _rows(db_file)[3] == ("reject", "none", "skipped")


def test_revert_needs_a_new_backup(db_file: Path, tmp_path: Path) -> None:
    listing = tmp_path / "audit.csv"
    reclass.main(
        [
            "--db",
            str(db_file),
            "--apply",
            "--backup",
            str(tmp_path / "b.db"),
            "--list",
            str(listing),
        ]
    )

    assert reclass.main(["--db", str(db_file), "--revert", str(listing)]) == 1
    assert (
        reclass.main(
            ["--db", str(db_file), "--revert", str(listing), "--backup", str(tmp_path / "b.db")]
        )
        == 1
    )


def test_a_nonempty_wal_or_a_fresh_shm_is_warned_about(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = tmp_path / "x.db"
    fake.write_text("")
    (tmp_path / "x.db-wal").write_text("frames")
    (tmp_path / "x.db-shm").write_text("index")

    reclass._warn_live_writer(fake)

    err = capsys.readouterr().err
    assert "x.db-wal is not empty" in err
    assert "x.db-shm changed in the last" in err


def test_the_lookups_are_chunked_and_the_backup_directory_is_created(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(reclass, "CHUNK", 1)  # one id per IN (...) query

    reclass.main(["--db", str(db_file)])
    assert json.loads(capsys.readouterr().out)["by_status"] == {"fetched": 1, "skipped": 1}

    nested = tmp_path / "a" / "b" / "backup.db"
    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(nested)]) == 0
    assert nested.is_file()
