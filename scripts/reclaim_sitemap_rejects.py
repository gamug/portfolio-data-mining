"""T-073: evidence and reclaim of the never-fetched sitemap rejects (Work item 15, gate G3).

    # read-only evidence (any file; the real one is only read): what qualifies, the split, the proof
    PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py evidence --db COPY.db

    # the deletion, on a COPY first (the rehearsal), with a backup that matches it:
    PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py reclaim --db COPY.db \
        --backup COPY.pre.db --expect 578113 --rehearsal-copy

    # the real file, only with the maintainer's approval (G3), the services stopped:
    PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py reclaim --db REAL.db \
        --backup REAL_BACKUP.db --expect 578113 --expect-kept 593 --i-have-the-g3-approval

    # finish a run that printed INCOMPLETE (rows deleted, file not compacted); the backup must
    # exist and pass quick_check (it is not compared: the file has lost the rows it still has):
    PYTHONPATH=src uv run python scripts/reclaim_sitemap_rejects.py compact --db REAL.db \
        --backup REAL_BACKUP.db --i-have-the-g3-approval

`reclaim` needs `--backup` (another file that passes `quick_check` and has the same fingerprint),
`--expect N` (the count the maintainer approved: a different count deletes nothing) and exactly one
of `--i-have-the-g3-approval` (the process and open-file checks run, whatever the file is called) or
`--rehearsal-copy` (refused for a file called `urls.db` or the one `DATABASE_URL` names). It holds
the file exclusively, so it also refuses while any program has the file open. See
`news_collector/reclaim_sitemap.py` and `TASKS.md` T-073.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sqlite3
import sys
from datetime import date
from pathlib import Path

from data_mining.env import load_env

load_env()

from news_collector.config import build_date_range, default_start_date  # noqa: E402
from news_collector.orchestrator import build_connectors  # noqa: E402
from news_collector.reclaim_sitemap import (  # noqa: E402
    PROOF_TICKERS,
    ReclaimError,
    ReclaimIncomplete,
    approval_gate,
    check_database,
    compact,
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
    try:
        check_database(Path(args.db))
    except ReclaimError as exc:
        print(f"REFUSED, nothing changed: {exc}")
        return 1
    date_range = build_date_range(args.start_date, args.end_date)
    tickers = args.tickers or _tickers(args.db, args.sample, args.seed)
    async with build_client() as client:
        ev = await gather_evidence(args.db, build_connectors(client), DATA_DIR, date_range, tickers)
    print(format_evidence(ev, date_range))
    for ticker, url, cls in ev.proof.rejected_now[:20]:
        print(f"  NOW REJECTED {ticker} {url} (cache class {cls})")
    found, accepted = len(ev.proof.rejected_now), args.expect_now_rejected
    print(f"Now-rejected pairs: {found} (the maintainer accepted {accepted})")
    return 0 if found == accepted else 1


def gate(args: argparse.Namespace) -> bool | None:
    """`check_processes` for the step, or None (after printing why) when it is refused."""
    try:
        return approval_gate(Path(args.db), g3=args.g3, rehearsal=args.rehearsal)
    except ReclaimError as exc:
        print(f"REFUSED, nothing changed: {exc}")
        return None


def cmd_reclaim(args: argparse.Namespace) -> int:
    check_processes = gate(args)
    if check_processes is None:
        return 1
    try:
        done = reclaim(
            Path(args.db),
            Path(args.backup),
            DATA_DIR,
            expect=args.expect,
            expect_kept=args.expect_kept,
            check_processes=check_processes,
        )
    except ReclaimIncomplete as exc:
        print(exc)
        return 2
    except ReclaimError as exc:
        print(f"REFUSED, nothing changed: {exc}")
        return 1
    print(f"Deleted          : {done.deleted}")
    print(f"Protected ids    : {done.kept} (hand-labeled samples)")
    print(f"discovered_urls  : {done.before.discovered} -> {done.after.discovered}")
    print(
        f"articles         : {done.before.articles} -> {done.after.articles} (orphans {done.after.orphans})"
    )
    print(f"accept/review    : {done.before.kept_rows()} -> {done.after.kept_rows()}")
    print(
        f"File             : {done.bytes_before} -> {done.bytes_after} bytes ({done.bytes_before - done.bytes_after} reclaimed)"
    )
    print(f"Backup kept      : {done.backup} (the maintainer deletes it)")
    print("Final check      : the file in place has the checked counts and user_version")
    print(f"Elapsed          : {done.seconds:.1f}s")
    if not done.owner_kept:
        print("WARNING: the new file is owned by this user, not by the original's owner: chown it")
    return 0


def cmd_compact(args: argparse.Namespace) -> int:
    check_processes = gate(args)
    if check_processes is None:
        return 1
    if check_processes and not args.backup:
        print("the real database needs --backup (a file that exists and passes quick_check)")
        return 1
    try:
        done = compact(
            Path(args.db),
            backup=Path(args.backup) if args.backup else None,
            check_processes=check_processes,
        )
    except ReclaimError as exc:
        print(f"REFUSED, nothing changed: {exc}")
        return 1
    print(f"File             : {done.bytes_before} -> {done.bytes_after} bytes")
    print(f"discovered_urls  : {done.after.discovered}, articles {done.after.articles}")
    print("Final check      : the file in place has the checked counts and user_version")
    if not done.owner_kept:
        print("WARNING: the new file is owned by this user, not by the original's owner: chown it")
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
    ev.add_argument(
        "--expect-now-rejected",
        type=int,
        default=0,
        help="the accepted number of stored accept/review pairs the matcher now rejects (G3: 17); "
        "exit 1 when the count differs, so a different set is never read as the accepted one",
    )
    ev.add_argument("--start-date", type=date.fromisoformat, default=default_start_date())
    ev.add_argument("--end-date", type=date.fromisoformat, default=None)
    rc = sub.add_parser("reclaim", help="delete, compact and swap (a copy, or the real file at G3)")
    rc.add_argument("--db", required=True)
    rc.add_argument("--backup", required=True)
    rc.add_argument("--expect", type=int, required=True, help="the approved count of rows")
    rc.add_argument("--expect-kept", type=int, help="the number of protected sample ids (593)")
    cp = sub.add_parser("compact", help="compact and swap with no delete (finishes an INCOMPLETE)")
    cp.add_argument("--db", required=True)
    cp.add_argument("--backup", help="required on the real file: must exist and pass quick_check")
    for step in (rc, cp):
        step.add_argument("--i-have-the-g3-approval", dest="g3", action="store_true")
        step.add_argument("--rehearsal-copy", dest="rehearsal", action="store_true")
    args = parser.parse_args()
    if args.command == "evidence":
        return asyncio.run(cmd_evidence(args))
    return cmd_reclaim(args) if args.command == "reclaim" else cmd_compact(args)


if __name__ == "__main__":
    sys.exit(main())
