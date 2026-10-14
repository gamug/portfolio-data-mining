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
        [--json] [--min-rows N]
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
from pathlib import Path
from typing import cast

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


def audit(db_path: Path, min_rows: int) -> dict[str, object]:
    """Scan every discovered_urls row once and aggregate counts by mode."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    cache: dict[tuple[str, str], tuple[re.Pattern[str], re.Pattern[str]]] = {}
    by_conn: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_ticker: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_first_word: dict[str, Counter[str]] = defaultdict(Counter)
    total = 0
    with sqlite3.connect(uri, uri=True) as con:
        rows = con.execute(
            "SELECT url, title, domain, company, ticker, source FROM discovered_urls"
        )
        for url, title, domain, company, ticker, source in rows:
            mode = classify(url, title, domain, company, ticker, cache)
            total += 1
            by_conn[(domain, source)][mode] += 1
            by_ticker[(ticker, company)][mode] += 1
            words = company.split()
            by_first_word[words[0] if words else company][mode] += 1

    def share(c: Counter[str], mode: str) -> float:
        n = sum(c.values())
        return c[mode] / n if n else 0.0

    ticker_rows = [
        {
            "ticker": t,
            "company": co,
            "rows": sum(c.values()),
            **{m: c[m] for m in MODES},
            "ticker_only_share": round(share(c, "ticker_only"), 4),
        }
        for (t, co), c in by_ticker.items()
        if sum(c.values()) >= min_rows
    ]
    return {
        "total_rows": total,
        "by_connector": [
            {"domain": d, "source": s, "rows": sum(c.values()), **{m: c[m] for m in MODES}}
            for (d, s), c in sorted(by_conn.items())
        ],
        "word_tickers": sorted(
            (r for r in ticker_rows if r["ticker"] in WORD_TICKERS),
            key=lambda r: -int(r["rows"]),  # type: ignore[call-overload]
        ),
        "generic_first_words": sorted(
            (
                {
                    "first_word": w,
                    "rows": sum(c.values()),
                    "name_only": c["name_only"],
                    "name_only_share": round(share(c, "name_only"), 4),
                }
                for w, c in by_first_word.items()
                if sum(c.values()) >= min_rows and share(c, "name_only") >= GENERIC_SHARE
            ),
            key=lambda r: -int(r["rows"]),  # type: ignore[call-overload]
        ),
    }


def render(result: dict[str, object]) -> str:
    """Format the audit result as plain-text tables."""
    out = [
        f"discovered_urls rows scanned: {result['total_rows']:,}",
        "",
        "== by (domain, source) ==",
    ]
    out.append(f"{'domain':20}{'source':9}{'rows':>9}" + "".join(f"{m:>15}" for m in MODES))
    for r in result["by_connector"]:  # type: ignore[attr-defined]
        out.append(
            f"{r['domain']:20}{r['source']:9}{r['rows']:>9,}"
            + "".join(f"{r[m]:>15,}" for m in MODES)
        )
    out += [
        "",
        "== English-word tickers ==",
        f"{'ticker':8}{'company':30}{'rows':>9}{'ticker_only':>13}{'share':>8}",
    ]
    for r in result["word_tickers"]:  # type: ignore[attr-defined]
        out.append(
            f"{r['ticker']:8}{r['company'][:28]:30}{r['rows']:>9,}{r['ticker_only']:>13,}{r['ticker_only_share']:>8.1%}"
        )
    out += ["", "== company first words matched by the name token alone in >=90% of rows =="]
    for r in result["generic_first_words"]:  # type: ignore[attr-defined]
        out.append(
            f"{r['first_word']:20}{r['rows']:>9,}{r['name_only']:>9,}{r['name_only_share']:>8.1%}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--db", default=os.environ.get("URLS_DB_PATH", DEFAULT_DB), help="path to urls.db"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--min-rows", type=int, default=1000, help="hide tickers/words with fewer rows"
    )
    args = parser.parse_args(argv)
    db = Path(args.db)
    if not db.is_file():
        print(f"error: {db} not found", file=sys.stderr)
        return 1
    result = audit(db, args.min_rows)
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
