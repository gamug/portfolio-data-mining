"""Does a token-index preselection lose any pair the full scan finds? (T-068, read-only)

Today every ticker classes every entry of a domain's sitemap. The design of Work item 15 caches the
entries once per domain and classes, per ticker, only the *candidates* of a token index. This script
holds the prototype of that index and every check that the design rests on. **Nothing here writes to
a database**: the cache is an in-memory SQLite table with the DDL of the design, and ``stored`` opens
the real file with ``mode=ro``.

The baseline is the production code, not a copy of it: ``SitemapRSSStrategy._dedupe_and_filter`` over
the entries in range (the date filter of ``_parse_sitemap_leaf``). The preselected path reads its
entries back from the cache table, so the normalized URL, the ``lastmod`` (the ``pub_date`` of the
pair) and the title are the ones the cache would hold. A pair is equal when its class, reason and
``pub_date`` are.

Why the candidate set is a superset (the argument ``T-071`` implements and tests): every signal of
``data_mining.matching.classify`` needs one *form* (the name, an alias, the ticker, a loose-name
spelling, the first word) to appear in ``flat(path + query + title)`` as consecutive whole tokens, so
each token of the form is a token of the entry; the index holds those tokens, and any one token of the
form is a valid anchor (the script takes the rarest). Two forms change a token and are handled: the
possessive ``form + "s"`` of a loose name changes only the last token (the anchor is a token other
than the last, or the postings of the last token and of the last token plus ``s``), and the title-only
signals (``NYSE: X``, ``$X``, an upper-case ticker, an abbreviation) need the ticker as a token of the
*title*, which has its own index.

Subcommands:
    crawl       write the cnbc sitemap entries (network only)
    check       baseline vs preselection on the tickers; ``--csv`` writes the table per ticker
    synthetic   the same on a synthetic corpus with titles, and with one anchor family removed (the
                check must then fail); includes the ``news:title`` fixture
    cache-size  size of the cache table with its indexes (in range, all dates, with titles)
    duplicates  normalized URLs that the sitemap lists twice with different ``lastmod`` values
    stored      read-only on a ``urls.db``: stored cnbc sitemap rows against the crawl
    probe       crawl one sitemap root of the ft or nasdaq connector (network only; ft takes ~15 min)

Usage:
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py crawl --out entries.jsonl
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py check --entries entries.jsonl \\
        --tickers-file data/sitemap_preselect_per_ticker.csv [--csv per_ticker.csv] [--workers 14]
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py synthetic --entries entries.jsonl \\
        --tickers-file data/sitemap_preselect_per_ticker.csv
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py cache-size --entries entries.jsonl
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py duplicates --entries entries.jsonl
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py stored --entries entries.jsonl \\
        --db /workspaces/thesis/data/urls.db
    PYTHONPATH=src uv run python scripts/sitemap_preselect_check.py probe --domain ft.com \\
        --root https://www.ft.com/sitemaps/index.xml
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import json
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from multiprocessing import Pool
from urllib.parse import urlsplit

from data_mining import matching as M
from news_collector.config import build_date_range
from news_collector.connectors.cnbc import CNBCConnector
from news_collector.connectors.ft import FTConnector
from news_collector.connectors.nasdaq import NasdaqConnector
from news_collector.models import SitemapEntry
from news_collector.sp500 import fetch_sp500_companies
from news_collector.strategies.sitemap import SitemapRSSStrategy
from news_collector.utils.http import build_client
from news_collector.utils.url import is_valid_http_url, normalize_url

Row = tuple[str, str | None, str | None]  # normalized url, lastmod (ISO), title
Pair = tuple[str, str, str | None]  # class, reason, pub_date (ISO)

START = date(2022, 1, 1)
# The design's DDL (T-068 note, G2); created only in memory or in a temporary file.
CACHE_DDL = """
CREATE TABLE sitemap_entries (
    domain TEXT NOT NULL, url TEXT NOT NULL, lastmod TEXT, title TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY (domain, url)
) WITHOUT ROWID;
CREATE INDEX ix_sitemap_entries_lastmod ON sitemap_entries (domain, lastmod);
"""
# Cumulative shares of the synthetic URL variants, then the share of entries with a title and of
# entries without a `lastmod`.
P_TICKER_URL, P_NAME_S_URL, P_NAME_CORP_URL, P_TITLE, P_NO_LASTMOD = 0.25, 0.4, 0.5, 0.7, 0.03
FAMILIES = ("name", "alias", "weak_ticker", "loose_name", "weak_first_word", "title")


def entry_text(url: str, title: str | None) -> str:
    """The text `classify` reads (the host is never read)."""
    parts = urlsplit(url)
    return f"{parts.path} {parts.query} {title or ''}"


class TokenIndex:
    """`flat()` tokens of the entry text -> row ids; a second index for the title alone.

    ``skip`` names anchor families to leave out: only the mutation check uses it, to prove that
    the equality check fails when an anchor is missing.
    """

    def __init__(self, rows: list[Row], skip: frozenset[str] = frozenset()) -> None:
        self.skip = skip
        self.text: dict[str, list[int]] = defaultdict(list)
        self.title: dict[str, list[int]] = defaultdict(list)
        for i, (url, _lastmod, title) in enumerate(rows):
            for token in set(M.flat(entry_text(url, title)).split()):
                self.text[token].append(i)
            if title:
                for token in set(M.flat(title).split()):
                    self.title[token].append(i)

    @staticmethod
    def _rarest(tokens: list[str], index: dict[str, list[int]]) -> list[int]:
        """Postings of the rarest token: a form needs *all* its tokens, so any one is an anchor."""
        postings = [index.get(token, []) for token in tokens]
        return min(postings, key=len) if postings else []

    def _form(self, tokens: list[str]) -> set[int]:
        """Anchor of a loose-name form. The possessive (`form` + "s") changes only the last token,
        so a form of 2+ words is anchored on a token other than the last, or on the entries that
        have the last token or the last token plus `s` (both are valid; the smaller wins)."""
        if len(tokens) < M.MIN_WORDS:
            return set(self._rarest(tokens, self.text))
        head = self._rarest(tokens[:-1], self.text)
        tail = set(self.text.get(tokens[-1], ())) | set(self.text.get(tokens[-1] + "s", ()))
        return set(head) if len(head) <= len(tail) else tail

    def candidates(self, company: str, ticker: str) -> set[int]:
        common = M.COMMON_TICKER_WORDS | M.ABBREVIATIONS
        found: set[int] = set()
        name = M.full_name(company)
        distinctive = (len(name) >= M.MIN_NAME or any(c.isdigit() for c in name)) and (
            name not in M.STOPWORDS
        )
        if distinctive and "name" not in self.skip:
            found.update(self._rarest(name.split(), self.text))
        if "alias" not in self.skip:
            for alias in M.ALIASES.get(ticker, ()):
                found.update(self._rarest(alias.split(), self.text))
        if len(ticker) >= M.MIN_TICKER and ticker not in common and "weak_ticker" not in self.skip:
            found.update(self._rarest(M.flat(ticker).split(), self.text))
        if "loose_name" not in self.skip:
            brand = M.flat(
                M._CAMEL_TAIL.sub("", re.sub(r"\([^)]*\)", " ", company).strip())
            ).strip()
            kept = M._kept_name(company)
            legal = re.sub(r" corp$", " corporation", kept)
            for form in {name, name.replace(" and ", " "), kept, legal, brand}:
                if form and M._spelled_enough(form):
                    found.update(self._form(form.split()))
        words = company.split(maxsplit=1)
        first = words[0].strip(",.") if words else ""
        first_ok = len(first) >= M.MIN_NAME and first.lower() not in M.STOPWORDS
        if first_ok and "weak_first_word" not in self.skip:
            found.update(self._rarest(M.flat(first).split(), self.text))
        if "title" not in self.skip:  # explicit_ticker, upper_ticker, cashtag, abbreviation
            found.update(self._rarest(M.flat(ticker).split(), self.title))
        return found


# ---------------------------------------------------------------------------------------------
# Entries, the cache table and the two paths
# ---------------------------------------------------------------------------------------------


def read_raw(path: str) -> list[SitemapEntry]:
    """The entries as the crawl produced them (all dates), in file order."""
    out: list[SitemapEntry] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            url, lastmod, title = json.loads(line)
            out.append(
                SitemapEntry(
                    url=url, lastmod=date.fromisoformat(lastmod) if lastmod else None, title=title
                )
            )
    return out


def in_range(raw: list[SitemapEntry], start: date) -> list[SitemapEntry]:
    """The date filter of `_parse_sitemap_leaf`: an entry without a `lastmod` is kept."""
    window = build_date_range(start, None)
    return [e for e in raw if e.lastmod is None or window.contains(e.lastmod)]


def cache_rows(
    raw: list[SitemapEntry], connector: CNBCConnector, domain: str = "cnbc.com"
) -> list[Row]:
    """What the cache would hold, read back from the table of the design (in memory).

    An entry the connector does not call an article is not stored, the URL is normalized, and
    within one crawl the first entry of a URL wins (as `_dedupe_and_filter` does today).
    """
    db = sqlite3.connect(":memory:")
    db.executescript(CACHE_DDL)
    now = datetime.now(UTC).isoformat()
    db.executemany(
        "INSERT OR IGNORE INTO sitemap_entries VALUES (?, ?, ?, ?, ?, ?)",
        (
            (domain, url, e.lastmod.isoformat() if e.lastmod else None, e.title, now, now)
            for e in raw
            if is_valid_http_url(e.url) and connector.is_article_url(url := normalize_url(e.url))
        ),
    )
    rows = db.execute(
        "SELECT url, lastmod, title FROM sitemap_entries WHERE domain = ?", (domain,)
    ).fetchall()
    db.close()
    return rows


_RAW: list[SitemapEntry] = []
_ROWS: list[Row] = []
_INDEX: TokenIndex | None = None
_STRATEGY: SitemapRSSStrategy | None = None


def _baseline(company: str, ticker: str) -> dict[str, Pair]:
    """Today's path: the production filter over every entry in range."""
    assert _STRATEGY is not None
    found = _STRATEGY._dedupe_and_filter(_RAW, company, ticker)
    return {
        d.url: (d.match_class, d.match_reason or "", d.pub_date.isoformat() if d.pub_date else None)
        for d in found
        if d.match_class != "reject"
    }


