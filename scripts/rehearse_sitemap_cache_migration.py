"""Rehearse the sitemap cache migration (T-069, schema version 4) on a COPY of `urls.db`.

The source is opened read-only (`mode=ro`) and copied with SQLite's backup API to a path that must
not exist and must not be the source; everything else runs on the copy. On the copy it checks, and
prints, `PRAGMA quick_check`, the fingerprint of the pipeline tables (row counts, a hash of their
definitions and indexes, the schema version) before and after `apply_schema` + `run_migrations`,
that a second run changes nothing, the rollback (`rollback_sitemap_cache`) back to the previous
fingerprint and version 3, and the schema applied again. Exit status 1 if any check fails.

    PYTHONPATH=src uv run python scripts/rehearse_sitemap_cache_migration.py \
        --source /workspaces/thesis/data/urls.db --copy /tmp/urls_rehearsal.db

The real file is never written. The copy of a 2.5 GB database takes a few minutes and as much disk.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from portfolio_common.db import Database

from data_mining.schema import (
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    apply_schema,
    rollback_sitemap_cache,
    run_migrations,
)

PIPELINE = ("discovered_urls", "articles", "discovery_progress")
CACHE_PREFIX = "sitemap_"
BACKUP_PAGES = 20000


def fingerprint(db: Database) -> tuple[dict[str, int], str, int]:
    """Row counts of the pipeline tables, a hash of their definitions and indexes, the version."""
    counts = {
        t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
        for t in PIPELINE
        if db.table_exists(t)
    }
    marks = ", ".join("?" * len(PIPELINE))
    definitions = db.execute(
        f"SELECT name, sql FROM sqlite_master WHERE name IN ({marks}) "  # noqa: S608 - marks only
        f"OR (type = 'index' AND tbl_name IN ({marks})) ORDER BY name",
        (*PIPELINE, *PIPELINE),
    ).fetchall()
    digest = hashlib.sha256(repr([tuple(r) for r in definitions]).encode()).hexdigest()[:16]
    return counts, digest, db.schema_version


def cache_objects(db: Database) -> list[str]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE ? AND name NOT LIKE 'sqlite_autoindex%' "
        "ORDER BY name",
        (f"{CACHE_PREFIX}%",),
    ).fetchall()
    return [r[0] for r in rows]


def copy_read_only(source: Path, copy: Path) -> float:
    if copy.exists():
        raise SystemExit(f"refusing to overwrite {copy}")
    if copy.resolve() == source.resolve():
        raise SystemExit("the copy must not be the source")
    began = time.perf_counter()
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(copy)
    try:
        src.backup(dst, pages=BACKUP_PAGES)
    finally:
        src.close()
        dst.close()
    return time.perf_counter() - began


def rehearse(source: Path, copy: Path, say: Callable[[str], object] = print) -> bool:
    """Run every check on the copy; return True when all hold."""
    say(f"copy {copy_read_only(source, copy):.0f}s")
    db = Database(sqlite3.connect(copy))
    ok = True

    def check(label: str, passed: bool) -> None:
        nonlocal ok
        ok = ok and passed
        say(f"{'ok  ' if passed else 'FAIL'} {label}")

    try:
        check("quick_check before", db.execute("PRAGMA quick_check").fetchone()[0] == "ok")
        before = fingerprint(db)
        say(f"before: {before}")
        began = time.perf_counter()
        apply_schema(db)
        run_migrations(db)
        db.commit()
        say(f"migrate {time.perf_counter() - began:.3f}s")
        after = fingerprint(db)
        say(f"after:  {after}")
        check("pipeline tables unchanged", after[:2] == before[:2])
        check(f"version {before[2]} -> {SCHEMA_VERSION}", after[2] == SCHEMA_VERSION)
        check("cache objects created", len(cache_objects(db)) == len(set(cache_objects(db))) > 0)
        run_migrations(db)
        apply_schema(db)
        check("second run changes nothing", fingerprint(db) == after)
        began = time.perf_counter()
        rollback_sitemap_cache(db)
        db.commit()
        say(f"rollback {time.perf_counter() - began:.3f}s")
        back = fingerprint(db)
        check("rollback: pipeline equal to before", back[:2] == before[:2])
        check(f"rollback: version {PREVIOUS_SCHEMA_VERSION}", back[2] == PREVIOUS_SCHEMA_VERSION)
        check("rollback: no cache object left", cache_objects(db) == [])
        apply_schema(db)
        db.commit()
        check("schema applied again equals the first migration", fingerprint(db) == after)
        check("quick_check after", db.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    finally:
        db.close()
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--copy", required=True, type=Path)
    args = parser.parse_args()
    sys.exit(0 if rehearse(args.source, args.copy) else 1)


if __name__ == "__main__":
    main()
