"""scripts/reclaim_sitemap_rejects.py and scripts/fill_sitemap_cache.py: the gate and the exit codes."""

from __future__ import annotations

import argparse
import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from news_collector import reclaim_sitemap as rs
from news_collector.models import SitemapEntry
from news_collector.sitemap_cache import Crawl, SitemapRun
from news_collector.sitemap_fill import FillReport

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reclaim_cli = load("reclaim_sitemap_rejects")
fill_cli = load("fill_sitemap_cache")


def args(
    db: str, *, g3: bool = False, rehearsal: bool = False, **extra: object
) -> argparse.Namespace:
    return argparse.Namespace(
        db=db, g3=g3, rehearsal=rehearsal, rehearsal_copy=rehearsal, backup="b.db", expect=1,
        expect_kept=None, **extra,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def real_is_elsewhere(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`DATABASE_URL` names a file in the temp dir, as on the maintainer's machine it names the real one."""
    real = tmp_path / "somewhere" / "the-real.db"
    monkeypatch.setattr(rs, "resolve_db_path", lambda _x: str(real))
    return real


def test_the_gate_wants_exactly_one_of_the_two_flags(tmp_path: Path) -> None:
    db = str(tmp_path / "x.db")
    assert reclaim_cli.gate(args(db)) is None
    assert reclaim_cli.gate(args(db, g3=True, rehearsal=True)) is None
    assert reclaim_cli.gate(args(db, g3=True)) is True  # the process checks run
    assert reclaim_cli.gate(args(db, rehearsal=True)) is False


def test_the_real_file_is_never_a_rehearsal_copy(tmp_path: Path, real_is_elsewhere: Path) -> None:
    for name in (
        "urls.db",
        str(real_is_elsewhere),
    ):  # called urls.db, or the one DATABASE_URL names
        path = tmp_path / name if name == "urls.db" else Path(name)
        assert rs.looks_real(path) is True
        assert reclaim_cli.gate(args(str(path), rehearsal=True)) is None
        assert rs.looks_real(path) is True
    # the real file given by its path, as the maintainer types it, runs every guard
    assert reclaim_cli.gate(args("/workspaces/thesis/data/urls.db", g3=True)) is True


def test_reclaim_without_a_flag_is_refused_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def must_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("reclaim ran")

    monkeypatch.setattr(reclaim_cli, "reclaim", must_not_run)
    assert reclaim_cli.cmd_reclaim(args(str(tmp_path / "x.db"))) == 1
    assert "exactly one of" in capsys.readouterr().out


def test_the_exit_codes_say_what_state_the_file_is_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(*_a: object, **_k: object) -> None:
        raise rs.ReclaimError("2 qualify")

    def incomplete(*_a: object, **_k: object) -> None:
        raise rs.ReclaimIncomplete("INCOMPLETE: the delete of 2 rows is COMMITTED in x.db")

    ns = args(str(tmp_path / "x.db"), rehearsal=True)
    monkeypatch.setattr(reclaim_cli, "reclaim", refused)
    assert reclaim_cli.cmd_reclaim(ns) == 1
    assert "REFUSED, nothing changed: 2 qualify" in capsys.readouterr().out
    monkeypatch.setattr(reclaim_cli, "reclaim", incomplete)
    assert reclaim_cli.cmd_reclaim(ns) == 2  # a different code, and never "nothing changed"
    out = capsys.readouterr().out
    assert "COMMITTED" in out
    assert "nothing changed" not in out


def test_the_process_check_follows_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []

    def spy(*_a: object, **kw: object) -> None:
        seen.append(bool(kw["check_processes"]))
        raise rs.ReclaimError("stop")

    monkeypatch.setattr(reclaim_cli, "reclaim", spy)
    reclaim_cli.cmd_reclaim(args(str(tmp_path / "x.db"), g3=True))
    reclaim_cli.cmd_reclaim(args(str(tmp_path / "x.db"), rehearsal=True))
    assert seen == [True, False]


async def test_fill_needs_a_backup_and_the_flag_on_the_real_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ns = argparse.Namespace(
        db=str(tmp_path / "x.db"), domain="cnbc.com", backup=None, g3=True, rehearsal_copy=False,
        force=False, start_date=None, end_date=None,
    )  # fmt: skip
    assert await fill_cli.run(ns) == 1  # --i-have-the-g3-approval without --backup
    assert "needs --backup" in capsys.readouterr().out
    ns.g3, ns.rehearsal_copy = False, True
    ns.db = str(tmp_path / "urls.db")
    assert await fill_cli.run(ns) == 1  # a file called urls.db is not a rehearsal copy
    assert "looks like the real database" in capsys.readouterr().out


def evidence_args(accepted: int | None, db: str = "x.db") -> argparse.Namespace:
    return argparse.Namespace(
        db=db, tickers=["A"], sample=0, seed=0, start_date=None, end_date=None,
        expect_now_rejected=accepted if accepted is not None else 0,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("found", "accepted", "code"), [(0, 0, 0), (17, 17, 0), (18, 17, 1), (17, 0, 1)]
)
async def test_evidence_exits_0_only_for_the_accepted_number_of_now_rejected_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    found: int,
    accepted: int,
    code: int,
) -> None:
    pairs = [("ON", f"https://x/{i}", "reject") for i in range(found)]
    fake = SimpleNamespace(proof=SimpleNamespace(rejected_now=pairs))

    async def gather(*_a: object, **_k: object) -> SimpleNamespace:
        return fake

    monkeypatch.setattr(reclaim_cli, "gather_evidence", gather)
    monkeypatch.setattr(reclaim_cli, "format_evidence", lambda *_a: "")
    monkeypatch.setattr(reclaim_cli, "build_date_range", lambda *_a: None)

    db = tmp_path / "e.db"
    db.write_bytes(b"")  # the file must exist: a missing one is refused before anything runs
    assert await reclaim_cli.cmd_evidence(evidence_args(accepted, str(db))) == code
    assert (
        f"Now-rejected pairs: {found} (the maintainer accepted {accepted})"
        in capsys.readouterr().out
    )


def test_compact_on_the_real_file_needs_a_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def must_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("compact ran")

    monkeypatch.setattr(reclaim_cli, "compact", must_not_run)
    ns = argparse.Namespace(db=str(tmp_path / "x.db"), g3=True, rehearsal=False, backup=None)
    assert reclaim_cli.cmd_compact(ns) == 1
    assert "needs --backup" in capsys.readouterr().out


# --- the success paths: what the maintainer reads after the file has been swapped ----------------


def fingerprint(discovered: int, articles: int = 2) -> rs.Fingerprint:
    return rs.Fingerprint(
        discovered=discovered, articles=articles, orphans=0, quick_check="ok",
        by_class={"accept": 1, "review": 1, "reject": discovered - 2},
    )  # fmt: skip


def reclaimed(owner_kept: bool = True) -> rs.Reclaimed:
    return rs.Reclaimed(
        deleted=2, before=fingerprint(10), after=fingerprint(8), bytes_before=2000,
        bytes_after=1500, backup=Path("urls_t_073.db"), seconds=3.5, kept=593, owner_kept=owner_kept,
    )  # fmt: skip


def test_a_successful_reclaim_prints_the_report_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(reclaim_cli, "reclaim", lambda *_a, **_k: reclaimed())
    assert reclaim_cli.cmd_reclaim(args(str(tmp_path / "x.db"), rehearsal=True)) == 0
    out = capsys.readouterr().out
    for line in ("Deleted          : 2", "Protected ids    : 593", "discovered_urls  : 10 -> 8",
                 "(orphans 0)", "500 reclaimed", "Backup kept      : urls_t_073.db", "Final check"):  # fmt: skip
        assert line in out
    assert "WARNING" not in out


def test_a_reclaim_whose_owner_could_not_be_kept_still_exits_0_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(reclaim_cli, "reclaim", lambda *_a, **_k: reclaimed(owner_kept=False))
    assert reclaim_cli.cmd_reclaim(args(str(tmp_path / "x.db"), rehearsal=True)) == 0
    assert "WARNING: the new file is owned by this user" in capsys.readouterr().out


def test_a_successful_compact_prints_and_exits_0_and_a_refused_one_says_the_file_is_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ns = argparse.Namespace(db=str(tmp_path / "x.db"), g3=False, rehearsal=True, backup=None)
    monkeypatch.setattr(reclaim_cli, "compact", lambda *_a, **_k: reclaimed(owner_kept=False))
    assert reclaim_cli.cmd_compact(ns) == 0
    out = capsys.readouterr().out
    assert "File             : 2000 -> 1500 bytes" in out
    assert "discovered_urls  : 8, articles 2" in out
    assert "WARNING" in out

    def refused(*_a: object, **_k: object) -> None:
        raise rs.ReclaimError("the file is as it was (disk is full)")

    monkeypatch.setattr(reclaim_cli, "compact", refused)
    assert reclaim_cli.cmd_compact(ns) == 1
    assert (
        "REFUSED, nothing changed: the file is as it was (disk is full)" in capsys.readouterr().out
    )


def fill_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        db=str(tmp_path / "x.db"), domain="cnbc.com", backup=None, g3=False, rehearsal_copy=True,
        force=False, start_date=date(2022, 1, 1), end_date=date(2026, 10, 20),
    )  # fmt: skip


