"""Reclaim the stored sitemap rejects that were never fetched (T-073, Work item 15, gate G3).

`T-071` stops storing the pairs the matcher rejects, so the 578,113 `skipped` `cnbc.com` rejects of
the `KLAC` run are dead weight: no article, never fetched, and re-derivable from the sitemap cache
by `reclass-sitemap` (T-072) if the matcher ever changes. This module holds the two halves of the
task, both usable on a **copy** with no approval, and the second on the real file only at G3:

* `gather_evidence` is read-only: what qualifies, how it splits (in the cache, before the range,
  left the sitemap), what the hand-labeled samples keep, and the proof that the cache re-derives
  every in-range stored `accept`/`review` pair of a sample of tickers.
* `reclaim` deletes the qualifying rows in one transaction, checks the fingerprint before and
  after, writes a compacted file with `VACUUM INTO` and swaps it in. The old file stays as the
  backup until the maintainer deletes it.

The rows that qualify are exactly `candidate_where`: `match_class='reject'`, `status='skipped'`,
no `articles` row, source `sitemap`/`rss`, a domain whose cache is filled, and an id that no
committed hand-labeled sample names. Nothing else is ever deleted.
"""

# ruff: noqa: S608 - every SQL string is built from fixed text and runs of "?" marks; values are bound
from __future__ import annotations

import csv
import hashlib
import logging
import re
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from data_mining.matching import CLASS_RANK
from news_collector.connectors.base import BaseConnector
from news_collector.models import Company, DateRange
from news_collector.reclass_cache import _classify
from news_collector.sitemap_cache import SitemapCache

log = logging.getLogger(__name__)

SAMPLE_GLOBS = ("ticker_*_sample.csv", "ticker_*_manual.csv")
SOURCES = ("sitemap", "rss")
_YEAR_IN_URL = re.compile(r"/((?:19|20)\d\d)/\d\d/\d\d/")
# The tickers the proof always includes: the ordinary-word and one-letter ones are on purpose.
PROOF_TICKERS = ("KLAC", "AAPL", "ON", "A", "IT", "ARE", "TTD", "MSFT", "NVDA", "CRM")


class ReclaimError(Exception):
    """A check failed: nothing was deleted, or the file is as it was before the step."""


def kept_ids(data_dir: Path) -> set[int]:
    """The ids named by the committed hand-labeled samples (`data/ticker_*_sample|manual.csv`)."""
    ids: set[int] = set()
    for pattern in SAMPLE_GLOBS:
        for path in sorted(data_dir.glob(pattern)):
            with path.open(newline="", encoding="utf-8") as fh:
                ids.update(int(r["id"]) for r in csv.DictReader(fh) if r.get("id"))
    return ids


