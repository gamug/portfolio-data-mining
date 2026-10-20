"""Reclaim the stored sitemap rejects that were never fetched (T-073, Work item 15, gate G3).

`T-071` stops storing the pairs the matcher rejects, so the 578,113 `skipped` `cnbc.com` rejects of
the `KLAC` run are dead weight: no article, never fetched, and re-derivable from the sitemap cache
by `reclass-sitemap` (T-072) if the matcher ever changes. Three entry points, all usable on a
**copy** with no approval, and the writing ones on the real file only at G3:

* `gather_evidence` is read-only: what qualifies, how it splits (in the cache, before the range,
  left the sitemap), what the hand-labeled samples keep, and the proof that the cache re-derives
  every in-range stored `accept`/`review` pair of a sample of tickers. (Filling the cache first is
  `news_collector.sitemap_fill`.)
* `reclaim` deletes the qualifying rows in one transaction, checks the fingerprint before and
  after, writes a compacted file with `VACUUM INTO`, checks it, and swaps it in. The old file
  stays as the backup until the maintainer deletes it.
* `compact` does the compaction and the swap with no delete: it finishes a `reclaim` that raised
  `ReclaimIncomplete` (the delete committed, the compaction failed).

The rows that qualify are exactly `candidate_where`: `match_class='reject'`, `status='skipped'`,
no `articles` row, source `sitemap` **only** (the proof re-derives stored pairs from the sitemap
cache, which no `rss` pair can be), a domain whose latest complete crawl holds entries, and an id
that no committed hand-labeled sample names. Nothing else is ever deleted.

Locking: `reclaim` and `compact` hold the file exclusively (WAL: it fails while any other
connection has it open) from the first read until the compacted file has been checked in full; only
the unlink of `-wal`/`-shm` and the rename follow the close. A last read of the file in place then
compares its counts with the checked ones.
"""

# ruff: noqa: S608, TRY301 - every SQL string is built from fixed text and runs of "?" marks; values are bound
from __future__ import annotations

import csv
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import stat
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
from news_collector.sitemap_cache import SitemapCache, SitemapRun

log = logging.getLogger(__name__)

SAMPLE_GLOBS = ("ticker_*_sample.csv", "ticker_*_manual.csv")
# Only `sitemap`: the proof re-derives stored pairs from the sitemap cache, which no `rss` pair can
# be. 0 `rss` rows qualified on the rehearsal copy; add `rss` back only with a proof that covers it.
SOURCES = ("sitemap",)
WAL_HEADROOM = (
    0  # bytes of free disk required beyond the file size (set from the measured WAL peak)
)
LOCK_WAIT = 10  # seconds `reclaim` waits for the exclusive lock before it says the file is in use
_YEAR_IN_URL = re.compile(r"/((?:19|20)\d\d)/\d\d/\d\d/")
# The tickers the proof always includes: the ordinary-word and one-letter ones are on purpose.
PROOF_TICKERS = ("KLAC", "AAPL", "ON", "A", "IT", "ARE", "TTD", "MSFT", "NVDA", "CRM")


class ReclaimError(Exception):
    """A check failed before anything was committed: the file is as it was."""


class ReclaimIncomplete(ReclaimError):
    """The delete is COMMITTED in the file but the step after it failed: the file has lost the
    rows and is not compacted (the backup is intact). `compact` finishes it."""


def kept_ids(data_dir: Path) -> set[int]:
    """The ids named by the committed hand-labeled samples (`data/ticker_*_sample|manual.csv`).

    Refuses when a kind of sample file is missing or names no id: with a wrong `data_dir` the
    delete would run with nothing protected, and say nothing.
    """
    ids: set[int] = set()
    for pattern in SAMPLE_GLOBS:
        paths = sorted(data_dir.glob(pattern))
        if not paths:
            raise ReclaimError(f"no {pattern} in {data_dir}: the labeled ids are not protected")
        for path in paths:
            ids.update(_ids_of(path))
    if not ids:
        raise ReclaimError(f"the sample files in {data_dir} name no id")
    return ids