def _preselected(company: str, ticker: str) -> tuple[int, dict[str, Pair]]:
    """The design: the candidates of the token index, classed by the same `classify`."""
    assert _INDEX is not None
    ids = _INDEX.candidates(company, ticker)
    hits: dict[str, Pair] = {}
    for i in ids:
        url, lastmod, title = _ROWS[i]
        match = M.classify(url, title or "", company, ticker)
        if match.cls != "reject":
            hits[url] = (match.cls, match.reason, lastmod)
    return len(ids), hits


def _scan(args: tuple[str, str, str]) -> tuple[str, int, dict[str, Pair]]:
    ticker, company, mode = args
    if mode == "full":
        return ticker, len(_ROWS), _baseline(company, ticker)
    n, hits = _preselected(company, ticker)
    return ticker, n, hits


def compare(
    raw: list[SitemapEntry],
    companies: list[tuple[str, str]],
    *,
    start: date = START,
    workers: int = 1,
    skip: frozenset[str] = frozenset(),
) -> dict[str, dict]:
    """Both paths for each `(ticker, name)`: per ticker the candidates, both sets, and the difference.

    ``raw`` is every entry of the crawl; the date filter is applied here, as the strategy does.
    """
    global _RAW, _ROWS, _INDEX, _STRATEGY  # noqa: PLW0603 - read by the forked workers
    connector = CNBCConnector(None)  # type: ignore[arg-type]
    _RAW = in_range(raw, start)
    _ROWS = cache_rows(_RAW, connector)
    _INDEX = TokenIndex(_ROWS, skip)
    _STRATEGY = SitemapRSSStrategy(connector, None)  # type: ignore[arg-type]
    jobs = [(t, n, mode) for t, n in companies for mode in ("full", "pre")]
    if workers > 1:
        with Pool(workers) as pool:
            done = pool.map(_scan, jobs, chunksize=4)
    else:
        done = [_scan(j) for j in jobs]
    by = {(t, mode): (n, hits) for (t, _n, mode), (_t, n, hits) in zip(jobs, done, strict=True)}
    out = {}
    for ticker, _name in companies:
        full, pre = by[(ticker, "full")][1], by[(ticker, "pre")][1]
        out[ticker] = {
            "candidates": by[(ticker, "pre")][0],
            "full": full,
            "pre": pre,
            "missing": sorted(set(full) - set(pre)),
            "extra": sorted(set(pre) - set(full)),
            "changed": sorted(u for u in set(full) & set(pre) if full[u] != pre[u]),
        }
    return out