def fill_report(run: SitemapRun, after: rs.Fingerprint) -> FillReport:
    return FillReport("cnbc.com", run, True, fingerprint(10), after)


async def test_a_successful_fill_prints_the_report_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entries = (
        SitemapEntry(url="https://www.cnbc.com/2024/a.html"),
        SitemapEntry(url="https://www.cnbc.com/2024/b.html"),
    )
    crawl = Crawl("2026-10-20T10:00:00.000000", date(2022, 1, 1), "sig", 2)

    async def fake(*_a: object, **_k: object) -> FillReport:
        return fill_report(SitemapRun("cnbc.com", crawl, rows=entries), fingerprint(10))

    monkeypatch.setattr(fill_cli, "fill_cache", fake)
    assert await fill_cli.run(fill_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Crawl            : made now" in out
    assert "Entries          : 2 (range from 2022-01-01)" in out
    assert "Pipeline tables  : unchanged" in out


async def test_an_incomplete_fill_exits_1_and_a_changed_pipeline_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(*_a: object, **_k: object) -> FillReport:
        return fill_report(
            SitemapRun("cnbc.com", None, failures=("child 3 failed",)), fingerprint(11)
        )

    monkeypatch.setattr(fill_cli, "fill_cache", fake)
    assert await fill_cli.run(fill_args(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "child 3 failed" in out
    assert "Pipeline tables  : CHANGED" in out
    assert "INCOMPLETE: the cache is not usable" in out


# --- review round 5: one gate for the three commands, a mistyped --db is a refusal ----------------


@pytest.mark.parametrize(("g3", "rehearsal"), [(True, True), (False, False)])
async def test_fill_reads_the_two_flags_like_reclaim_and_compact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], g3: bool, rehearsal: bool
) -> None:
    ns = fill_args(tmp_path)
    ns.g3, ns.rehearsal_copy, ns.backup = g3, rehearsal, "b.db"
    assert await fill_cli.run(ns) == 1
    assert "REFUSED, nothing changed: give exactly one of" in capsys.readouterr().out
    other = args(str(tmp_path / "x.db"), g3=g3, rehearsal=rehearsal)  # the same pair, reclaim
    assert reclaim_cli.gate(other) is None
    assert "give exactly one of" in capsys.readouterr().out


def test_the_three_commands_refuse_a_database_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.db"
    here = tmp_path  # a directory is not a database either
    for target in (missing, here):
        with pytest.raises(rs.ReclaimError, match="no database at"):
            rs.reclaim(target, Path("b.db"), tmp_path, expect=1, check_processes=False)
        with pytest.raises(rs.ReclaimError, match="no database at"):
            rs.compact(target, check_processes=False)
        assert reclaim_cli.cmd_reclaim(args(str(target), rehearsal=True)) == 1
        assert "REFUSED, nothing changed: no database at" in capsys.readouterr().out
        assert reclaim_cli.cmd_compact(args(str(target), rehearsal=True)) == 1
        assert "REFUSED, nothing changed: no database at" in capsys.readouterr().out
    assert not missing.exists()  # nothing was created


async def test_evidence_on_a_missing_database_is_a_refusal_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def must_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("evidence ran")

    monkeypatch.setattr(reclaim_cli, "gather_evidence", must_not_run)
    ns = evidence_args(0)
    ns.db = str(tmp_path / "nope.db")
    assert await reclaim_cli.cmd_evidence(ns) == 1
    assert "REFUSED, nothing changed: no database at" in capsys.readouterr().out
    assert not (tmp_path / "nope.db").exists()
    with pytest.raises(rs.ReclaimError, match="no database at"):
        await rs.gather_evidence(str(tmp_path / "nope.db"), {}, tmp_path, None, [])  # type: ignore[arg-type]
