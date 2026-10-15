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

``--final`` draws the T-057 acceptance check instead: **60** ``accept`` and **40** ``reject``
pairs classed by the shipped ``data_mining.matching.classify`` (re-tag mode, the fetched
title), excluding the pairs of *both* earlier samples, so the frozen rule has never seen them.
Pass = at most 5 of the 60 ``accept`` labeled ``no`` and at most 1 of the 40 ``reject`` labeled
``yes``; otherwise the work goes back to T-055.

Usage:
    PYTHONPATH=src uv run python scripts/ticker_validation_sample.py [--db PATH]
        [--existing PATH] [--out PATH] [--seed N] [--final]
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
from data_mining.matching import classify

DEFAULT_OUT = "data/ticker_validation_sample.csv"
FINAL_OUT = "data/ticker_retag_check_sample.csv"
SEED = 55
FINAL_SEED = 57
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


FINAL_ALLOCATION = {
    ("accept", "sitemap"): 35,
    ("accept", "ddg"): 25,
    ("reject", "sitemap"): 20,
    ("reject", "ddg"): 20,
}


def draw(
    db_path: Path,
    exclude: set[str],
    seed: int = SEED,
    *,
    final: bool = False,
) -> list[dict[str, object]]:
    """Return the sampled rows (label columns empty), deterministic for a seed.

    With ``final`` the T-057 check is drawn: the shipped ``classify`` and ``FINAL_ALLOCATION``."""
    allocation = FINAL_ALLOCATION if final else ALLOCATION
    uri = f"file:{db_path.resolve()}?mode=ro"
    members: dict[tuple[str, str], list[tuple[object, ...]]] = defaultdict(list)
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(
            "SELECT d.id, d.url, d.ticker, d.company, d.domain, d.source,"
            " COALESCE(NULLIF(d.title, ''), a.title, '') FROM discovered_urls d"
            " LEFT JOIN articles a ON a.id = d.id ORDER BY d.id"
        )
        for row_id, url, ticker, company, domain, source, title in cur:
            cls: str
            if final:
                cls = classify(url, title, company, ticker).cls
            else:
                cls = design_class({"url": url, "ticker": ticker, "company": company}, title)
            members[(cls, source)].append((row_id, url, ticker, company, domain, source, cls))
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    out: list[dict[str, object]] = []
    for key in sorted(allocation):
        pool = [m for m in members[key] if str(m[0]) not in exclude]
        for row_id, url, ticker, company, domain, source, klass in rng.sample(
            pool, min(allocation[key], len(pool))
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


MAX_ACCEPT_WRONG = 5
MAX_REJECT_CORRECT = 1


def final_verdict(sample_path: Path) -> tuple[bool, str]:
    """Score the labeled T-057 check (``label`` is ``yes`` / ``no`` / ``unsure``): pass when at most
    5 ``accept`` pairs are ``no`` and at most 1 ``reject`` pair is ``yes``. Unlabeled rows fail it."""
    with sample_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_class = {
        c: [r["label"].strip().lower() for r in rows if r["match_mode"] == c]
        for c in ("accept", "reject")
    }
    unlabeled = sum(
        1 for labels in by_class.values() for lab in labels if lab not in ("yes", "no", "unsure")
    )
    wrong = by_class["accept"].count("no")
    correct = by_class["reject"].count("yes")
    ok = not unlabeled and wrong <= MAX_ACCEPT_WRONG and correct <= MAX_REJECT_CORRECT
    text = (
        f"accept: {len(by_class['accept'])} pairs, {wrong} wrong (max {MAX_ACCEPT_WRONG}); "
        f"reject: {len(by_class['reject'])} pairs, {correct} correct (max {MAX_REJECT_CORRECT}); "
        f"unlabeled: {unlabeled} -> {'PASS' if ok else 'FAIL (back to T-055)'}"
    )
    return ok, text


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=None, help="path to urls.db (default: $DATABASE_URL)")
    parser.add_argument("--existing", default=DEFAULT_SAMPLE, help="sample whose ids are excluded")
    parser.add_argument("--out", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--final", action="store_true", help="the T-057 60/40 acceptance check")
    parser.add_argument("--verdict", metavar="LABELED_CSV", help="score a labeled --final sample")
    args = parser.parse_args(argv)
    if args.verdict:
        ok, text = final_verdict(Path(args.verdict))
        print(text)
        return 0 if ok else 1
    db = Path(args.db or resolve_db_path())
    existing = Path(args.existing)
    if not db.is_file() or not existing.is_file():
        print("error: urls.db or the existing sample not found", file=sys.stderr)
        return 1
    excluded = [existing]
    if args.final:  # the rule is frozen: skip every pair either earlier sample used
        excluded.append(Path(DEFAULT_OUT))
    exclude: set[str] = set()
    for path in excluded:
        if not path.is_file():
            print(f"error: {path} not found", file=sys.stderr)
            return 1
        with path.open(newline="", encoding="utf-8") as fh:
            exclude |= {r["id"] for r in csv.DictReader(fh)}
    seed = args.seed if args.seed is not None else (FINAL_SEED if args.final else SEED)
    rows = draw(db, exclude, seed, final=args.final)
    out = Path(args.out or (FINAL_OUT if args.final else DEFAULT_OUT))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
