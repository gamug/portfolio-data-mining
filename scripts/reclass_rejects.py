"""Re-class the `reject` pairs with the current matcher and promote the ones it no longer rejects (T-062).

The T-057 re-tag only touched rows with `match_reason IS NULL`, so the pairs the matcher rejected then
keep `match_class = 'reject'` after a matcher fix. This classes every `reject` row of `discovered_urls`
again (URL, and the best title known: the discovery title, else the fetched article's) and writes the
new class and reason where it is higher: a stored class is only ever promoted. A `skipped` row that is
promoted goes back to `pending` (it was never fetched because of the reject). Then `sync_quarantine`
releases any article whose pair is now `accept`; one that becomes `review` stays quarantined.

Default is a dry run (read-only; a few minutes on the real 2 GB database, which is read cold): it
prints the promotions, the status of the promoted rows, what the quarantine sync will do, the top
tickers and the `api` rows it leaves alone. `--apply` needs `--backup PATH` (a consistent copy of the
database, never overwriting a file; its fingerprint, `PRAGMA quick_check` and SHA-256 are checked or
printed) and refuses to start when the database is busy or the disk cannot hold the copy; it warns when
the `-wal` file suggests a live writer. Stop the collector and the crawler first: the fingerprint of the
database (row counts, highest ids, rows per status and per class) taken with the plan must still hold
inside the write transaction, or nothing is written. Idempotent: a second run finds nothing to promote.

Before it writes, `--apply` saves the audit list of every row it will change (`--list`, default
`data/retag/reclass_promoted.csv`, which is git-ignored; it refuses to overwrite one). `--revert LIST`
undoes a run from that list: a row goes back to its old class, reason and status only if it still holds
what this run wrote (a row changed since is left alone), then the quarantine sync runs. Take a backup
first (`--backup`), as for `--apply`; the list is the way back once the collector has written again.

If the process dies after the promotions are committed but before the quarantine sync, run `--apply`
again without `--backup`: with nothing left to promote it needs no backup and only runs the sync.

Usage:
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db COPY.db
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db --apply --backup urls.pre-reclass.db
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db --revert LIST.csv --backup NEW.db
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from functools import partial
from pathlib import Path
from typing import cast

from data_mining.db import connect, resolve_db_path
from data_mining.matching import CLASS_RANK, classify
from data_mining.quarantine import sync_quarantine

Promotion = tuple[int, str, str]  # (discovered_urls.id, new class, new reason)
Fingerprint = tuple[object, ...]
TOP_TICKERS = 10
BACKUP_HEADROOM = 1.1  # the copy needs the database size (and its -wal) plus a margin
CHUNK = 500  # ids per `IN (...)`, well under SQLite's variable limit (999 on older builds)
DEFAULT_LIST = "data/retag/reclass_promoted.csv"
LIST_FIELDS = [
    "id",
    "article_id",
    "ticker",
    "company",
    "url",
    "old_class",
    "old_reason",
    "new_class",
    "new_reason",
    "old_status",
    "new_status",
    "article_fetch_status",
]
LIVE_WRITER_SECONDS = 60
PARTIAL_SUFFIX = ".partial"


@contextmanager
def _stage(name: str) -> Iterator[None]:
    """Print how long a stage took (stderr), so a slow step of the real run is seen as it happens."""
    start = time.monotonic()
    try:
        yield
    finally:
        print(f"stage {name}: {time.monotonic() - start:.1f}s", file=sys.stderr)


def _fingerprint(con: sqlite3.Connection) -> Fingerprint:
    """What the plan was computed on: row counts, the highest ids, and the rows per discovery status
    and per class. A crawler flipping a status or a class changes it even when no row is added."""
    return (
        tuple(con.execute("SELECT COUNT(*), MAX(id) FROM discovered_urls").fetchone()),
        tuple(con.execute("SELECT COUNT(*), MAX(id) FROM articles").fetchone()),
        tuple(con.execute("SELECT status, COUNT(*) FROM discovered_urls GROUP BY 1 ORDER BY 1")),
        tuple(
            con.execute("SELECT match_class, COUNT(*) FROM discovered_urls GROUP BY 1 ORDER BY 1")
        ),
    )


def _class_counts(fp: Fingerprint) -> dict[str, int]:
    """Rows per `match_class` out of a fingerprint."""
    return {str(k): int(v) for k, v in cast(tuple[tuple[str, int], ...], fp[3])}


def _chunks(items: Sequence[int]) -> Iterator[list[int]]:
    for i in range(0, len(items), CHUNK):
        yield list(items[i : i + CHUNK])


def plan(con: sqlite3.Connection) -> list[Promotion]:
    """The `reject` rows the current matcher classes higher.

    `api` rows are not re-classed: a ticker-keyed feed row is `accept` by construction (the re-tag
    never rejects one), so a `reject` there would be a data problem to look at, not a URL guess."""
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT d.id, d.url, d.ticker, d.company,"
        " COALESCE(NULLIF(d.title, ''), a.title, '') AS title"
        " FROM discovered_urls d LEFT JOIN articles a ON a.id = d.id"
        " WHERE d.match_class = 'reject' AND d.source != 'api'"
    )
    out: list[Promotion] = []
    for r in rows:
        m = classify(r["url"], r["title"], r["company"], r["ticker"])
        if CLASS_RANK[m.cls] > CLASS_RANK["reject"]:
            out.append((r["id"], m.cls, m.reason))
    return out


def summarize(promotions: list[Promotion]) -> dict[str, dict[str, int]]:
    """Counts of the promotions per new class and per reason."""
    return {
        "by_class": dict(Counter(cls for _, cls, _ in promotions)),
        "by_reason": dict(Counter(reason for _, _, reason in promotions)),
    }


_TO_QUARANTINE = (
    "SELECT id FROM articles WHERE (fetch_status IS NULL OR fetch_status != 'quarantined')"
    " AND id IN (SELECT id FROM discovered_urls WHERE match_class != 'accept')"
)
_TO_RELEASE = (
    "SELECT id FROM articles WHERE (fetch_status = 'quarantined' OR prev_fetch_status IS NOT NULL)"
    " AND id IN (SELECT id FROM discovered_urls WHERE match_class = 'accept')"
)


def _is_quarantined(row: sqlite3.Row) -> bool:
    """The row has an article, and that article is held back (or carries a stale previous status)."""
    return row["fetch_status"] == "quarantined" or row["prev_fetch_status"] is not None


def _rows_of(con: sqlite3.Connection, ids: Sequence[int]) -> dict[int, sqlite3.Row]:
    """The current discovery and article state of the given ids, read in chunks."""
    con.row_factory = sqlite3.Row
    out: dict[int, sqlite3.Row] = {}
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for r in con.execute(
            "SELECT d.id, d.status, d.ticker, d.company, d.url, d.match_class, d.match_reason,"  # noqa: S608
            " a.id AS article_id, a.fetch_status, a.prev_fetch_status FROM discovered_urls d"
            f" LEFT JOIN articles a ON a.id = d.id WHERE d.id IN ({marks})",
            chunk,
        ):
            out[r["id"]] = r
    return out


def report(con: sqlite3.Connection, promotions: list[Promotion]) -> dict[str, object]:
    """What `--apply` would do, read from the database: the discovery status of the promoted rows
    (a `skipped` one goes back to `pending`), the tickers, and the sync's predicted
    `(quarantined, released)`, which the apply's own line must equal."""
    ids = {p[0]: p[1] for p in promotions}
    info = _rows_of(con, list(ids))
    to_quarantine = {r[0] for r in con.execute(_TO_QUARANTINE)}
    to_release = {r[0] for r in con.execute(_TO_RELEASE)}
    accepted = {i for i, cls in ids.items() if cls == "accept"}
    api_rejects = con.execute(
        "SELECT COUNT(*) FROM discovered_urls WHERE match_class = 'reject' AND source = 'api'"
    ).fetchone()[0]
    return {
        "by_status": dict(Counter(info[i]["status"] for i in ids)),
        "skipped_back_to_pending": sum(1 for i in ids if info[i]["status"] == "skipped"),
        "promoted_with_a_quarantined_article": sum(
            1 for i in ids if info[i]["fetch_status"] == "quarantined"
        ),
        "top_tickers": dict(Counter(info[i]["ticker"] for i in ids).most_common(TOP_TICKERS)),
        "predicted_sync": {
            "quarantined": len(to_quarantine - accepted),
            "released": len(to_release | {i for i in accepted if _is_quarantined(info[i])}),
        },
        "api_rejects_left_alone": api_rejects,
    }


