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
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import cast

from portfolio_common.db import Database

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


_DURATIONS: dict[str, float] = {}  # stage name -> seconds, for the run summary


@contextmanager
def _stage(name: str) -> Iterator[None]:
    """Print how long a stage took (stderr), so a slow step of the real run is seen as it happens, and
    keep it for the run summary."""
    start = time.monotonic()
    try:
        yield
    finally:
        _DURATIONS[name] = round(time.monotonic() - start, 1)
        print(f"stage {name}: {_DURATIONS[name]:.1f}s", file=sys.stderr)


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
    "SELECT id, fetch_status FROM articles"
    " WHERE (fetch_status IS NULL OR fetch_status != 'quarantined')"
    " AND id IN (SELECT id FROM discovered_urls WHERE match_class != 'accept')"
)
_TO_RELEASE = (
    "SELECT id, fetch_status, prev_fetch_status FROM articles"
    " WHERE (fetch_status = 'quarantined' OR prev_fetch_status IS NOT NULL)"
    " AND id IN (SELECT id FROM discovered_urls WHERE match_class = 'accept')"
)


def _ok_articles(con: sqlite3.Connection) -> int:
    """Articles `portfolio-nlp` reads (`fetch_status = 'ok'`): the set the run must not change by
    accident."""
    return int(con.execute("SELECT COUNT(*) FROM articles WHERE fetch_status = 'ok'").fetchone()[0])


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
    to_quarantine = {r[0]: r[1] for r in con.execute(_TO_QUARANTINE)}  # id -> fetch_status
    to_release = {r[0]: (r[1], r[2]) for r in con.execute(_TO_RELEASE)}  # id -> (status, prev)
    accepted = {i for i, cls in ids.items() if cls == "accept"}
    for i in accepted:
        if _is_quarantined(info[i]):
            to_release.setdefault(i, (info[i]["fetch_status"], info[i]["prev_fetch_status"]))
    quarantined_ok = sum(1 for i, st in to_quarantine.items() if i not in accepted and st == "ok")
    released_ok = sum(1 for st, prev in to_release.values() if st == "quarantined" and prev == "ok")
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
            "quarantined": len(set(to_quarantine) - accepted),
            "released": len(to_release),
        },
        "ok_articles": _ok_articles(con),
        "predicted_ok_articles_delta": released_ok - quarantined_ok,
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


def _load_list(path: Path) -> list[dict[str, str]]:
    """The rows of an audit list file; exit when it is partial, unreadable or has other columns."""
    if path.name.endswith(PARTIAL_SUFFIX):
        raise SystemExit(
            f"{path} is partial (the run never committed); there is nothing to revert."
        )
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            rows = list(reader)
    except OSError as exc:
        raise SystemExit(f"cannot read the audit list {path}: {exc}.") from exc
    if set(header) != set(LIST_FIELDS):
        raise SystemExit(
            f"{path} is not an audit list from this script: columns {header}, expected {LIST_FIELDS}."
        )
    return rows


def _row_problems(rows: list[dict[str, str]]) -> tuple[list[int], list[str]]:
    """The integer ids of the rows and one line per row that is not a recorded promotion."""
    ids: list[int] = []
    problems: list[str] = []
    for n, r in enumerate(rows, 2):  # line numbers as in the file
        try:
            ids.append(int(r["id"]))
        except ValueError:
            problems.append(f"line {n}: id {r['id']!r} is not an integer")
            continue
        old, new = r["old_class"], r["new_class"]
        if old not in CLASS_RANK or new not in CLASS_RANK or CLASS_RANK[old] >= CLASS_RANK[new]:
            problems.append(f"line {n}: class {old!r} -> {new!r} is not a promotion")
        if not all(r[k] for k in ("old_reason", "new_reason", "old_status", "new_status")):
            problems.append(f"line {n}: a reason or status is empty")
    return ids, problems


