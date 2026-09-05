"""Connection factory for `data/urls.db`, the shared crawl-pipeline database
this domain reads discovery/article rows from.

The actual connection engine (pragma policy, ATTACH/DETACH, statement
execution) lives in ``portfolio_common.db.Database`` -- this module only
knows this domain's own defaults: where the file lives
(:func:`resolve_db_path`) and which pragma flags a caller here is allowed to
ask for (:func:`connect`).
"""

import os

from portfolio_common.db import Database

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
# internally for up to this many ms. Passed explicitly to
# Database.connect() below rather than relying on the engine's own default
# so a future change to that default doesn't silently change this domain's
# behavior.
BUSY_TIMEOUT_MS = 30_000


def resolve_db_path() -> str:
    """The shared pipeline DB path: `$DATABASE_URL`, or the default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DB_PATH)


def connect(
    db_path: str,
    *,
    wal: bool = False,
    foreign_keys: bool = False,
    check_same_thread: bool = True,
    uri: bool = False,
) -> Database:
    """Open `db_path` configured the way this domain expects.

    Always: rows returned as `sqlite3.Row` (via the engine), `busy_timeout`
    set to `BUSY_TIMEOUT_MS`.

    `wal=True` also sets `journal_mode=WAL` + `synchronous=NORMAL` -- the
    writer path. `journal_mode` is persistent at the file level, so setting
    it once there is enough; other stages inherit it.

    `foreign_keys=True` also sets `PRAGMA foreign_keys=ON` -- so the
    `articles -> discovered_urls` FK is enforced.

    `check_same_thread=False` lifts sqlite3's thread-binding.

    `uri=True` interprets `db_path` as a `file:...` URI -- required so an
    `ATTACH DATABASE 'file:...?mode=ro'` on this connection is honored.

    Thin wrapper around `portfolio_common.db.Database.connect()`: forwards
    these flags plus this domain's own `BUSY_TIMEOUT_MS`, and returns the
    `Database` wrapper rather than a raw `sqlite3.Connection`.
    """
    return Database.connect(
        db_path,
        wal=wal,
        foreign_keys=foreign_keys,
        check_same_thread=check_same_thread,
        uri=uri,
        busy_timeout_ms=BUSY_TIMEOUT_MS,
    )
