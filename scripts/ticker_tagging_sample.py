"""Shared labeled sample of ``(url, ticker)`` pairs for the ticker-tagging work (T-054).

Four subcommands (``sample``, ``report``, ``manual-export``, ``manual-apply``):

``sample``
    Draw a seeded, stratified sample from ``urls.db`` and write it as a CSV with
    an empty ``label`` column. Strata are ``(domain, source, match_mode)`` (the
    modes of ``audit_ticker_tagging.classify``), with ``ticker_only`` further
    split into English-word tickers and other tickers, plus one stratum of
    *known-correct candidates* (the company's full normalized name appears in
    the URL or title). ``stratum_size`` is the population size of each stratum so
    the report can weight rates back to the corpus.

``report``
    Read the labeled CSV and print the wrong-company rate per match mode and per
    connector, population-weighted from ``stratum_size`` with a stratified 95%
    interval, and the retention of the known-correct candidates.

Label values are ``yes`` (the tagged company is a subject of the article: named
and discussed, including its own section of a roundup), ``no`` (a different
company or a homonym, or a passing mention only) or ``unclear`` (the available
text cannot decide; excluded from every rate and counted in the report).
``labeled_by`` is ``hand`` (the maintainer) or ``assistant`` (an AI assistant
reading the title/body excerpt, kept in ``assistant_label`` once a human label
replaces it). This repo labels its own data; nothing here depends on
``portfolio-nlp`` (data flows from this repo to it, not back).

``manual-export``
    Write a CSV for a person to label: the sample pairs with title and a body
    excerpt and an empty ``label`` column (the assistant's labels are left out
    so they do not bias the person). It refuses to overwrite an existing file
    (the committed sheet holds the hand labels) unless ``--force`` is given.

``manual-apply``
    Merge the person's ``label`` values back into the sample (``labeled_by =
    hand``), keep the assistant's label in ``assistant_label`` and print the
    agreement between the two.

Usage:
    PYTHONPATH=src uv run python scripts/ticker_tagging_sample.py sample --out PATH
    PYTHONPATH=src uv run python scripts/ticker_tagging_sample.py report --sample PATH
    PYTHONPATH=src uv run python scripts/ticker_tagging_sample.py manual-export --out NEW.csv
    PYTHONPATH=src uv run python scripts/ticker_tagging_sample.py manual-apply --manual NEW.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import re
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from audit_ticker_tagging import WORD_TICKERS, classify  # sibling script, same directory

from data_mining.db import connect, resolve_db_path
from data_mining.matching import normalized_name

DEFAULT_SAMPLE = "data/ticker_tagging_sample.csv"
DEFAULT_MANUAL = "data/ticker_tagging_manual.csv"  # the committed, filled sheet
DEFAULT_EXPORT = "data/ticker_tagging_manual_new.csv"  # fresh blank sheet, not committed
SEED = 54
BIG_STRATUM_N = 15  # cnbc.com sitemap strata are 96% of the corpus
SMALL_STRATUM_N = 8
KNOWN_CORRECT_N = 20
BIG_THRESHOLD = 100_000
WILSON_Z = 1.96

FIELDS = [
    "id",
    "url",
    "ticker",
    "company",
    "domain",
    "source",
    "match_mode",
    "stratum",
    "stratum_size",
    "label",
    "labeled_by",
    "assistant_label",
]
MANUAL_FIELDS = ["id", "ticker", "company", "url", "title", "excerpt", "label", "notes"]
EXCERPT_CHARS = 400
LABELS = ("yes", "no", "unclear")
KNOWN_CORRECT = "known_correct_candidate"


def full_name_present(company: str, url: str, title: str | None) -> bool:
    """True when the whole normalized company name appears in the URL slug or title."""
    name = normalized_name(company)
    if len(name) < 4:  # noqa: PLR2004 - too short to be distinctive
        return False
    haystack = " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()) for t in (url, title or ""))
    return f" {name} " in f" {haystack} "


def stratum_of(domain: str, source: str, mode: str, ticker: str) -> str:
    """Stratum key; ticker-only is split so English-word tickers are not drowned out."""
    key = f"{domain}/{source}/{mode}"
    if mode == "ticker_only":
        key += "/word_ticker" if ticker in WORD_TICKERS else "/other_ticker"
    return key


def draw(db_path: Path, seed: int = SEED) -> list[dict[str, Any]]:
    """Return the sampled rows (label columns empty), deterministic for a given seed."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    cache: dict[Any, Any] = {}
    strata: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    known: list[tuple[Any, ...]] = []
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(
            "SELECT d.id, d.url, d.ticker, d.company, d.domain, d.source,"
            " COALESCE(NULLIF(d.title, ''), a.title) FROM discovered_urls d"
            " LEFT JOIN articles a ON a.id = d.id ORDER BY d.id"
        )
        for row_id, url, ticker, company, domain, source, title in cur:
            mode = classify(url, title, domain, company, ticker, cache)
            item = (row_id, url, ticker, company, domain, source, mode)
            strata[stratum_of(domain, source, mode, ticker)].append(item)
            if full_name_present(company, url, title):
                known.append(item)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    out: list[dict[str, Any]] = []
    chosen: set[int] = set()

    def emit(item: tuple[Any, ...], stratum: str, size: int) -> None:
        row_id, url, ticker, company, domain, source, mode = item
        chosen.add(row_id)
        out.append(
            {
                "id": row_id,
                "url": url,
                "ticker": ticker,
                "company": company,
                "domain": domain,
                "source": source,
                "match_mode": mode,
                "stratum": stratum,
                "stratum_size": size,
                "label": "",
                "labeled_by": "",
                "assistant_label": "",
            }
        )

    for key in sorted(strata):
        members = strata[key]
        n = BIG_STRATUM_N if len(members) >= BIG_THRESHOLD else SMALL_STRATUM_N
        for item in rng.sample(members, min(n, len(members))):
            emit(item, key, len(members))
    pool = [k for k in known if k[0] not in chosen]
    for item in rng.sample(pool, min(KNOWN_CORRECT_N, len(pool))):
        emit(item, KNOWN_CORRECT, len(known))
    return out