def _read_list(path: Path, db_path: Path) -> list[dict[str, str]]:
    """Read and validate an audit list before anything is touched (fingerprint, idle check, backup):
    exit with every kind of problem found, one line each, when it is not a list this script wrote for this
    database. Nothing is written."""
    rows = _load_list(path)
    ids, problems = _row_problems(rows)
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"duplicated ids: {dupes[:5]}")
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        current = _rows_of(con, ids)
    if missing := sorted(set(ids) - set(current)):
        problems.append(
            f"{len(missing)} id(s) not in {db_path.name} (a list from another database?): {missing[:5]}"
        )
    if other := [
        r["id"]
        for r in rows
        if r["id"].isdigit()
        and int(r["id"]) in current
        and (current[int(r["id"])]["ticker"], current[int(r["id"])]["url"])
        != (r["ticker"], r["url"])
    ]:
        problems.append(
            f"{len(other)} id(s) are another pair in {db_path.name} (ticker or url differ; a list from "
            f"another database?): {other[:5]}"
        )
    if problems:
        shown = "\n  ".join(problems[:10])
        raise SystemExit(f"the audit list {path} is not valid; nothing was written:\n  {shown}")
    return rows


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
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(backup.resolve().parent).free
    except OSError as exc:
        raise SystemExit(
            f"cannot create or use the backup directory {backup.parent}: {exc}."
        ) from exc
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
    write: Callable[[sqlite3.Connection], int],
    on_commit: Callable[[], None] | None = None,
) -> int:
    """Run `write(con)` in one `BEGIN IMMEDIATE` transaction, after checking that the database is
    still what the plan saw (inside the write lock, so nothing can move between check and write).
    `on_commit` runs right after the commit, before anything else can fail. Returns the number of rows
    `write` changed (what the `UPDATE`s reported, not what the plan hoped)."""
    con = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        if _fingerprint(con) != expected:
            con.execute("ROLLBACK")
            raise SystemExit(
                "the database changed since the plan was computed; nothing was written."
            )
        changed = write(con)
        con.execute("COMMIT")
    finally:
        con.close()
    if on_commit is not None:
        on_commit()
    return changed


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
) -> tuple[int, int, int]:
    """Write the promotions in one transaction, then sync the quarantine. Returns
    `(quarantined, released, changed)`: the sync's counts and the rows the `UPDATE`s changed. Rolls
    back and writes nothing if the database is no longer the `expected` fingerprint, checked inside
    the write lock."""

    def write(con: sqlite3.Connection) -> int:
        changed = 0
        for i, cls, reason in promotions:
            changed += (
                con.execute(  # `AND match_class = 'reject'`: a row that moved is left as it is
                    "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                    " status = CASE WHEN status = 'skipped' THEN 'pending' ELSE status END"
                    " WHERE id = ? AND match_class = 'reject'",
                    (cls, reason, i),
                ).rowcount
            )
        return changed

    changed = _in_transaction(db_path, expected, write, on_commit)
    return (*_sync(db_path), changed)


def revert(
    db_path: Path, rows: list[dict[str, str]], expected: Fingerprint
) -> tuple[int, int, int]:
    """Undo a run from its audit list: a row returns to its old class, reason and status only if it
    is the listed pair (id, ticker and url) and still holds what the run wrote; anything else is left
    alone. Then the quarantine sync. Returns `(quarantined, released, changed)`."""

    def write(con: sqlite3.Connection) -> int:
        changed = 0
        for r in rows:
            changed += con.execute(
                "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                " status = CASE WHEN status = ? THEN ? ELSE status END"
                " WHERE id = ? AND ticker = ? AND url = ? AND match_class = ? AND match_reason = ?",
                (
                    r["old_class"],
                    r["old_reason"],
                    r["new_status"],
                    r["old_status"],
                    r["id"],
                    r["ticker"],
                    r["url"],
                    r["new_class"],
                    r["new_reason"],
                ),
            ).rowcount
        return changed

    changed = _in_transaction(db_path, expected, write)
    return (*_sync(db_path), changed)


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
    result: tuple[int, int, int],
    predicted: dict[str, int],
    ok_before: int,
    ok_delta: int,
) -> tuple[list[str], dict[str, object]]:
    """Read the database back after `apply`. Returns what does not hold (empty when all does) and the
    state after: the `UPDATE`s changed exactly the planned rows, the rows per class moved by exactly the
    promotions, none of them is still `reject`, the sync printed what the dry run predicted, no
    `accept` pair keeps a quarantined article, and the articles `portfolio-nlp` reads (`ok`) changed
    by exactly what the dry run predicted (nothing, when no pair becomes `accept`)."""
    problems: list[str] = []
    if result[2] != len(promotions):
        problems.append(f"the updates changed {result[2]} row(s), the plan had {len(promotions)}")
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        after_fp = _fingerprint(con)
        now = _class_counts(after_fp)
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
        ok_after = _ok_articles(con)
    if result[:2] != (predicted["quarantined"], predicted["released"]):
        problems.append(
            f"the sync did (quarantined, released) = {result[:2]}, the dry run predicted "
            f"({predicted['quarantined']}, {predicted['released']})"
        )
    if ok_after - ok_before != ok_delta:
        problems.append(
            f"the articles portfolio-nlp reads (ok) went {ok_before} -> {ok_after}, "
            f"the dry run predicted a change of {ok_delta}"
        )
    return problems, {
        "pairs": cast(tuple[int, int], after_fp[0])[0],
        "articles": cast(tuple[int, int], after_fp[1])[0],
        "classes": now,
        "ok_articles": ok_after,
    }


