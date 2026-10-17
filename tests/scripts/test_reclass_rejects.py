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
    monkeypatch.chdir(tmp_path)  # a relative default (list, summary) never lands in the repository
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

    real_sync_quarantine = reclass.sync_quarantine
    monkeypatch.setattr(reclass, "sync_quarantine", crash)
    with pytest.raises(RuntimeError):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    assert _rows(db_file)[1][:2] == ("review", "loose_name")  # promotions are in

    monkeypatch.setattr(reclass, "sync_quarantine", real_sync_quarantine)
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
    applied = capsys.readouterr().out.strip().splitlines()
    assert "promoted 2 rows; quarantined 0, released 0" in applied


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
    assert "promoted 3 rows; quarantined 0, released 1" in capsys.readouterr().out.splitlines()


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


def test_an_aborted_apply_leaves_no_list_and_the_rerun_with_the_same_list_works(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list describes committed changes only: the fingerprint abort must not leave one behind."""
    real_backup = reclass._backup

    def backup_then_a_collector_writes(
        db_path: Path, backup: Path, expected: tuple[object, ...]
    ) -> str:
        sha = real_backup(db_path, backup, expected)
        _insert_row(db_path)
        return sha

    listing = tmp_path / "audit.csv"
    monkeypatch.setattr(reclass, "_backup", backup_then_a_collector_writes)
    with pytest.raises(SystemExit, match="changed since the plan"):
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
    assert not listing.exists() and not reclass._partial(listing).exists()

    monkeypatch.setattr(
        reclass, "_backup", real_backup
    )  # the writer is stopped: retry, same list path
    assert (
        reclass.main(
            [
                "--db",
                str(db_file),
                "--apply",
                "--backup",
                str(tmp_path / "b2.db"),
                "--list",
                str(listing),
            ]
        )
        == 0
    )
    assert listing.is_file() and not reclass._partial(listing).exists()


def test_a_crash_between_the_commit_and_the_sync_leaves_the_final_list(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The changes happened, so the list must be there (it is how they are reverted)."""

    def crash(_db: object) -> tuple[int, int]:
        raise RuntimeError("killed")

    listing = tmp_path / "audit.csv"
    monkeypatch.setattr(reclass, "_sync", crash)
    with pytest.raises(RuntimeError):
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

    assert listing.is_file() and not reclass._partial(listing).exists()
    assert _rows(db_file)[1][:2] == ("review", "loose_name")


def test_a_leftover_partial_list_blocks_a_run_and_cannot_be_reverted(
    db_file: Path, tmp_path: Path
) -> None:
    listing = tmp_path / "audit.csv"
    reclass._partial(listing).write_text("id\n")

    with pytest.raises(SystemExit, match="exists"):
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
    with pytest.raises(SystemExit, match="partial"):
        reclass.main(
            [
                "--db",
                str(db_file),
                "--revert",
                str(reclass._partial(listing)),
                "--backup",
                str(tmp_path / "r.db"),
            ]
        )
    assert _rows(db_file)[1][0] == "reject"


def _accepting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reclass,
        "classify",
        lambda url, *_a, **_k: Match("accept" if "kla" in url or "bank" in url else "reject", "t"),
    )


