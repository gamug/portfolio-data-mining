"""Shared helper for opening either a local SQLite file or a remote Turso/
libSQL database off the same $DATABASE_URL env var news_collector,
extractor, and news_nlp each read independently (see CLAUDE.md).
$DATABASE_URL is a plain filesystem path by default; pointing it at a
`libsql://...` URL instead (with $TURSO_AUTH_TOKEN set) switches every
connect() call site in all three modules over to Turso with no other code
change -- the "one-line env change" CLAUDE.md's DATABASE_URL comment always
described.

The `libsql` package's remote (non-embedded-replica) Connection was
empirically verified (2026-08-26, against a live Turso database) to differ
from sqlite3.Connection in two ways this codebase depends on:
  - it has no `.row_factory` attribute at all -- assigning one raises
    AttributeError -- so every fetch comes back as a plain tuple instead of
    a dict/index-addressable row.
  - its Cursor isn't iterable directly (`for row in cursor` raises
    TypeError) -- only fetchone()/fetchall()/fetchmany() work.
Everything else this codebase actually uses behaved identically to
sqlite3: execute/executemany/executescript, `?` placeholders, commit/
rollback, `PRAGMA foreign_keys = ON`, `PRAGMA table_info(...)`, `ALTER
TABLE ... ADD/DROP COLUMN`, `cursor.rowcount`/`.lastrowid`/`.description`,
and FK constraint enforcement. `PRAGMA busy_timeout` and `PRAGMA
journal_mode` are rejected outright by Turso's server ("SQL not allowed
statement") -- both are local-WAL-file concerns Turso has no equivalent
knob for, so callers must skip them when is_remote_url() is true (see
news_nlp/db.py, extractor/db.py, news_collector/storage/queue.py).

_Row/_TursoCursor/TursoConnection below patch over just those two gaps so
the rest of the codebase's `row["col"]` / positional-unpack / `dict(row)`
call sites work unchanged against either backend.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from typing import Any, cast

_REMOTE_URL_PREFIXES = ("libsql://", "http://", "https://", "ws://", "wss://")


def is_remote_url(database_url: str) -> bool:
    """True if `database_url` is a libsql/Turso remote URL rather than a
    local filesystem path."""
    return database_url.startswith(_REMOTE_URL_PREFIXES)


class _Row(Sequence[Any]):
    """sqlite3.Row-alike: row["col"], row[0], tuple-unpacking, and
    dict(row) (via .keys()) all work, backed by a plain result tuple plus
    its cursor's column names -- libsql's remote Cursor has no row_factory
    support of its own (see module docstring)."""

    __slots__ = ("_columns", "_values")

    def __init__(self, values: tuple[Any, ...], columns: tuple[str, ...]) -> None:
        self._values = values
        self._columns = columns

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            try:
                return self._values[self._columns.index(key)]
            except ValueError:
                raise KeyError(key) from None
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._columns

    def __repr__(self) -> str:
        return f"<Row {dict(zip(self._columns, self._values, strict=True))}>"


class _TursoCursor:
    """Wraps a raw libsql cursor so fetch* return _Row objects (matching a
    sqlite3.Connection(row_factory=sqlite3.Row) cursor) and so it's
    iterable (the raw one isn't -- see module docstring)."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def _columns(self) -> tuple[str, ...]:
        return tuple(d[0] for d in self._raw.description) if self._raw.description else ()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _TursoCursor:
        self._raw.execute(sql, params)
        return self

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> _TursoCursor:
        self._raw.executemany(sql, seq_of_params)
        return self

    def fetchone(self) -> _Row | None:
        row = self._raw.fetchone()
        return None if row is None else _Row(row, self._columns())

    def fetchall(self) -> list[_Row]:
        columns = self._columns()
        return [_Row(r, columns) for r in self._raw.fetchall()]

    def fetchmany(self, size: int | None = None) -> list[_Row]:
        columns = self._columns()
        rows = self._raw.fetchmany(size) if size is not None else self._raw.fetchmany()
        return [_Row(r, columns) for r in rows]

    def __iter__(self) -> Iterator[_Row]:
        # The raw libsql cursor raises TypeError on direct iteration (see
        # module docstring) -- fetchall() is the one confirmed-working path.
        return iter(self.fetchall())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)  # rowcount, lastrowid, description, ...


class TursoConnection:
    """Wraps a remote `libsql.connect(...)` Connection so it satisfies the
    subset of sqlite3.Connection's interface this codebase relies on. See
    module docstring for what was verified and what's patched."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        # Accepted for API parity with every call site's
        # `conn.row_factory = sqlite3.Row` -- rows from this connection are
        # always Row-shaped regardless (see _TursoCursor), so the value
        # stored here is never actually read.
        self.row_factory: Any = None

    def cursor(self) -> _TursoCursor:
        return _TursoCursor(self._raw.cursor())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _TursoCursor:
        return _TursoCursor(self._raw.execute(sql, params))

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> _TursoCursor:
        return _TursoCursor(self._raw.executemany(sql, seq_of_params))

    def executescript(self, script: str) -> None:
        self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def open_connection(
    database_url: str, *, auth_token: str | None = None, **sqlite3_kwargs: Any
) -> sqlite3.Connection:
    """Open a connection to whatever `database_url` points at: a local
    sqlite3.Connection for a filesystem path, or a Turso-backed one for a
    `libsql://...` URL (requires `auth_token`, i.e. $TURSO_AUTH_TOKEN).

    `sqlite3_kwargs` (e.g. `check_same_thread=False`) are forwarded to
    `sqlite3.connect()` in the local case only -- libsql's remote connect()
    doesn't take them, and none of the ones this codebase passes have a
    remote equivalent.

    Typed as returning sqlite3.Connection even in the remote case: every
    call site across news_collector/extractor/news_nlp already types its
    `conn` parameters as sqlite3.Connection, and real callers only ever use
    the subset of the interface TursoConnection reproduces (see module
    docstring) -- casting here instead of re-typing every one of those call
    sites is the smaller, equally-correct change.
    """
    if is_remote_url(database_url):
        import libsql  # noqa: PLC0415 -- lazy: only pulled in when actually pointed at Turso

        if not auth_token:
            raise RuntimeError(
                f"DATABASE_URL ({database_url!r}) looks like a remote libsql/Turso "
                "URL but $TURSO_AUTH_TOKEN is not set."
            )
        turso_conn = TursoConnection(libsql.connect(database_url, auth_token=auth_token))
        return cast(sqlite3.Connection, turso_conn)
    # **sqlite3_kwargs being typed Any widens sqlite3.connect(...)'s own
    # return type to Any too -- the explicit annotation here (not a cast)
    # is what actually re-narrows it back to Connection for mypy.
    local_conn: sqlite3.Connection = sqlite3.connect(database_url, **sqlite3_kwargs)
    return local_conn