def audit_rows(con: sqlite3.Connection, promotions: list[Promotion]) -> list[dict[str, object]]:
    """One row per change `apply` will make: old and new class, reason and status, so a run can be
    audited, handed over and reverted without diffing a 2 GB file."""
    info = _rows_of(con, [p[0] for p in promotions])
    out: list[dict[str, object]] = []
    for i, cls, reason in promotions:
        r = info[i]
        out.append(
            {
                "id": i,
                "article_id": r["article_id"] if r["article_id"] is not None else "",
                "ticker": r["ticker"],
                "company": r["company"],
                "url": r["url"],
                "old_class": r["match_class"],
                "old_reason": r["match_reason"],
                "new_class": cls,
                "new_reason": reason,
                "old_status": r["status"],
                "new_status": "pending" if r["status"] == "skipped" else r["status"],
                "article_fetch_status": r["fetch_status"] or "",
            }
        )
    return out


def _partial(path: Path) -> Path:
    """Where the audit list is written until the promotions are committed."""
    return path.with_name(path.name + PARTIAL_SUFFIX)


def _check_list(path: Path) -> None:
    """Exit before the backup when the audit list (or a leftover partial one) exists (custody) or its
    place is not writable."""
    for existing in (path, _partial(path)):
        if existing.exists():
            raise SystemExit(f"the audit list {existing} exists; move it or pass another --list.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(path.parent, os.W_OK)
    except OSError as exc:
        raise SystemExit(
            f"cannot write the audit list {path}: {exc}; nothing was written."
        ) from exc
    if not writable:
        raise SystemExit(f"cannot write the audit list {path}: directory not writable.")


def _write_list(path: Path, rows: list[dict[str, object]]) -> None:
    """Save the audit list as `PATH.partial` (its place was checked by `_check_list`) before anything
    is changed. It becomes `PATH` only once the promotions are committed (`_publish_list`): a list
    must never describe changes that did not happen."""
    partial = _partial(path)
    try:
        with partial.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=LIST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise SystemExit(
            f"cannot write the audit list {path}: {exc}; nothing was written."
        ) from exc


def _publish_list(path: Path) -> None:
    """The promotions are committed: the partial list becomes the audit list."""
    _partial(path).replace(path)


def _discard_list(path: Path) -> None:
    """The run stopped before committing: the partial list describes nothing that happened."""
    if _partial(path).exists():
        _partial(path).unlink()


def _warn_live_writer(db_path: Path) -> None:
    """Warn (not fail) when the database looks like a process is writing to it."""
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    if wal.exists() and wal.stat().st_size > 0:
        print(f"warning: {wal.name} is not empty; a writer may be running.", file=sys.stderr)
    if shm.exists() and time.time() - shm.stat().st_mtime < LIVE_WRITER_SECONDS:
        print(
            f"warning: {shm.name} changed in the last {LIVE_WRITER_SECONDS}s; a process has "
            "the database open.",
            file=sys.stderr,
        )


def _check_idle(db_path: Path) -> None:
    """Exit when another process holds the write lock (the collector or crawler is running)."""
    con = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"the database is busy ({exc}); stop the collector and the crawler."
        ) from exc
    finally:
        con.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _backup(db_path: Path, backup: Path, expected: Fingerprint) -> str:
    """Copy the database to `backup`, check the copy (fingerprint, `quick_check`), remove a failed
    copy, and return the copy's SHA-256 for the maintainer to record."""
    wal = db_path.with_name(db_path.name + "-wal")
    needed = int(
        (db_path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)) * BACKUP_HEADROOM
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(backup.resolve().parent).free
    if free < needed:
        raise SystemExit(f"not enough disk for the backup: {needed} bytes needed, {free} free.")
    try:
        with (
            closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(backup)) as dest,
        ):
            src.backup(dest)
        with closing(sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True)) as copy:
            copied = _fingerprint(copy)
            check = copy.execute("PRAGMA quick_check").fetchone()[0]
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    if copied != expected or check != "ok":
        backup.unlink(missing_ok=True)
        raise SystemExit(
            f"the backup does not match the plan (quick_check: {check}); stopped, copy removed."
        )
    return _sha256(backup)


