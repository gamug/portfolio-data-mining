"""How often is a `loose_name` pair really the company? (T-062 decision for the maintainer)

`reclass_rejects.py` promotes the pairs the old rule rejected to `review`/`loose_name`; `review` stays
quarantined, so `portfolio-nlp` reads none of them. Whether a subset can be released to `accept` is a
decision that needs a number: this draws a small stratified sample of the planned promotions
(News Corp, which is most of them, and everything else) for a person to label, and scores the labels.

``sample`` is read-only on the database and writes two files: the sample (same columns as the other
check sheets) and the labeling sheet (title and excerpt, label `yes` / `no` / `unclear`), which
``ticker_tagging_sample.py manual-apply`` merges back. ``summary`` reports, per stratum and overall,
how many are the company and a 95% lower bound on that share.

Usage:
    PYTHONPATH=src uv run python scripts/loose_name_precision.py sample --db urls.db
    PYTHONPATH=src uv run python scripts/ticker_tagging_sample.py manual-apply \\
        --sample data/ticker_loose_name_sample.csv --manual data/ticker_loose_name_manual.csv
    PYTHONPATH=src uv run python scripts/loose_name_precision.py summary
"""

from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from reclass_rejects import plan
from ticker_tagging_sample import FIELDS, manual_export, wilson

from data_mining.db import resolve_db_path

DEFAULT_SAMPLE = "data/ticker_loose_name_sample.csv"
DEFAULT_MANUAL = "data/ticker_loose_name_manual.csv"
SEED = 62
NEWS_CORP = frozenset({"NWS", "NWSA"})
ALLOCATION = {"news_corp": 10, "other": 20}


def draw(con: sqlite3.Connection, seed: int = SEED) -> list[dict[str, object]]:
    """The sampled promoted pairs, label columns empty, deterministic for a seed."""
    promoted = {i: (cls, reason) for i, cls, reason in plan(con)}
    con.row_factory = sqlite3.Row
    pool: dict[str, list[sqlite3.Row]] = {"news_corp": [], "other": []}
    for r in con.execute(
        "SELECT id, url, ticker, company, domain, source FROM discovered_urls ORDER BY id"
    ):
        if r["id"] in promoted:
            pool["news_corp" if r["ticker"] in NEWS_CORP else "other"].append(r)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    out: list[dict[str, object]] = []
    for stratum, want in ALLOCATION.items():
        for r in rng.sample(pool[stratum], min(want, len(pool[stratum]))):
            out.append(
                {
                    **{k: r[k] for k in ("id", "url", "ticker", "company", "domain", "source")},
                    "match_mode": promoted[r["id"]][0],
                    "stratum": stratum,
                    "stratum_size": len(pool[stratum]),
                    "label": "",
                    "labeled_by": "",
                    "assistant_label": "",
                }
            )
    rng.shuffle(out)  # the labeling order must not reveal the stratum
    return out


def score(rows: list[dict[str, str]]) -> str:
    """Per stratum and overall: pairs that are the company (`yes`), not (`no`), `unclear`, the share
    that is the company among the decided ones and its 95% lower bound; the overall share weights each
    stratum by its size in the plan. Rows without a label are reported, not guessed."""
    lines: list[str] = []
    weighted = total = 0.0
    for stratum in ALLOCATION:
        sub = [r for r in rows if r["stratum"] == stratum]
        if not sub:
            continue
        yes = sum(r["label"].strip().lower() == "yes" for r in sub)
        no = sum(r["label"].strip().lower() == "no" for r in sub)
        unclear = sum(r["label"].strip().lower() == "unclear" for r in sub)
        blank = len(sub) - yes - no - unclear
        decided = yes + no
        share = yes / decided if decided else 0.0
        low = 1 - wilson(no, decided)[1] if decided else 0.0
        size = int(sub[0]["stratum_size"])
        weighted += share * size
        total += size
        lines.append(
            f"{stratum}: {len(sub)} sampled of {size}; {yes} the company, {no} not, {unclear} unclear, "
            f"{blank} unlabeled; share {share:.0%} (95% lower bound {low:.0%})"
        )
    if total:
        lines.append(f"overall (weighted by stratum size): {weighted / total:.0%} are the company")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="draw the sample and the labeling sheet")
    p.add_argument("--db", default=None)
    p.add_argument("--out", default=DEFAULT_SAMPLE)
    p.add_argument("--manual", default=DEFAULT_MANUAL)
    p = sub.add_parser("summary", help="score the labeled sample")
    p.add_argument("--sample", default=DEFAULT_SAMPLE)
    args = ap.parse_args(argv)
    if args.cmd == "summary":
        with Path(args.sample).open(newline="", encoding="utf-8") as fh:
            print(score(list(csv.DictReader(fh))))
        return 0
    db = Path(args.db or resolve_db_path())
    if not db.is_file():
        print(f"error: {db} not found", file=sys.stderr)
        return 1
    with closing(sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)) as con:
        rows = draw(con)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    manual_export(db, out, Path(args.manual))
    print(f"wrote {len(rows)} rows to {out} and the labeling sheet to {args.manual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