def differing(result: dict[str, dict]) -> list[str]:
    return [t for t, r in result.items() if r["missing"] or r["extra"] or r["changed"]]


# ---------------------------------------------------------------------------------------------
# Synthetic corpus (titles, spellings) and the fixture
# ---------------------------------------------------------------------------------------------

TEMPLATES = (
    "{n} shares jump", "(NASDAQ: {t}) rallies", "(NYSE: {t}) falls", "${t} breaks out",
    "{t} stock news", "{f}'s CEO speaks", "Egypt PM says rates", "{n}'s outlook",
    "{N} corp. results", "Café {t} news", "Stocks to watch: {t}", "{n}s of the market",
    "A look at IT and ON and ARE", "{t}-based fund",
)  # fmt: skip


def synthetic_corpus(
    raw: list[SitemapEntry], companies: list[tuple[str, str]], n: int = 12000, seed: int = 7
) -> list[SitemapEntry]:
    """Real slugs and dates, plus URLs that spell a ticker, a name, `<name>s` or `<name>-corp`, and
    titles in the forms the matcher reads (exchange forms, cashtags, abbreviations, non-ASCII)."""
    rnd = random.Random(seed)  # noqa: S311 - a reproducible corpus, not a secret
    out: list[SitemapEntry] = []
    for i in range(n):
        base = rnd.choice(raw)
        ticker, name = rnd.choice(companies)
        slug = re.sub("[^a-z0-9]+", "-", name.lower()).strip("-")
        roll = rnd.random()
        url = base.url
        if roll < P_TICKER_URL:
            url = f"https://www.cnbc.com/2024/05/01/{ticker.lower().replace('.', '-')}-stock-rallies-{i}.html"
        elif roll < P_NAME_S_URL:
            url = f"https://www.cnbc.com/2024/05/01/{slug}s-ceo-{i}.html"
        elif roll < P_NAME_CORP_URL:
            url = f"https://www.cnbc.com/2024/05/01/{slug}-corp-{i}.html"
        title = None
        if rnd.random() < P_TITLE:
            title = rnd.choice(TEMPLATES).format(
                n=name, N=name.upper(), t=ticker, f=name.split()[0]
            )
        lastmod = None if rnd.random() < P_NO_LASTMOD else date(2024, 5, 1)  # always in range
        out.append(SitemapEntry(url=url, lastmod=lastmod, title=title))
    return out