def _in_transaction(
    db_path: Path,
    expected: Fingerprint,
    write: Callable[[sqlite3.Connection], None],
    on_commit: Callable[[], None] | None = None,
) -> None:
    """Run `write(con)` in one `BEGIN IMMEDIATE` transaction, after checking that the database is
    still what the plan saw (inside the write lock, so nothing can move between check and write).
    `on_commit` runs right after the commit, before anything else can fail."""
    con = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        if _fingerprint(con) != expected:
            con.execute("ROLLBACK")
            raise SystemExit(
                "the database changed since the plan was computed; nothing was written."
            )
        write(con)
        con.execute("COMMIT")
    finally:
        con.close()
    if on_commit is not None:
        on_commit()


def _sync(db_path: Path) -> tuple[int, int]:
    # `wal=True` issues `PRAGMA journal_mode=WAL`; the real file already is WAL (its `-wal` exists), so
    # this is a no-op there. It stays so that a copy opened here gets the mode the collector expects.
    db = connect(str(db_path), wal=True, foreign_keys=True)
    try:
        return sync_quarantine(db)
    finally:
        db.close()


def apply(
    db_path: Path,
    promotions: list[Promotion],
    expected: Fingerprint,
    on_commit: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Write the promotions in one transaction, then sync the quarantine. Returns
    `(quarantined, released)` by the sync. Rolls back and writes nothing if the database is no
    longer the `expected` fingerprint, checked inside the write lock."""

    def write(con: sqlite3.Connection) -> None:
        for i, cls, reason in promotions:
            con.execute(  # `AND match_class = 'reject'`: a row that moved meanwhile is left as it is
                "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                " status = CASE WHEN status = 'skipped' THEN 'pending' ELSE status END"
                " WHERE id = ? AND match_class = 'reject'",
                (cls, reason, i),
            )

    _in_transaction(db_path, expected, write, on_commit)
    return _sync(db_path)


def revert(db_path: Path, rows: list[dict[str, str]], expected: Fingerprint) -> tuple[int, int]:
    """Undo a run from its audit list: a row returns to its old class, reason and status only if it
    still holds what the run wrote; a row changed since is left alone. Then the quarantine sync."""

    def write(con: sqlite3.Connection) -> None:
        for r in rows:
            con.execute(
                "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                " status = CASE WHEN status = ? THEN ? ELSE status END"
                " WHERE id = ? AND match_class = ? AND match_reason = ?",
                (
                    r["old_class"],
                    r["old_reason"],
                    r["new_status"],
                    r["old_status"],
                    r["id"],
                    r["new_class"],
                    r["new_reason"],
                ),
            )

    _in_transaction(db_path, expected, write)
    return _sync(db_path)


def _held_accepts(con: sqlite3.Connection) -> int:
    """`accept` pairs whose article is still quarantined (the sync must have released them)."""
    return int(
        con.execute(
            "SELECT COUNT(*) FROM articles a JOIN discovered_urls d ON d.id = a.id"
            " WHERE d.match_class = 'accept' AND a.fetch_status = 'quarantined'"
        ).fetchone()[0]
    )


def _verify_apply(
    db_path: Path,
    promotions: list[Promotion],
    expected: Fingerprint,
    result: tuple[int, int],
    predicted: dict[str, int],
) -> list[str]:
    """Read the database back after `apply` and list what does not hold (empty when all does): the
    rows per class moved by exactly the promotions, none of them is still `reject`, the sync printed
    what the dry run predicted, and no `accept` pair keeps a quarantined article."""
    problems: list[str] = []
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        now = _class_counts(_fingerprint(con))
        want: Counter[str] = Counter(_class_counts(expected))
        for _, cls, _ in promotions:
            want["reject"] -= 1
            want[cls] += 1
        for cls in sorted(set(want) | set(now)):
            if want.get(cls, 0) != now.get(cls, 0):
                problems.append(f"class {cls}: {now.get(cls, 0)} rows, expected {want.get(cls, 0)}")
        rows = _rows_of(con, [p[0] for p in promotions])
        still = sum(1 for r in rows.values() if r["match_class"] == "reject")
        if still:
            problems.append(f"{still} promoted row(s) are still `reject`")
        if held := _held_accepts(con):
            problems.append(f"{held} `accept` pair(s) still have a quarantined article")
    if result != (predicted["quarantined"], predicted["released"]):
        problems.append(
            f"the sync did (quarantined, released) = {result}, the dry run predicted "
            f"({predicted['quarantined']}, {predicted['released']})"
        )
    return problems


def _verify_revert(db_path: Path, rows: list[dict[str, str]]) -> list[str]:
    """After `revert`: no listed row still holds what the run wrote, and no `accept` pair keeps a
    quarantined article."""
    problems: list[str] = []
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        current = _rows_of(con, [int(r["id"]) for r in rows])
        left = [
            r["id"]
            for r in rows
            if (current[int(r["id"])]["match_class"], current[int(r["id"])]["match_reason"])
            == (r["new_class"], r["new_reason"])
        ]
        if left:
            problems.append(f"{len(left)} listed row(s) still hold what the run wrote: {left[:5]}")
        if held := _held_accepts(con):
            problems.append(f"{held} `accept` pair(s) still have a quarantined article")
    return problems


def _report_problems(problems: list[str], what: str) -> int:
    for problem in problems:
        print(f"POST-CONDITION FAILED: {problem}", file=sys.stderr)
    print(
        f"error: {what} did not leave the database as planned; the changes are committed. "
        "Check the lines above, then `--revert LIST --backup NEW` if needed.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=resolve_db_path())
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", type=Path, metavar="LIST", help="undo a run from its audit list")
    ap.add_argument("--backup", type=Path)
    ap.add_argument(
        "--list", type=Path, default=Path(DEFAULT_LIST), help="audit list `--apply` writes"
    )
    args = ap.parse_args(argv)
    db_path = Path(args.db)
    if args.revert:
        return _revert_main(db_path, args)
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        with _stage("fingerprint"):
            expected = _fingerprint(con)  # taken with the plan, so `apply` can tell it moved
        with _stage("plan"):
            promotions = plan(con)
        with _stage("report"):
            details = report(con, promotions)
            audit = audit_rows(con, promotions)
    print(json.dumps({"promotions": len(promotions), **summarize(promotions), **details}, indent=2))
    if not args.apply:
        return 0
    if args.backup is not None and args.backup.exists():
        print("error: --backup names a file that exists; pick a new path", file=sys.stderr)
        return 1
    if promotions and args.backup is None:
        print("error: --apply needs --backup PATH naming a new file", file=sys.stderr)
        return 1
    if promotions:
        _check_list(args.list)  # before the copy: a missing list place must not cost a 2 GB backup
    _warn_live_writer(db_path)
    _check_idle(db_path)
    if promotions and args.backup is not None:
        with _stage("backup, check and sha256"):
            sha = _backup(db_path, args.backup, expected)
        print(f"backup {args.backup} sha256 {sha}")
    on_commit = None
    if promotions:
        _write_list(args.list, audit)
        on_commit = partial(_publish_list, args.list)
    try:
        with _stage("apply and sync"):
            result = apply(db_path, promotions, expected, on_commit)
    except BaseException:
        _discard_list(args.list)  # a list only describes changes that were committed
        raise
    if promotions:
        print(f"audit list {args.list} ({len(audit)} rows)")
    print(f"promoted {len(promotions)} rows; quarantined {result[0]}, released {result[1]}")
    with _stage("verify"):
        predicted = cast(dict[str, int], details["predicted_sync"])
        problems = _verify_apply(db_path, promotions, expected, result, predicted)
    return _report_problems(problems, "--apply") if problems else 0


def _revert_main(db_path: Path, args: argparse.Namespace) -> int:
    if args.revert.name.endswith(PARTIAL_SUFFIX):
        print(
            "error: that audit list is partial (the run never committed); nothing to revert",
            file=sys.stderr,
        )
        return 1
    with args.revert.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if args.backup is None or args.backup.exists():
        print("error: --revert needs --backup PATH naming a new file", file=sys.stderr)
        return 1
    with (
        closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con,
        _stage("fingerprint"),
    ):
        expected = _fingerprint(con)
    _warn_live_writer(db_path)
    _check_idle(db_path)
    with _stage("backup, check and sha256"):
        sha = _backup(db_path, args.backup, expected)
    print(f"backup {args.backup} sha256 {sha}")
    with _stage("revert and sync"):
        quarantined, released = revert(db_path, rows, expected)
    print(f"reverted from {len(rows)} listed rows; quarantined {quarantined}, released {released}")
    with _stage("verify"):
        problems = _verify_revert(db_path, rows)
    return _report_problems(problems, "--revert") if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
