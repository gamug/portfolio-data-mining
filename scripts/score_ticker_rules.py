"""Score prototype matching rules on the hand-labeled sample (T-054 evidence for T-055).

Explorative and in-sample: the rules were written after reading the errors in
``data/ticker_tagging_sample.csv``, so the figures are optimistic and are not a
measurement of the T-056 implementation. This script makes the table in
``PLAN.md`` Work item 10 reproducible; T-055 replaces the rules with the
approved design and re-runs it.

Each rule says whether to *keep* a ``(url, ticker)`` pair, from the URL, the
title and the first 6,000 characters of the body. Per rule it prints
kept-correct / kept-wrong / lost-correct / dropped-wrong (``yes`` is correct),
precision and recall, for all rows and for ``fetch_status = 'ok'`` rows (what
``portfolio-nlp`` reads). ``--exclude-ids`` drops rows, e.g. the two labels
awaiting confirmation, to show the figures with and without them.

Usage:
    PYTHONPATH=src uv run python scripts/score_ticker_rules.py [--db PATH]
        [--sample PATH] [--exclude-ids ID ...]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

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

Row = dict[str, str]
Rule = Callable[[Row, str, str], bool]


def _flat(text: str) -> str:
    return " " + " ".join(re.sub(r"[^a-z0-9&]+", " ", text.lower()).split()) + " "


def _name_in(row: Row, text: str) -> bool:
    name = normalized_name(row["company"])
    return len(name) >= MIN_NAME and f" {name} " in _flat(text)


def _explicit(ticker: str, title: str, body: str) -> bool:
    t = re.escape(ticker)
    pattern = re.compile(
        rf"(\((?:NYSE|NASDAQ|NYSEARCA|Nasdaq|NYSE American)?:?\s*{t}\)"
        rf"|(?:NYSE|NASDAQ)\s*:\s*{t}\b|\${t}\b)"
    )
    return bool(pattern.search(title) or pattern.search(body[:BODY_CHARS]))


def _name_count(row: Row, body: str) -> int:
    name = normalized_name(row["company"])
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
    args = parser.parse_args(argv)
    db = Path(args.db or resolve_db_path())
    sample = Path(args.sample)
    if not db.is_file() or not sample.is_file():
        print("error: urls.db or the sample CSV not found", file=sys.stderr)
        return 1
    data = [d for d in load(db, sample) if d[0]["id"] not in set(args.exclude_ids)]
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