def _revert_ok_delta(rows: list[dict[str, str]], current: dict[int, sqlite3.Row]) -> int:
    """The change a revert will make to the `ok` articles: a listed row that is `accept` and still
    holds what the run wrote goes back to a lower class, so the sync quarantines its `ok` article."""
    return -sum(
        1
        for r in rows
        if int(r["id"]) in current
        and r["new_class"] == "accept"
        and (current[int(r["id"])]["match_class"], current[int(r["id"])]["match_reason"])
        == (r["new_class"], r["new_reason"])
        and current[int(r["id"])]["fetch_status"] == "ok"
    )


def _verify_revert(
    db_path: Path, rows: list[dict[str, str]], ok_before: int, ok_delta: int
) -> tuple[list[str], dict[str, object]]:
    """After `revert`: no listed row still holds what the run wrote, no `accept` pair keeps a
    quarantined article, and the `ok` articles changed by exactly what the revert implies."""
    problems: list[str] = []
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        current = _rows_of(con, [int(r["id"]) for r in rows])
        if gone := [r["id"] for r in rows if int(r["id"]) not in current]:
            problems.append(f"{len(gone)} listed id(s) are no longer in the database: {gone[:5]}")
        left = [
            r["id"]
            for r in rows
            if int(r["id"]) in current
            and (current[int(r["id"])]["match_class"], current[int(r["id"])]["match_reason"])
            == (r["new_class"], r["new_reason"])
        ]
        if left:
            problems.append(f"{len(left)} listed row(s) still hold what the run wrote: {left[:5]}")
        if held := _held_accepts(con):
            problems.append(f"{held} `accept` pair(s) still have a quarantined article")
        ok_after = _ok_articles(con)
        classes = _class_counts(_fingerprint(con))
    if ok_after - ok_before != ok_delta:
        problems.append(
            f"the articles portfolio-nlp reads (ok) went {ok_before} -> {ok_after}, "
            f"a revert should change them by {ok_delta}"
        )
    return problems, {"classes": classes, "ok_articles": ok_after}


def _library_version() -> str:
    try:
        return importlib.metadata.version("portfolio-common")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


MIN_LIBRARY = (1, 2, 2)


def _library_problem() -> str | None:
    """Why the installed `portfolio-common` cannot be trusted with the reader check, or None.

    Checked before any write: a run that finds out afterwards has taken the backup and committed."""
    version = _library_version()
    parts = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if parts is None:
        return f"portfolio-common is {version}; run `uv sync` (needs >= 1.2.2)"
    if tuple(int(x) for x in parts.groups()) < MIN_LIBRARY:
        return f"portfolio-common {version} is installed; run `uv sync` (needs >= 1.2.2)"
    return None