def wilson(wrong: int, n: int) -> tuple[float, float]:
    """Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = wrong / n
    denom = 1 + WILSON_Z**2 / n
    centre = (p + WILSON_Z**2 / (2 * n)) / denom
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + WILSON_Z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _rate_rows(
    rows: list[dict[str, str]], key: str
) -> list[tuple[str, int, int, float, tuple[float, float]]]:
    """Per ``key`` value: n labeled, n wrong, weighted wrong rate, 95% interval.

    The rate is the stratified estimator ``sum(W_h * p_h)`` with ``W_h`` the
    stratum's share of the population in that group. Because strata here are
    small (8-15 rows) and often all-wrong or all-right, ``p_h`` is smoothed to
    ``(k + 0.5) / (n + 1)`` for the variance; the interval is
    ``rate +/- 1.96 * sqrt(sum(W_h^2 * p_h(1 - p_h) / (n_h + 1)))``.
    """
    groups: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[r[key]][r["stratum"]].append(r)
    out = []
    for name, by_stratum in sorted(groups.items()):
        pop = sum(int(s[0]["stratum_size"]) for s in by_stratum.values())
        rate = var = 0.0
        n = wrong = 0
        for s in by_stratum.values():
            k = sum(1 for r in s if r["label"] == "no")
            n += len(s)
            wrong += k
            w = int(s[0]["stratum_size"]) / pop
            rate += w * k / len(s)
            smooth = (k + 0.5) / (len(s) + 1)
            var += w * w * smooth * (1 - smooth) / (len(s) + 1)
        half = WILSON_Z * math.sqrt(var)
        out.append((name, n, wrong, rate, (max(0.0, rate - half), min(1.0, rate + half))))
    return out


def report(sample_path: Path) -> str:
    """Render wrong-company rates per match mode and connector from the labeled CSV."""
    with sample_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    unlabeled = [r for r in rows if r["label"] not in ("yes", "no", "unclear")]
    if unlabeled:
        msg = f"{len(unlabeled)} of {len(rows)} rows have no yes/no/unclear label"
        raise ValueError(msg)
    unclear = [r for r in rows if r["label"] == "unclear"]
    rows = [r for r in rows if r["label"] != "unclear"]
    random_rows = [r for r in rows if r["stratum"] != KNOWN_CORRECT]
    lines = [
        f"unclear rows excluded from every rate: {len(unclear)}",
        f"sample rows: {len(rows)} ({len(random_rows)} stratified, {len(rows) - len(random_rows)} known-correct candidates)",
    ]
    for r in random_rows:
        r["all"] = "whole corpus"
    for title, key in (
        ("match_mode", "match_mode"),
        ("connector (domain)", "domain"),
        ("corpus", "all"),
    ):
        lines += [
            "",
            f"== wrong-company rate by {title} (stratified, population-weighted, 95% interval) ==",
        ]
        lines.append(f"{'':28}{'n':>5}{'wrong':>7}{'rate':>9}{'95% interval':>18}")
        for name, n, wrong, rate, (lo, hi) in _rate_rows(random_rows, key):
            lines.append(f"{name:28}{n:>5}{wrong:>7}{rate:>9.1%}{f'{lo:.1%}-{hi:.1%}':>18}")
    lines += ["", "== wrong-company rate by stratum (raw) =="]
    by_stratum: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in random_rows:
        by_stratum[r["stratum"]].append(r)
    for name in sorted(by_stratum):
        s = by_stratum[name]
        k = sum(1 for r in s if r["label"] == "no")
        lines.append(f"{name:55}{len(s):>4} of {s[0]['stratum_size']:>7}  wrong {k}/{len(s)}")
    known = [r for r in rows if r["stratum"] == KNOWN_CORRECT]
    if known:
        k = sum(1 for r in known if r["label"] == "yes")
        lo, hi = wilson(k, len(known))
        lines += [
            "",
            f"known-correct retention (labeled yes): {k}/{len(known)} = {k / len(known):.0%}"
            f" (95% interval {lo:.0%}-{hi:.0%})",
        ]
    return "\n".join(lines)


def manual_export(db_path: Path, sample_path: Path, out_path: Path) -> int:
    """Write the blank labeling sheet (pairs + title + excerpt) and return its row count."""
    with sample_path.open(newline="", encoding="utf-8") as fh:
        sample = list(csv.DictReader(fh))
    uri = f"file:{db_path.resolve()}?mode=ro"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(connect(uri, uri=True)) as con,
        out_path.open("w", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.DictWriter(fh, fieldnames=MANUAL_FIELDS)
        writer.writeheader()
        for r in sample:
            row = con.execute(
                "SELECT COALESCE(NULLIF(d.title, ''), a.title), a.body_text FROM discovered_urls d"
                " LEFT JOIN articles a ON a.id = d.id WHERE d.id = ?",
                (r["id"],),
            ).fetchone()
            title, body = (row[0], row[1]) if row else ("", "")
            writer.writerow(
                {
                    "id": r["id"],
                    "ticker": r["ticker"],
                    "company": r["company"],
                    "url": r["url"],
                    "title": title or "",
                    "excerpt": " ".join((body or "").split())[:EXCERPT_CHARS],
                    "label": "",
                    "notes": "",
                }
            )
    return len(sample)


def manual_apply(sample_path: Path, manual_path: Path) -> str:
    """Merge a person's labels into the sample in place; return an agreement summary."""
    with sample_path.open(newline="", encoding="utf-8") as fh:
        sample = list(csv.DictReader(fh))
    with manual_path.open(newline="", encoding="utf-8") as fh:
        manual = {r["id"]: r["label"].strip().lower() for r in csv.DictReader(fh)}
    bad = {i: v for i, v in manual.items() if v and v not in LABELS}
    if bad:
        msg = f"labels must be yes/no/unclear; got {sorted(set(bad.values()))} for ids {sorted(bad)[:5]}"
        raise ValueError(msg)
    applied = agree = 0
    for r in sample:
        if not r.get("assistant_label") and r["labeled_by"] == "assistant":
            r["assistant_label"] = r["label"]
        human = manual.get(r["id"], "")
        if not human:
            continue
        applied += 1
        agree += human == r["assistant_label"]
        r["label"], r["labeled_by"] = human, "hand"
    with sample_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sample)
    both = [r for r in sample if r["labeled_by"] == "hand" and r["assistant_label"]]
    disagree = [r["id"] for r in both if r["label"] != r["assistant_label"]]
    head = f"applied {applied} hand labels ({len(sample) - applied} rows still unlabeled by hand); "
    if not both:
        return head + "no assistant labels to compare with"
    pct = f"{agree / applied:.0%}" if applied else "n/a"
    return (
        head
        + f"agreement with the assistant on {len(both)} rows: {agree}/{applied} ({pct}); "
        + f"disagreeing ids: {disagree[:30]}"
    )


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _cmd_sample(args: argparse.Namespace) -> int:
    db = Path(args.db or resolve_db_path())
    if not db.is_file():
        return _fail(f"{db} not found")
    rows = draw(db, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.sample)
    if not path.is_file():
        return _fail(f"{path} not found")
    try:
        print(report(path))
    except ValueError as exc:
        return _fail(str(exc))
    return 0