def _ids_of(path: Path) -> set[int]:
    """The ids of one sample file. A file with no `id` column would protect nothing, silently."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if "id" not in (reader.fieldnames or []):
            raise ReclaimError(f"{path.name} has no id column: its rows would not be protected")
        try:
            return {int(r["id"]) for r in reader if r.get("id")}
        except ValueError as exc:
            raise ReclaimError(f"{path.name} has an id that is not a number: {exc}") from exc


def cache_domains(conn: sqlite3.Connection) -> list[str]:
    """Domains whose latest complete crawl holds entries: an empty crawl is not a filled cache."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT domain FROM sitemap_crawls c WHERE complete = 1 AND started_at = "
            "(SELECT MAX(started_at) FROM sitemap_crawls WHERE domain = c.domain AND complete = 1) "
            "AND entries > 0 ORDER BY domain"
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
    id_sum: int = 0  # of `discovered_urls.id`: a backup of other rows with the same counts differs
    max_id: int = 0

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
        id_sum=one("SELECT COALESCE(SUM(id), 0) FROM discovered_urls"),
        max_id=one("SELECT COALESCE(MAX(id), 0) FROM discovered_urls"),
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
                domain,
                connector.config.sitemap_roots,
                db_path,
                _never,
                connector.is_article_url,
                read_only=True,
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
    runs: dict[str, SitemapRun],
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
                    if CLASS_RANK.get(cls, 0) >= CLASS_RANK["review"]:
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
    kept: int = 0  # protected sample ids
    owner_kept: bool = True  # False: the new file's owner is not the original's


def _same_content(a: Fingerprint, b: Fingerprint) -> bool:
    return (
        a.discovered,
        a.articles,
        a.orphans,
        a.by_class,
        a.user_version,
        a.schema_hash,
        a.id_sum,
        a.max_id,
    ) == (
        b.discovered,
        b.articles,
        b.orphans,
        b.by_class,
        b.user_version,
        b.schema_hash,
        b.id_sum,
        b.max_id,
    )


def verify_backup(db_path: Path, backup: Path) -> Fingerprint:
    """The backup is another file, passes `quick_check` and has the live file's fingerprint."""
    if backup.resolve() == db_path.resolve():
        raise ReclaimError("the backup is the database itself")
    if not backup.is_file():
        raise ReclaimError(f"no backup at {backup}")
    try:
        live = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=60)
        saved = sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True, timeout=60)
        try:
            want, have = fingerprint(live), fingerprint(saved)
        finally:
            live.close()
            saved.close()
    except sqlite3.Error as exc:
        raise ReclaimError(f"the backup or the database cannot be read: {exc}") from exc
    if have.quick_check != "ok":
        raise ReclaimError(f"the backup fails quick_check: {have.quick_check}")
    if not _same_content(want, have):
        raise ReclaimError(f"the backup differs from the database: {have} vs {want}")
    return want


