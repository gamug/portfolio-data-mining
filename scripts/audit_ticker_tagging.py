"""Read-only audit of how `urls.db` rows were matched to their company (T-053).

Reproduces, per ``(connector, source)``, which part of the *current* matching
rule accepts each ``discovered_urls`` row:

* ``both``            ticker and company token both match (word boundary)
* ``ticker_only``     only the ticker matches (word boundary)
* ``name_only``       only the company first-word token matches
* ``shortcut_only``   neither does, but the raw ``ticker in url`` substring
                      shortcut (nasdaq, seeking_alpha, stocktwits,
                      yahoo_finance) accepts it
* ``unmatched``       the current rule would reject it

The word-boundary patterns come from ``BaseConnector._company_pattern`` itself,
so the audit cannot drift from the code under test. The database is opened
read-only.

Usage:
    PYTHONPATH=src python scripts/audit_ticker_tagging.py [--db PATH]
        [--json] [--min-rows N] [--generic-share F]

``--db`` defaults to ``$DATABASE_URL``, else ``data/urls.db``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from news_collector.connectors.base import BaseConnector

DEFAULT_DB = "data/urls.db"

# Connectors whose url_matches_company first runs `ticker.lower() in url.lower()`.
SHORTCUT_DOMAINS = frozenset(
    {"nasdaq.com", "seekingalpha.com", "stocktwits.com", "finance.yahoo.com"}
)

# The English-word tickers named in PLAN.md Work item 10, failure mode 1.
WORD_TICKERS = ("ON", "A", "ARE", "IT", "TECH", "HAS", "NOW", "ALL")

GENERIC_SHARE = 0.9

MODES = ("both", "ticker_only", "name_only", "shortcut_only", "unmatched")

# A string no URL or title contains; makes one half of the pattern inert.
_NEVER = "\x00"


# `_company_pattern` never touches `self`, so call it unbound: no connector needed.
_company_pattern = cast(
    "Callable[[object, str, str], re.Pattern[str]]",
    BaseConnector._company_pattern,
)


def _patterns(company: str, ticker: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    return _company_pattern(None, _NEVER, ticker), _company_pattern(None, company, _NEVER)


def classify(
    url: str,
    title: str | None,
    domain: str,
    company: str,
    ticker: str,
    cache: dict[tuple[str, str], tuple[re.Pattern[str], re.Pattern[str]]],
) -> str:
    """Return the mode (one of MODES) by which today's rule accepts this pair."""
    key = (company, ticker)
    if key not in cache:
        cache[key] = _patterns(company, ticker)
    ticker_re, name_re = cache[key]
    texts = [t for t in (url, title) if t]
    by_ticker = any(ticker_re.search(t) for t in texts)
    by_name = any(name_re.search(t) for t in texts)
    if by_ticker and by_name:
        return "both"
    if by_ticker:
        return "ticker_only"
    if by_name:
        return "name_only"
    if domain in SHORTCUT_DOMAINS and ticker.lower() in url.lower():
        return "shortcut_only"
    return "unmatched"


def _rollup(
    cells: dict[tuple[str, str, str, str], Counter[str]],
    key: Callable[[tuple[str, str, str, str]], Any],
) -> dict[Any, Counter[str]]:
    out: dict[Any, Counter[str]] = defaultdict(Counter)
    for cell, counts in cells.items():
        out[key(cell)].update(counts)
    return out


def _row(counts: Counter[str], **ident: object) -> dict[str, Any]:
    rows = sum(counts.values())
    return {
        **ident,
        "rows": rows,
        **{m: counts[m] for m in MODES},
        "ticker_only_share": round(counts["ticker_only"] / rows, 4) if rows else 0.0,
        "name_only_share": round(counts["name_only"] / rows, 4) if rows else 0.0,
    }


