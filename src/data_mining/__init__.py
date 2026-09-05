"""data_mining: the `urls.db` crawl-pipeline connection/schema layer and the
S&P 500 universe loader (live scrape + point-in-time history), owned by
``portfolio-data-mining``.

- `db` / `schema` -- `data/urls.db`'s connection factory and canonical DDL
  (shared with the news-collector/extractor pipeline stages that write it).
- `portfolio` -- the live tracked universe: Wikipedia scrape, in-process cache,
  `list_universe`/`resolve_symbol`/`is_tracked`.
- `universe_history` / `queries` -- point-in-time (`as_of`) membership,
  backed by `data/universe.db`; `queries` holds the DB-touching functions,
  `universe_history` the parsing/reconstruction and public operations.
- `errors` -- `UpstreamDataError`, the shared upstream-failure exception type.

See this folder's README.md for what changes on the way into
``portfolio-data-mining``'s own `src/`.
"""

from __future__ import annotations

from data_mining.db import DEFAULT_DB_PATH, connect, resolve_db_path
from data_mining.errors import UpstreamDataError
from data_mining.portfolio import is_tracked, list_universe, load_universe, resolve_symbol
from data_mining.schema import (
    ARTICLE_COLUMNS,
    ARTICLES_SCHEMA,
    SCHEMA_VERSION,
    apply_schema,
    run_migrations,
)
from data_mining.universe_history import (
    backfill_from_changes,
    query_as_of,
    record_snapshot,
    resolve_as_of,
)

__all__ = [
    "ARTICLES_SCHEMA",
    "ARTICLE_COLUMNS",
    "DEFAULT_DB_PATH",
    "SCHEMA_VERSION",
    "UpstreamDataError",
    "apply_schema",
    "backfill_from_changes",
    "connect",
    "is_tracked",
    "list_universe",
    "load_universe",
    "query_as_of",
    "record_snapshot",
    "resolve_as_of",
    "resolve_db_path",
    "resolve_symbol",
    "run_migrations",
]
