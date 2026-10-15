"""Score the T-055 matching rules on the hand-labeled samples.

Two parts. (1) The first prototype rules (``RULES``) written after reading the
errors in ``data/ticker_tagging_sample.csv`` (T-054): per rule kept-correct /
kept-wrong / lost-correct / dropped-wrong (``yes`` is correct), precision and
recall, for all rows and for ``fetch_status = 'ok'`` rows. (2) ``design_class``,
the approved design v1 (accept / review / reject from the URL path and title,
with a hand-kept alias map, ``&`` read as ``and``, an ordinary-word-name rule and
an abbreviation list), scored post-stratified on either sample, in ``re-tag``
mode (fetched title) and ``discovery`` mode (no title for ``sitemap`` rows).

Both samples are in-sample for the current rule (it was tuned on the first
sample's errors and then the validation sample's), so the figures are optimistic;
they are not the acceptance evidence, which is the fresh check in T-057.
``--exclude-ids`` leaves sample rows out; ``--diff`` scans the whole corpus and
lists the rows whose class changed because of the host fix and the abbreviation
list (slow; read-only).

Usage:
    PYTHONPATH=src uv run python scripts/score_ticker_rules.py [--db PATH]
        [--sample PATH] [--exclude-ids ID ...] [--diff] [--sizes] [--names [--top N]]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

from ticker_tagging_sample import DEFAULT_SAMPLE

from data_mining import matching
from data_mining.db import connect, resolve_db_path
from data_mining.matching import (
    ALIASES,  # noqa: F401 - re-exported for the tests
    BASELINE_ABBREVIATIONS,
    MIN_NAME,
    MIN_TICKER,
    STOPWORDS,
    WORD_TICKERS,
)

BODY_CHARS = 6000
REPEATS = 2

Row = dict[str, str]
Rule = Callable[[Row, str, str], bool]


def _name_in(row: Row, text: str) -> bool:
    return matching.name_in(row["company"], row["ticker"], text)


def _explicit(ticker: str, title: str, body: str) -> bool:
    return matching.explicit_ticker(ticker, title, body[:BODY_CHARS])


def _name_count(row: Row, body: str) -> int:
    name = matching.full_name(row["company"])
    return matching.flat(body[:BODY_CHARS]).count(f" {name} ") if len(name) >= MIN_NAME else 0


def rule_current(_row: Row, _title: str, _body: str) -> bool:
    """Today's behaviour: every discovered pair is kept."""
    return True


def rule_name_or_explicit(row: Row, title: str, body: str) -> bool:
    """Full normalized name in the URL/title, or an explicit ticker form."""
    return _name_in(row, f"{row['url']} {title}") or _explicit(row["ticker"], title, body)


def rule_plus_body_name(row: Row, title: str, body: str) -> bool:
    """Previous rule, or the full name at least twice in the body."""
    return rule_name_or_explicit(row, title, body) or _name_count(row, body) >= REPEATS


