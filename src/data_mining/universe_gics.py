"""data_mining/universe_gics.py

GICS sector / sub-industry backfill for the S&P 500 leaver stints.

`universe_history.backfill_from_changes()` reconstructs each leaver's
`valid_from`/`valid_to` interval but leaves `gics_sector` /
`gics_sub_industry` empty -- the live snapshot (`live_snapshot` rows) only
classifies today's members. This module fills those two columns for the
leaver stints from the revision history of Wikipedia's "List of S&P 500
companies" page, and records the citation (the MediaWiki revision id and its
timestamp) on the same row.

Rule (from the downstream request,
`portfolio-financial-analysis`'s `docs/upstream_requests/data_mining_leaver_gics.md`):
for each stint, use the LAST revision of the page that still lists the company
and is dated on or before the stint's `valid_to`; find the row by CIK and
check the symbol. A revision that no longer lists the company is skipped --
the edit that removes a company can land on or before its `valid_to`, and a
CIK can be reused by a later entity (CIK 0001437107 is Discovery's DISCA and
DISCK, then Warner Bros. Discovery's WBD), so the symbol check is what keeps
the classes and the reused CIK apart. Labels are copied exactly as written in
that revision -- a pre-2023-03-17 label is never translated into today's GICS
structure.

The page's column set changed over time (2022 revisions carry an extra "SEC
filings" column; 2023+ do not), so the revision table is parsed by header
name, not by fixed position -- unlike `data_mining.portfolio`'s live
`_parse_constituents_table`, which is positional and only ever sees the
current page.

`universe-gics` is an explicit, documented build step run after the
backfill (`cli/pricing_cli.py`); it is idempotent (re-running on an
existing `--out` resumes from it, and a fully filled one is left unchanged
with no network call) and writes to an explicit file built atomically via a
temporary sibling, never to the default `data/universe.db` or the delivered
`universe_history.db`. Requests self-identify with the `NAME`/`EMAIL` env
vars (`mediawiki_headers`). The two citation
columns are added to that copy by the builder
(`queries.migrate_gics_columns`), never on open -- so the gateway's
`/universe` read path never ALTERs the sha1-tracked file it opens, and
`queries._row_to_public_dict` reports the columns as None when a file
predates them.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from portfolio_common.db import Database

from data_mining import queries

log = logging.getLogger(__name__)

MEDIAWIKI_API_URL = "https://en.wikipedia.org/w/api.php"
SP500_PAGE_TITLE = "List of S&P 500 companies"

# The walk-back window: the earliest valid_to is 2022-02-02, so the revision
# list starts a year earlier to let an early-2022 exit step behind its own
# window. The walk-back itself is bounded by each stint's valid_from.
REVISION_WINDOW_START = date(2021, 1, 1)

# Same cutoff as the request's "90 stints" definition.
GICS_CUTOFF = "2022-01-01"

# The stability check compares each chosen label with the same CIK+symbol's
# row in the newest revision at least this many days older.
STABILITY_DAYS = 30

_REVISION_LIST_LIMIT = 500

# MediaWiki caps `revids` at 50 per query, so content is fetched in batches
# rather than one request per revision -- far fewer requests, which keeps the
# builder well inside Wikimedia's rate limit.
_REVISION_BATCH_SIZE = 50

# Wikimedia rate-limits bursts from a shared egress with HTTP 429; spacing
# serial requests keeps the builder inside the limit (measured: 0.5s is
# enough). Tests set this to 0 via an autouse fixture.
REQUEST_DELAY_SECONDS = 0.5


def mediawiki_headers() -> dict[str, str]:
    """Wikimedia asks that automated callers self-identify with a working
    contact (constitution Technological #6), so the User-Agent is built from
    the same `NAME`/`EMAIL` env vars the EDGAR client uses. Raises
    `ValueError` when either is unset rather than send a placeholder."""
    name = os.environ.get("NAME", "").strip()
    email = os.environ.get("EMAIL", "").strip()
    if not name or not email:
        raise ValueError(
            "NAME and EMAIL must be set (see .env.example): Wikimedia requires "
            "automated callers to identify themselves with a real contact"
        )
    return {"User-Agent": f"portfolio-data-mining/1.0 ({name}; {email})"}


def _new_client() -> httpx.Client:
    return httpx.Client(headers=mediawiki_headers())


_PROTECTED_OUTPUT_NAMES = {"universe_history.db", "universe.db"}

# Columns the parser needs from a revision's constituents table.
_SYMBOL = "Symbol"
_SECTOR = "GICS Sector"
_SUB_INDUSTRY = "GICS Sub-Industry"
_CIK = "CIK"


@dataclass(frozen=True)
class Revision:
    """One MediaWiki revision of the S&P 500 page."""

    revid: int
    timestamp: datetime


@dataclass
class ParsedTable:
    """A revision's parsed constituents table: mapped rows plus a count of
    rows the parser could not map (cell count != header count)."""

    rows: list[dict[str, str]]
    dropped: int


@dataclass
class ParseStats:
    """Aggregate parser accounting across every revision examined, so the
    builder can fail loudly if any row was dropped (expected: zero)."""

    revisions: int = 0
    rows_dropped: int = 0
    dropped_revids: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------
# MediaWiki API
# ---------------------------------------------------------------------


def _utc_iso(day: date, *, end_of_day: bool) -> str:
    moment = datetime(
        day.year,
        day.month,
        day.day,
        23 if end_of_day else 0,
        59 if end_of_day else 0,
        59 if end_of_day else 0,
        tzinfo=UTC,
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(client: httpx.Client, params: dict[str, Any], *, attempts: int = 8) -> httpx.Response:
    """One MediaWiki API GET with backoff. Wikimedia rate-limits bursts with
    HTTP 429; the builder runs one request at a time and retries with
    exponential backoff rather than hammering."""
    response: httpx.Response | None = None
    for attempt in range(attempts):
        if REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS)
        response = client.get(MEDIAWIKI_API_URL, params=params, timeout=60.0)
        if response.is_success:
            return response
        if response.status_code in (429, 500, 502, 503, 504):
            log.warning(
                "MediaWiki API returned %s; retrying (attempt %d/%d)",
                response.status_code,
                attempt + 1,
                attempts,
            )
            time.sleep(2**attempt)
            continue
        response.raise_for_status()
    assert response is not None
    response.raise_for_status()
    raise RuntimeError("unreachable")  # pragma: no cover -- raise_for_status above


def fetch_revision_list(
    start: date,
    end: date,
    client: httpx.Client | None = None,
) -> list[Revision]:
    """Every revision of the S&P 500 page in `[start, end]`, oldest first.
    Paged through `rvcontinue`; `rvlimit` is 500, so ~900 revisions is two
    calls."""
    owns_client = client is None
    client = client or _new_client()
    try:
        params: dict[str, Any] = {
            "action": "query",
            "prop": "revisions",
            "titles": SP500_PAGE_TITLE,
            "rvprop": "ids|timestamp",
            "rvlimit": str(_REVISION_LIST_LIMIT),
            "rvdir": "newer",
            "rvstart": _utc_iso(start, end_of_day=False),
            "rvend": _utc_iso(end, end_of_day=True),
            "format": "json",
            "formatversion": "2",
        }
        revisions: list[Revision] = []
        continuation: dict[str, Any] = {}
        while True:
            data = _get(client, {**params, **continuation}).json()
            for page in data.get("query", {}).get("pages", []):
                for raw in page.get("revisions", []):
                    revisions.append(
                        Revision(int(raw["revid"]), _parse_timestamp(raw["timestamp"]))
                    )
            continuation = data.get("continue") or {}
            if not continuation:
                break
        revisions.sort(key=lambda r: (r.timestamp, r.revid))
        return revisions
    finally:
        if owns_client:
            client.close()


def fetch_revision_content(revid: int, client: httpx.Client | None = None) -> str:
    """The page's wikitext at revision `revid`."""
    contents = fetch_revision_contents([revid], client)
    if revid not in contents:
        raise ValueError(f"revision {revid} returned no content")
    return contents[revid]


def fetch_revision_contents(
    revids: list[int] | set[int],
    client: httpx.Client | None = None,
) -> dict[int, str]:
    """Wikitext for many revisions, fetched `_REVISION_BATCH_SIZE` per API
    call. Returns `{revid: wikitext}` (a revid the API omits is simply
    absent)."""
    owns_client = client is None
    client = client or _new_client()
    try:
        contents: dict[int, str] = {}
        ordered = sorted(set(revids))
        for start in range(0, len(ordered), _REVISION_BATCH_SIZE):
            batch = ordered[start : start + _REVISION_BATCH_SIZE]
            data = _get(
                client,
                {
                    "action": "query",
                    "prop": "revisions",
                    "revids": "|".join(str(revid) for revid in batch),
                    "rvprop": "ids|content",
                    "rvslots": "main",
                    "format": "json",
                    "formatversion": "2",
                },
            ).json()
            for page in data.get("query", {}).get("pages", []):
                for raw in page.get("revisions", []):
                    revid = raw.get("revid")
                    content: str | None = raw.get("slots", {}).get("main", {}).get("content")
                    if revid is not None and content is not None:
                        contents[int(revid)] = content
        return contents
    finally:
        if owns_client:
            client.close()


# ---------------------------------------------------------------------
# Parse a revision's constituents table (header-aware)
# ---------------------------------------------------------------------


def _clean_cell(cell: str) -> str:
    """Strip wiki markup so a cell reads as the rendered text."""
    cell = re.sub(r"<ref[^>]*>.*?</ref>", "", cell, flags=re.S)
    cell = re.sub(r"<ref[^>]*/>", "", cell)
    cell = re.sub(r"<!--.*?-->", "", cell, flags=re.S)
    # {{NyseSymbol|MMM}} / {{NasdaqSymbol|DISCK}} -> the ticker
    cell = re.sub(r"\{\{[^{}]*?\|([^{}|]+)\}\}", r"\1", cell)
    cell = re.sub(r"\{\{([^{}|]+)\}\}", r"\1", cell)
    # [https://... reports] -> "reports"
    cell = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", cell)
    cell = re.sub(r"\[\[[^\]|]*\|([^\]]+)\]\]", r"\1", cell)
    cell = re.sub(r"\[\[([^\]]+)\]\]", r"\1", cell)
    cell = re.sub(r"''+", "", cell)
    return re.sub(r"\s+", " ", cell).strip()


def _split_cell_line(line: str) -> list[str]:
    """Cells on one table line. A line may start with `|` or `||`; both open
    the first cell, and further cells are separated by `||`."""
    body = line[2:] if line.startswith("||") else line[1:]
    return [_clean_cell(cell) for cell in body.split("||")]


def parse_constituents_table(text: str) -> ParsedTable:
    """The constituents table in a revision's wikitext, keyed by the table's
    own header names, plus a count of rows the parser could not map.

    Returns `ParsedTable([], 0)` when the table is absent (e.g. a vandalised
    or template-only revision), so the caller's walk-back can simply step to
    an older revision. A row whose cell count differs from the header's is
    counted in `dropped` (not silently discarded): the builder fails loudly
    on a non-zero count rather than let a malformed row read as "company not
    listed"."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if 'id="constituents"' in line),
        None,
    )
    if start is None:
        return ParsedTable([], 0)
    return _parse_table_body(lines[start + 1 :])


def _parse_table_body(lines: list[str]) -> ParsedTable:
    header: list[str] = []
    rows: list[dict[str, str]] = []
    dropped = 0
    current: list[str] | None = None

    def flush() -> None:
        nonlocal dropped
        if not current or not header:
            return
        if len(current) == len(header):
            rows.append(dict(zip(header, current, strict=True)))
        else:
            dropped += 1

    for line in lines:
        if line.startswith("|}"):
            flush()
            break
        if line.startswith("|-"):
            flush()
            current = None
        elif line.startswith("!"):
            header.extend(_clean_cell(cell.lstrip("!")) for cell in line.split("!!"))
        elif line.startswith("|"):
            current = current or []
            current.extend(_split_cell_line(line))
    return ParsedTable(rows, dropped)


def parse_constituents_wikitext(text: str) -> list[dict[str, str]]:
    """The parsed row list only (see `parse_constituents_table`)."""
    return parse_constituents_table(text).rows


def find_row(rows: list[dict[str, str]], cik: str, symbol: str) -> dict[str, str] | None:
    """The row whose CIK column matches and whose symbol column matches.
    CIK alone is not unique (Discovery's two classes share one; a later
    company can reuse a delisted CIK), so the symbol is checked too."""
    wanted = symbol.upper()
    for row in rows:
        if row.get(_CIK) == cik and (row.get(_SYMBOL) or "").upper() == wanted:
            return row
    return None


# ---------------------------------------------------------------------
# Pick each stint's revision
# ---------------------------------------------------------------------


def _window_end(valid_to: date) -> datetime:
    """The last instant of `valid_to` (UTC)."""
    return datetime(valid_to.year, valid_to.month, valid_to.day, 23, 59, 59, tzinfo=UTC)


def _newest_index_at_or_before(revisions: list[Revision], window_end: datetime) -> int | None:
    """Index of the newest revision dated at or before `window_end` in the
    oldest-first `revisions`, or None if every revision is later."""
    found = None
    for index, revision in enumerate(revisions):
        if revision.timestamp <= window_end:
            found = index
        else:
            break
    return found


def _record_parse(stats: ParseStats, revision: Revision, parsed: ParsedTable) -> None:
    """Account one parsed revision, remembering any dropped rows."""
    stats.revisions += 1
    if parsed.dropped:
        stats.rows_dropped += parsed.dropped
        stats.dropped_revids.append(revision.revid)


def _unfilled_reason(depth: int, skipped: list[dict[str, Any]]) -> str:
    if depth == 0:
        # The newest revision at or before valid_to predates valid_from, so
        # the page has no revision inside this stint's own window.
        return "no revision dated within valid_from..valid_to"
    return "symbol_mismatch" if skipped[0]["cik_present"] else "cik_not_found"


def _pick_revision(
    stint: dict[str, Any],
    revisions: list[Revision],
    *,
    client: httpx.Client,
    cache: dict[int, str],
    stats: ParseStats,
) -> dict[str, Any]:
    """Walk back from the newest revision at or before `valid_to` to the
    last one that still lists this CIK+symbol, bounded by `valid_from`.
    Returns a dict with the chosen revision/row, the walk-back depth
    (candidate revisions examined), a per-candidate `skipped` explanation,
    and -- when nothing matched -- the reason and any symbols the CIK
    appeared under instead."""
    valid_from = date.fromisoformat(stint["valid_from"])
    valid_to = date.fromisoformat(stint["valid_to"])
    start_index = _newest_index_at_or_before(revisions, _window_end(valid_to))
    if start_index is None:
        return {
            "revision": None,
            "row": None,
            "depth": 0,
            "skipped": [],
            "other_symbols": (),
            "reason": "no revision at or before valid_to in the window",
        }

    depth = 0
    skipped: list[dict[str, Any]] = []
    for index in range(start_index, -1, -1):
        revision = revisions[index]
        if revision.timestamp.date() < valid_from:
            break  # bound the walk-back at the stint's own start
        depth += 1
        content = cache.get(revision.revid)
        if content is None:
            content = fetch_revision_content(revision.revid, client)
            cache[revision.revid] = content
        parsed = parse_constituents_table(content)
        _record_parse(stats, revision, parsed)
        symbols_for_cik = sorted(
            {row.get(_SYMBOL, "") for row in parsed.rows if row.get(_CIK) == stint["cik"]}
        )
        row = find_row(parsed.rows, stint["cik"], stint["symbol"])
        if row is not None:
            return {
                "revision": revision,
                "row": row,
                "depth": depth,
                "skipped": skipped,
                "other_symbols": (),
                "reason": None,
            }
        skipped.append(
            {
                "revid": revision.revid,
                "timestamp": _format_timestamp(revision.timestamp),
                "cik_present": bool(symbols_for_cik),
                "symbols_for_cik": symbols_for_cik,
                "rows_dropped": parsed.dropped,
                "outcome": "cik_absent" if not symbols_for_cik else "symbol_differs",
            }
        )

    return {
        "revision": None,
        "row": None,
        "depth": depth,
        "skipped": skipped,
        "other_symbols": tuple(skipped[0]["symbols_for_cik"]) if skipped else (),
        "reason": _unfilled_reason(depth, skipped),
    }


def _first_candidate_revid(stint: dict[str, Any], revisions: list[Revision]) -> int | None:
    """The revid of the newest revision at or before the stint's `valid_to`
    (the first revision the walk-back would examine), or None if there is no
    such revision in the window. Used only to batch-prefetch the common
    depth-1 case."""
    index = _newest_index_at_or_before(
        revisions, _window_end(date.fromisoformat(stint["valid_to"]))
    )
    return None if index is None else revisions[index].revid


def _stability_check(
    picks: list[dict[str, Any]],
    revisions: list[Revision],
    *,
    client: httpx.Client,
    cache: dict[int, str],
    stats: ParseStats,
) -> list[dict[str, Any]]:
    """Report only: for each filled stint, compare the chosen label with the
    same CIK+symbol's row in the newest revision at least `STABILITY_DAYS`
    older. A vandalised label would differ; a legitimate reclassification
    would too, so every difference is surfaced for a human to accept or
    reject rather than silently trusted. Never changes the chosen label."""
    # Resolve each pick's older revision first, then batch-fetch the ones not
    # already cached (one or two API calls instead of one per stint).
    plan: list[tuple[dict[str, Any], Revision | None]] = []
    for pick in picks:
        cutoff = pick["_revision"].timestamp - timedelta(days=STABILITY_DAYS)
        older = None
        for candidate in revisions:
            if candidate.timestamp <= cutoff:
                older = candidate
            else:
                break
        plan.append((pick, older))
    missing = sorted({o.revid for _, o in plan if o is not None and o.revid not in cache})
    if missing:
        cache.update(fetch_revision_contents(missing, client))

    report: list[dict[str, Any]] = []
    for pick, older in plan:
        entry: dict[str, Any] = {
            "symbol": pick["symbol"],
            "cik": pick["cik"],
            "chosen_revid": pick["gics_revision_id"],
            "chosen_timestamp": pick["gics_revision_timestamp"],
            "chosen_sector": pick["gics_sector"],
            "chosen_sub_industry": pick["gics_sub_industry"],
        }
        if older is None:
            entry.update({"comparable": False, "reason": "no revision >=30 days older in window"})
            report.append(entry)
            continue
        content = cache.get(older.revid, "")
        parsed = parse_constituents_table(content)
        stats.revisions += 1
        if parsed.dropped:
            stats.rows_dropped += parsed.dropped
            stats.dropped_revids.append(older.revid)
        row = find_row(parsed.rows, pick["cik"], pick["symbol"])
        entry.update(
            {"older_revid": str(older.revid), "older_timestamp": _format_timestamp(older.timestamp)}
        )
        if row is None:
            entry.update(
                {"comparable": False, "reason": "same CIK+symbol not listed 30 days earlier"}
            )
            report.append(entry)
            continue
        same = (
            row.get(_SECTOR) == pick["gics_sector"]
            and row.get(_SUB_INDUSTRY) == pick["gics_sub_industry"]
        )
        entry.update(
            {
                "comparable": True,
                "same": same,
                "older_sector": row.get(_SECTOR),
                "older_sub_industry": row.get(_SUB_INDUSTRY),
            }
        )
        report.append(entry)
    return report


# ---------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------


def validate_paths(source: str | Path, out: str | Path) -> None:
    """Reject an output that would clobber the source or one of the two
    canonical universe databases. Raises ValueError."""
    source_path = Path(source)
    out_path = Path(out)
    if out_path.resolve() == source_path.resolve():
        raise ValueError("--out must differ from --source")
    if out_path.name in _PROTECTED_OUTPUT_NAMES:
        raise ValueError(
            f"--out must not be named {out_path.name!r} "
            "(never write the source or the default universe database)"
        )


def fill_gics(
    db: Database,
    *,
    cutoff: str = GICS_CUTOFF,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fill the GICS label + citation for every leaver stint in `db`.

    Idempotent: stints already carrying a `gics_revision_id` are skipped, so
    a second run on a filled file makes no network calls and writes nothing.
    Returns a JSON-serialisable report (filled rows, unfilled rows with
    reasons, the stability comparison)."""
    owns_client = client is None
    client = client or _new_client()
    try:
        targets = queries.fetch_gics_targets(db, cutoff)
        pending = [t for t in targets if not t.get("gics_revision_id")]
        if not pending:
            return {
                "cutoff": cutoff,
                "targets": len(targets),
                "already_filled": len(targets),
                "filled": [],
                "unfilled": [],
                "stability": [],
                "stability_differences": [],
                "parse_stats": {"revisions": 0, "rows_dropped": 0, "dropped_revids": []},
            }

        revisions = fetch_revision_list(REVISION_WINDOW_START, date.today(), client)
        log.info(
            "Loaded %d revisions of %r for the walk-back window", len(revisions), SP500_PAGE_TITLE
        )
        cache: dict[int, str] = {}
        # Prefetch each stint's first candidate (the newest revision at or
        # before valid_to) in batched calls; _pick_revision then only fetches
        # the rarer deeper walk-back revisions lazily. Cuts the API calls
        # from ~one-per-stint to a couple of batches plus the deep walk-backs.
        first_candidates = {
            revid
            for stint in pending
            if (revid := _first_candidate_revid(stint, revisions)) is not None
        }
        if first_candidates:
            cache.update(fetch_revision_contents(sorted(first_candidates), client))
        stats = ParseStats()
        picks: list[dict[str, Any]] = []
        unfilled: list[dict[str, Any]] = []
        for index, stint in enumerate(pending, start=1):
            picked = _pick_revision(stint, revisions, client=client, cache=cache, stats=stats)
            if picked["row"] is None:
                log.info(
                    "[%d/%d] %s: unfilled (%s)",
                    index,
                    len(pending),
                    stint["symbol"],
                    picked["reason"],
                )
                unfilled.append(
                    {
                        "symbol": stint["symbol"],
                        "cik": stint["cik"],
                        "valid_from": stint["valid_from"],
                        "valid_to": stint["valid_to"],
                        "reason": picked["reason"],
                        "symbols_seen_for_cik": list(picked["other_symbols"]),
                    }
                )
                continue
            revision = picked["revision"]
            row = picked["row"]
            log.info(
                "[%d/%d] %s: rev %d (depth %d) -> %s / %s",
                index,
                len(pending),
                stint["symbol"],
                revision.revid,
                picked["depth"],
                row.get(_SECTOR),
                row.get(_SUB_INDUSTRY),
            )
            picks.append(
                {
                    "symbol": stint["symbol"],
                    "cik": stint["cik"],
                    "valid_from": stint["valid_from"],
                    "valid_to": stint["valid_to"],
                    "gics_sector": row.get(_SECTOR),
                    "gics_sub_industry": row.get(_SUB_INDUSTRY),
                    "gics_revision_id": str(revision.revid),
                    "gics_revision_timestamp": _format_timestamp(revision.timestamp),
                    "walk_back_depth": picked["depth"],
                    "matched_symbol": row.get(_SYMBOL),
                    "walk_back_skipped": picked["skipped"],
                    "_revision": revision,
                }
            )

        stability = _stability_check(picks, revisions, client=client, cache=cache, stats=stats)
        differences = [
            entry for entry in stability if entry.get("comparable") and not entry.get("same")
        ]
        if stats.rows_dropped:
            dropped_revids = sorted(set(stats.dropped_revids))
            raise ValueError(
                f"parser dropped {stats.rows_dropped} constituents row(s) across "
                f"{len(dropped_revids)} revision(s) {dropped_revids} -- refusing to "
                "treat a malformed row as 'company not listed'"
            )

        queries.update_gics(
            db,
            [
                {
                    key: pick[key]
                    for key in (
                        "symbol",
                        "cik",
                        "valid_from",
                        "valid_to",
                        "gics_sector",
                        "gics_sub_industry",
                        "gics_revision_id",
                        "gics_revision_timestamp",
                    )
                }
                for pick in picks
            ],
        )

        log.info(
            "Filled %d/%d stints; %d unfilled; %d stability differences; "
            "%d revision(s) parsed, %d row(s) dropped",
            len(picks),
            len(targets),
            len(unfilled),
            len(differences),
            stats.revisions,
            stats.rows_dropped,
        )
        return {
            "cutoff": cutoff,
            "targets": len(targets),
            "already_filled": len(targets) - len(pending),
            "filled": [
                {key: value for key, value in pick.items() if not key.startswith("_")}
                for pick in picks
            ],
            "unfilled": unfilled,
            "stability": stability,
            "stability_differences": differences,
            "parse_stats": {
                "revisions": stats.revisions,
                "rows_dropped": stats.rows_dropped,
                "dropped_revids": sorted(set(stats.dropped_revids)),
            },
        }
    finally:
        if owns_client:
            client.close()


def build_gics_db(source: str | Path, out: str | Path) -> dict[str, Any]:
    """Fill the leaver GICS labels into `out` and return the fill report.

    `out` is built on a temporary sibling and swapped in only on success, so
    a failed fill never leaves a half-built file. When `out` already exists
    it is the starting point instead of `source`, so a re-run resumes from
    (and never discards) what is already filled: on a fully filled `out` it
    makes no network call and leaves the file unchanged. The source file is
    never opened for writing."""
    validate_paths(source, out)
    source_path = Path(source)
    out_path = Path(out)
    if not source_path.is_file():
        raise ValueError(f"--source {source_path} does not exist")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = out_path if out_path.exists() else source_path
    work_path = out_path.with_name(out_path.name + ".tmp")
    shutil.copy2(base, work_path)
    try:
        db = queries.connect(work_path)
        try:
            # Add the citation columns to this copy only; the read path never
            # migrates, so the source and the default universe DB stay untouched.
            queries.migrate_gics_columns(db)
            report = fill_gics(db)
        finally:
            db.close()
        work_path.replace(out_path)
    except BaseException:
        work_path.unlink(missing_ok=True)
        raise
    return report


__all__ = [
    "MEDIAWIKI_API_URL",
    "SP500_PAGE_TITLE",
    "ParseStats",
    "ParsedTable",
    "Revision",
    "build_gics_db",
    "fetch_revision_content",
    "fetch_revision_contents",
    "fetch_revision_list",
    "fill_gics",
    "find_row",
    "mediawiki_headers",
    "parse_constituents_table",
    "parse_constituents_wikitext",
    "validate_paths",
]