def audit(
    db_path: Path, min_rows: int = 1000, generic_share: float = GENERIC_SHARE
) -> dict[str, Any]:
    """Scan every discovered_urls row once and aggregate counts by mode.

    Every figure is derived from one cell per ``(ticker, company, domain,
    source)``, so any breakdown can be split per connector and strategy.
    """
    uri = f"file:{db_path.resolve()}?mode=ro"
    cache: dict[tuple[str, str], tuple[re.Pattern[str], re.Pattern[str]]] = {}
    cells: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    total = 0
    with closing(sqlite3.connect(uri, uri=True)) as con:
        rows = con.execute(
            "SELECT url, title, domain, company, ticker, source FROM discovered_urls"
        )
        for url, title, domain, company, ticker, source in rows:
            total += 1
            cells[(ticker, company, domain, source)][
                classify(url, title, domain, company, ticker, cache)
            ] += 1

    by_conn = _rollup(cells, lambda c: (c[2], c[3]))
    by_ticker = _rollup(cells, lambda c: (c[0], c[1]))
    by_word = _rollup(cells, lambda c: (c[1].split() or [c[1]])[0])

    word_tickers = []
    for (ticker, company), counts in by_ticker.items():
        if ticker not in WORD_TICKERS or sum(counts.values()) < min_rows:
            continue
        per_conn = [
            _row(c, domain=cell[2], source=cell[3])
            for cell, c in sorted(cells.items())
            if cell[:2] == (ticker, company)
        ]
        word_tickers.append(_row(counts, ticker=ticker, company=company, by_connector=per_conn))

    generic = []
    for word, counts in by_word.items():
        rows_n = sum(counts.values())
        if rows_n < min_rows or counts["name_only"] / rows_n < generic_share:
            continue
        companies = _rollup(
            {k: v for k, v in cells.items() if (k[1].split() or [k[1]])[0] == word},
            lambda c: (c[1], c[0]),
        )
        generic.append(
            _row(
                counts,
                first_word=word,
                companies=[
                    _row(c, company=co, ticker=t)
                    for (co, t), c in sorted(companies.items(), key=lambda kv: -sum(kv[1].values()))
                ],
            )
        )
    return {
        "total_rows": total,
        "thresholds": {"min_rows": min_rows, "generic_share": generic_share},
        "by_connector": [_row(c, domain=d, source=src) for (d, src), c in sorted(by_conn.items())],
        "word_tickers": sorted(word_tickers, key=lambda r: -r["rows"]),
        "generic_first_words": sorted(generic, key=lambda r: -r["rows"]),
    }


def render(result: dict[str, Any]) -> str:
    """Format the audit result as plain-text tables."""
    th = result["thresholds"]
    out = [
        f"discovered_urls rows scanned: {result['total_rows']:,}",
        f"thresholds: min_rows={th['min_rows']}, generic_share={th['generic_share']:.0%}",
        "",
        "== by (domain, source) ==",
        f"{'domain':20}{'source':9}{'rows':>9}" + "".join(f"{m:>15}" for m in MODES),
    ]
    for r in result["by_connector"]:
        out.append(
            f"{r['domain']:20}{r['source']:9}{r['rows']:>9,}"
            + "".join(f"{r[m]:>15,}" for m in MODES)
        )
    out += [
        "",
        "== English-word tickers (per connector/source below each) ==",
        f"{'ticker':8}{'company':30}{'rows':>9}{'ticker_only':>13}{'share':>8}",
    ]
    for r in result["word_tickers"]:
        out.append(
            f"{r['ticker']:8}{r['company'][:28]:30}{r['rows']:>9,}"
            f"{r['ticker_only']:>13,}{r['ticker_only_share']:>8.1%}"
        )
        out.extend(
            f"    {c['domain']:20}{c['source']:9}{c['rows']:>9,}{c['ticker_only']:>13,}"
            for c in r["by_connector"]
        )
    out += [
        "",
        f"== company first words matched by the name token alone in >={th['generic_share']:.0%} "
        "of rows (companies below each) ==",
    ]
    for r in result["generic_first_words"]:
        out.append(
            f"{r['first_word']:20}{r['rows']:>9,}{r['name_only']:>9,}{r['name_only_share']:>8.1%}"
        )
        out.extend(
            f"    {c['ticker']:8}{c['company'][:28]:30}{c['rows']:>9,}{c['name_only']:>9,}"
            for c in r["companies"][:5]
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL") or DEFAULT_DB,
        help="path to urls.db (default: $DATABASE_URL, else data/urls.db)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--min-rows", type=int, default=1000, help="hide tickers/words with fewer rows"
    )
    parser.add_argument(
        "--generic-share",
        type=float,
        default=GENERIC_SHARE,
        help="list first words matched by the name token alone in at least this share of rows",
    )
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.is_file():
        print(f"error: {db} not found", file=sys.stderr)
        return 1
    result = audit(db, args.min_rows, args.generic_share)
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