def title_fixture() -> tuple[list[SitemapEntry], list[tuple[str, str]]]:
    """The `news:title` case: the URL says nothing, only the title does."""
    raw = [
        SitemapEntry(
            url="https://www.cnbc.com/2026/10/19/neutral-slug.html",
            lastmod=date(2026, 10, 19),
            title="(NASDAQ: KLAC) shares jump",
        ),
        SitemapEntry(
            url="https://www.cnbc.com/2026/10/19/other-slug.html", lastmod=date(2026, 10, 19)
        ),
    ]
    return raw, [("KLAC", "KLA Corporation")]


# ---------------------------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------------------------


def load_companies(path: str | None) -> tuple[list[tuple[str, str]], str]:
    """`ticker,name` rows of a CSV, or the live S&P 500 list when no file is given."""
    if path:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = [(r["ticker"], r["name"]) for r in csv.DictReader(fh)]
        return rows, f"{path} ({len(rows)} tickers)"

    async def fetch() -> list:
        async with build_client() as client:
            return await fetch_sp500_companies(client)

    rows = [(c.ticker, c.name) for c in asyncio.run(fetch())]
    return rows, f"live Wikipedia list of {date.today()} ({len(rows)} tickers)"


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


async def cmd_probe(args: argparse.Namespace) -> None:
    """Crawl one root and say what it holds: entries, titles, article URLs, failures."""
    window = build_date_range(START, None)
    async with build_client() as client:
        connector = {"ft.com": FTConnector, "nasdaq.com": NasdaqConnector}[args.domain](client)
        strategy = SitemapRSSStrategy(connector, client)
        t0 = time.time()
        entries = await strategy._crawl_sitemaps(
            [args.root], build_date_range(date(1990, 1, 1), None)
        )
    articles = [
        e
        for e in entries
        if is_valid_http_url(e.url) and connector.is_article_url(normalize_url(e.url))
    ]
    kinds = collections.Counter("/".join(e.url.split("/")[3:5]) for e in entries)
    print(
        f"{args.domain}: crawl {time.time() - t0:.0f}s, {len(entries)} entries, "
        f"{sum(1 for e in entries if e.lastmod)} with lastmod, {sum(1 for e in entries if e.title)} with a title, "
        f"{len(articles)} article urls, {sum(1 for e in articles if e.lastmod is None or window.contains(e.lastmod))} in range, "
        f"{len(strategy.failures)} failures"
    )
    print(f"first failures: {[f[:120] for f in strategy.failures[:3]]}")
    print(f"top path prefixes: {kinds.most_common(4)}; sample urls: {[e.url for e in entries[:2]]}")