def running_collectors() -> list[str]:
    """Processes of the gateway or the crawler by command line (they must be stopped first).

    A missing `pgrep` is not an error here: `open_handles` and the exclusive lock of `reclaim`
    do not need it.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-af", r"apps/gateway\.py|news_collector|crawler_cli|apps/crawler"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
    except OSError:
        return []
    return [line for line in out if "pgrep" not in line and "reclaim" not in line]


def open_handles(db_path: Path) -> list[str]:
    """Processes on this host (this one excluded) that hold the database, its `-wal` or `-shm` open.

    Reads `/proc/*/fd`, so it sees any command line. A process of another user may not be
    readable: the exclusive lock of `reclaim` is the check that does not depend on who it is.
    """
    base = str(Path(db_path).resolve())
    targets = {base, base + "-wal", base + "-shm"}
    me = str(os.getpid())
    found: list[str] = []
    try:
        links = list(Path("/proc").glob("[0-9]*/fd/*"))
    except OSError:
        return found
    for link in links:
        pid = link.parts[2]
        if pid == me:
            continue
        try:
            target = os.readlink(link)
        except OSError:
            continue
        if target in targets:
            found.append(f"pid {pid} has {target} open")
    return found


def _open_exclusive(db_path: Path) -> sqlite3.Connection:
    """A connection that holds the file exclusively until it closes, or a `ReclaimError`.

    In WAL mode the exclusive lock cannot be taken while any other connection has the file open,
    even an idle one, on this host, whoever owns it. While it is held no other process can read or
    write the file, so nothing starts on it between the delete and the swap.
    """
    conn = sqlite3.connect(db_path, timeout=LOCK_WAIT, isolation_level=None)
    try:
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.execute("PRAGMA journal_mode").fetchone()  # the first read of the file takes the lock
    except sqlite3.OperationalError as exc:
        conn.close()
        raise ReclaimError(
            f"another connection has {db_path} open ({exc}): stop the gateway, the crawler and "
            "every other program that opens it"
        ) from exc
    return conn


def _check_start(db_path: Path, check_processes: bool) -> None:
    if check_processes and (procs := running_collectors() + open_handles(db_path)):
        raise ReclaimError(f"stop the gateway and the crawler first: {procs}")
    size = db_path.stat().st_size
    free = shutil.disk_usage(db_path.parent).free
    need = size + WAL_HEADROOM
    if free < need:
        raise ReclaimError(
            f"not enough disk for the delete and VACUUM INTO: {free} free, {need} needed"
        )
    compact = db_path.with_name(db_path.name + ".compact")
    if compact.exists():
        raise ReclaimError(f"{compact} exists: remove it first")


def _plan(
    conn: sqlite3.Connection, domains: Sequence[str] | None, expect: int, backup_fp: Fingerprint
) -> tuple[str, list[str], Fingerprint, str]:
    """The WHERE clause, the fingerprint and the journal mode, after the checks that need the file."""
    filled = cache_domains(conn)
    if domains is not None and (missing := [d for d in domains if d not in filled]):
        raise ReclaimError(f"no complete cache crawl for {missing}: nothing deleted")
    where, params = candidate_where(list(domains) if domains is not None else filled)
    before = fingerprint(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]  # VACUUM INTO writes "delete"
    if before.quick_check != "ok":
        raise ReclaimError(f"the database fails quick_check: {before.quick_check}")
    if not _same_content(before, backup_fp):
        raise ReclaimError(
            "the file changed after the backup was checked (a program wrote to it): "
            "make the backup again"
        )
    will = conn.execute(f"SELECT COUNT(*) FROM discovered_urls WHERE {where}", params).fetchone()[0]
    if will != expect:
        raise ReclaimError(f"the approval names {expect} rows, {will} qualify: nothing deleted")
    return where, params, before, mode


def _delete(
    conn: sqlite3.Connection, where: str, params: list[str], before: Fingerprint, expect: int
) -> tuple[int, Fingerprint]:
    """One transaction: delete, check the fingerprint (a failure rolls back), commit."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        deleted = conn.execute(f"DELETE FROM discovered_urls WHERE {where}", params).rowcount
        after = fingerprint(conn)
        _check_after(before, after, deleted, expect)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return deleted, after


