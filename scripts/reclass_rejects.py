"""Re-class the `reject` pairs with the current matcher and promote the ones it no longer rejects (T-062).

The T-057 re-tag only touched rows with `match_reason IS NULL`, so the pairs the matcher rejected then
keep `match_class = 'reject'` after a matcher fix. This classes every `reject` row of `discovered_urls`
again (URL, and the best title known: the discovery title, else the fetched article's) and writes the
new class and reason where it is higher: a stored class is only ever promoted. A `skipped` row that is
promoted goes back to `pending` (it was never fetched because of the reject). Then `sync_quarantine`
releases any article whose pair is now `accept`; one that becomes `review` stays quarantined.

Default is a dry run (read-only). `--apply` needs `--backup PATH` (a consistent copy, never overwriting
a file) and stops the run if the row counts moved since the plan; stop the collector and the crawler
first. Idempotent: a second run finds nothing to promote.

Usage:
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db COPY.db
    PYTHONPATH=src uv run python scripts/reclass_rejects.py --db urls.db --apply --backup urls.pre-reclass.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

from data_mining.db import connect, resolve_db_path
from data_mining.matching import CLASS_RANK, classify
from data_mining.quarantine import sync_quarantine

Promotion = tuple[int, str, str]  # (discovered_urls.id, new class, new reason)


def plan(con: sqlite3.Connection) -> list[Promotion]:
    """The `reject` rows the current matcher classes higher."""
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


def _counts(db_path: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as con:
        return (
            con.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        )


def apply(db_path: Path, promotions: list[Promotion], expected: tuple[int, int]) -> tuple[int, int]:
    """Write the promotions in one transaction, then sync the quarantine. Returns
    `(quarantined, released)` by the sync. Aborts before writing if the counts moved."""
    if _counts(db_path) != expected:
        raise SystemExit("the database changed since the plan was computed; nothing was written.")
    db = connect(str(db_path), wal=True, foreign_keys=True)
    try:
        for i, cls, reason in promotions:
            db.execute(
                "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                " status = CASE WHEN status = 'skipped' THEN 'pending' ELSE status END"
                " WHERE id = ? AND match_class = 'reject'",
                (cls, reason, i),
            )
        db.commit()
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
        promotions = plan(con)
    print(json.dumps({"promotions": len(promotions), **summarize(promotions)}, indent=2))
    if not args.apply:
        return 0
    if args.backup is None or args.backup.exists():
        print(
            "error: --apply needs --backup PATH naming a file that does not exist", file=sys.stderr
        )
        return 1
    with (
        closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(args.backup)) as dest,
    ):
        src.backup(dest)
    quarantined, released = apply(db_path, promotions, _counts(db_path))
    print(f"promoted {len(promotions)} rows; quarantined {quarantined}, released {released}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
