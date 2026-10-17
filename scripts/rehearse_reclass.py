"""Rehearse `reclass_rejects.py` on a copy of the real database before the real run (T-062, T-063).

The real run edits a 2 GB SQLite file on a mounted drive; what the unit tests prove on a five-row fixture is
not what that file does (locking, the write-ahead log, the time of each stage). This copies the database
into ``--workdir`` (which must be on the **same filesystem** as the real file, so the rehearsal meets the same
mount) with SQLite's online backup, never opening the real file for writing, then runs the sequence the real
run will: an optional lock-contention test, ``--apply`` with a backup, the state of ``-wal`` and ``-shm``,
a second ``--apply`` (must write nothing), ``--revert``, and a comparison of the reverted copy with the
pristine one (``EXCEPT`` both ways on ``discovered_urls``, and on ``articles`` with ``--articles``). It prints a
Markdown summary, writes the same as JSON and exits 1 when a check fails. The real file's size and mtime are
asserted unchanged at the end. Files it created are removed unless ``--keep``.

Usage:
    PYTHONPATH=src uv run python scripts/rehearse_reclass.py --real-db /path/urls.db \\
        --workdir /path/on/the/same/mount/rehearsal [--articles] [--lock-test] [--keep] [--report PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parent / "reclass_rejects.py"
STAGE = re.compile(r"^stage (.+): ([0-9.]+)s$", re.MULTILINE)
Check = tuple[str, bool, str]  # (what, held, detail)


def _counts(path: Path) -> dict[str, Any]:
    """Rows per class, per discovery status and per article status of a database, read-only."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as con:
        return {
            "classes": dict(
                con.execute("SELECT match_class, COUNT(*) FROM discovered_urls GROUP BY 1")
            ),
            "loose_name": con.execute(
                "SELECT COUNT(*) FROM discovered_urls WHERE match_reason = 'loose_name'"
            ).fetchone()[0],
            "status": dict(con.execute("SELECT status, COUNT(*) FROM discovered_urls GROUP BY 1")),
            "articles": dict(con.execute("SELECT fetch_status, COUNT(*) FROM articles GROUP BY 1")),
        }


def _wal_state(path: Path) -> dict[str, Any]:
    wal, shm = Path(f"{path}-wal"), Path(f"{path}-shm")
    return {"wal_bytes": wal.stat().st_size if wal.exists() else 0, "shm_exists": shm.exists()}


def _run(work: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], float]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(SCRIPT.parent.parent / "src"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    start = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - the rehearsed script, our own argv
        [sys.executable, str(SCRIPT), "--db", str(work), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc, round(time.monotonic() - start, 1)


def _stages(proc: subprocess.CompletedProcess[str]) -> dict[str, float]:
    return {name: float(sec) for name, sec in STAGE.findall(proc.stderr)}


def _promotions(proc: subprocess.CompletedProcess[str]) -> int:
    """The planned promotions out of the JSON the dry-run part of a run prints."""
    start = proc.stdout.index("{")
    return int(json.JSONDecoder().raw_decode(proc.stdout[start:])[0]["promotions"])


def _lock_test(work: Path, workdir: Path) -> dict[str, Any]:
    """A second process holding the write lock must make `--apply` refuse, before any backup or list."""
    holder = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            "import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1], isolation_level=None);"
            " c.execute('BEGIN IMMEDIATE'); print('locked', flush=True); time.sleep(3600)",
            str(work),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        holder.stdout.readline()
        proc, seconds = _run(
            work,
            "--apply",
            "--backup",
            str(workdir / "lock_b.db"),
            "--list",
            str(workdir / "lock_l.csv"),
        )
    finally:
        holder.kill()
        holder.wait()
    return {
        "exit": proc.returncode,
        "busy_message": "the database is busy" in proc.stderr,
        "backup_created": (workdir / "lock_b.db").exists(),
        "list_created": (workdir / "lock_l.csv").exists()
        or (workdir / "lock_l.csv.partial").exists(),
        "seconds": seconds,
    }


def _except(work: Path, pre: Path, table: str, columns: str) -> tuple[int, int]:
    with closing(sqlite3.connect(f"file:{work}?mode=ro", uri=True)) as con:
        con.execute(f"ATTACH DATABASE 'file:{pre}?mode=ro' AS pre")
        only_work = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {columns} FROM main.{table} EXCEPT SELECT {columns} FROM pre.{table})"  # noqa: S608
        ).fetchone()[0]
        only_pre = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {columns} FROM pre.{table} EXCEPT SELECT {columns} FROM main.{table})"  # noqa: S608
        ).fetchone()[0]
    return only_work, only_pre


