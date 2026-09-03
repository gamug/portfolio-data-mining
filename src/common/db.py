"""One SQLite connection factory for the shared pipeline database.

Both pipeline stages open their own connection to the same file (see
CLAUDE.md): `news_collector` writes `discovered_urls`, `extractor` reads
those and writes `articles`. The PRAGMA policy that keeps those concurrent
connections well-behaved used to be duplicated at each connection site --
this module is the single place it lives now.
"""

import os
import sqlite3

# $DATABASE_URL is just a filesystem path today (this is still SQLite) --
# read here from the env rather than hardcoded at each call site, so
# pointing it at a real connection string later is a one-line env change,
# not a code change across the modules that share it. Repo-root-relative
# default when unset.
DEFAULT_DB_PATH = "data/urls.db"

# Not persistent (unlike journal_mode, like foreign_keys), so every
# connection has to set it. Without it, a connection that finds the file
# locked by another one's write transaction gets an immediate
# `sqlite3.OperationalError: database is locked` instead of retrying
# internally for up to this many ms.
BUSY_TIMEOUT_MS = 30_000


def resolve_db_path() -> str:
    """The shared pipeline DB path: `$DATABASE_URL`, or the default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DB_PATH)


def enable_foreign_keys(conn: sqlite3.Connection) -> None:
    """Turn on FK constraint enforcement for this connection.

    SQLite declares FK constraints in the schema but, for
    backward-compatibility reasons, does not enforce them unless this pragma
    is set on every connection -- it is not a persistent DB setting. Without
    it, `articles.id -> discovered_urls.id` is documentation only, not a
    guarantee.
    """
    conn.execute("PRAGMA foreign_keys = ON")


def connect(
    db_path: str,
    *,
    wal: bool = False,
    foreign_keys: bool = False,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a connection configured the way the pipeline expects.

    Always: rows returned as `sqlite3.Row`, `busy_timeout` set.

    `wal=True` also sets `journal_mode=WAL` + `synchronous=NORMAL` -- the
    writer path (`news_collector`'s `URLQueue`). `journal_mode` is
    persistent at the file level, so setting it once there is enough; the
    other stages inherit it.

    `foreign_keys=True` also sets `PRAGMA foreign_keys=ON` -- the
    `extractor` path, so the `articles -> discovered_urls` FK is enforced.

    `check_same_thread=False` lifts sqlite3's thread-binding (needed by
    `URLQueue`, which is shared across an async event loop).
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    if foreign_keys:
        enable_foreign_keys(conn)
    return conn
