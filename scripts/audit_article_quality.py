"""Read-only audit of the `ok` rows of `articles`, per domain (T-060).

Reports, for every ``source_domain``: the ``ok`` rows, how many match a wall phrase
(``extractor.parse.wall_kind``), how many fall under the word-count floor, and how many
bodies are duplicated (the same body stored once per tagged ticker). A non-zero ``walls`` means a
wall page was stored as ``ok`` (rows fetched before the detector existed).

Quarantined rows come back as ``ok`` when their pair is promoted (``release_articles`` restores
``prev_fetch_status``), so the ones quarantined from ``ok`` are audited too, in the ``held`` and
``held_walls`` columns (``ok`` / ``walls`` / ``under_floor`` / ``duplicate_bodies`` cover ``ok`` rows only).

``--by-status`` instead runs the detector over *every* body and counts, per ``fetch_status``, how many
are classed ``failed`` / ``paywalled`` / neither (the precision check of the detector).

``--list`` prints the ids of the ``ok`` and held rows that are walls. To re-fetch them, set their
``discovered_urls.status`` back to ``pending`` and run ``cli/news_crawler_cli.py``; the detector
then stores them as ``failed`` / ``paywalled`` (or ``ok`` again if the page is now an article).

Usage:
    PYTHONPATH=src uv run python scripts/audit_article_quality.py [--db PATH] [--json]
        [--floor N] [--list] [--by-status]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from data_mining.db import connect, resolve_db_path
from extractor.parse import MIN_WORDS_FOR_FULL_CONTENT, is_wall_page, wall_kind

# `ok` now, or `ok` again once released from quarantine
_EFFECTIVE_OK = (
    "(fetch_status = 'ok' OR (fetch_status = 'quarantined' AND prev_fetch_status = 'ok'))"
)


def audit(db_path: Path, floor: int = MIN_WORDS_FOR_FULL_CONTENT) -> dict[str, dict[str, int]]:
    """Per-domain counters over the `ok` articles (database opened read-only)."""
    per: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "ok": 0,
            "walls": 0,
            "under_floor": 0,
            "duplicate_bodies": 0,
            "held": 0,
            "held_walls": 0,
        }
    )
    bodies: dict[str, Counter[bytes]] = defaultdict(Counter)
    uri = f"file:{db_path.resolve()}?mode=ro"
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(
            f"SELECT source_domain, body_text, fetch_status FROM articles WHERE {_EFFECTIVE_OK}"  # noqa: S608
        )
        for domain, body, status in cur:
            text = body or ""
            row = per[domain or ""]
            if status == "quarantined":
                row["held"] += 1
                row["held_walls"] += is_wall_page(text)
                continue
            row["ok"] += 1
            row["walls"] += is_wall_page(text)
            row["under_floor"] += len(text.split()) < floor
            bodies[domain or ""][hashlib.blake2b(text.encode(), digest_size=16).digest()] += 1
    for domain, counter in bodies.items():
        per[domain]["duplicate_bodies"] = sum(n - 1 for n in counter.values())
    return dict(sorted(per.items()))


def wall_ids(db_path: Path) -> list[int]:
    """Ids of the `ok` articles whose body is a wall page (database opened read-only)."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(f"SELECT id, body_text FROM articles WHERE {_EFFECTIVE_OK} ORDER BY id")  # noqa: S608
        return [row_id for row_id, body in cur if is_wall_page(body or "")]


def by_status(db_path: Path) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """`wall_kind` over every body (database opened read-only): per `fetch_status`, the rows classed
    `failed`, `paywalled` or `none`, and the number of wall hits per `source_domain`."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"failed": 0, "paywalled": 0, "none": 0})
    domains: Counter[str] = Counter()
    uri = f"file:{db_path.resolve()}?mode=ro"
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute("SELECT fetch_status, body_text, source_domain FROM articles")
        for status, body, domain in cur:
            kind = wall_kind(body or "")
            out[status or ""][kind or "none"] += 1
            if kind:
                domains[domain or ""] += 1
    return dict(sorted(out.items())), dict(sorted(domains.items()))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; exits 1 when an `ok` row is a wall or under the floor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="path to urls.db (default: $DATABASE_URL)")
    parser.add_argument("--floor", type=int, default=MIN_WORDS_FOR_FULL_CONTENT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true", help="print the ids of `ok`/held wall rows")
    parser.add_argument("--by-status", action="store_true", help="detector hits per fetch_status")
    args = parser.parse_args(argv)
    if args.by_status:
        statuses, domains = by_status(Path(args.db or resolve_db_path()))
        for status, r in statuses.items():
            print(
                f"{status:14} failed={r['failed']:>6} paywalled={r['paywalled']:>6} none={r['none']:>7}"
            )
        print("wall hits per domain:", domains or "none")
        return 0
    if args.list:
        ids = wall_ids(Path(args.db or resolve_db_path()))
        print("\n".join(map(str, ids)))
        return int(bool(ids))
    result = audit(Path(args.db or resolve_db_path()), args.floor)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"{'domain':32} {'ok':>8} {'walls':>6} {'<floor':>7} {'dup bodies':>11} "
            f"{'held':>8} {'held walls':>11}"
        )
        for domain, r in result.items():
            print(
                f"{domain:32} {r['ok']:>8} {r['walls']:>6} {r['under_floor']:>7} "
                f"{r['duplicate_bodies']:>11} {r['held']:>8} {r['held_walls']:>11}"
            )
    return int(any(r["walls"] or r["held_walls"] or r["under_floor"] for r in result.values()))


if __name__ == "__main__":
    raise SystemExit(main())