def rule_plus_title_signals(row: Row, title: str, body: str) -> bool:
    """Previous rule, or an upper-case ticker or distinctive first word in the title."""
    ticker = row["ticker"]
    upper = (
        len(ticker) >= MIN_TICKER
        and ticker not in WORD_TICKERS
        and re.search(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", title) is not None
    )
    first = row["company"].split()[0].strip(",.")
    word = (
        len(first) >= MIN_NAME
        and first.lower() not in STOPWORDS
        and re.search(rf"\b{re.escape(first)}\b", title) is not None
    )
    return rule_plus_body_name(row, title, body) or upper or word


def design_class(row: Row, title: str, **overrides: object) -> str:
    """T-055 design v1 class of one pair (``data_mining.matching.classify``).

    ``overrides`` (``abbreviations``, ``with_host``, ``aliases``) reproduce earlier
    versions of the rule for ``--diff``.
    """
    return matching.classify(
        row["url"],
        title,
        row["company"],
        row["ticker"],
        **overrides,  # type: ignore[arg-type]
    ).cls


def _iter_corpus(db_path: Path) -> Iterator[tuple[str, str, str, str, str]]:
    """Read-only scan of every pair: ``(url, ticker, company, source, title)``.

    ``title`` is the discovery title, else the fetched article's, else blank.
    """
    uri = f"file:{db_path.resolve()}?mode=ro"
    with closing(connect(uri, uri=True)) as con:
        yield from con.execute(
            "SELECT d.url, d.ticker, d.company, d.source, COALESCE(NULLIF(d.title, ''), a.title, '')"
            " FROM discovered_urls d LEFT JOIN articles a ON a.id = d.id"
        )


def class_diff(db_path: Path, **overrides: object) -> tuple[Counter[tuple[str, str]], Counter[str]]:
    """Scan the whole corpus: rows whose class changes when ``overrides`` are applied.

    Returns the ``(old, new)`` transition counts and, per ticker, how many rows
    moved. ``overrides`` are ``design_class`` keywords for the *old* behaviour
    (``abbreviations=...``, ``with_host=True``); read-only.
    """
    moves: Counter[tuple[str, str]] = Counter()
    by_ticker: Counter[str] = Counter()
    for url, ticker, company, _source, title in _iter_corpus(db_path):
        row = {"url": url, "ticker": ticker, "company": company}
        old = design_class(row, title, **overrides)  # type: ignore[arg-type]
        new = design_class(row, title)
        if old != new:
            moves[(old, new)] += 1
            by_ticker[ticker] += 1
    return moves, by_ticker


def corpus_sizes(db_path: Path, discovery: bool = False) -> Counter[tuple[str, str]]:
    """Exact ``(class, source)`` row counts over the whole corpus for the current rule.

    With ``discovery`` the title is blank for ``sitemap`` rows (discovery sees only
    their URL). Read-only.
    """
    sizes: Counter[tuple[str, str]] = Counter()
    for url, ticker, company, source, title in _iter_corpus(db_path):
        row = {"url": url, "ticker": ticker, "company": company}
        seen = "" if discovery and source == "sitemap" else title
        sizes[(design_class(row, seen), source)] += 1
    return sizes


def single_word_name_hits(db_path: Path) -> Counter[tuple[str, str]]:
    """Rows accepted on the company name alone, for one-word names, per ``(ticker, name)``.

    One-word names (Visa, Apple, Block) are the ones most likely to be ordinary
    words; the counts show which companies lean on that signal. Read-only.
    """
    hits: Counter[tuple[str, str]] = Counter()
    for url, ticker, company, _source, title in _iter_corpus(db_path):
        name = matching.full_name(company)
        if " " in name:
            continue
        found = matching.classify(url, title, company, ticker)
        if found.cls == "accept" and found.reason == "name":
            hits[(ticker, name)] += 1
    return hits


def render_name_hits(hits: Counter[tuple[str, str]], top: int = 25) -> str:
    """Format ``single_word_name_hits`` output (largest first)."""
    total = sum(hits.values())
    lines = [
        f"== re-tag mode (fetched title): accept on a one-word name alone, {total:,} rows,"
        f" {len(hits)} companies; top {min(top, len(hits))} =="
    ]
    lines += [f"  {t:6} {n:<14} {c:>8,}" for (t, n), c in hits.most_common(top)]
    return "\n".join(lines)


def render_sizes(label: str, sizes: Counter[tuple[str, str]]) -> str:
    """Format ``corpus_sizes`` output with each class's share of the corpus."""
    total = sum(sizes.values())
    lines = [f"== current-rule class sizes, {label}: {total:,} rows =="]
    for cls in ("accept", "review", "reject"):
        n = sum(v for (c, _s), v in sizes.items() if c == cls)
        per = ", ".join(f"{src} {sizes[(cls, src)]:,}" for src in ("ddg", "sitemap"))
        lines.append(f"  {cls:7}{n:>9,} ({n / total:5.1%})  {per}")
    return "\n".join(lines)


def render_diff(label: str, moves: Counter[tuple[str, str]], by_ticker: Counter[str]) -> str:
    """Format ``class_diff`` output."""
    lines = [f"== class changes vs {label}: {sum(moves.values()):,} rows =="]
    lines += [f"  {old} -> {new}: {n:,}" for (old, new), n in sorted(moves.items())]
    lines.append("  top tickers: " + ", ".join(f"{t} {n:,}" for t, n in by_ticker.most_common(12)))
    return "\n".join(lines)


def design_report(data: list[tuple[Row, str, str, str]], discovery: bool = False) -> str:
    """Population-weighted shares and wrong rates of the three design classes.

    Uses ``stratum_size`` from the sample (known-correct candidates excluded), so
    the figures estimate the whole corpus, not the sample mix. Rows are
    post-stratified: the weight is the *drawn* stratum's size over its sample
    count, whatever class today's rule gives the row. With ``discovery`` the
    title is blank for ``sitemap`` rows (discovery sees only their URL, the
    stored title comes from the later fetch); ``ddg`` rows keep their title.
    """
    strata: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    size: dict[str, int] = {}
    known: list[tuple[str, str]] = []
    for row, _status, title, _body in data:
        seen = "" if discovery and row["source"] == "sitemap" else title
        pair = (design_class(row, seen), row["label"])
        if row["stratum"] == "known_correct_candidate":
            known.append(pair)
            continue
        strata[row["stratum"]].append((*pair, row["source"]))
        size[row["stratum"]] = int(row["stratum_size"])
    weight: dict[tuple[str, str], float] = defaultdict(float)
    raw: dict[tuple[str, str, str], int] = defaultdict(int)
    for name, pairs in strata.items():
        for cls, label, source in pairs:
            weight[(cls, label)] += size[name] / len(pairs)
            raw[(cls, source, label)] += 1
    total = sum(weight.values())
    yes_total = sum(w for (_k, lab), w in weight.items() if lab == "yes")
    mode = "discovery (sitemap URL only)" if discovery else "re-tag (fetched title)"
    lines = [f"== design v1, {mode}, population-weighted =="]
    for cls in ("accept", "review", "reject"):
        yes, no = weight[(cls, "yes")], weight[(cls, "no")]
        per = ", ".join(
            f"{src} {raw[(cls, src, 'yes')]}ok/{raw[(cls, src, 'no')]}wrong"
            for src in ("ddg", "sitemap")
        )
        lines.append(
            f"{cls:8} share {(yes + no) / total:6.1%}  wrong in class "
            f"{no / (yes + no) if yes + no else 0:6.1%}  raw: {per}"
        )
    kept_yes = weight[("accept", "yes")] + weight[("review", "yes")]
    lines.append(
        f"correct pairs kept (accept+review): {kept_yes / yes_total:.1%}; "
        f"accept only: {weight[('accept', 'yes')] / yes_total:.1%}; "
        f"hidden as reject: {weight[('reject', 'yes')] / yes_total:.1%}"
    )
    yes_known = [c for c, lab in known if lab == "yes"]
    lines.append(
        f"known-correct (labeled yes) classed accept: "
        f"{sum(c == 'accept' for c in yes_known)}/{len(yes_known)}"
    )
    return "\n".join(lines)


RULES: dict[str, Rule] = {
    "current (accepts all)": rule_current,
    "name in URL/title, or explicit ticker": rule_name_or_explicit,
    "above, or name twice in body": rule_plus_body_name,
    "above, or ticker/first word in title": rule_plus_title_signals,
}


def load(db_path: Path, sample_path: Path) -> list[tuple[Row, str, str, str]]:
    """Return ``(row, fetch_status, title, body)`` for every hand-labeled sample row."""
    with sample_path.open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["label"] in ("yes", "no")]
    uri = f"file:{db_path.resolve()}?mode=ro"
    out = []
    with closing(connect(uri, uri=True)) as con:
        for r in rows:
            found = con.execute(
                "SELECT a.fetch_status, COALESCE(NULLIF(d.title, ''), a.title, ''),"
                " COALESCE(a.body_text, '') FROM discovered_urls d"
                " LEFT JOIN articles a ON a.id = d.id WHERE d.id = ?",
                (r["id"],),
            ).fetchone()
            status, title, body = found or ("", "", "")
            out.append((r, status or "", title, body))
    return out


