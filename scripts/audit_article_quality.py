"""Read-only audit of the `ok` rows of `articles`, per domain (T-060).

Reports, for every ``source_domain``: the ``ok`` rows, how many match a wall phrase
(``extractor.parse.is_wall_page``), how many fall under the word-count floor, and how many
bodies are duplicated (the same body stored once per tagged ticker). A non-zero ``walls`` means a
wall page was stored as ``ok`` (rows fetched before the detector existed): re-fetch them.

Usage:
    PYTHONPATH=src uv run python scripts/audit_article_quality.py [--db PATH] [--json]
        [--floor N]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from data_mining.db import connect, resolve_db_path
from extractor.parse import MIN_WORDS_FOR_FULL_CONTENT, is_wall_page


def audit(db_path: Path, floor: int = MIN_WORDS_FOR_FULL_CONTENT) -> dict[str, dict[str, int]]:
    """Per-domain counters over the `ok` articles (database opened read-only)."""
    per: dict[str, dict[str, int]] = defaultdict(
        lambda: {"ok": 0, "walls": 0, "under_floor": 0, "duplicate_bodies": 0}
    )
    bodies: dict[str, Counter[str]] = defaultdict(Counter)
    uri = f"file:{db_path.resolve()}?mode=ro"
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute("SELECT source_domain, body_text FROM articles WHERE fetch_status = 'ok'")
        for domain, body in cur:
            text = body or ""
            row = per[domain or ""]
            row["ok"] += 1
            row["walls"] += is_wall_page(text)
            row["under_floor"] += len(text.split()) < floor
            bodies[domain or ""][text] += 1
    for domain, counter in bodies.items():
        per[domain]["duplicate_bodies"] = sum(n - 1 for n in counter.values())
    return dict(sorted(per.items()))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; exits 1 when an `ok` row is a wall or under the floor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="path to urls.db (default: $DATABASE_URL)")
    parser.add_argument("--floor", type=int, default=MIN_WORDS_FOR_FULL_CONTENT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = audit(Path(args.db or resolve_db_path()), args.floor)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{'domain':32} {'ok':>8} {'walls':>6} {'<floor':>7} {'dup bodies':>11}")
        for domain, r in result.items():
            print(
                f"{domain:32} {r['ok']:>8} {r['walls']:>6} {r['under_floor']:>7} "
                f"{r['duplicate_bodies']:>11}"
            )
    return int(any(r["walls"] or r["under_floor"] for r in result.values()))


if __name__ == "__main__":
    raise SystemExit(main())
