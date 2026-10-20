"""scripts/reclaim_sitemap_rejects.py and scripts/fill_sitemap_cache.py: the gate and the exit codes."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from news_collector import reclaim_sitemap as rs

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
    for mod in (reclaim_cli, fill_cli):
        monkeypatch.setattr(mod, "resolve_db_path", lambda _x, real=real: str(real))
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
        assert reclaim_cli.looks_real(path) is True
        assert reclaim_cli.gate(args(str(path), rehearsal=True)) is None
        assert fill_cli.looks_real(path) is True
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
    assert "needs --backup and --i-have-the-g3-approval" in capsys.readouterr().out
    ns.g3, ns.rehearsal_copy = False, True
    ns.db = str(tmp_path / "urls.db")
    assert await fill_cli.run(ns) == 1  # a file called urls.db is not a rehearsal copy
    assert "looks like the real database" in capsys.readouterr().out


def evidence_args(accepted: int | None) -> argparse.Namespace:
    return argparse.Namespace(
        db="x.db", tickers=["A"], sample=0, seed=0, start_date=None, end_date=None,
        expect_now_rejected=accepted if accepted is not None else 0,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("found", "accepted", "code"), [(0, 0, 0), (17, 17, 0), (18, 17, 1), (17, 0, 1)]
)
async def test_evidence_exits_0_only_for_the_accepted_number_of_now_rejected_pairs(
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

    assert await reclaim_cli.cmd_evidence(evidence_args(accepted)) == code
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
