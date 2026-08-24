"""Manual edit/delete operations on sentiment, entity, and category results.

Kept separate from db.py's pipeline write-path (write_sentiment/write_entities/
write_category) since this is a distinct concern: human correction of model
output rather than model-generated writes. Deleting a sentiment row (or all of
an article's entities, or its category) makes that article eligible for
reprocessing again, since db.fetch_pending_articles/fetch_pending_category_articles
select rows missing from the result table.
"""

import sqlite3
from datetime import UTC, datetime
from typing import Any

# field name -> literal "<column> = ?" SQL fragment. update_*() below select the
# SET-clause fragments to join from these fixed maps rather than interpolating
# the caller-supplied field name into the query string directly, so the SQL text
# is always drawn from a hardcoded set of literals, never built from runtime
# input, even though runtime input (validated against the same map's keys)
# selects which fragments are used.
_SENTIMENT_SET_CLAUSES = {
    "label": "label = ?",
    "score": "score = ?",
    "positive": "positive = ?",
    "negative": "negative = ?",
    "neutral": "neutral = ?",
}
_ENTITY_SET_CLAUSES = {
    "entity_type": "entity_type = ?",
    "text": "text = ?",
    "start_char": "start_char = ?",
    "end_char": "end_char = ?",
    "score": "score = ?",
}
# Deliberately excludes the 9 raw distribution columns: those exist as an
# audit trail of what the model actually scored (for CATEGORY_CONFIDENCE_
# THRESHOLD tuning), so a human correction only changes the winning label/
# score, never rewrites the original NLI breakdown.
_CATEGORY_SET_CLAUSES = {
    "label": "label = ?",
    "score": "score = ?",
}
_SENTIMENT_FIELDS = set(_SENTIMENT_SET_CLAUSES)
_ENTITY_FIELDS = set(_ENTITY_SET_CLAUSES)
_CATEGORY_FIELDS = set(_CATEGORY_SET_CLAUSES)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def update_sentiment(conn: sqlite3.Connection, article_id: int, **fields: Any) -> dict | None:
    unknown = set(fields) - _SENTIMENT_FIELDS
    if unknown:
        raise ValueError(f"Unknown sentiment field(s): {unknown}")

    if fields:
        # S608: `fields` is checked against _SENTIMENT_FIELDS above, and
        # set_clause is built only from the fixed _SENTIMENT_SET_CLAUSES
        # "col = ?" fragments -- values are always bound as query params.
        set_clause = ", ".join(_SENTIMENT_SET_CLAUSES[k] for k in fields) + ", processed_at = ?"
        params = [*list(fields.values()), _now_iso(), article_id]
        cur = conn.execute(
            f"UPDATE article_sentiment SET {set_clause} WHERE article_id = ?",  # noqa: S608
            params,
        )
        if cur.rowcount == 0:
            return None

    row = conn.execute(
        "SELECT * FROM article_sentiment WHERE article_id = ?", (article_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_sentiment(conn: sqlite3.Connection, article_id: int) -> bool:
    cur = conn.execute("DELETE FROM article_sentiment WHERE article_id = ?", (article_id,))
    return cur.rowcount > 0


def update_entity(conn: sqlite3.Connection, entity_id: int, **fields: Any) -> dict | None:
    unknown = set(fields) - _ENTITY_FIELDS
    if unknown:
        raise ValueError(f"Unknown entity field(s): {unknown}")

    if fields:
        # S608: `fields` is checked against _ENTITY_FIELDS above, and
        # set_clause is built only from the fixed _ENTITY_SET_CLAUSES
        # "col = ?" fragments -- values are always bound as query params.
        set_clause = ", ".join(_ENTITY_SET_CLAUSES[k] for k in fields)
        params = [*list(fields.values()), entity_id]
        cur = conn.execute(f"UPDATE article_entities SET {set_clause} WHERE id = ?", params)  # noqa: S608
        if cur.rowcount == 0:
            return None

    row = conn.execute("SELECT * FROM article_entities WHERE id = ?", (entity_id,)).fetchone()
    return dict(row) if row else None


def delete_entity(conn: sqlite3.Connection, entity_id: int) -> bool:
    cur = conn.execute("DELETE FROM article_entities WHERE id = ?", (entity_id,))
    return cur.rowcount > 0


def delete_entities_for_article(conn: sqlite3.Connection, article_id: int) -> int:
    cur = conn.execute("DELETE FROM article_entities WHERE article_id = ?", (article_id,))
    return cur.rowcount


def update_category(conn: sqlite3.Connection, article_id: int, **fields: Any) -> dict | None:
    unknown = set(fields) - _CATEGORY_FIELDS
    if unknown:
        raise ValueError(f"Unknown category field(s): {unknown}")

    if fields:
        # S608: `fields` is checked against _CATEGORY_FIELDS above, and
        # set_clause is built only from the fixed _CATEGORY_SET_CLAUSES
        # "col = ?" fragments -- values are always bound as query params.
        set_clause = ", ".join(_CATEGORY_SET_CLAUSES[k] for k in fields) + ", processed_at = ?"
        params = [*list(fields.values()), _now_iso(), article_id]
        cur = conn.execute(f"UPDATE article_category SET {set_clause} WHERE article_id = ?", params)  # noqa: S608
        if cur.rowcount == 0:
            return None

    row = conn.execute(
        "SELECT * FROM article_category WHERE article_id = ?", (article_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_category(conn: sqlite3.Connection, article_id: int) -> bool:
    cur = conn.execute("DELETE FROM article_category WHERE article_id = ?", (article_id,))
    return cur.rowcount > 0