def reclaim(
    db_path: Path,
    backup: Path,
    data_dir: Path,
    *,
    expect: int,
    expect_kept: int | None = None,
    domains: Sequence[str] | None = None,
    check_processes: bool = True,
) -> Reclaimed:
    """Delete the qualifying rows, compact with `VACUUM INTO`, swap, keep the old file as `backup`.

    `backup` must exist, pass `quick_check` and match the file (counts, classes, ids, schema)
    before anything is written; it is the file the maintainer keeps. `expect` is the count the
    approval named: a different count deletes nothing; `expect_kept` does the same for the number
    of protected sample ids. The file is held exclusively from the first read until the compacted
    file has been fully checked; only the unlink of `-wal`/`-shm` and the rename follow the close.
    A last read of the file in place compares its counts and `user_version` with the checked ones.

    Two kinds of failure. `ReclaimError`: nothing was committed, the file is as it was (a failed
    fingerprint check after the delete rolls it back). `ReclaimIncomplete`: the delete is
    committed but the compaction or the swap failed, so the file has lost the rows and is not
    compacted; the backup is intact and `compact` finishes the job.
    """
    began = time.monotonic()
    db_path = Path(db_path)
    kept = kept_ids(data_dir)
    if expect_kept is not None and len(kept) != expect_kept:
        raise ReclaimError(f"the approval names {expect_kept} protected ids, {len(kept)} found")
    _check_start(db_path, check_processes)
    backup_fp = verify_backup(db_path, backup)
    size = db_path.stat().st_size
    compact = db_path.with_name(db_path.name + ".compact")

    conn = _open_exclusive(db_path)
    deleted = 0
    committed = False
    try:
        _load_kept(conn, kept)
        where, params, before, mode = _plan(conn, domains, expect, backup_fp)
        deleted, after = _delete(conn, where, params, before, expect)
        committed = True
        _write_compact(conn, compact)
        # everything slow is checked while the lock is still held; only the rename follows the close
        owner_kept = _prepare_compact(db_path, compact, mode, after)
        conn.close()
        _install(db_path, compact)
    except BaseException as exc:
        if committed:  # whatever failed after the commit, the rows are gone from the file
            compact.unlink(missing_ok=True)
            raise _incomplete(deleted, db_path, backup, exc) from exc
        if isinstance(exc, ReclaimError):
            raise
        if isinstance(exc, sqlite3.Error | OSError):  # the transaction was rolled back
            raise ReclaimError(f"the delete failed and was rolled back ({exc})") from exc
        raise
    finally:
        conn.close()
    try:
        _check_installed(db_path, after)
    except Exception as exc:
        raise ReclaimIncomplete(
            f"INCOMPLETE: the compacted file of {db_path} was swapped in but the last check of the "
            f"file in place failed ({exc}). The backup {backup} is intact: restore it."
        ) from exc
    return Reclaimed(
        deleted,
        before,
        after,
        size,
        db_path.stat().st_size,
        backup,
        time.monotonic() - began,
        kept=len(kept),
        owner_kept=owner_kept,
    )


def _incomplete(deleted: int, db_path: Path, backup: Path, why: BaseException) -> ReclaimIncomplete:
    return ReclaimIncomplete(
        f"INCOMPLETE: the delete of {deleted} rows is COMMITTED in {db_path}, but the file is not "
        f"compacted ({why}). The backup {backup} is intact. Finish with the `compact` command, or "
        "restore the backup."
    )


def _write_compact(conn: sqlite3.Connection, compact: Path) -> None:
    conn.execute("VACUUM INTO ?", (str(compact),))


def check_backup_file(db_path: Path, backup: Path) -> None:
    """The backup exists, is another file and passes `quick_check`. Not compared to the database:
    after an `INCOMPLETE` the database has lost the rows the backup still has."""
    if backup.resolve() == db_path.resolve():
        raise ReclaimError("the backup is the database itself")
    if not backup.is_file():
        raise ReclaimError(f"no backup at {backup}")
    try:
        saved = sqlite3.connect(f"file:{backup.resolve()}?mode=ro", uri=True, timeout=60)
        try:
            result = saved.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            saved.close()
    except sqlite3.Error as exc:
        raise ReclaimError(f"the backup cannot be read: {exc}") from exc
    if result != "ok":
        raise ReclaimError(f"the backup fails quick_check: {result}")