def cache_domains(conn: sqlite3.Connection) -> list[str]:
    """Domains whose sitemap cache holds a complete crawl."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT domain FROM sitemap_crawls WHERE complete = 1 ORDER BY domain"
        )
    ]


def _load_kept(conn: sqlite3.Connection, ids: Iterable[int]) -> None:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS kept_ids (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM temp.kept_ids")
    conn.executemany("INSERT OR IGNORE INTO temp.kept_ids VALUES (?)", [(i,) for i in ids])


def candidate_where(domains: Sequence[str], *, keep: bool = True) -> tuple[str, list[str]]:
    """The WHERE clause of the rows that qualify, and its parameters. Needs `temp.kept_ids`."""
    if not domains:
        return "0", []
    marks = ",".join("?" * len(domains))
    sources = ",".join("?" * len(SOURCES))
    where = (
        "match_class = 'reject' AND status = 'skipped' "
        "AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.id = discovered_urls.id) "
        f"AND source IN ({sources}) AND domain IN ({marks})"
    )
    if keep:
        where += " AND id NOT IN (SELECT id FROM temp.kept_ids)"
    return where, [*SOURCES, *domains]


@dataclass
class Fingerprint:
    """What must not change, and what must change by exactly the deleted count."""

    discovered: int
    articles: int
    orphans: int  # `articles.id` with no `discovered_urls` row
    by_class: dict[str, int]
    quick_check: str
    user_version: int = 0
    schema_hash: str = ""  # of every table and index definition: the compacted file keeps it

    def kept_rows(self) -> dict[str, int]:
        return {k: v for k, v in self.by_class.items() if k != "reject"}


def fingerprint(conn: sqlite3.Connection) -> Fingerprint:

    def one(sql: str) -> Any:
        return conn.execute(sql).fetchone()[0]

    by_class = dict(
        conn.execute("SELECT match_class, COUNT(*) FROM discovered_urls GROUP BY match_class")
    )
    schema = "\n".join(
        f"{n}|{q}" for n, q in conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name")
    )
    return Fingerprint(
        user_version=one("PRAGMA user_version"),
        schema_hash=hashlib.sha256(schema.encode()).hexdigest(),
        discovered=one("SELECT COUNT(*) FROM discovered_urls"),
        articles=one("SELECT COUNT(*) FROM articles"),
        orphans=one(
            "SELECT COUNT(*) FROM articles a "
            "WHERE NOT EXISTS (SELECT 1 FROM discovered_urls d WHERE d.id = a.id)"
        ),
        by_class=by_class,
        quick_check=one("PRAGMA quick_check"),
    )


def _year(url: str, pub_date: str | None) -> int | None:
    if pub_date and pub_date[:4].isdigit():
        return int(pub_date[:4])
    m = _YEAR_IN_URL.search(url)
    return int(m.group(1)) if m else None


# --- evidence (read-only) --------------------------------------------------------------------


@dataclass
class Split:
    """How a set of stored pairs splits against the cache of its domain."""

    in_cache: int = 0
    before_range: Counter[int] = field(default_factory=Counter)  # by year
    left_sitemap: int = 0  # in range by date, not in the cache

    def total(self) -> int:
        return self.in_cache + sum(self.before_range.values()) + self.left_sitemap


@dataclass
class Proof:
    """The T-072 proof for a sample of tickers: stored in-range accept/review pairs re-derived."""

    tickers: list[str] = field(default_factory=list)
    stored: int = 0
    found: int = 0
    # In range and in the cache, but the current matcher classes the stored accept/review pair reject.
    rejected_now: list[tuple[str, str, str]] = field(default_factory=list)  # (ticker, url, class)
    not_in_cache: Split = field(default_factory=Split)


@dataclass
class Evidence:
    domains: list[str]
    qualifying: int
    without_keep: int
    by_domain: dict[str, int]
    split: Split
    kept_ids: int
    kept_in_candidates: int
    proof: Proof
    before: Fingerprint
    file_bytes: int


def _split_pairs(
    rows: Iterable[tuple[str, str | None]], urls_in_cache: set[str], range_start: date
) -> Split:
    split = Split()
    for url, pub_date in rows:
        if url in urls_in_cache:
            split.in_cache += 1
            continue
        year = _year(url, pub_date)
        if year is not None and year < range_start.year:
            split.before_range[year] += 1
        else:
            split.left_sitemap += 1
    return split


async def gather_evidence(
    db_path: str,
    connectors: dict[str, BaseConnector],
    data_dir: Path,
    date_range: DateRange,
    proof_tickers: Sequence[str],
) -> Evidence:
    """Everything G3 needs to see, read-only: the file is opened `mode=ro`."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=60)
    try:
        ids = kept_ids(data_dir)
        _load_kept(conn, ids)
        domains = cache_domains(conn)
        where, params = candidate_where(domains)
        bare, bare_params = candidate_where(domains, keep=False)
        qualifying = conn.execute(
            f"SELECT COUNT(*) FROM discovered_urls WHERE {where}", params
        ).fetchone()[0]
        without_keep = conn.execute(
            f"SELECT COUNT(*) FROM discovered_urls WHERE {bare}", bare_params
        ).fetchone()[0]
        by_domain = dict(
            conn.execute(
                f"SELECT domain, COUNT(*) FROM discovered_urls WHERE {where} GROUP BY domain",
                params,
            )
        )
        runs = {}
        for domain in domains:
            connector = connectors[domain]
            cache = SitemapCache(
                domain, connector.config.sitemap_roots, db_path, _never, connector.is_article_url
            )
            runs[domain] = await cache.begin(date_range, allow_crawl=False)
        split = Split()
        for domain, run in runs.items():
            in_cache = {e.url for e in run.rows}
            rows = conn.execute(
                f"SELECT url, pub_date FROM discovered_urls WHERE {where} AND domain = ?",
                [*params, domain],
            )
            part = _split_pairs(rows, in_cache, date_range.start)
            split.in_cache += part.in_cache
            split.before_range.update(part.before_range)
            split.left_sitemap += part.left_sitemap
        proof = await _prove(conn, connectors, runs, date_range, proof_tickers)
        return Evidence(
            domains=domains,
            qualifying=qualifying,
            without_keep=without_keep,
            by_domain=by_domain,
            split=split,
            kept_ids=len(ids),
            kept_in_candidates=without_keep - qualifying,
            proof=proof,
            before=fingerprint(conn),
            file_bytes=Path(db_path).stat().st_size,
        )
    finally:
        conn.close()


