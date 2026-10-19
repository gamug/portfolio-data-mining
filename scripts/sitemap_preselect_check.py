"""Does a token-index preselection lose any pair the full scan finds? (T-068, read-only)

Today every ticker classes every entry of a domain's sitemap. The design of Work item 15 caches the
entries once per domain and classes, per ticker, only the *candidates* of a token index. This script
holds the prototype of that index and the check that it is exact: for every ticker, the non-reject
pairs of the candidates must equal the non-reject pairs of the full scan (same class, same reason).

Why the candidate set is a superset (the argument ``T-071`` implements and tests): every signal of
``data_mining.matching.classify`` needs one *form* (the name, an alias, the ticker, a loose-name
spelling, the first word) to appear in ``flat(path + query + title)`` as consecutive whole tokens, so
each token of the form is a token of the entry; the index holds those tokens, and any one token of the
form is a valid anchor (the script takes the rarest). Two forms change a token and are handled: the
possessive ``form + "s"`` of a loose name changes only the last token (the anchor is taken among the
others), and the title-only signals (``NYSE: X``, ``$X``, an upper-case ticker, an abbreviation) need
the ticker as a token of the *title*, which has its own index.

``crawl`` writes the sitemap entries (read-only, network only); ``check`` runs both paths on all the
tickers. Nothing here opens a database.

Usage:
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py crawl --out entries.jsonl
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py check --entries entries.jsonl \\
        [--start 2022-01-01] [--csv per_ticker.csv] [--workers 14]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date
from multiprocessing import Pool
from urllib.parse import urlsplit

from data_mining import matching as M
from news_collector.config import build_date_range
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.sp500 import fetch_sp500_companies
from news_collector.strategies.sitemap import SitemapRSSStrategy
from news_collector.utils.http import build_client
from news_collector.utils.url import is_valid_http_url, normalize_url

Entry = tuple[str, str | None, str | None]  # url, lastmod (ISO), title


def entry_text(url: str, title: str | None) -> str:
    """The text `classify` reads (the host is never read)."""
    parts = urlsplit(url)
    return f"{parts.path} {parts.query} {title or ''}"


class TokenIndex:
    """`flat()` tokens of the entry text -> entry ids; a second index for the title alone."""

    def __init__(self, entries: list[Entry]) -> None:
        self.text: dict[str, list[int]] = defaultdict(list)
        self.title: dict[str, list[int]] = defaultdict(list)
        for i, (url, _lastmod, title) in enumerate(entries):
            for token in set(M.flat(entry_text(url, title)).split()):
                self.text[token].append(i)
            for token in set(M.flat(title or "").split()) if title else ():
                self.title[token].append(i)

    @staticmethod
    def _rarest(tokens: list[str], index: dict[str, list[int]]) -> list[int]:
        """Postings of the rarest token: a form needs *all* its tokens, so any one is an anchor."""
        postings = [index.get(token, []) for token in tokens]
        return min(postings, key=len) if postings else []

    def candidates(self, company: str, ticker: str) -> set[int]:
        common = M.COMMON_TICKER_WORDS | M.ABBREVIATIONS
        found: set[int] = set()
        name = M.full_name(company)
        distinctive = (len(name) >= M.MIN_NAME or any(c.isdigit() for c in name)) and (
            name not in M.STOPWORDS
        )
        if distinctive:  # `name`
            found.update(self._rarest(name.split(), self.text))
        for alias in M.ALIASES.get(ticker, ()):  # `alias`
            found.update(self._rarest(alias.split(), self.text))
        if len(ticker) >= M.MIN_TICKER and ticker not in common:  # `weak_ticker`
            found.update(self._rarest(M.flat(ticker).split(), self.text))
        brand = M.flat(M._CAMEL_TAIL.sub("", re.sub(r"\([^)]*\)", " ", company).strip())).strip()
        kept = M._kept_name(company)
        legal = re.sub(r" corp$", " corporation", kept)
        for form in {name, name.replace(" and ", " "), kept, legal, brand}:  # `loose_name`
            if not form or not M._spelled_enough(form):
                continue
            tokens = form.split()
            # the possessive (`form` + "s") changes only the last token of a form of 2+ words
            found.update(
                self._rarest(tokens[:-1] if len(tokens) >= M.MIN_WORDS else tokens, self.text)
            )
        words = company.split(maxsplit=1)  # `weak_first_word`
        first = words[0].strip(",.") if words else ""
        if len(first) >= M.MIN_NAME and first.lower() not in M.STOPWORDS:
            found.update(self._rarest(M.flat(first).split(), self.text))
        # title-only signals: explicit_ticker, upper_ticker, cashtag, abbreviation
        found.update(self._rarest(M.flat(ticker).split(), self.title))
        return found


def load_entries(path: str, start: date) -> list[Entry]:
    """What the strategy feeds the matcher today: in range, valid, an article, normalized, deduped."""
    article = CNBCConnector(None).is_article_url  # type: ignore[arg-type]
    seen: set[str] = set()
    out: list[Entry] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            url, lastmod, title = json.loads(line)
            if lastmod and date.fromisoformat(lastmod) < start:
                continue
            if not is_valid_http_url(url):
                continue
            url = normalize_url(url)
            if url in seen or not article(url):
                continue
            seen.add(url)
            out.append((url, lastmod, title))
    return out


async def cmd_crawl(out: str) -> None:
    async with build_client() as client:
        connector = CNBCConnector(client)
        strategy = SitemapRSSStrategy(connector, client)
        t0 = time.time()
        entries = await strategy._crawl_sitemaps(
            connector.config.sitemap_roots, build_date_range(date(1990, 1, 1), None)
        )
    with open(out, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(
                json.dumps([e.url, e.lastmod.isoformat() if e.lastmod else None, e.title]) + "\n"
            )
    print(
        f"{len(entries)} entries in {time.time() - t0:.1f}s, {len(strategy.failures)} failures -> {out}"
    )


_ENTRIES: list[Entry] = []
_INDEX: TokenIndex | None = None


def _scan(args: tuple[str, str, bool]) -> tuple[str, int, dict[int, tuple[str, str]]]:
    ticker, company, preselect = args
    ids = _INDEX.candidates(company, ticker) if preselect and _INDEX else range(len(_ENTRIES))
    hits: dict[int, tuple[str, str]] = {}
    for i in ids:
        url, _lastmod, title = _ENTRIES[i]
        match = M.classify(url, title or "", company, ticker)
        if match.cls != "reject":
            hits[i] = (match.cls, match.reason)
    return ticker, len(ids), hits


def cmd_check(entries_path: str, start: date, csv_path: str | None, workers: int) -> int:
    global _ENTRIES, _INDEX  # noqa: PLW0603 - read by the forked workers
    _ENTRIES = load_entries(entries_path, start)
    companies = [(c.ticker, c.name) for c in asyncio.run(_sp500())]
    t0 = time.time()
    _INDEX = TokenIndex(_ENTRIES)
    print(
        f"{len(_ENTRIES)} entries, {len(companies)} tickers, index built in {time.time() - t0:.1f}s"
    )
    with Pool(workers) as pool:
        t0 = time.time()
        full = {t: (n, h) for t, n, h in pool.map(_scan, [(t, c, False) for t, c in companies])}
        print(f"full scan: {len(_ENTRIES) * len(companies)} pairs in {time.time() - t0:.0f}s")
        t0 = time.time()
        pre = {t: (n, h) for t, n, h in pool.map(_scan, [(t, c, True) for t, c in companies])}
        print(
            f"preselected: {sum(n for n, _ in pre.values())} candidates in {time.time() - t0:.1f}s"
        )
    differing = [t for t, _ in companies if full[t][1] != pre[t][1]]
    total = sum(len(h) for _, h in full.values())
    print(f"non-reject pairs: full {total}, preselected {sum(len(h) for _, h in pre.values())}")
    print(f"tickers whose pairs differ: {differing or 'none'}")
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["ticker", "name", "candidates", "accept", "review", "missing"])
            for ticker, name in companies:
                hits = full[ticker][1]
                accept = sum(1 for cls, _ in hits.values() if cls == "accept")
                missing = len(set(hits) - set(pre[ticker][1]))
                writer.writerow([ticker, name, pre[ticker][0], accept, len(hits) - accept, missing])
    return 1 if differing else 0


async def _sp500() -> list:
    async with build_client() as client:
        return await fetch_sp500_companies(client)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    crawl = sub.add_parser("crawl", help="crawl the cnbc sitemap once (network only)")
    crawl.add_argument("--out", required=True)
    check = sub.add_parser("check", help="full scan vs preselection on the S&P 500")
    check.add_argument("--entries", required=True)
    check.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    check.add_argument("--csv")
    check.add_argument("--workers", type=int, default=14)
    args = parser.parse_args()
    if args.cmd == "crawl":
        asyncio.run(cmd_crawl(args.out))
    else:
        sys.exit(cmd_check(args.entries, args.start, args.csv, args.workers))


if __name__ == "__main__":
    main()