def compact(
    db_path: Path, *, backup: Path | None = None, check_processes: bool = True
) -> Reclaimed:
    """Compact and swap, with no delete: the step that finishes a `ReclaimIncomplete`.

    The file keeps every row it has now; the compacted file is checked against it and swapped in.
    Nothing is committed, so any failure leaves the file as it was (a `ReclaimError`). `backup`,
    when given, must exist and pass `quick_check`.
    """
    began = time.monotonic()
    db_path = Path(db_path)
    if backup is not None:
        check_backup_file(db_path, backup)
    _check_start(db_path, check_processes)
    size = db_path.stat().st_size
    packed = db_path.with_name(db_path.name + ".compact")
    conn = _open_exclusive(db_path)
    try:
        before = fingerprint(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if before.quick_check != "ok":
            raise ReclaimError(f"the database fails quick_check: {before.quick_check}")
        _write_compact(conn, packed)
        owner_kept = _prepare_compact(db_path, packed, mode, before)  # the lock is still held
        conn.close()
        _install(db_path, packed)
    except BaseException as exc:
        packed.unlink(missing_ok=True)
        if isinstance(exc, ReclaimError):
            raise
        if isinstance(exc, sqlite3.Error | OSError):
            raise ReclaimError(f"the file is as it was ({exc})") from exc
        raise
    finally:
        conn.close()
    try:
        _check_installed(db_path, before)
    except Exception as exc:
        raise ReclaimError(
            f"swapped in, but the last check of the file in place failed ({exc})"
        ) from exc
    return Reclaimed(
        0,
        before,
        before,
        size,
        db_path.stat().st_size,
        db_path,
        time.monotonic() - began,
        kept=0,
        owner_kept=owner_kept,
    )


def _prepare_compact(db_path: Path, compact: Path, mode: str, expected: Fingerprint) -> bool:
    """Check the compacted file against the database, give it back its journal mode, mode bits and
    owner. Returns whether the owner is the original's. The caller still holds the lock on
    `db_path`: this is the slow part (a `quick_check` of the whole file) and must not run unlocked.
    """
    packed = sqlite3.connect(compact)
    try:
        if packed.execute(f"PRAGMA journal_mode={mode}").fetchone()[0] != mode:
            raise ReclaimError(f"the compacted file could not be put in {mode} mode")
        packed_fp = fingerprint(packed)
    finally:
        packed.close()
    if packed_fp.quick_check != "ok" or not _same_content(expected, packed_fp):
        raise ReclaimError("the compacted file differs from the database: not swapped in")
    return _copy_permissions(db_path, compact)


def _install(db_path: Path, compact: Path) -> None:
    """Drop the old file's `-wal`/`-shm` and rename the compacted file over it: milliseconds."""
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    compact.replace(db_path)


def _check_installed(db_path: Path, expected: Fingerprint) -> None:
    """A last cheap read of the file in place (counts, not `quick_check`): the checked file."""
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=60)
    try:
        got = (
            conn.execute("PRAGMA user_version").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        )
    finally:
        conn.close()
    want = (expected.user_version, expected.discovered, expected.articles)
    if got != want:
        raise ReclaimError(f"user_version, discovered_urls, articles are {got}, expected {want}")


def _copy_permissions(original: Path, new: Path) -> bool:
    """The compacted file is made by this user with the default umask: give it the original's mode
    and, when permitted, owner. False when the owner could not be kept (the CLI warns)."""
    info = original.stat()
    new.chmod(stat.S_IMODE(info.st_mode))
    if not hasattr(os, "getuid"):  # Windows: no owner to keep
        return True
    if (info.st_uid, info.st_gid) == (os.getuid(), os.getgid()):
        return True
    try:
        os.chown(new, info.st_uid, info.st_gid)
    except PermissionError:
        log.warning("the compacted file is owned by uid %s, not %s", os.getuid(), info.st_uid)
        return False
    return True


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