def _reader_check(
    db_path: Path, expected: Fingerprint, ok_articles: int
) -> tuple[list[str], dict[str, object]]:
    """Attach the file read-only the way `portfolio-nlp` does (`Database.attach(read_only=True)` of the
    `portfolio-common` installed here), count the pairs and the readable (`ok`) articles through that
    attach and compare them with the fingerprint and the post-run count. This is the failure that made
    `urls.db` unreachable while it was in WAL mode, so a run that leaves the file in a state a reader
    refuses must not print success."""
    rows = cast(tuple[int, int], expected[0])[0]
    version = _library_version()
    info: dict[str, object] = {"portfolio_common": version, "ok": False}
    try:
        with closing(Database.connect(":memory:")) as db:
            db.attach(db_path, "urls", read_only=True)
            seen = db.execute("SELECT COUNT(*) FROM urls.discovered_urls").fetchone()[0]
            readable = db.execute(
                "SELECT COUNT(*) FROM urls.articles WHERE fetch_status = 'ok'"
            ).fetchone()[0]
    except (RuntimeError, sqlite3.Error) as exc:
        info["error"] = str(exc)
        return [
            f"a reader cannot attach {db_path.name} read-only (portfolio-common {version}): {exc}"
        ], info
    print(
        f"reader check: attach(read_only=True) ok, {seen} rows, {readable} readable articles "
        f"(portfolio-common {version})"
    )
    info.update(pairs=seen, readable_articles=readable)
    problems = []
    if seen != rows:
        problems.append(f"the reader sees {seen} pairs, the fingerprint had {rows}")
    if readable != ok_articles:
        problems.append(
            f"the reader sees {readable} readable articles, a direct count gave {ok_articles}"
        )
    info["ok"] = not problems
    return problems, info


def _not_a_database(db_path: Path) -> str | None:
    """A one-line reason when `db_path` cannot be read as a SQLite database, else None."""
    try:
        with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
            con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        return f"{db_path} is not a readable SQLite database ({exc})"
    return None


def _checkpoint_command(db_path: Path) -> str:
    """The shell command that truncates the WAL with Python: the `sqlite3` CLI is not installed on the machines
    that run this."""
    code = f"import sqlite3; print(sqlite3.connect({str(db_path.resolve())!r}).execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())"
    return shlex.join(["python3", "-c", code])