def score(
    data: list[tuple[Row, str, str, str]], rule: Rule, only_ok: bool = False
) -> dict[str, float]:
    """Confusion counts, precision and recall of ``rule`` against the ``yes`` labels."""
    tp = fp = fn = tn = 0
    for row, status, title, body in data:
        if only_ok and status != "ok":
            continue
        keep, yes = rule(row, title, body), row["label"] == "yes"
        tp += keep and yes
        fp += keep and not yes
        fn += (not keep) and yes
        tn += (not keep) and not yes
    return {
        "kept_correct": tp,
        "kept_wrong": fp,
        "lost_correct": fn,
        "dropped_wrong": tn,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
    }


def render(data: list[tuple[Row, str, str, str]]) -> str:
    """Format the rule table for all rows and for ``ok`` rows."""
    lines = []
    for title, only_ok in (("all labeled rows", False), ("fetch_status = ok rows", True)):
        lines += [
            f"== {title} ==",
            f"{'rule':42}{'kept-ok':>8}{'kept-bad':>9}{'lost-ok':>8}{'drop-bad':>9}{'prec':>6}{'rec':>6}",
        ]
        for name, rule in RULES.items():
            s = score(data, rule, only_ok)
            lines.append(
                f"{name:42}{s['kept_correct']:>8.0f}{s['kept_wrong']:>9.0f}"
                f"{s['lost_correct']:>8.0f}{s['dropped_wrong']:>9.0f}"
                f"{s['precision']:>6.0%}{s['recall']:>6.0%}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=None, help="path to urls.db (default: $DATABASE_URL)")
    parser.add_argument("--sample", default=DEFAULT_SAMPLE)
    parser.add_argument("--exclude-ids", nargs="*", default=[], help="sample ids to leave out")
    parser.add_argument(
        "--sizes",
        action="store_true",
        help="scan the whole corpus (slow) for the exact class sizes of the current rule",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="scan the whole corpus (slow) for rows moved by the host fix and the abbreviation list",
    )
    parser.add_argument(
        "--names",
        action="store_true",
        help="scan the whole corpus (slow) for rows accepted on a one-word company name alone",
    )
    parser.add_argument("--top", type=int, default=25, help="companies listed by --names")
    args = parser.parse_args(argv)
    db = Path(args.db or resolve_db_path())
    sample = Path(args.sample)
    if not db.is_file() or not sample.is_file():
        print("error: urls.db or the sample CSV not found", file=sys.stderr)
        return 1
    with sample.open(newline="", encoding="utf-8") as fh:
        unclear = sum(1 for r in csv.DictReader(fh) if r["label"] not in ("yes", "no"))
    data = [d for d in load(db, sample) if d[0]["id"] not in set(args.exclude_ids)]
    print(f"rows scored: {len(data)}; excluded (unclear or unlabeled): {unclear}\n")
    print(render(data))
    print(design_report(data))
    print(design_report(data, discovery=True))
    if args.sizes:
        print(render_sizes("re-tag (fetched title)", corpus_sizes(db)))
        print(render_sizes("discovery (sitemap URL only)", corpus_sizes(db, discovery=True)))
    if args.names:
        print(render_name_hits(single_word_name_hits(db), args.top))
    if args.diff:
        for label, kw in (
            ("matching the host (old)", {"with_host": True}),
            ("no abbreviation list (first draft)", {"abbreviations": BASELINE_ABBREVIATIONS}),
            ("no alias map", {"aliases": {}}),
        ):
            print(render_diff(label, *class_diff(db, **kw)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