def _same_filesystem(real: Path, workdir: Path) -> bool:
    return os.stat(real).st_dev == os.stat(workdir).st_dev


def rehearse(real: Path, workdir: Path, *, articles: bool, lock_test: bool) -> dict[str, Any]:
    """Run the sequence and return the report (steps, timings, counts, checks)."""
    pre, work = workdir / "pre.db", workdir / "work.db"
    real_before = (real.stat().st_size, real.stat().st_mtime_ns)
    report: dict[str, Any] = {"real_db": str(real), "workdir": str(workdir), "steps": {}}
    steps = report["steps"]
    checks: list[Check] = []

    start = time.monotonic()
    with (
        closing(sqlite3.connect(f"file:{real}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(pre)) as dst,
    ):
        src.backup(dst)
    shutil.copyfile(pre, work)
    steps["copy"] = {"seconds": round(time.monotonic() - start, 1), "bytes": pre.stat().st_size}
    before = _counts(work)
    steps["before"] = before

    if lock_test:
        steps["lock_test"] = _lock_test(work, workdir)
        lock = steps["lock_test"]
        checks.append(
            (
                "a second writer makes --apply refuse before any backup or list",
                lock["exit"] != 0
                and lock["busy_message"]
                and not lock["backup_created"]
                and not lock["list_created"],
                str(lock),
            )
        )

    listing = workdir / "list.csv"
    proc, seconds = _run(work, "--apply", "--backup", str(workdir / "b.db"), "--list", str(listing))
    promotions = _promotions(proc) if proc.returncode == 0 else 0
    after = _counts(work)
    steps["apply"] = {
        "exit": proc.returncode,
        "seconds": seconds,
        "stages": _stages(proc),
        "stdout_tail": proc.stdout.strip().splitlines()[-6:],
        "promotions": promotions,
        **_wal_state(work),
    }
    steps["after_apply"] = after
    reject_drop = before["classes"].get("reject", 0) - after["classes"].get("reject", 0)
    checks += [
        (
            "apply exits 0 with no POST-CONDITION FAILED",
            proc.returncode == 0 and "POST-CONDITION FAILED" not in proc.stderr,
            f"exit {proc.returncode}",
        ),
        (
            "reject fell by the promotions",
            reject_drop == promotions,
            f"{reject_drop} vs {promotions}",
        ),
        (
            "article statuses unchanged by review promotions",
            after["articles"] == before["articles"],
            "",
        ),
        (
            "the WAL is empty after the run",
            steps["apply"]["wal_bytes"] == 0,
            f"{steps['apply']['wal_bytes']} bytes",
        ),
        ("the reader check passed", "reader check: attach(read_only=True) ok" in proc.stdout, ""),
    ]

    proc2, seconds2 = _run(work, "--apply", "--list", str(workdir / "second.csv"))
    steps["second_apply"] = {
        "exit": proc2.returncode,
        "seconds": seconds2,
        "stages": _stages(proc2),
    }
    checks.append(
        (
            "a second apply writes nothing and no list",
            proc2.returncode == 0 and not (workdir / "second.csv").exists(),
            "",
        )
    )

    proc3, seconds3 = _run(work, "--revert", str(listing), "--backup", str(workdir / "b2.db"))
    steps["revert"] = {
        "exit": proc3.returncode,
        "seconds": seconds3,
        "stages": _stages(proc3),
        "stdout_tail": proc3.stdout.strip().splitlines()[-4:],
        **_wal_state(work),
    }
    checks += [
        (
            "revert exits 0 with no POST-CONDITION FAILED",
            proc3.returncode == 0 and "POST-CONDITION FAILED" not in proc3.stderr,
            f"exit {proc3.returncode}",
        ),
        ("the WAL is empty after the revert", steps["revert"]["wal_bytes"] == 0, ""),
    ]

    start = time.monotonic()
    urls_diff = _except(work, pre, "discovered_urls", "*")
    articles_diff = (
        _except(work, pre, "articles", "id, fetch_status, prev_fetch_status") if articles else None
    )
    steps["compare"] = {
        "discovered_urls": urls_diff,
        "articles": articles_diff,
        "seconds": round(time.monotonic() - start, 1),
    }
    checks.append(
        (
            "the reverted copy equals the pristine one on discovered_urls",
            urls_diff == (0, 0),
            str(urls_diff),
        )
    )
    if articles_diff is not None:
        checks.append(("... and on articles", articles_diff == (0, 0), str(articles_diff)))

    checks.append(
        (
            "the real file was not touched",
            (real.stat().st_size, real.stat().st_mtime_ns) == real_before,
            "",
        )
    )
    report["checks"] = [{"check": c, "held": bool(ok), "detail": d} for c, ok, d in checks]
    report["passed"] = all(ok for _, ok, _ in checks)
    return report


def _markdown(report: dict[str, Any]) -> str:
    steps = report["steps"]
    lines = [
        f"Rehearsal on a copy of `{report['real_db']}` in `{report['workdir']}`",
        "",
        "| step | seconds | detail |",
        "|---|---|---|",
    ]
    lines.append(f"| copy | {steps['copy']['seconds']} | {steps['copy']['bytes']} bytes |")
    for name in ("apply", "second_apply", "revert"):
        s = steps[name]
        lines.append(f"| {name} | {s['seconds']} | exit {s['exit']}; stages {s['stages']} |")
    lines += ["", "| check | held | detail |", "|---|---|---|"]
    lines += [
        f"| {c['check']} | {'yes' if c['held'] else '**NO**'} | {c['detail']} |"
        for c in report["checks"]
    ]
    lines.append("")
    lines.append("PASSED" if report["passed"] else "FAILED")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--real-db", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument(
        "--articles",
        action="store_true",
        help="also compare `articles` (about 8 minutes on the real file)",
    )
    ap.add_argument(
        "--lock-test", action="store_true", help="also hold the write lock from a second process"
    )
    ap.add_argument("--keep", action="store_true", help="keep the copies and backups in --workdir")
    ap.add_argument(
        "--report", type=Path, help="JSON report (default: WORKDIR/rehearsal_report.json)"
    )
    args = ap.parse_args(argv)
    real, workdir = args.real_db.resolve(), args.workdir.resolve()
    if not real.is_file():
        print(f"error: {real} does not exist", file=sys.stderr)
        return 1
    existed = workdir.exists()
    if existed and any(workdir.iterdir()):
        print(f"error: {workdir} is not empty; pass a new directory", file=sys.stderr)
        return 1
    workdir.mkdir(parents=True, exist_ok=True)
    if not _same_filesystem(real, workdir):
        print(
            f"error: {workdir} is not on the same filesystem as {real}; the rehearsal would not meet the same mount",
            file=sys.stderr,
        )
        if not existed:
            workdir.rmdir()
        return 1
    try:
        report = rehearse(real, workdir, articles=args.articles, lock_test=args.lock_test)
        path = args.report or workdir / "rehearsal_report.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(_markdown(report))
        print(f"report {path}")
    finally:
        if not args.keep:
            for leftover in list(workdir.iterdir()):
                if leftover.name != "rehearsal_report.json" or args.report:
                    leftover.unlink()
            if not any(workdir.iterdir()) and not existed:
                workdir.rmdir()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
