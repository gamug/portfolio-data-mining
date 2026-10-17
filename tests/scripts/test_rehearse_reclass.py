"""scripts/rehearse_reclass.py: the rehearsal of the real re-class run on a copy (T-062)."""

from __future__ import annotations

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
_spec = importlib.util.spec_from_file_location("rehearse_reclass", _SCRIPTS / "rehearse_reclass.py")
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

ROWS = [
    (1, "https://x.com/kla-corporation-q4-results", "KLAC", "KLA Corporation", "fetched"),
    (2, "https://x.com/turn-on-the-tv.html", "ON", "ON Semiconductor", "fetched"),
    (3, "https://x.com/news-corp-earnings", "NWS", "News Corp", "skipped"),
]


@pytest.fixture
def real_db(tmp_path: Path) -> Path:
    path = tmp_path / "real" / "urls.db"
    path.parent.mkdir()
    db = connect(str(path))
    apply_schema(db)
    run_migrations(db)
    for i, url, ticker, company, status in ROWS:
        db.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at,"
            " status, match_class, match_reason) VALUES (?, ?, 'x.com', ?, ?, 'sitemap', 'now', ?,"
            " 'reject', 'none')",
            (i, url, company, ticker, status),
        )
        if status == "fetched":
            db.execute("INSERT INTO articles (id, fetch_status) VALUES (?, 'ok')", (i,))
    db.commit()
    quarantine_articles(db)
    db.close()
    return path


def test_the_whole_sequence_passes_on_a_small_copy_and_the_real_file_is_untouched(
    real_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (real_db.stat().st_size, real_db.stat().st_mtime_ns)
    workdir = tmp_path / "real" / "rehearsal"  # same directory, hence the same filesystem
    report_path = tmp_path / "report.json"

    code = mod.main(
        [
            "--real-db",
            str(real_db),
            "--workdir",
            str(workdir),
            "--articles",
            "--lock-test",
            "--report",
            str(report_path),
        ]
    )

    out = capsys.readouterr().out
    report = json.loads(report_path.read_text())
    assert code == 0 and report["passed"] is True and "PASSED" in out
    assert (
        report["steps"]["apply"]["promotions"] == 2
    )  # KLA and News Corp; `turn-on-the-tv` stays reject
    assert (
        report["steps"]["apply"]["wal_bytes"] == 0 and report["steps"]["revert"]["wal_bytes"] == 0
    )
    assert report["steps"]["lock_test"]["busy_message"] is True
    assert report["steps"]["compare"]["articles"] == [0, 0]
    assert {c["check"] for c in report["checks"] if not c["held"]} == set()
    assert (real_db.stat().st_size, real_db.stat().st_mtime_ns) == before
    assert not workdir.exists()  # the copies were removed (the report was written elsewhere)


def test_a_workdir_on_another_filesystem_is_refused(
    real_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(mod, "_same_filesystem", lambda *_a: False)

    assert mod.main(["--real-db", str(real_db), "--workdir", str(tmp_path / "elsewhere")]) == 1

    assert "not on the same filesystem" in capsys.readouterr().err
    assert not (tmp_path / "elsewhere").exists()


def test_a_non_empty_workdir_or_a_missing_database_is_refused(
    real_db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("mine")

    assert mod.main(["--real-db", str(real_db), "--workdir", str(busy)]) == 1
    assert mod.main(["--real-db", str(tmp_path / "nope.db"), "--workdir", str(tmp_path / "w")]) == 1

    assert (busy / "keep.txt").read_text() == "mine"
    err = capsys.readouterr().err
    assert "is not empty" in err and "does not exist" in err


def test_a_failing_check_makes_the_rehearsal_fail(
    real_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A copy that the revert does not restore must be reported, not passed."""
    real_run = mod._run

    def revert_does_nothing(work: Path, *args: str):  # type: ignore[no-untyped-def]
        if "--revert" in args:
            with sqlite3.connect(work) as con:  # damage the copy after the revert's own run
                proc, seconds = real_run(work, *args)
                con.execute("UPDATE discovered_urls SET match_reason = 'damaged' WHERE id = 1")
                return proc, seconds
        return real_run(work, *args)

    monkeypatch.setattr(mod, "_run", revert_does_nothing)

    code = mod.main(
        [
            "--real-db",
            str(real_db),
            "--workdir",
            str(tmp_path / "real" / "w"),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )

    assert code == 1 and "FAILED" in capsys.readouterr().out
    report = json.loads((tmp_path / "r.json").read_text())
    failed = [c["check"] for c in report["checks"] if not c["held"]]
    assert "the reverted copy equals the pristine one on discovered_urls" in failed