def cmd_check(args: argparse.Namespace) -> int:
    raw = read_raw(args.entries)
    companies, source = load_companies(args.tickers_file)
    t0 = time.time()
    result = compare(raw, companies, start=args.start, workers=args.workers)
    print(f"tickers: {source}; {len(_RAW)} entries in range, {len(_ROWS)} cache rows")
    pairs = len(_ROWS) * len(companies)
    candidates = sum(r["candidates"] for r in result.values())
    print(
        f"both paths in {time.time() - t0:.0f}s: {pairs} pairs, {candidates} candidates ({candidates / pairs:.3%})"
    )
    total = sum(len(r["full"]) for r in result.values())
    bad = differing(result)
    print(
        f"non-reject pairs: baseline {total}, preselected {sum(len(r['pre']) for r in result.values())}"
    )
    print(f"tickers whose pairs differ (class, reason or pub_date): {bad or 'none'}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["ticker", "name", "candidates", "accept", "review", "missing", "extra", "changed"]
            )
            for ticker, name in companies:
                r = result[ticker]
                accept = sum(1 for p in r["full"].values() if p[0] == "accept")
                writer.writerow([ticker, name, r["candidates"], accept, len(r["full"]) - accept,
                                 len(r["missing"]), len(r["extra"]), len(r["changed"])])  # fmt: skip
    return 1 if bad else 0


def cmd_synthetic(args: argparse.Namespace) -> int:
    raw = read_raw(args.entries)
    companies, source = load_companies(args.tickers_file)
    companies = [*companies, ("TTD", "The Trade Desk")]
    corpus = synthetic_corpus(raw, companies, args.size)
    print(
        f"tickers: {source} plus TTD; {len(corpus)} synthetic entries, {sum(1 for e in corpus if e.title)} titled"
    )
    result = compare(corpus, companies, workers=args.workers)
    reasons = collections.Counter(p[1] for r in result.values() for p in r["full"].values())
    bad = differing(result)
    print(f"non-reject pairs: baseline {sum(len(r['full']) for r in result.values())}, "
          f"preselected {sum(len(r['pre']) for r in result.values())}; tickers differing: {bad or 'none'}")  # fmt: skip
    print(f"reasons seen ({len(reasons)} of 9): {dict(reasons)}")
    fixture_raw, fixture_companies = title_fixture()
    fixture = compare(fixture_raw, fixture_companies)["KLAC"]
    print(f"news:title fixture: candidates {fixture['candidates']}, pairs {list(fixture['full'].values())}, "
          f"differs: {bool(differing({'KLAC': fixture}))}")  # fmt: skip
    status = 1 if bad or not fixture["full"] else 0
    for (
        family
    ) in FAMILIES:  # each anchor family left out in turn: the check must fail for the ones in use
        weak = compare(corpus, companies, workers=args.workers, skip=frozenset({family}))
        lost = sum(len(r["missing"]) for r in weak.values())
        print(
            f"mutation, no `{family}` anchors: {len(differing(weak))} tickers lose pairs ({lost} pairs)"
        )
    return status


def cmd_cache_size(args: argparse.Namespace) -> None:
    raw = read_raw(args.entries)
    connector = CNBCConnector(None)  # type: ignore[arg-type]
    titled = [
        SitemapEntry(e.url, e.lastmod, "x" * 70) for e in in_range(raw, START)
    ]  # a 70-character title
    for label, entries in (
        ("in range", in_range(raw, START)),
        ("all dates", raw),
        ("in range, each with a 70-character title", titled),
    ):
        rows = cache_rows(entries, connector)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = sqlite3.connect(path)
            db.executescript(CACHE_DDL)
            db.executemany(
                "INSERT INTO sitemap_entries VALUES ('cnbc.com', ?, ?, ?, '2026-10-19T00:00:00', '2026-10-19T00:00:00')",
                rows,
            )
            db.commit()
            db.execute("VACUUM")
            db.close()
            size = os.path.getsize(path)
        finally:
            os.remove(path)
        print(f"{label}: {len(rows)} rows, {size} bytes, {size / len(rows):.0f} bytes a row")