async def _never(_url: str) -> str:
    raise RuntimeError("the evidence never crawls")


async def _prove(
    conn: sqlite3.Connection,
    connectors: dict[str, BaseConnector],
    runs: dict,
    date_range: DateRange,
    tickers: Sequence[str],
) -> Proof:
    import asyncio  # noqa: PLC0415 - only the proof is async

    proof = Proof(tickers=list(tickers))
    for ticker in tickers:
        for domain, run in runs.items():
            if run.crawl is None:
                continue
            row = conn.execute(
                "SELECT company FROM discovered_urls WHERE ticker = ? AND domain = ? LIMIT 1",
                (ticker, domain),
            ).fetchone()
            if row is None:
                continue
            stored = conn.execute(
                "SELECT url, pub_date, match_class FROM discovered_urls WHERE ticker = ? "
                "AND domain = ? AND source = 'sitemap' AND match_class IN ('accept','review')",
                (ticker, domain),
            ).fetchall()
            _entries, pairs = await asyncio.to_thread(
                _classify, connectors[domain], run, Company(ticker=ticker, name=row[0]), date_range
            )
            classes = {p.url: p.match_class for p in pairs}
            in_cache = {e.url for e in run.rows}
            absent: list[tuple[str, str | None]] = []
            for url, pub_date, _stored_class in stored:
                proof.stored += 1
                if url in in_cache:
                    cls = classes.get(url, "reject")
                    if CLASS_RANK[cls] >= CLASS_RANK["review"]:  # type: ignore[index]
                        proof.found += 1
                    else:
                        proof.rejected_now.append((ticker, url, cls))
                else:
                    absent.append((url, pub_date))
            part = _split_pairs(absent, in_cache, date_range.start)
            proof.not_in_cache.in_cache += part.in_cache
            proof.not_in_cache.before_range.update(part.before_range)
            proof.not_in_cache.left_sitemap += part.left_sitemap
    return proof


def format_evidence(ev: Evidence, date_range: DateRange) -> str:
    """The text of the PR: counts, the split, the proof."""
    sp, pr = ev.split, ev.proof
    years = ", ".join(f"{y}: {n}" for y, n in sorted(sp.before_range.items()))
    return "\n".join(
        [
            f"Domains with a filled cache : {', '.join(ev.domains) or 'none'}",
            f"Range of the cache          : {date_range.start} to {date_range.end}",
            f"Qualifying rows             : {ev.qualifying} {ev.by_domain}",
            f"  also qualifying but kept  : {ev.kept_in_candidates} (ids of the hand-labeled samples; {ev.kept_ids} ids)",
            f"  in the cache (in range)   : {sp.in_cache}",
            f"  before the range          : {sum(sp.before_range.values())} ({years})",
            f"  left the sitemap          : {sp.left_sitemap}",
            f"File                        : {ev.file_bytes} bytes, quick_check {ev.before.quick_check}",
            f"Rows now                    : discovered_urls {ev.before.discovered}, articles {ev.before.articles},"
            f" orphan articles {ev.before.orphans}, by class {ev.before.by_class}",
            f"Proof tickers               : {' '.join(pr.tickers)}",
            f"  stored accept/review      : {pr.stored}",
            f"  re-derived from the cache : {pr.found}",
            f"  in cache, NOW REJECTED    : {len(pr.rejected_now)}  (stored accept/review, in range; the plan says 0 missing)",
            f"  not in the cache          : {pr.not_in_cache.total()} (before the range"
            f" {sum(pr.not_in_cache.before_range.values())}, left the sitemap {pr.not_in_cache.left_sitemap};"
            " they stay stored)",
        ]
    )


# --- the deletion (a copy, or the real file at G3) ------------------------------------------------


@dataclass
class Reclaimed:
    deleted: int
    before: Fingerprint
    after: Fingerprint
    bytes_before: int
    bytes_after: int
    backup: Path
    seconds: float


def _same_content(a: Fingerprint, b: Fingerprint) -> bool:
    return (a.discovered, a.articles, a.orphans, a.by_class, a.user_version, a.schema_hash) == (
        b.discovered,
        b.articles,
        b.orphans,
        b.by_class,
        b.user_version,
        b.schema_hash,
    )


