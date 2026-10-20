"""T-073: evidence and reclaim of the never-fetched sitemap rejects (Work item 15, gate G3).

    # read-only evidence (any file; the real one is only read): what qualifies, the split, the proof
    PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py evidence --db COPY.db

    # the deletion, on a COPY first (the rehearsal), with a backup that matches it:
    PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py reclaim --db COPY.db \
        --backup COPY.pre.db --expect 578113

`reclaim` needs `--backup` (another file that passes `quick_check` and has the same fingerprint),
`--expect N` (the count the maintainer approved: a different count deletes nothing) and, on the
file `DATABASE_URL` names, `--i-have-the-g3-approval`. It refuses while the gateway or a crawler
runs. See `news_collector/reclaim_sitemap.py` and `TASKS.md` T-073.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sqlite3
import sys
from datetime import date
from pathlib import Path

from data_mining import resolve_db_path
from data_mining.env import load_env

load_env()

from news_collector.config import build_date_range, default_start_date  # noqa: E402
from news_collector.orchestrator import build_connectors  # noqa: E402
from news_collector.reclaim_sitemap import (  # noqa: E402
    PROOF_TICKERS,
    ReclaimError,
    format_evidence,
    gather_evidence,
    reclaim,
)
from news_collector.utils.http import build_client  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _tickers(db: str, extra: int, seed: int) -> list[str]:
    conn = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=60)
    try:
        pool = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT ticker FROM discovered_urls WHERE source = 'sitemap' "
                "AND match_class IN ('accept','review') ORDER BY ticker"
            )
        ]
    finally:
        conn.close()
    rest = [t for t in pool if t not in PROOF_TICKERS]
    picked = random.Random(seed).sample(rest, min(extra, len(rest)))  # noqa: S311 - not security
    return list(PROOF_TICKERS) + picked


async def cmd_evidence(args: argparse.Namespace) -> int:
    date_range = build_date_range(args.start_date, args.end_date)
    tickers = args.tickers or _tickers(args.db, args.sample, args.seed)
    async with build_client() as client:
        ev = await gather_evidence(args.db, build_connectors(client), DATA_DIR, date_range, tickers)
    print(format_evidence(ev, date_range))
    for ticker, url, cls in ev.proof.rejected_now[:20]:
        print(f"  NOW REJECTED {ticker} {url} (cache class {cls})")
    return 1 if ev.proof.rejected_now else 0


def cmd_reclaim(args: argparse.Namespace) -> int:
    db = Path(args.db)
    real = resolve_db_path(None)
    is_real = real is not None and db.resolve() == Path(real).resolve()
    if is_real and not args.g3:
        print("this is the real database: it needs --i-have-the-g3-approval (T-073, G3)")
        return 1
    try:
        done = reclaim(db, Path(args.backup), DATA_DIR, expect=args.expect, check_processes=is_real)
    except ReclaimError as exc:
        print(f"REFUSED, nothing changed: {exc}")
        return 1
    print(f"Deleted          : {done.deleted}")
    print(f"discovered_urls  : {done.before.discovered} -> {done.after.discovered}")
    print(
        f"articles         : {done.before.articles} -> {done.after.articles} (orphans {done.after.orphans})"
    )
    print(f"accept/review    : {done.before.kept_rows()} -> {done.after.kept_rows()}")
    print(
        f"File             : {done.bytes_before} -> {done.bytes_after} bytes ({done.bytes_before - done.bytes_after} reclaimed)"
    )
    print(f"Backup kept      : {done.backup} (the maintainer deletes it)")
    print(f"Elapsed          : {done.seconds:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evidence", help="read-only: what qualifies, the split and the proof")
    ev.add_argument("--db", required=True)
    ev.add_argument(
        "--tickers", nargs="+", help="proof tickers (default: the fixed ones + a seeded sample)"
    )
    ev.add_argument("--sample", type=int, default=10, help="seeded extra tickers for the proof")
    ev.add_argument("--seed", type=int, default=0)
    ev.add_argument("--start-date", type=date.fromisoformat, default=default_start_date())
    ev.add_argument("--end-date", type=date.fromisoformat, default=None)
    rc = sub.add_parser("reclaim", help="delete, compact and swap (a copy, or the real file at G3)")
    rc.add_argument("--db", required=True)
    rc.add_argument("--backup", required=True)
    rc.add_argument("--expect", type=int, required=True, help="the approved count of rows")
    rc.add_argument("--i-have-the-g3-approval", dest="g3", action="store_true")
    args = parser.parse_args()
    if args.command == "evidence":
        return asyncio.run(cmd_evidence(args))
    return cmd_reclaim(args)


if __name__ == "__main__":
    sys.exit(main())