def duplicate_lastmods(raw: list[SitemapEntry]) -> tuple[int, int, int]:
    """(normalized URLs listed twice or more, of them with different `lastmod`, with and without one)."""
    seen: dict[str, set[date | None]] = defaultdict(set)
    count: dict[str, int] = defaultdict(int)
    for e in raw:
        if is_valid_http_url(e.url):
            url = normalize_url(e.url)
            seen[url].add(e.lastmod)
            count[url] += 1
    twice = [u for u, c in count.items() if c > 1]
    differ = [u for u in twice if len(seen[u]) > 1]
    mixed = [u for u in differ if None in seen[u]]
    return len(twice), len(differ), len(mixed)


def cmd_duplicates(args: argparse.Namespace) -> None:
    twice, differ, mixed = duplicate_lastmods(read_raw(args.entries))
    print(
        f"normalized URLs listed more than once: {twice}; with different lastmod: {differ} ({mixed} of them with one missing)"
    )


def cmd_stored(args: argparse.Namespace) -> None:
    """Stored cnbc sitemap rows against today's crawl; read-only on the database."""
    raw = read_raw(args.entries)
    connector = CNBCConnector(None)  # type: ignore[arg-type]
    lastmod: dict[str, date | None] = {}
    for e in raw:
        if is_valid_http_url(e.url):
            url = normalize_url(e.url)
            if connector.is_article_url(url):
                lastmod.setdefault(url, e.lastmod)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    stored = {
        r[0]: r[1]
        for r in db.execute(
            "SELECT url, MIN(discovered_at) FROM discovered_urls "
            "WHERE domain = 'cnbc.com' AND source = 'sitemap' GROUP BY url"
        )
    }
    gone = [u for u in stored if u not in lastmod]
    print(f"stored distinct urls {len(stored)}, crawl article urls {len(lastmod)}, stored and gone {len(gone)} "
          f"({len(gone) / len(stored):.1%}); stored on {sorted({stored[u][:10] for u in gone})[:4]}")  # fmt: skip
    by_total = collections.Counter(u.split("/")[3] for u in stored)
    by_gone = collections.Counter(u.split("/")[3] for u in gone)
    print("year  stored   gone  share")
    for year in sorted(by_total):
        print(
            f"{year}  {by_total[year]:>6} {by_gone[year]:>6}  {by_gone[year] / by_total[year]:.1%}"
        )
    rows = db.execute(
        "SELECT url FROM discovered_urls WHERE domain = 'cnbc.com' AND source = 'sitemap' "
        "AND match_class IN ('accept', 'review')"
    ).fetchall()
    kinds: collections.Counter[str] = collections.Counter()
    for (url,) in rows:
        if url not in lastmod:
            kinds["left the sitemap"] += 1
        elif (modified := lastmod[url]) is None or modified >= START:
            kinds["in range today"] += 1
        else:
            kinds[f"before {START} (lastmod in the crawl)"] += 1
    print(f"stored accept/review cnbc sitemap rows: {len(rows)}")
    for kind, n in kinds.most_common():
        print(f"  {kind}: {n} ({n / len(rows):.1%})")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    crawl = sub.add_parser("crawl", help="crawl the cnbc sitemap once (network only)")
    crawl.add_argument("--out", required=True)
    probe = sub.add_parser("probe", help="crawl one ft or nasdaq root (network only)")
    probe.add_argument("--domain", required=True, choices=["ft.com", "nasdaq.com"])
    probe.add_argument("--root", required=True)
    for name in ("check", "synthetic", "cache-size", "duplicates", "stored"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--entries", required=True)
        if name in ("check", "synthetic"):
            cmd.add_argument(
                "--tickers-file", help="CSV with ticker,name; default: live Wikipedia list"
            )
            cmd.add_argument("--workers", type=int, default=14)
        if name == "check":
            cmd.add_argument("--start", type=date.fromisoformat, default=START)
            cmd.add_argument("--csv")
        if name == "synthetic":
            cmd.add_argument("--size", type=int, default=12000)
        if name == "stored":
            cmd.add_argument("--db", required=True)
    args = parser.parse_args()
    if args.cmd == "crawl":
        asyncio.run(cmd_crawl(args.out))
        return
    if args.cmd == "probe":
        asyncio.run(cmd_probe(args))
        return
    commands = {"check": cmd_check, "synthetic": cmd_synthetic, "cache-size": cmd_cache_size,
                "duplicates": cmd_duplicates, "stored": cmd_stored}  # fmt: skip
    sys.exit(commands[args.cmd](args) or 0)


if __name__ == "__main__":
    main()
