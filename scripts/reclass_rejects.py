"""Re-class the `reject` pairs with the current matcher and promote the ones it no longer rejects (T-062).

The T-057 re-tag only touched rows with `match_reason IS NULL`, so the pairs the matcher rejected then
keep `match_class = 'reject'` after a matcher fix. This classes every `reject` row of `discovered_urls`
again (URL, and the best title known: the discovery title, else the fetched article's) and writes the
new class and reason where it is higher: a stored class is only ever promoted. A `skipped` row that is
promoted goes back to `pending` (it was never fetched because of the reject). Then `sync_quarantine`
releases any article whose pair is now `accept`; one that becomes `review` stays quarantined.

Default is a dry run (read-only; a few minutes on the real 2 GB database, which is read cold): it prints the promotions, the status of the promoted rows, what the
quarantine sync will do, the top tickers and the `api` rows it leaves alone. `--apply` needs
`--backup PATH` (a consistent copy of the database, never overwriting a file, checked after the copy)
and refuses to start when the database is busy or the disk cannot hold the copy. Stop the collector and
the crawler first: the row counts taken with the plan must still hold inside the write transaction, or
nothing is written. Idempotent: a second run finds nothing to promote.

If the process dies after the promotions are committed but before the quarantine sync, run `--apply`
again without `--backup`: with nothing left to promote it needs no backup and only runs the sync.

Usage:
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db COPY.db
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db --apply --backup urls.pre-reclass.db
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

from data_mining.db import connect, resolve_db_path
from data_mining.matching import CLASS_RANK, classify
from data_mining.quarantine import sync_quarantine

Promotion = tuple[int, str, str]  # (discovered_urls.id, new class, new reason)
Counts = tuple[int, int]  # (discovered_urls rows, articles rows)
TOP_TICKERS = 10
BACKUP_HEADROOM = 1.1  # the copy needs the database size plus a margin


def _counts(con: sqlite3.Connection) -> Counts:
    return (
        con.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0],
        con.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
    )


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


def report(con: sqlite3.Connection, promotions: list[Promotion]) -> dict[str, object]:
    """What `--apply` would do, read from the database: the discovery status of the promoted rows
    (a `skipped` one goes back to `pending`), the tickers, and the sync's predicted
    `(quarantined, released)`, which the apply's own line must equal."""
    con.row_factory = sqlite3.Row
    ids = {p[0]: p[1] for p in promotions}
    marks = ",".join("?" * len(ids))
    info = {
        r["id"]: r
        for r in con.execute(
            "SELECT d.id, d.status, d.ticker, a.fetch_status FROM discovered_urls d"  # noqa: S608
            f" LEFT JOIN articles a ON a.id = d.id WHERE d.id IN ({marks})",
            list(ids),
        )
    }
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
            "released": len(to_release | {i for i in accepted if i in info}),
        },
        "api_rejects_left_alone": api_rejects,
    }


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


def _backup(db_path: Path, backup: Path, expected: Counts) -> None:
    """Copy the database to `backup`, check the copy's counts, and remove a failed copy."""
    needed = int(db_path.stat().st_size * BACKUP_HEADROOM)
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
            copied = _counts(copy)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    if copied != expected:
        backup.unlink(missing_ok=True)
        raise SystemExit(f"the backup holds {copied} rows, the plan saw {expected}; stopped.")


def apply(db_path: Path, promotions: list[Promotion], expected: Counts) -> tuple[int, int]:
    """Write the promotions in one transaction, then sync the quarantine. Returns
    `(quarantined, released)` by the sync. Rolls back and writes nothing if the row counts are no
    longer `expected` (the counts the plan was computed on), checked inside the write lock."""
    con = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        if _counts(con) != expected:
            con.execute("ROLLBACK")
            raise SystemExit(
                "the database changed since the plan was computed; nothing was written."
            )
        for i, cls, reason in promotions:
            con.execute(  # `AND match_class = 'reject'`: a row that moved meanwhile is left as it is
                "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                " status = CASE WHEN status = 'skipped' THEN 'pending' ELSE status END"
                " WHERE id = ? AND match_class = 'reject'",
                (cls, reason, i),
            )
        con.execute("COMMIT")
    finally:
        con.close()
    db = connect(str(db_path), wal=True, foreign_keys=True)
    try:
        return sync_quarantine(db)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=resolve_db_path())
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup", type=Path)
    args = ap.parse_args(argv)
    db_path = Path(args.db)
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        expected = _counts(con)  # taken with the plan, so `apply` can tell the database moved
        promotions = plan(con)
        details = report(con, promotions)
    print(json.dumps({"promotions": len(promotions), **summarize(promotions), **details}, indent=2))
    if not args.apply:
        return 0
    if args.backup is not None and args.backup.exists():
        print("error: --backup names a file that exists; pick a new path", file=sys.stderr)
        return 1
    if promotions and args.backup is None:
        print("error: --apply needs --backup PATH naming a new file", file=sys.stderr)
        return 1
    _check_idle(db_path)
    if promotions and args.backup is not None:
        _backup(db_path, args.backup, expected)
    quarantined, released = apply(db_path, promotions, expected)
    print(f"promoted {len(promotions)} rows; quarantined {quarantined}, released {released}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
