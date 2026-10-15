"""Re-tag `urls.db` with the three-class matcher and quarantine what is not `accept` (T-057).

Every legacy `discovered_urls` row (`match_reason IS NULL`: stored `accept` by the old
`ticker OR first word` rule) is classed again with `data_mining.matching.classify` from the
URL and the best title known (the discovery title, else the fetched article's), the same
"re-tag mode" the scorer measures. Rows that already carry a reason (classed by the T-056c
strategies, or `feed`) are kept, so a second run changes nothing. A `pending` row that comes
out `reject` becomes `skipped` (never fetched); `fetched`/`failed` rows keep their status.
Then every fetched `review`/`reject` article is quarantined
(`data_mining.quarantine`: `fetch_status = 'quarantined'`, previous status in
`prev_fetch_status`), so `portfolio-nlp`'s `fetch_status = 'ok'` queries stop reading it.
Nothing is deleted.

Two modes, on the database named by `--db`:

* default (dry run): read-only, writes nothing to the database. Prints before/after counts per
  class, source and ticker and the number of rejected/review URLs that are also tagged to a
  correct ticker, and writes the hand-off lists to `--out`.
* `--apply`: needs `--backup PATH` (a consistent copy is written first; an existing file is
  never overwritten) and `--hand-check-passed` (the fresh 60 `accept` / 40 `reject` check of
  T-057 passed; otherwise the work goes back to T-055). Migrates the schema, re-tags (one
  transaction), quarantines (a second; both idempotent, so a re-run finishes an interrupted one), then
  then compares the database with the dry-run plan (class counts, quarantined counts, no `accept`
  article quarantined, nothing non-`accept` left readable or pending): on a mismatch it writes no lists,
  marks the summary `verified: false` and exits 1. Otherwise it writes the same lists.

Always run it on a copy first. Usage:
    PYTHONPATH=src uv run python scripts/retag_urls.py --db COPY.db [--out DIR]
    PYTHONPATH=src uv run python scripts/retag_urls.py --db urls.db --apply \
        --backup urls.pre-retag.db --hand-check-passed [--out DIR]

Outputs in `--out` (default `data/retag`): `removed_articles.csv` (fetched `reject` pairs: article
id, ticker, company, url, class, reason), `review_quarantine.csv` (fetched `review` pairs, the T-058 selection
plus class evidence) and `retag_summary.json`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

from data_mining.db import connect, resolve_db_path
from data_mining.matching import classify
from data_mining.quarantine import quarantine_articles
from data_mining.schema import apply_schema, run_migrations

FEED_REASON = "feed"
CLASSES = ("accept", "review", "reject")
DEFAULT_OUT = Path("data/retag")
_LIST_HEADER = (
    "article_id",
    "ticker",
    "company",
    "url",
    "match_class",
    "match_reason",
    "prev_fetch_status",  # `ok` = a readable body; `failed`/`thin_content` rows cannot be judged
)


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in con.execute(f"PRAGMA table_info({table})"))


def _legacy_rows(con: sqlite3.Connection) -> Iterator[sqlite3.Row]:
    """Rows still to re-tag: no reason recorded yet."""
    con.row_factory = sqlite3.Row
    # a schema-1 database (the real corpus before this run) has no match columns: every row is legacy
    legacy = (
        "d.match_reason IS NULL" if _has_column(con, "discovered_urls", "match_reason") else "1"
    )
    join = "LEFT JOIN articles a ON a.id = d.id" if _has_table(con, "articles") else ""
    fetched = "a.title" if join else "NULL"
    yield from con.execute(
        "SELECT d.id, d.url, d.ticker, d.company, d.source, d.status,"  # noqa: S608
        f" COALESCE(NULLIF(d.title, ''), {fetched}, '') AS title"
        f" FROM discovered_urls d {join} WHERE {legacy}"
    )


def retag_plan(con: sqlite3.Connection) -> list[tuple[int, str, str, str | None]]:
    """`(id, class, reason, new_status)` for every legacy row; `new_status` is `skipped` for
    a `pending` row that comes out `reject`, else None (status untouched)."""
    plan = []
    for r in _legacy_rows(con):
        if r["source"] == "api":
            cls, reason = "accept", FEED_REASON  # ticker-keyed feeds, accept by construction
        else:
            m = classify(r["url"], r["title"], r["company"], r["ticker"])
            cls, reason = m.cls, m.reason
        skip = "skipped" if cls == "reject" and r["status"] == "pending" else None
        plan.append((r["id"], cls, reason, skip))
    return plan


def summarize(con: sqlite3.Connection, plan: list[tuple[int, str, str, str | None]]) -> dict:
    """Before/after counts for the planned re-tag, from a read-only scan."""
    cls_col = "match_class" if _has_column(con, "discovered_urls", "match_class") else "'accept'"
    meta = {
        r[0]: r[1:]
        for r in con.execute(
            f"SELECT id, ticker, source, status, {cls_col}, url FROM discovered_urls"  # noqa: S608
        )
    }
    new_class = {i: cls for i, cls, _r, _s in plan}
    after = {i: new_class.get(i, m[3]) for i, m in meta.items()}
    out: dict[str, Any] = {"rows": len(meta), "retagged": len(plan)}
    for axis, pos in (("class", None), ("source", 1), ("ticker", 0)):
        before_c: Counter[tuple[str, str]] = Counter()
        after_c: Counter[tuple[str, str]] = Counter()
        for i, m in meta.items():
            key = "all" if pos is None else m[pos]
            before_c[(key, m[3])] += 1
            after_c[(key, after[i])] += 1
        out[f"by_{axis}"] = {
            key: {
                "before": {c: before_c[(key, c)] for c in CLASSES},
                "after": {c: after_c[(key, c)] for c in CLASSES},
            }
            for key in sorted({k for k, _ in before_c} | {k for k, _ in after_c})
        }
    # URLs whose quarantined pair shares the URL with a pair that stays `accept`
    accepted_urls = {m[4] for i, m in meta.items() if after[i] == "accept"}
    out["non_accept_urls_also_accepted"] = {
        c: len({m[4] for i, m in meta.items() if after[i] == c and m[4] in accepted_urls})
        for c in ("review", "reject")
    }
    fetched = (
        {r[0] for r in con.execute("SELECT id FROM articles")}
        if _has_table(con, "articles")
        else set()
    )
    out["articles_to_quarantine"] = {
        c: sum(1 for i in fetched if after.get(i) == c) for c in ("review", "reject")
    }
    out["articles_to_quarantine_by_prev_status"] = _by_prev_status(con, after)
    out["pending_rejects_to_skip"] = sum(1 for p in plan if p[3])
    return out


def _by_prev_status(con: sqlite3.Connection, after: dict[int, str]) -> dict[str, dict[str, int]]:
    """Fetched `review`/`reject` articles per status they had before the quarantine."""
    if not _has_table(con, "articles"):
        return {}
    prev = "prev_fetch_status" if _has_column(con, "articles", "prev_fetch_status") else "NULL"
    counts: dict[str, Counter[str]] = {"review": Counter(), "reject": Counter()}
    for aid, status, was in con.execute(f"SELECT id, fetch_status, {prev} FROM articles"):  # noqa: S608
        if after.get(aid) in counts:
            counts[after[aid]][str(was if status == "quarantined" else status)] += 1
    return {c: dict(sorted(n.items())) for c, n in counts.items()}


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        is not None
    )


def write_lists(con: sqlite3.Connection, out: Path) -> dict[str, int]:
    """The hand-off lists, from the quarantined articles (run after `--apply`) or, on a dry run,
    from the planned classes via `plan_lists`."""
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, cls in (("removed_articles.csv", "reject"), ("review_quarantine.csv", "review")):
        rows = con.execute(
            "SELECT a.id, d.ticker, d.company, d.url, d.match_class, d.match_reason,"
            " a.prev_fetch_status FROM articles a"
            " JOIN discovered_urls d ON d.id = a.id"
            " WHERE a.fetch_status = 'quarantined' AND d.match_class = ? ORDER BY a.id",
            (cls,),
        ).fetchall()
        with (out / name).open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(_LIST_HEADER)
            w.writerows(rows)
        counts[name] = len(rows)
    return counts


def plan_lists(
    con: sqlite3.Connection, plan: list[tuple[int, str, str, str | None]], out: Path
) -> dict[str, int]:
    """Dry-run lists: the same files, from the planned class of each fetched article."""
    out.mkdir(parents=True, exist_ok=True)
    new = {i: (cls, reason) for i, cls, reason, _s in plan}
    has = _has_column(con, "discovered_urls", "match_class")
    stored = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            f"SELECT id, {'match_class, match_reason' if has else chr(39) + 'accept' + chr(39) + ', NULL'} FROM discovered_urls"  # noqa: S608
        )
    }
    counts = {}
    rows_by_cls: dict[str, list[tuple]] = {"reject": [], "review": []}
    prev = "a.prev_fetch_status" if _has_column(con, "articles", "prev_fetch_status") else "NULL"
    for aid, ticker, company, url, status, was in con.execute(
        f"SELECT a.id, d.ticker, d.company, d.url, a.fetch_status, {prev} FROM articles a"  # noqa: S608
        " JOIN discovered_urls d ON d.id = a.id ORDER BY a.id"
    ):
        cls, reason = new.get(aid, stored[aid])
        if cls in rows_by_cls:  # what the article will have been before the quarantine
            rows_by_cls[cls].append(
                (aid, ticker, company, url, cls, reason, was if status == "quarantined" else status)
            )
    for name, cls in (("removed_articles.csv", "reject"), ("review_quarantine.csv", "review")):
        with (out / name).open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(_LIST_HEADER)
            w.writerows(rows_by_cls[cls])
        counts[name] = len(rows_by_cls[cls])
    return counts


def backup(src: Path, dest: Path) -> None:
    """Consistent copy through the SQLite backup API; never overwrites."""
    if dest.exists():
        raise SystemExit(f"backup {dest} already exists; refusing to overwrite it")
    with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(dest)) as d:
        s.backup(d)
    with closing(sqlite3.connect(dest)) as d:
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise SystemExit(f"backup {dest} failed its integrity check: {ok}")


def apply(db_path: Path, plan: list[tuple[int, str, str, str | None]]) -> int:
    """Migrate, re-tag and quarantine. Returns the articles quarantined."""
    db = connect(str(db_path), wal=True, foreign_keys=True)
    try:
        apply_schema(db)
        run_migrations(db)
        db.commit()
        for i, cls, reason, new_status in plan:
            db.execute(
                "UPDATE discovered_urls SET match_class = ?, match_reason = ?,"
                " status = COALESCE(?, status) WHERE id = ? AND match_reason IS NULL",
                (cls, reason, new_status, i),
            )
        db.commit()
        return quarantine_articles(db)
    finally:
        db.close()


def verify(db_path: Path, summary: dict) -> list[str]:
    """Compare the database after `--apply` with the plan the dry-run scan produced; return the
    mismatches (empty when it agrees). Read-only."""
    problems: list[str] = []
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        got = dict(con.execute("SELECT match_class, COUNT(*) FROM discovered_urls GROUP BY 1"))
        want = summary["by_class"]["all"]["after"]
        for c in CLASSES:
            if got.get(c, 0) != want[c]:
                problems.append(f"class {c}: {got.get(c, 0)} rows, plan said {want[c]}")
        quarantined = con.execute(
            "SELECT d.match_class, COUNT(*) FROM articles a JOIN discovered_urls d ON d.id = a.id"
            " WHERE a.fetch_status = 'quarantined' GROUP BY 1"
        ).fetchall()
        quarantined_by = dict(quarantined)
        for c in ("review", "reject"):
            planned = summary["articles_to_quarantine"][c]
            if quarantined_by.get(c, 0) != planned:
                problems.append(
                    f"{c} articles quarantined: {quarantined_by.get(c, 0)}, plan said {planned}"
                )
        if quarantined_by.get("accept"):
            problems.append(f"{quarantined_by['accept']} accept article(s) are quarantined")
        loose = con.execute(
            "SELECT COUNT(*) FROM articles a JOIN discovered_urls d ON d.id = a.id"
            " WHERE d.match_class != 'accept' AND a.fetch_status != 'quarantined'"
        ).fetchone()[0]
        if loose:
            problems.append(f"{loose} non-accept article(s) are not quarantined")
        pending = con.execute(
            "SELECT COUNT(*) FROM discovered_urls WHERE match_class = 'reject' AND status = 'pending'"
        ).fetchone()[0]
        if pending:
            problems.append(f"{pending} rejected row(s) are still pending")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=resolve_db_path())
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup", type=Path)
    ap.add_argument("--hand-check-passed", action="store_true")
    args = ap.parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"{db_path} does not exist")
    if args.apply and not (args.backup and args.hand_check_passed):
        raise SystemExit("--apply needs --backup PATH and --hand-check-passed (T-057)")

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        plan = retag_plan(con)
        summary = summarize(con, plan)
        if not args.apply:
            summary["lists"] = plan_lists(con, plan, args.out)
    if args.apply:
        backup(db_path, args.backup)
        summary["quarantined_now"] = apply(db_path, plan)
        summary["problems"] = verify(db_path, summary)
        summary["verified"] = not summary["problems"]
        if summary["verified"]:
            with closing(sqlite3.connect(db_path)) as con:
                summary["lists"] = write_lists(con, args.out)
    summary["mode"] = "apply" if args.apply else "dry-run"
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "retag_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("by_source", "by_ticker")},
            indent=2,
            sort_keys=True,
        )
    )
    if args.apply and not summary["verified"]:
        print(
            "VERIFICATION FAILED: the database does not match the plan; no lists were written."
            f" Restore {args.backup} or investigate: {summary['problems']}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
