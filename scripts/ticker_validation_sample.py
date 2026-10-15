"""Fresh validation sample for the T-055 design (rule not tuned on it).

The design-v1 rule (``score_ticker_rules.design_class``) was written after
reading the first hand-labeled sample, so its figures are in-sample. This draws a
**new** stratified sample, excluding every pair already labeled, with strata
``(class, source)`` where ``class`` is ``accept`` / ``review`` / ``reject``. The
CSV uses the same columns as ``ticker_tagging_sample.csv`` (``match_mode`` holds
the class, ``stratum_size`` the population size), so
``ticker_tagging_sample.py manual-export / manual-apply / report`` work on it
unchanged. The class is not written to the labeling sheet, so it cannot bias the
person labeling.

Usage:
    PYTHONPATH=src uv run python scripts/ticker_validation_sample.py [--db PATH]
        [--existing PATH] [--out PATH] [--seed N]
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from score_ticker_rules import design_class
from ticker_tagging_sample import DEFAULT_SAMPLE, FIELDS

from data_mining.db import connect, resolve_db_path

DEFAULT_OUT = "data/ticker_validation_sample.csv"
SEED = 55
# Draw sizes per (class, source): more where the question matters (what
# portfolio-nlp reads by default, and what would be thrown out).
ALLOCATION = {
    ("accept", "sitemap"): 25,
    ("accept", "ddg"): 15,
    ("review", "sitemap"): 20,
    ("review", "ddg"): 10,
    ("reject", "sitemap"): 15,
    ("reject", "ddg"): 15,
}


def draw(db_path: Path, exclude: set[str], seed: int = SEED) -> list[dict[str, object]]:
    """Return the sampled rows (label columns empty), deterministic for a seed."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    members: dict[tuple[str, str], list[tuple[object, ...]]] = defaultdict(list)
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(
            "SELECT d.id, d.url, d.ticker, d.company, d.domain, d.source,"
            " COALESCE(NULLIF(d.title, ''), a.title, '') FROM discovered_urls d"
            " LEFT JOIN articles a ON a.id = d.id ORDER BY d.id"
        )
        for row_id, url, ticker, company, domain, source, title in cur:
            cls = design_class({"url": url, "ticker": ticker, "company": company}, title)
            members[(cls, source)].append((row_id, url, ticker, company, domain, source, cls))
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    out: list[dict[str, object]] = []
    for key in sorted(ALLOCATION):
        pool = [m for m in members[key] if str(m[0]) not in exclude]
        for row_id, url, ticker, company, domain, source, klass in rng.sample(
            pool, min(ALLOCATION[key], len(pool))
        ):
            out.append(
                {
                    "id": row_id,
                    "url": url,
                    "ticker": ticker,
                    "company": company,
                    "domain": domain,
                    "source": source,
                    "match_mode": klass,
                    "stratum": f"{klass}/{source}",
                    "stratum_size": len(members[key]),
                    "label": "",
                    "labeled_by": "",
                    "assistant_label": "",
                }
            )
    rng.shuffle(out)  # the labeling order must not reveal the class
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=None, help="path to urls.db (default: $DATABASE_URL)")
    parser.add_argument("--existing", default=DEFAULT_SAMPLE, help="sample whose ids are excluded")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    db = Path(args.db or resolve_db_path())
    existing = Path(args.existing)
    if not db.is_file() or not existing.is_file():
        print("error: urls.db or the existing sample not found", file=sys.stderr)
        return 1
    with existing.open(newline="", encoding="utf-8") as fh:
        exclude = {r["id"] for r in csv.DictReader(fh)}
    rows = draw(db, exclude, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
