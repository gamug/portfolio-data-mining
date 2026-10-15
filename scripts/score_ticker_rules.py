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
        [--sample PATH] [--exclude-ids ID ...] [--diff]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from audit_ticker_tagging import WORD_TICKERS
from ticker_tagging_sample import DEFAULT_SAMPLE, normalized_name

from data_mining.db import connect, resolve_db_path

BODY_CHARS = 6000
MIN_NAME = 4
MIN_TICKER = 3
REPEATS = 2
# Lone first words too ordinary to identify a company by themselves.
STOPWORDS = frozenset(
    {
        *("dollar", "american", "trade", "bank", "first", "news", "best", "global", "home"),
        *("target", "public", "live", "state", "federal", "capital", "texas", "digital"),
        *("extra", "union", "equity", "international", "general", "united", "national"),
        *("new", "pacific", "southern", "western", "norfolk"),
    }
)

# Ordinary words that are also tickers or look like them in a headline; an
# upper-case ticker among these never counts as evidence by itself.
COMMON_TICKER_WORDS = frozenset(WORD_TICKERS) | frozenset(
    {
        *("AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN", "IS", "ME", "MY"),
        *("NO", "OF", "OR", "SO", "TO", "UP", "US", "WE", "I", "CAN", "FOR", "ONE", "OUT"),
        *("NEW", "BIG", "FAST", "KEY", "APP", "FIX"),
    }
)
# 2-letter tickers that are also upper-case abbreviations in a headline ("Egypt PM
# says"). Never `accept` on their own, but never excluded either: an upper-case
# hit in the title is `review` (judged downstream), so the list cannot hide a pair.
ABBREVIATIONS = frozenset({"PM", "MA", "ED", "EL", "IP", "CI", "HD", "PH", "MS", "NI", "DD"})
BASELINE_ABBREVIATIONS: frozenset[str] = frozenset()  # the first draft had none
# Names a company is also known by, matched like the full name (hand-kept, tested).
# `coke` for KO was rejected: it is also a fuel and a steel input.
ALIASES: dict[str, tuple[str, ...]] = {
    "GOOGL": ("google",),
    "GOOG": ("google",),
    "PG": ("p and g",),
    "META": ("facebook",),
    "BRK.B": ("berkshire",),
    "BRK.A": ("berkshire",),
    "JPM": ("jpmorgan",),
    "WMT": ("wal mart",),
}
MIN_SHORT_TICKER = 2

Row = dict[str, str]
Rule = Callable[[Row, str, str], bool]


def _flat(text: str) -> str:
    """Lower-case words of ``text``; ``&`` is read as ``and`` (URLs spell it out)."""
    return (
        " " + " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and ")).split()) + " "
    )


def _full_name(row: Row) -> str:
    return normalized_name(row["company"].replace("&", " and "))


def _name_in(row: Row, text: str, aliases: dict[str, tuple[str, ...]] | None = None) -> bool:
    """Full normalized name (or an alias) in ``text``; an ordinary word alone never counts."""
    name = _full_name(row)
    distinctive = (len(name) >= MIN_NAME or any(c.isdigit() for c in name)) and (
        name not in STOPWORDS
    )  # "3M" yes; "News" (News Corp) no
    flat = _flat(text)
    names = [name] if distinctive else []
    names += list((ALIASES if aliases is None else aliases).get(row["ticker"], ()))
    return any(f" {n} " in flat for n in names)


def _explicit(ticker: str, title: str, body: str) -> bool:
    t = re.escape(ticker)
    pattern = re.compile(
        rf"(\((?:NYSE|NASDAQ|NYSEARCA|Nasdaq|NYSE American)?:?\s*{t}\)"
        rf"|(?:NYSE|NASDAQ)\s*:\s*{t}\b|\${t}\b)"
    )
    return bool(pattern.search(title) or pattern.search(body[:BODY_CHARS]))


def _name_count(row: Row, body: str) -> int:
    name = _full_name(row)
    return _flat(body[:BODY_CHARS]).count(f" {name} ") if len(name) >= MIN_NAME else 0


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


def _word(token: str, text: str, flags: int = re.IGNORECASE) -> bool:
    return (
        re.search(rf"(?<![A-Za-z0-9&]){re.escape(token)}(?![A-Za-z0-9&])", text, flags) is not None
    )


def design_class(
    row: Row,
    title: str,
    *,
    abbreviations: frozenset[str] = ABBREVIATIONS,
    with_host: bool = False,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> str:
    """T-055 design v1, from the URL path and the title (what discovery can see).

    ``accept``: the full normalized company name (``&`` read as ``and``; a name
    that is itself an ordinary word such as ``News`` never counts) or a hand-kept
    alias is in the URL path or title, or an explicit ticker form, or an
    upper-case ticker (2+ letters, not an ordinary word or abbreviation) in the
    title. ``review``: only a weaker sign, a non-common ticker or a distinctive
    first word in the URL/title, or an abbreviation-like 2-letter ticker in
    upper case in the title. ``reject``: nothing else. The host is not matched
    (``with_host`` exists only to measure what that leak used to accept).
    """
    ticker = row["ticker"]
    parts = urlsplit(row["url"])
    slug = (
        f"{parts.netloc} {parts.path} {parts.query}" if with_host else f"{parts.path} {parts.query}"
    )
    text = f"{slug} {title}"
    common = COMMON_TICKER_WORDS | abbreviations
    strong = (
        _name_in(row, text, aliases)
        or _explicit(ticker, title, "")
        or (len(ticker) >= MIN_SHORT_TICKER and ticker not in common and _word(ticker, title, 0))
    )
    if strong:
        return "accept"
    first = row["company"].split()[0].strip(",.")
    weak = (
        (len(ticker) >= MIN_TICKER and ticker not in common and _word(ticker, text))
        or (len(first) >= MIN_NAME and first.lower() not in STOPWORDS and _word(first, text))
        or (ticker in abbreviations and _word(ticker, title, 0))
    )
    return "review" if weak else "reject"


def class_diff(db_path: Path, **overrides: object) -> tuple[Counter[tuple[str, str]], Counter[str]]:
    """Scan the whole corpus: rows whose class changes when ``overrides`` are applied.

    Returns the ``(old, new)`` transition counts and, per ticker, how many rows
    moved. ``overrides`` are ``design_class`` keywords for the *old* behaviour
    (``abbreviations=...``, ``with_host=True``); read-only.
    """
    uri = f"file:{db_path.resolve()}?mode=ro"
    moves: Counter[tuple[str, str]] = Counter()
    by_ticker: Counter[str] = Counter()
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(
            "SELECT d.url, d.ticker, d.company, COALESCE(NULLIF(d.title, ''), a.title, '')"
            " FROM discovered_urls d LEFT JOIN articles a ON a.id = d.id"
        )
        for url, ticker, company, title in cur:
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
    uri = f"file:{db_path.resolve()}?mode=ro"
    sizes: Counter[tuple[str, str]] = Counter()
    with closing(connect(uri, uri=True)) as con:
        cur = con.execute(
            "SELECT d.url, d.ticker, d.company, d.source, COALESCE(NULLIF(d.title, ''), a.title, '')"
            " FROM discovered_urls d LEFT JOIN articles a ON a.id = d.id"
        )
        for url, ticker, company, source, title in cur:
            row = {"url": url, "ticker": ticker, "company": company}
            seen = "" if discovery and source == "sitemap" else title
            sizes[(design_class(row, seen), source)] += 1
    return sizes


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