def _cmd_manual_export(args: argparse.Namespace) -> int:
    db = Path(args.db or resolve_db_path())
    if not db.is_file() or not Path(args.sample).is_file():
        return _fail("urls.db or the sample CSV not found")
    if Path(args.out).exists() and not args.force:
        return _fail(f"{args.out} exists and may hold hand labels; pass --force to overwrite")
    print(f"wrote {manual_export(db, Path(args.sample), Path(args.out))} rows to {args.out}")
    return 0


def _cmd_manual_apply(args: argparse.Namespace) -> int:
    if not Path(args.sample).is_file() or not Path(args.manual).is_file():
        return _fail("sample or manual CSV not found")
    try:
        print(manual_apply(Path(args.sample), Path(args.manual)))
    except ValueError as exc:
        return _fail(str(exc))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    db_help = "path to urls.db (default: $DATABASE_URL)"
    p = sub.add_parser("sample", help="draw the stratified sample")
    p.add_argument("--db", default=None, help=db_help)
    p.add_argument("--out", default=DEFAULT_SAMPLE)
    p.add_argument("--seed", type=int, default=SEED)
    p.set_defaults(func=_cmd_sample)
    p = sub.add_parser("report", help="wrong-company rates from a labeled sample")
    p.add_argument("--sample", default=DEFAULT_SAMPLE)
    p.set_defaults(func=_cmd_report)
    p = sub.add_parser("manual-export", help="write a blank sheet for a person to label")
    p.add_argument("--db", default=None, help=db_help)
    p.add_argument("--sample", default=DEFAULT_SAMPLE)
    p.add_argument("--out", default=DEFAULT_EXPORT)
    p.add_argument("--force", action="store_true", help="overwrite an existing sheet")
    p.set_defaults(func=_cmd_manual_export)
    p = sub.add_parser("manual-apply", help="merge a person's labels into the sample")
    p.add_argument("--sample", default=DEFAULT_SAMPLE)
    p.add_argument("--manual", default=DEFAULT_MANUAL)
    p.set_defaults(func=_cmd_manual_apply)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