def _checkpoint(db_path: Path) -> None:
    """Leave the database checkpointed: the last connection to close after a run is read-only (the
    verification) and cannot fold the WAL into the file, so `-wal` would stay non-empty and a reader
    on an older `portfolio-common` refuses to attach. `TRUNCATE` empties it; a reader holding the log
    only delays that, which is reported, not an error."""
    try:
        with closing(sqlite3.connect(db_path, timeout=0, isolation_level=None)) as con:
            if con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                print("checkpoint: not in WAL mode, nothing to truncate", file=sys.stderr)
                return
            busy, log, done = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.OperationalError as exc:
        print(
            f"warning: could not checkpoint the WAL ({exc}); it will clear when readers close.",
            file=sys.stderr,
        )
        return
    if busy:
        print(
            "warning: a reader holds the WAL, so it was not truncated; it will clear later.",
            file=sys.stderr,
        )
    else:
        print(f"checkpoint: WAL truncated ({done} of {log} frames folded in)", file=sys.stderr)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _summary_path(explicit: Path | None, anchor: Path, mode: str) -> Path:
    """Where the run summary goes: `--summary`, or next to the audit list (git-ignored `data/retag/`)
    under a name with the run's time, so a recovery run never collides with the first one's record."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return (explicit or anchor.parent / f"reclass_{mode}_summary_{stamp}.json").resolve()


def _check_summary(path: Path) -> None:
    """Exit before the backup when the summary exists (custody) or its place is not writable."""
    if path.exists():
        raise SystemExit(f"the run summary {path} exists; move it or pass another --summary.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(path.parent, os.W_OK)
    except OSError as exc:
        raise SystemExit(f"cannot write the run summary {path}: {exc}.") from exc
    if not writable:
        raise SystemExit(f"cannot write the run summary {path}: directory not writable.")


def _write_summary(path: Path, summary: dict[str, object]) -> None:
    """The record of the run for the hand-off and the plan (the counterpart of `retag_summary.json`);
    written whether or not the post-conditions held, with the problems in it."""
    summary["finished_at"] = _now()
    summary["stage_seconds"] = dict(_DURATIONS)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"run summary {path}")


def _finish(
    data_problems: list[str],
    reader_problems: list[str],
    what: str,
    db_path: Path,
    backup: Path | None,
    listing: Path,
) -> int:
    """Print the problems by kind with the remedy that fits each, and return the exit code. A data
    problem means the database is not as planned; a reader problem means the data is as planned but
    the file is not readable by a reader, which a revert does not fix."""
    for problem in data_problems + reader_problems:
        print(f"POST-CONDITION FAILED: {problem}", file=sys.stderr)
    if data_problems and what == "--apply":
        print(
            "error: --apply did not leave the database as planned; the changes are committed. "
            f"To undo them: --revert {listing.resolve()} --backup <a new path> "
            f"(the backup taken before the run is {backup.resolve() if backup else 'none: nothing to promote'}).",
            file=sys.stderr,
        )
    elif data_problems:
        print(
            "error: --revert did not complete as planned; the backup taken before it is "
            f"{backup.resolve() if backup else 'unknown'}; restore it only if nothing has run since.",
            file=sys.stderr,
        )
    if reader_problems:
        print(
            "error: the file is not readable by a reader"
            + (
                " (and see the data problems above)"
                if data_problems
                else "; the data is as planned"
            )
            + f". With the collector and every reader stopped run: {_checkpoint_command(db_path)} "
            "(prints (0, n, n) when done) or `uv sync` to portfolio-common >= 1.2.2. "
            "No revert is needed for this.",
            file=sys.stderr,
        )
    return 1 if (data_problems or reader_problems) else 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=resolve_db_path())
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--revert", type=Path, metavar="LIST", help="undo a run from its audit list")
    ap.add_argument("--backup", type=Path)
    ap.add_argument(
        "--list", type=Path, help=f"audit list `--apply` writes (default {DEFAULT_LIST})"
    )
    ap.add_argument(
        "--summary",
        type=Path,
        help="run summary (default: next to the audit list or the revert list)",
    )
    args = ap.parse_args(argv)
    if args.revert and args.list:
        ap.error("--list is for --apply; --revert reads the list it is given")
    args.list = args.list or Path(DEFAULT_LIST)
    return args


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse(argv)
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"error: the database {db_path} does not exist (pass --db PATH)", file=sys.stderr)
        return 1
    if reason := _not_a_database(db_path):
        print(f"error: {reason}", file=sys.stderr)
        return 1
    if problem := _library_problem():
        if args.apply or args.revert:
            print(f"error: {problem}", file=sys.stderr)
            return 1
        print(f"warning: {problem}; --apply and --revert would refuse", file=sys.stderr)
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
    return _apply_main(db_path, args, expected, promotions, details, audit)


def _apply_main(
    db_path: Path,
    args: argparse.Namespace,
    expected: Fingerprint,
    promotions: list[Promotion],
    details: dict[str, object],
    audit: list[dict[str, object]],
) -> int:
    if args.backup is not None and args.backup.exists():
        print("error: --backup names a file that exists; pick a new path", file=sys.stderr)
        return 1
    if promotions and args.backup is None:
        print("error: --apply needs --backup PATH naming a new file", file=sys.stderr)
        return 1
    summary_path = _summary_path(args.summary, args.list, "apply")
    if promotions:
        _check_list(args.list)  # before the copy: a missing list place must not cost a 2 GB backup
    _check_summary(summary_path)
    started = _now()
    _warn_live_writer(db_path)
    _check_idle(db_path)
    sha = None
    if promotions and args.backup is not None:
        with _stage("backup, check and sha256"):
            sha = _backup(db_path, args.backup, expected)
        print(f"backup {args.backup.resolve()} sha256 {sha}")
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
        print(f"audit list {args.list.resolve()} ({len(audit)} rows)")
    print(f"promoted {result[2]} rows; quarantined {result[0]}, released {result[1]}")
    with _stage("verify"):
        problems, after = _verify_apply(
            db_path,
            promotions,
            expected,
            result,
            cast(dict[str, int], details["predicted_sync"]),
            cast(int, details["ok_articles"]),
            cast(int, details["predicted_ok_articles_delta"]),
        )
    _checkpoint(db_path)
    reader_problems, reader = _reader_check(db_path, expected, cast(int, after["ok_articles"]))
    _write_summary(
        summary_path,
        {
            "mode": "apply",
            "started_at": started,
            "database": {"path": str(db_path.resolve()), "bytes": db_path.stat().st_size},
            "portfolio_common": _library_version(),
            "before": {
                "pairs": cast(tuple[int, int], expected[0])[0],
                "articles": cast(tuple[int, int], expected[1])[0],
                "classes": _class_counts(expected),
                "ok_articles": details["ok_articles"],
            },
            "after": after,
            "promotions": len(promotions),
            "rows_changed": result[2],
            "predicted_sync": details["predicted_sync"],
            "sync": {"quarantined": result[0], "released": result[1]},
            "predicted_ok_articles_delta": details["predicted_ok_articles_delta"],
            "backup": {"path": str(args.backup.resolve()) if args.backup else None, "sha256": sha},
            "audit_list": {
                "path": str(args.list.resolve()) if promotions else None,
                "rows": len(audit),
            },
            "postconditions": {"ok": not problems, "problems": problems},
            "reader_check": {**reader, "problems": reader_problems},
        },
    )
    return _finish(problems, reader_problems, "--apply", db_path, args.backup, args.list)


def _revert_main(db_path: Path, args: argparse.Namespace) -> int:
    rows = _read_list(
        args.revert, db_path
    )  # validated before the fingerprint, the lock and the backup
    if args.backup is None or args.backup.exists():
        print("error: --revert needs --backup PATH naming a new file", file=sys.stderr)
        return 1
    summary_path = _summary_path(args.summary, args.revert, "revert")
    _check_summary(summary_path)
    started = _now()
    with (
        closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con,
        _stage("fingerprint"),
    ):
        expected = _fingerprint(con)
        ok_before = _ok_articles(con)
        ok_delta = _revert_ok_delta(rows, _rows_of(con, [int(r["id"]) for r in rows]))
    _warn_live_writer(db_path)
    _check_idle(db_path)
    with _stage("backup, check and sha256"):
        sha = _backup(db_path, args.backup, expected)
    print(f"backup {args.backup.resolve()} sha256 {sha}")
    with _stage("revert and sync"):
        quarantined, released, changed = revert(db_path, rows, expected)
    print(
        f"reverted {changed} of {len(rows)} listed rows ({len(rows) - changed} changed since and "
        f"left alone); quarantined {quarantined}, released {released}"
    )
    with _stage("verify"):
        problems, after = _verify_revert(db_path, rows, ok_before, ok_delta)
    _checkpoint(db_path)
    reader_problems, reader = _reader_check(db_path, expected, cast(int, after["ok_articles"]))
    _write_summary(
        summary_path,
        {
            "mode": "revert",
            "started_at": started,
            "database": {"path": str(db_path.resolve()), "bytes": db_path.stat().st_size},
            "portfolio_common": _library_version(),
            "before": {
                "pairs": cast(tuple[int, int], expected[0])[0],
                "articles": cast(tuple[int, int], expected[1])[0],
                "classes": _class_counts(expected),
                "ok_articles": ok_before,
            },
            "after": after,
            "listed_rows": len(rows),
            "rows_changed": changed,
            "sync": {"quarantined": quarantined, "released": released},
            "expected_ok_articles_delta": ok_delta,
            "backup": {"path": str(args.backup.resolve()), "sha256": sha},
            "audit_list": {"path": str(args.revert.resolve()), "rows": len(rows)},
            "postconditions": {"ok": not problems, "problems": problems},
            "reader_check": {**reader, "problems": reader_problems},
        },
    )
    return _finish(problems, reader_problems, "--revert", db_path, args.backup, args.revert)


if __name__ == "__main__":
    raise SystemExit(main())