def verify_backup(db_path: Path, backup: Path) -> Fingerprint:
    """The backup is another file, passes `quick_check` and has the live file's fingerprint."""
    if backup.resolve() == db_path.resolve():
        raise ReclaimError("the backup is the database itself")
    if not backup.is_file():
        raise ReclaimError(f"no backup at {backup}")
    live = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=60)
    saved = sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True, timeout=60)
    try:
        want, have = fingerprint(live), fingerprint(saved)
    finally:
        live.close()
        saved.close()
    if have.quick_check != "ok":
        raise ReclaimError(f"the backup fails quick_check: {have.quick_check}")
    if not _same_content(want, have):
        raise ReclaimError(f"the backup differs from the database: {have} vs {want}")
    return want


def running_collectors() -> list[str]:
    """Processes of the gateway or the crawler (they must be stopped before the swap)."""
    out = subprocess.run(
        ["pgrep", "-af", r"apps/gateway\.py|news_collector|crawler_cli|apps/crawler"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    return [line for line in out if "pgrep" not in line and "reclaim" not in line]


def reclaim(
    db_path: Path,
    backup: Path,
    data_dir: Path,
    *,
    domains: Sequence[str] | None = None,
    expect: int | None = None,
    check_processes: bool = True,
) -> Reclaimed:
    """Delete the qualifying rows, compact with `VACUUM INTO`, swap, keep the old file as `backup`.

    `backup` must exist, pass `quick_check` and match the file before anything is written (it is
    the file the maintainer keeps). `expect` is the count the approval named: a different count
    deletes nothing. Raises `ReclaimError` before the transaction on any failed precondition and
    rolls the transaction back when a fingerprint check fails after the delete.
    """
    began = time.monotonic()
    db_path = Path(db_path)
    if check_processes and (procs := running_collectors()):
        raise ReclaimError(f"stop the gateway and the crawler first: {procs}")
    verify_backup(db_path, backup)
    size = db_path.stat().st_size
    free = shutil.disk_usage(db_path.parent).free
    if free < size:
        raise ReclaimError(f"not enough disk for VACUUM INTO: {free} free, {size} needed")
    compact = db_path.with_name(db_path.name + ".compact")
    if compact.exists():
        raise ReclaimError(f"{compact} exists: remove it first")

    conn = sqlite3.connect(db_path, timeout=60, isolation_level=None)
    try:
        _load_kept(conn, kept_ids(data_dir))
        targets = list(domains) if domains is not None else cache_domains(conn)
        where, params = candidate_where(targets)
        before = fingerprint(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]  # VACUUM INTO writes "delete"
        if before.quick_check != "ok":
            raise ReclaimError(f"the database fails quick_check: {before.quick_check}")
        will = conn.execute(
            f"SELECT COUNT(*) FROM discovered_urls WHERE {where}", params
        ).fetchone()[0]
        if expect is not None and will != expect:
            raise ReclaimError(f"the approval names {expect} rows, {will} qualify: nothing deleted")
        conn.execute("BEGIN IMMEDIATE")
        try:
            deleted = conn.execute(f"DELETE FROM discovered_urls WHERE {where}", params).rowcount
            after = fingerprint(conn)
            _check_after(before, after, deleted, will)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        conn.execute(f"VACUUM INTO '{compact}'")
    finally:
        conn.close()
    _swap_in(db_path, compact, mode, after)
    return Reclaimed(
        deleted, before, after, size, db_path.stat().st_size, backup, time.monotonic() - began
    )


def _swap_in(db_path: Path, compact: Path, mode: str, after: Fingerprint) -> None:
    """Check the compacted file against the database, give it back its journal mode and swap it in."""
    packed = sqlite3.connect(compact)
    try:
        if packed.execute(f"PRAGMA journal_mode={mode}").fetchone()[0] != mode:
            compact.unlink()
            raise ReclaimError(f"the compacted file could not be put in {mode} mode")
        packed_fp = fingerprint(packed)
    finally:
        packed.close()
    if packed_fp.quick_check != "ok" or not _same_content(after, packed_fp):
        compact.unlink()
        raise ReclaimError("the compacted file differs from the database: kept as it was")
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    compact.replace(db_path)


def _check_after(before: Fingerprint, after: Fingerprint, deleted: int, expected: int) -> None:
    problems = []
    if deleted != expected:
        problems.append(f"deleted {deleted}, {expected} qualified")
    if after.discovered != before.discovered - deleted:
        problems.append("discovered_urls did not drop by the deleted count")
    if after.articles != before.articles:
        problems.append("articles changed")
    if after.orphans != 0 or before.orphans != 0:
        problems.append(f"orphan articles: {before.orphans} before, {after.orphans} after")
    if after.kept_rows() != before.kept_rows():
        problems.append("accept/review rows changed")
    if after.quick_check != "ok":
        problems.append(f"quick_check {after.quick_check}")
    if problems:
        raise ReclaimError("; ".join(problems) + ": rolled back")