def test_apply_fails_loudly_when_the_sync_did_not_release_what_the_dry_run_predicted(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _accepting(monkeypatch)
    monkeypatch.setattr(reclass, "_sync", lambda _db: (0, 0))  # releases nothing

    code = reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    err = capsys.readouterr().err
    assert code == 1
    assert "the sync did (quarantined, released) = (0, 0), the dry run predicted (0, 1)" in err
    assert "1 `accept` pair(s) still have a quarantined article" in err
    assert "--revert " in err and "--backup <a new path>" in err
    assert "the backup taken before the run is" in err


def test_apply_fails_loudly_when_a_promotion_was_not_written(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real_apply = reclass.apply

    def apply_all_but_the_last(
        db_path: Path,
        promotions: list[tuple[int, str, str]],
        expected: tuple[object, ...],
        on_commit: object = None,
    ) -> tuple[int, int]:
        return real_apply(db_path, promotions[:-1], expected, on_commit)  # type: ignore[arg-type]

    monkeypatch.setattr(reclass, "apply", apply_all_but_the_last)

    code = reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    err = capsys.readouterr().err
    assert code == 1
    assert "class reject: 2 rows, expected 1" in err or "class review: 1 rows, expected 2" in err
    assert "promoted row(s) are still `reject`" in err


def test_revert_fails_loudly_when_a_listed_row_still_holds_what_the_run_wrote(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    monkeypatch.setattr(reclass, "revert", lambda *_a, **_k: (0, 0, 0))  # does nothing

    code = reclass.main(
        ["--db", str(db_file), "--revert", str(listing), "--backup", str(tmp_path / "r.db")]
    )

    assert code == 1
    assert "listed row(s) still hold what the run wrote" in capsys.readouterr().err


def test_a_clean_apply_and_revert_report_no_problem(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing = tmp_path / "audit.csv"
    assert (
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
        == 0
    )
    assert (
        reclass.main(
            ["--db", str(db_file), "--revert", str(listing), "--backup", str(tmp_path / "r.db")]
        )
        == 0
    )
    assert "POST-CONDITION FAILED" not in capsys.readouterr().err


def _list_with(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "given.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=reclass.LIST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _entry(id_: object = 1, **over: str) -> dict[str, str]:
    base = {
        "id": str(id_),
        "article_id": "",
        "ticker": "KLAC",
        "company": "KLA Corporation",
        "url": "https://x.com/kla-corporation-q4-results",
        "old_class": "reject",
        "old_reason": "none",
        "new_class": "review",
        "new_reason": "loose_name",
        "old_status": "fetched",
        "new_status": "fetched",
        "article_fetch_status": "",
    }
    return {**base, **over}


def _revert(db_file: Path, tmp_path: Path, listing: Path) -> int:
    return reclass.main(
        ["--db", str(db_file), "--revert", str(listing), "--backup", str(tmp_path / "r.db")]
    )


def test_a_list_of_the_wrong_shape_stops_before_any_backup(db_file: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("id,foo\n1,2\n")
    before = _rows(db_file)

    with pytest.raises(SystemExit, match="not an audit list from this script"):
        _revert(db_file, tmp_path, bad)

    assert not (tmp_path / "r.db").exists() and _rows(db_file) == before


def test_a_list_naming_an_id_the_database_does_not_have_stops_before_any_backup(
    db_file: Path, tmp_path: Path
) -> None:
    listing = _list_with(tmp_path, [_entry(1), _entry(999)])
    before = _rows(db_file)

    with pytest.raises(SystemExit, match=r"1 id\(s\) not in urls.db"):
        _revert(db_file, tmp_path, listing)

    assert not (tmp_path / "r.db").exists() and _rows(db_file) == before  # not even row 1 touched


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([_entry(1, id="x")], "is not an integer"),
        ([_entry(1, old_class="review", new_class="reject")], "is not a promotion"),
        ([_entry(1, old_class="nope")], "is not a promotion"),
        ([_entry(1, new_reason="")], "a reason or status is empty"),
        ([_entry(1), _entry(1)], "duplicated ids"),
    ],
)
def test_a_list_that_is_not_a_recorded_promotion_is_refused(
    db_file: Path, tmp_path: Path, rows: list[dict[str, str]], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        _revert(db_file, tmp_path, _list_with(tmp_path, rows))
    assert not (tmp_path / "r.db").exists()


def test_apply_and_revert_cannot_be_given_together_and_revert_takes_no_list(
    db_file: Path, tmp_path: Path
) -> None:
    listing = _list_with(tmp_path, [_entry(1)])
    base = ["--db", str(db_file), "--backup", str(tmp_path / "b.db")]

    with pytest.raises(SystemExit) as both:
        reclass.main([*base, "--apply", "--revert", str(listing)])
    with pytest.raises(SystemExit) as with_list:
        reclass.main([*base, "--revert", str(listing), "--list", str(tmp_path / "x.csv")])

    assert both.value.code == 2 and with_list.value.code == 2
    assert not (tmp_path / "b.db").exists()


def test_a_revert_that_finds_every_row_changed_since_says_zero_not_success(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    with sqlite3.connect(db_file) as con:  # a person re-classed both promoted rows meanwhile
        con.execute("UPDATE discovered_urls SET match_reason = 'hand' WHERE id IN (1, 3)")
    capsys.readouterr()

    assert _revert(db_file, tmp_path, listing) == 0

    assert "reverted 0 of 2 listed rows (2 changed since and left alone)" in capsys.readouterr().out
    assert _rows(db_file)[1][:2] == ("review", "hand")


def test_the_operator_mistakes_that_used_to_be_tracebacks_are_one_line_errors(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert reclass.main(["--db", str(tmp_path / "typo.db")]) == 1
    assert "does not exist" in capsys.readouterr().err

    with pytest.raises(SystemExit, match="cannot read the audit list"):
        _revert(db_file, tmp_path, tmp_path / "missing.csv")

    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    with pytest.raises(SystemExit, match="cannot create or use the backup directory"):
        reclass.main(
            [
                "--db",
                str(db_file),
                "--apply",
                "--backup",
                str(blocker / "b.db"),
                "--list",
                str(tmp_path / "l.csv"),
            ]
        )
    assert _rows(db_file)[1][0] == "reject" and not (tmp_path / "l.csv").exists()


def test_the_wal_is_truncated_at_the_end_of_a_run_and_a_held_reader_only_delays_it(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reader on an older portfolio-common refuses a non-empty `-wal`. A reader that is open while
    the run writes keeps frames in the log (the precondition), the end-of-run checkpoint reports that it
    could not truncate, and once the reader closes the same checkpoint empties the log."""
    wal = db_file.with_name(db_file.name + "-wal")
    reader = sqlite3.connect(db_file, isolation_level=None)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()  # holds a read snapshot

    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")]) == 0
    err = capsys.readouterr().err
    assert wal.exists() and wal.stat().st_size > 0  # the reader kept the frames in the log
    assert "a reader holds the WAL" in err

    reader.execute("ROLLBACK")
    reader.close()
    reclass._checkpoint(db_file)
    assert not wal.exists() or wal.stat().st_size == 0
    assert "checkpoint: WAL truncated" in capsys.readouterr().err


def test_a_run_with_no_reader_leaves_the_wal_empty(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")]) == 0
    wal = db_file.with_name(db_file.name + "-wal")

    assert not wal.exists() or wal.stat().st_size == 0
    assert "checkpoint: WAL truncated" in capsys.readouterr().err


def test_a_list_written_for_another_database_cannot_revert_an_unrelated_pair(
    db_file: Path, tmp_path: Path
) -> None:
    """Same id, same class and reason, but another ticker and url: the pair is not the listed one."""
    listing = _list_with(
        tmp_path, [_entry(2, ticker="KLAC", url="https://x.com/kla-corporation-q4-results")]
    )  # id 2 here is `ON`, `turn-on-the-tv`
    with sqlite3.connect(db_file) as con:
        con.execute(
            "UPDATE discovered_urls SET match_class='review', match_reason='loose_name' WHERE id = 2"
        )
    before = _rows(db_file)

    with pytest.raises(SystemExit, match=r"another pair in urls\.db"):
        _revert(db_file, tmp_path, listing)

    assert not (tmp_path / "r.db").exists() and _rows(db_file) == before


def test_one_row_of_many_with_another_url_refuses_the_whole_list(
    db_file: Path, tmp_path: Path
) -> None:
    listing = _list_with(
        tmp_path, [_entry(1), _entry(3, ticker="BAC", url="https://elsewhere.com/other")]
    )

    with pytest.raises(SystemExit, match=r"1 id\(s\) are another pair"):
        _revert(db_file, tmp_path, listing)


def test_the_revert_update_itself_only_touches_the_listed_pair(
    db_file: Path, tmp_path: Path
) -> None:
    """Even a list that skipped validation cannot revert a row whose ticker or url differ."""
    with sqlite3.connect(db_file) as con:
        con.execute(
            "UPDATE discovered_urls SET match_class='review', match_reason='loose_name' WHERE id = 1"
        )
        expected = reclass._fingerprint(con)
    wrong = _entry(1, url="https://elsewhere.com/other")
    right = _entry(1)

    assert reclass.revert(db_file, [wrong], expected)[2] == 0
    assert _rows(db_file)[1][:2] == ("review", "loose_name")
    with sqlite3.connect(db_file) as con:
        expected = reclass._fingerprint(con)
    assert reclass.revert(db_file, [right], expected)[2] == 1
    assert _rows(db_file)[1][:2] == ("reject", "none")


def test_the_reader_check_attaches_read_only_and_reports_the_library_version(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")]) == 0

    out = capsys.readouterr().out
    assert (
        "reader check: attach(read_only=True) ok, 4 rows, 0 readable articles (portfolio-common "
        in out
    )


def test_a_database_a_reader_refuses_fails_the_reader_check(tmp_path: Path) -> None:
    """A non-empty `-wal` with no `-shm` is what the library's own preflight rejects."""
    path = tmp_path / "orphan.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE discovered_urls (id INTEGER)")
    path.with_name("orphan.db-wal").write_bytes(b"x" * 64)

    problems, info = reclass._reader_check(path, ((0, None), (0, None), (), ()), 0)

    assert info["ok"] is False and "orphaned write-ahead log" in str(info["error"])
    assert len(problems) == 1 and "a reader cannot attach orphan.db read-only" in problems[0]
    assert "orphaned write-ahead log" in problems[0]


def test_the_reader_check_notices_a_different_row_count(db_file: Path) -> None:
    problems, _ = reclass._reader_check(db_file, ((999, 999), (0, None), (), ()), 0)

    assert problems == ["the reader sees 4 pairs, the fingerprint had 999"]


def test_apply_fails_loudly_when_a_reader_cannot_attach(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(self: object, *_a: object, **_k: object) -> None:
        raise RuntimeError("orphaned write-ahead log")

    monkeypatch.setattr(reclass.Database, "attach", refuse)

    code = reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    err = capsys.readouterr().err
    assert code == 1 and "POST-CONDITION FAILED: a reader cannot attach" in err


def test_the_updates_must_change_exactly_the_planned_rows(db_file: Path) -> None:
    with sqlite3.connect(db_file) as con:
        expected = reclass._fingerprint(con)
    promotions = [(1, "review", "loose_name"), (3, "review", "loose_name")]

    problems, _ = reclass._verify_apply(
        db_file, promotions, expected, (0, 0, 1), {"quarantined": 0, "released": 0}, 0, 0
    )

    assert "the updates changed 1 row(s), the plan had 2" in problems


def test_a_file_that_is_not_a_sqlite_database_is_a_one_line_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junk = tmp_path / "junk.db"
    junk.write_text("this is not a sqlite file")

    assert reclass.main(["--db", str(junk)]) == 1
    assert "is not a readable SQLite database" in capsys.readouterr().err


def test_the_checkpoint_says_so_when_the_database_is_not_in_wal_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plain.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE t (x)")

    reclass._checkpoint(path)

    assert "not in WAL mode, nothing to truncate" in capsys.readouterr().err


def _refuse_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: object, *_a: object, **_k: object) -> None:
        raise RuntimeError("orphaned write-ahead log")

    monkeypatch.setattr(reclass.Database, "attach", refuse)


def test_a_reader_failure_gets_the_reader_advice_and_no_revert(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _refuse_attach(monkeypatch)

    code = reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    err = capsys.readouterr().err
    assert code == 1
    assert "the data is as planned" in err and "wal_checkpoint(TRUNCATE)" in err
    assert "portfolio-common >= 1.2.2" in err and "No revert is needed" in err
    assert "--revert " not in err  # reverting does not fix a file a reader refuses
    assert str(db_file.resolve()) in err  # the real path, not a placeholder


def test_a_data_failure_on_apply_names_the_real_list_and_backup(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _accepting(monkeypatch)
    monkeypatch.setattr(reclass, "_sync", lambda _db: (0, 0))
    listing, backup = tmp_path / "audit.csv", tmp_path / "b.db"

    reclass.main(["--db", str(db_file), "--apply", "--backup", str(backup), "--list", str(listing)])

    err = capsys.readouterr().err
    assert f"--revert {listing.resolve()} --backup <a new path>" in err
    assert f"the backup taken before the run is {backup.resolve()}" in err
    assert "readable by a reader" not in err  # a data problem is not a reader problem


def test_a_data_failure_on_a_revert_points_to_the_backup_not_to_another_revert(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    monkeypatch.setattr(reclass, "revert", lambda *_a, **_k: (0, 0, 0))
    capsys.readouterr()
    backup = tmp_path / "r.db"

    reclass.main(["--db", str(db_file), "--revert", str(listing), "--backup", str(backup)])

    err = capsys.readouterr().err
    assert f"the backup taken before it is {backup.resolve()}" in err
    assert "restore it only if nothing has run since" in err
    assert "--revert " + str(listing.resolve()) not in err


def _summary_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("reclass_*_summary_*.json"))


def test_a_run_writes_a_summary_with_what_the_handoff_needs(
    db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listing, backup = tmp_path / "out" / "audit.csv", tmp_path / "b.db"
    assert (
        reclass.main(
            ["--db", str(db_file), "--apply", "--backup", str(backup), "--list", str(listing)]
        )
        == 0
    )
    (summary,) = _summary_files(tmp_path / "out")
    data = json.loads(summary.read_text())

    assert data["mode"] == "apply" and data["promotions"] == 2 and data["rows_changed"] == 2
    assert data["backup"]["path"] == str(backup.resolve()) and len(data["backup"]["sha256"]) == 64
    assert data["audit_list"] == {"path": str(listing.resolve()), "rows": 2}
    assert data["before"]["classes"]["reject"] == data["after"]["classes"]["reject"] + 2
    assert data["postconditions"] == {"ok": True, "problems": []}
    assert data["reader_check"]["ok"] is True and data["reader_check"]["portfolio_common"]
    assert {"fingerprint", "plan", "backup, check and sha256", "apply and sync", "verify"} <= set(
        data["stage_seconds"]
    )
    out = capsys.readouterr().out
    assert f"audit list {listing.resolve()} (2 rows)" in out and f"run summary {summary}" in out


def test_the_summary_is_written_even_when_a_post_condition_fails_and_carries_the_problems(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _accepting(monkeypatch)
    monkeypatch.setattr(reclass, "_sync", lambda _db: (0, 0))

    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")]) == 1

    (summary,) = _summary_files(tmp_path / "audit")
    data = json.loads(summary.read_text())
    assert data["postconditions"]["ok"] is False and data["postconditions"]["problems"]


def test_an_existing_summary_stops_the_run_before_the_backup(db_file: Path, tmp_path: Path) -> None:
    kept = tmp_path / "kept.json"
    kept.write_text("custody")

    with pytest.raises(SystemExit, match=r"run summary .* exists"):
        reclass.main(
            [
                "--db",
                str(db_file),
                "--apply",
                "--backup",
                str(tmp_path / "b.db"),
                "--summary",
                str(kept),
            ]
        )

    assert kept.read_text() == "custody" and not (tmp_path / "b.db").exists()
    assert _rows(db_file)[1][0] == "reject"


def test_a_recovery_apply_gets_its_own_summary_not_a_collision(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(_db: object) -> tuple[int, int]:
        raise RuntimeError("killed")

    real_sync = reclass._sync
    monkeypatch.setattr(reclass, "_sync", crash)
    with pytest.raises(RuntimeError):
        reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])
    monkeypatch.setattr(reclass, "_sync", real_sync)

    assert reclass.main(["--db", str(db_file), "--apply"]) == 0  # nothing left: only the sync
    assert (
        len(_summary_files(tmp_path / "audit")) == 1
    )  # the crashed run wrote none; the recovery one


def test_releasing_a_quarantined_ok_article_is_predicted_and_checked(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Row 1's article was `ok` before it was quarantined: promoting it to `accept` makes
    portfolio-nlp's readable set one larger, the dry run says +1 and the run confirms it."""
    _accepting(monkeypatch)
    reclass.main(["--db", str(db_file)])
    dry = json.loads(capsys.readouterr().out)
    assert dry["ok_articles"] == 0 and dry["predicted_ok_articles_delta"] == 1

    assert reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")]) == 0

    (summary,) = _summary_files(tmp_path / "audit")
    data = json.loads(summary.read_text())
    assert (data["before"]["ok_articles"], data["after"]["ok_articles"]) == (0, 1)
    assert data["reader_check"]["readable_articles"] == 1


def test_apply_fails_when_it_quarantines_an_ok_article_nobody_planned(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with sqlite3.connect(db_file) as con:  # an untouched `accept` pair whose article is readable
        con.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (6, 'https://x.com/fine', 'x.com', 'F', 'F',"
            " 'sitemap', 'now', 'fetched', 'accept', 'name')"
        )
        con.execute("INSERT INTO articles (id, fetch_status) VALUES (6, 'ok')")
    real_sync = reclass._sync

    def sync_then_a_bug(db_path: Path) -> tuple[int, int]:
        result = real_sync(db_path)
        with sqlite3.connect(db_path) as con:
            con.execute("UPDATE articles SET fetch_status = 'quarantined' WHERE id = 6")
        return result

    monkeypatch.setattr(reclass, "_sync", sync_then_a_bug)

    code = reclass.main(["--db", str(db_file), "--apply", "--backup", str(tmp_path / "b.db")])

    err = capsys.readouterr().err
    assert code == 1
    assert (
        "the articles portfolio-nlp reads (ok) went 1 -> 0, the dry run predicted a change of 0"
        in err
    )


def test_reverting_an_accept_expects_its_ok_article_to_be_quarantined_again(
    db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _accepting(monkeypatch)
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

    assert (
        reclass.main(
            ["--db", str(db_file), "--revert", str(listing), "--backup", str(tmp_path / "r.db")]
        )
        == 0
    )

    (summary,) = [p for p in _summary_files(tmp_path) if "revert" in p.name]
    data = json.loads(summary.read_text())
    assert data["expected_ok_articles_delta"] == -1
    assert (data["before"]["ok_articles"], data["after"]["ok_articles"]) == (1, 0)


@pytest.mark.parametrize("version", ["1.2.1", "1.1.9", "unknown"])
def test_apply_and_revert_refuse_an_old_portfolio_common_before_any_write(
    db_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: str,
) -> None:
    monkeypatch.setattr(reclass, "_library_version", lambda: version)
    before = _rows(db_file)
    backup, listing = tmp_path / "bk.db", tmp_path / "audit" / "l.csv"
    seed = tmp_path / "seed.csv"
    seed.write_text("id\n1\n")

    assert (
        reclass.main(
            ["--db", str(db_file), "--apply", "--backup", str(backup), "--list", str(listing)]
        )
        == 1
    )
    assert reclass.main(["--db", str(db_file), "--revert", str(seed), "--backup", str(backup)]) == 1

    err = capsys.readouterr().err
    assert err.count("run `uv sync` (needs >= 1.2.2)") == 2
    assert not backup.exists()
    assert not listing.exists()
    assert not list(tmp_path.glob("*.partial")) and not list((tmp_path / "audit").glob("*"))
    assert _rows(db_file) == before


def test_the_dry_run_only_warns_about_an_old_portfolio_common(
    db_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(reclass, "_library_version", lambda: "1.2.1")

    assert reclass.main(["--db", str(db_file)]) == 0

    captured = capsys.readouterr()
    assert "warning: portfolio-common 1.2.1 is installed" in captured.err
    assert '"promotions"' in captured.out
