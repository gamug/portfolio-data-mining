"""Tests for src/common/universe_history.py -- point-in-time S&P 500
membership: parsing the "Historical components of the S&P 500" changes
table, backward reconstruction, forward snapshotting, and as_of queries.

No test hits the network (Wikipedia fetches go through an injected
httpx.Client wired to an httpx.MockTransport) and no test touches a real
data/universe.db (UNIVERSE_DB_PATH is monkeypatched to a tmp_path file).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from common import universe_history

# Mirrors the verified live structure of table id="changes": a top header
# row (rowspan=2 Effective Date/Reason/Refs, colspan=2 Added/Removed) plus a
# Ticker/Security sub-header row, then 6-7 <td> data rows (Refs optional).
CHANGES_HTML = """
<html><body>
<table id="changes" class="wikitable sortable">
<tr>
  <th rowspan="2">Effective Date</th>
  <th colspan="2">Added</th>
  <th colspan="2">Removed</th>
  <th rowspan="2">Reason</th>
  <th rowspan="2">Refs</th>
</tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>June 1, 2021</td><td>aaa</td><td>Company A</td><td>bbb</td><td>Company B</td>
    <td>X replaced Y</td><td>[1]</td></tr>
<tr><td>March 1, 2015</td><td>bbb</td><td>Company B</td><td>ccc</td><td>Company C</td>
    <td>Y replaced Z</td></tr>
<tr><td>January 1, 2010</td><td>ddd</td><td>Company D</td><td></td><td></td>
    <td>Index expansion</td><td>[2]</td></tr>
<tr><td>January 1, 2005</td><td></td><td></td><td>eee</td><td>Company E</td>
    <td>Company delisted</td><td>[3]</td></tr>
</table>
</body></html>
"""

NO_TABLE_HTML = "<html><body><p>Page structure changed.</p></body></html>"


@pytest.fixture(autouse=True)
def _isolated_universe_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(tmp_path / "universe.db"))


def _today_roster(*symbols: str) -> list[dict]:
    return [
        {
            "Symbol": s,
            "Security": f"Company {s}",
            "GICS Sector": "Industrials",
            "GICS Sub-Industry": "Widgets",
            "Headquarters Location": "Nowhere",
            "Date added": "2021-06-01",
            "CIK": "1",
            "Founded": "2000",
        }
        for s in symbols
    ]


# ---------------------------------------------------------------------
# _parse_changes_table
# ---------------------------------------------------------------------


def test_parse_changes_table_extracts_all_rows() -> None:
    events = universe_history._parse_changes_table(CHANGES_HTML)

    assert len(events) == 4
    assert events[0].effective_date == date(2021, 6, 1)
    assert events[0].added_ticker == "AAA"
    assert events[0].removed_ticker == "BBB"


def test_parse_changes_table_row_without_refs_cell_still_parses() -> None:
    events = universe_history._parse_changes_table(CHANGES_HTML)

    march_event = events[1]
    assert march_event.effective_date == date(2015, 3, 1)
    assert march_event.reason == "Y replaced Z"


def test_parse_changes_table_add_only_row() -> None:
    events = universe_history._parse_changes_table(CHANGES_HTML)

    add_only = events[2]
    assert add_only.added_ticker == "DDD"
    assert add_only.removed_ticker == ""


def test_parse_changes_table_remove_only_row() -> None:
    events = universe_history._parse_changes_table(CHANGES_HTML)

    remove_only = events[3]
    assert remove_only.removed_ticker == "EEE"
    assert remove_only.added_ticker == ""


def test_parse_changes_table_missing_table_raises() -> None:
    with pytest.raises(ValueError, match="'changes' table not found"):
        universe_history._parse_changes_table(NO_TABLE_HTML)


def test_fetch_changes_html_uses_injected_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == universe_history.CHANGES_URL
        return httpx.Response(200, text=CHANGES_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    html = universe_history._fetch_changes_html(client=client)
    client.close()

    assert "changes" in html


# ---------------------------------------------------------------------
# backfill_from_changes + query_as_of -- the core reconstruction test
# ---------------------------------------------------------------------


def test_backfill_reconstructs_membership_at_event_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Today: only AAA is live. Events (most-recent-first): AAA replaced BBB
    # on 2021-06-01; BBB replaced CCC on 2015-03-01.
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("AAA"))
    monkeypatch.setattr(
        universe_history,
        "_fetch_changes_html",
        lambda: (
            """
<table id="changes">
<tr><th>Effective Date</th><th colspan="2">Added</th><th colspan="2">Removed</th><th>Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>June 1, 2021</td><td>AAA</td><td>Company A</td><td>BBB</td><td>Company B</td><td>r</td></tr>
<tr><td>March 1, 2015</td><td>BBB</td><td>Company B</td><td>CCC</td><td>Company C</td><td>r</td></tr>
</table>
"""
        ),
    )

    count = universe_history.backfill_from_changes()
    assert count == 3  # AAA, BBB, CCC

    # After the 2021 event: AAA in, BBB out.
    after = universe_history.query_as_of(date(2022, 1, 1))
    assert {r["Symbol"] for r in after} == {"AAA"}

    # Exactly on the boundary date: AAA already in (valid_from inclusive).
    on_boundary = universe_history.query_as_of(date(2021, 6, 1))
    assert {r["Symbol"] for r in on_boundary} == {"AAA"}

    # The day before: BBB still in, AAA not yet.
    before_boundary = universe_history.query_as_of(date(2021, 5, 31))
    assert {r["Symbol"] for r in before_boundary} == {"BBB"}

    # Between the two events: BBB in, AAA and CCC out.
    middle = universe_history.query_as_of(date(2016, 1, 1))
    assert {r["Symbol"] for r in middle} == {"BBB"}

    # CCC's valid_from is clamped to its own valid_to (2015-02-28) since its
    # "added" event predates the table's coverage -- a single-day interval,
    # not an inverted one.
    earliest = universe_history.query_as_of(date(2015, 2, 28))
    assert {r["Symbol"] for r in earliest} == {"CCC"}


def test_backfill_is_idempotent_unless_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("AAA"))
    monkeypatch.setattr(universe_history, "_fetch_changes_html", lambda: CHANGES_HTML)

    first = universe_history.backfill_from_changes()
    second = universe_history.backfill_from_changes()  # no-op, table already populated

    assert first > 0
    assert second == 0


def test_query_as_of_before_earliest_date_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("AAA"))
    monkeypatch.setattr(universe_history, "_fetch_changes_html", lambda: CHANGES_HTML)
    universe_history.backfill_from_changes()

    with pytest.raises(ValueError, match="predates the earliest supported date"):
        universe_history.query_as_of(date(1990, 1, 1))


def test_query_as_of_before_any_backfill_raises() -> None:
    with pytest.raises(ValueError, match="no data yet"):
        universe_history.query_as_of(date(2020, 1, 1))


def test_resolve_as_of_exact_and_partial_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("AAA"))
    monkeypatch.setattr(universe_history, "_fetch_changes_html", lambda: CHANGES_HTML)
    universe_history.backfill_from_changes()

    exact = universe_history.resolve_as_of("aaa", date(2026, 1, 1))
    assert exact is not None
    assert exact["Symbol"] == "AAA"

    partial = universe_history.resolve_as_of("company a", date(2026, 1, 1))
    assert partial is not None
    assert partial["Symbol"] == "AAA"

    assert universe_history.resolve_as_of("nope", date(2026, 1, 1)) is None
    assert universe_history.resolve_as_of("", date(2026, 1, 1)) is None


# ---------------------------------------------------------------------
# record_snapshot
# ---------------------------------------------------------------------


def test_record_snapshot_opens_and_closes_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed the DB with AAA already open.
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("AAA"))
    monkeypatch.setattr(universe_history, "_fetch_changes_html", lambda: CHANGES_HTML)
    universe_history.backfill_from_changes()

    # Now today's live roster has changed: AAA dropped, ZZZ added.
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("ZZZ"))

    summary = universe_history.record_snapshot()

    assert summary == {"added": ["ZZZ"], "removed": ["AAA"]}

    today = date.today()
    still_open = universe_history.query_as_of(today)
    assert {r["Symbol"] for r in still_open} == {"ZZZ"}


def test_record_snapshot_with_no_changes_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(universe_history, "list_universe", lambda: _today_roster("AAA"))
    monkeypatch.setattr(universe_history, "_fetch_changes_html", lambda: CHANGES_HTML)
    universe_history.backfill_from_changes()

    summary = universe_history.record_snapshot()

    assert summary == {"added": [], "removed": []}
