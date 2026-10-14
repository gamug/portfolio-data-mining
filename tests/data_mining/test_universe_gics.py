"""Tests for data_mining/universe_gics.py -- the leaver GICS backfill:
parsing a revision's constituents table (both historical layouts), picking
the last revision that lists a stint's CIK+symbol, filling a copy of the
universe DB, and the report (filled / unfilled / stability).

Hermetic: no test hits the network (the MediaWiki fetches are monkeypatched
or served by an httpx.MockTransport) and no test touches a real
data/universe.db (UNIVERSE_DB_PATH is monkeypatched to a tmp_path file).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from data_mining import queries, universe_gics
from data_mining.universe_gics import Revision

# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


def _table(rows: list[dict[str, str]], *, nine_col: bool) -> str:
    """A minimal constituents wikitable in one of the page's two historical
    layouts: 2022 revisions carry an extra "SEC filings" column, 2023+ do
    not."""
    lines = ['{| class="wikitable sortable" id="constituents"', "|-"]
    lines.append("![[Ticker symbol|Symbol]]")
    if nine_col:
        lines.append(
            "! Security !![[SEC filing]]s !! [[GICS Sector|GICS]] Sector !! GICS Sub-Industry "
            "!! Headquarters Location !! Date first added!! [[Central Index Key|CIK]] !! Founded"
        )
    else:
        lines.append(
            "! Security !! [[GICS Sector|GICS]] Sector !! GICS Sub-Industry "
            "!! Headquarters Location !! Date added !! [[Central Index Key|CIK]] !! Founded"
        )
    for row in rows:
        lines.append("|-")
        lines.append(f"|{{{{NasdaqSymbol|{row['symbol']}}}}}")
        filings = "||[https://example.test reports] " if nine_col else ""
        lines.append(
            f"|{row['security']}{filings}|| {row['sector']} || {row['sub_industry']} "
            f"|| [[Nowhere]] || 2000-01-01 || {row['cik']} || 1990"
        )
    lines.append("|}")
    return "\n".join(lines)


def _row(
    symbol: str,
    cik: str,
    *,
    sector: str | None = None,
    sub_industry: str | None = None,
    valid_from: str = "2000-01-01",
    valid_to: str | None = None,
    source: str = "wikipedia_changes_backfill",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "security": f"{symbol} Inc.",
        "gics_sector": sector,
        "gics_sub_industry": sub_industry,
        "hq_location": "Nowhere",
        "date_added": "2000-01-01",
        "cik": cik,
        "founded": "1990",
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source": source,
    }


def _rev(revid: int, timestamp: str) -> Revision:
    return Revision(revid, datetime.fromisoformat(timestamp))


GPS = {
    "symbol": "GPS",
    "security": "Gap",
    "sector": "Consumer Discretionary",
    "sub_industry": "Apparel Retail",
    "cik": "0000039911",
}
DISCK = {
    "symbol": "DISCK",
    "security": "Discovery (Series C)",
    "sector": "Communication Services",
    "sub_industry": "Broadcasting",
    "cik": "0001437107",
}
DISCA = {
    "symbol": "DISCA",
    "security": "Discovery (Series A)",
    "sector": "Communication Services",
    "sub_industry": "Broadcasting",
    "cik": "0001437107",
}
WBD = {
    "symbol": "WBD",
    "security": "Warner Bros. Discovery",
    "sector": "Communication Services",
    "sub_industry": "Broadcasting",
    "cik": "0001437107",
}


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------


def test_parse_nine_column_layout_finds_rows_by_cik_and_symbol() -> None:
    rows = universe_gics.parse_constituents_wikitext(_table([DISCK, DISCA], nine_col=True))

    assert len(rows) == 2
    assert rows[0]["SEC filings"] == "reports"
    disck = universe_gics.find_row(rows, "0001437107", "DISCK")
    disca = universe_gics.find_row(rows, "0001437107", "DISCA")
    assert disck is not None and disck["Symbol"] == "DISCK"
    assert disca is not None and disca["Symbol"] == "DISCA"
    assert disck["GICS Sector"] == "Communication Services"


def test_parse_eight_column_layout() -> None:
    rows = universe_gics.parse_constituents_wikitext(_table([GPS], nine_col=False))

    assert len(rows) == 1
    assert rows[0]["Symbol"] == "GPS"
    assert rows[0]["GICS Sub-Industry"] == "Apparel Retail"
    assert "SEC filings" not in rows[0]


def test_parse_missing_table_returns_empty() -> None:
    assert universe_gics.parse_constituents_wikitext("<html>page structure changed</html>") == []


def test_parse_counts_dropped_rows() -> None:
    # The second data row is one cell short of the header, so it cannot be
    # mapped -- it must be counted, not silently dropped.
    text = """
{| class="wikitable sortable" id="constituents"
|-
![[Ticker symbol|Symbol]]
! Security !! [[GICS Sector|GICS]] Sector !! GICS Sub-Industry !! Headquarters Location !! Date added !! [[Central Index Key|CIK]] !! Founded
|-
|{{NasdaqSymbol|AAA}}
|[[A]]|| Industrials || Widgets || [[X]] || 2000-01-01 || 0000000001 || 1990
|-
|{{NasdaqSymbol|BBB}}
|[[B]]|| Industrials || Widgets || [[X]] || 2000-01-01 || 0000000002
|}
"""
    parsed = universe_gics.parse_constituents_table(text)

    assert parsed.dropped == 1
    assert [row["Symbol"] for row in parsed.rows] == ["AAA"]
    assert [row["Symbol"] for row in universe_gics.parse_constituents_wikitext(text)] == ["AAA"]


def test_find_row_requires_symbol_match() -> None:
    rows = universe_gics.parse_constituents_wikitext(_table([WBD], nine_col=False))

    # The CIK matches, but the symbol is a later entity reusing it.
    assert universe_gics.find_row(rows, "0001437107", "DISCK") is None
    assert universe_gics.find_row(rows, "0001437107", "WBD") is not None


def test_clean_cell_strips_templates_links_and_refs() -> None:
    assert universe_gics._clean_cell("{{NyseSymbol|MMM}}") == "MMM"
    assert universe_gics._clean_cell("[[Central Index Key|CIK]]") == "CIK"
    assert universe_gics._clean_cell("[https://x.test reports]") == "reports"
    assert universe_gics._clean_cell("3M<ref name='a'>x</ref>") == "3M"


# ---------------------------------------------------------------------
# MediaWiki fetch
# ---------------------------------------------------------------------


def test_fetch_revision_list_paginates_and_sorts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(httpx.QueryParams(request.url.query))
        if "rvcontinue" in params:
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "revisions": [
                                    {"revid": 2, "timestamp": "2022-02-01T00:00:00Z"},
                                ]
                            }
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "continue": {"rvcontinue": "20220101|1"},
                "query": {
                    "pages": [
                        {
                            "revisions": [
                                {"revid": 1, "timestamp": "2022-01-01T00:00:00Z"},
                            ]
                        }
                    ]
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    revisions = universe_gics.fetch_revision_list(date(2021, 1, 1), date(2022, 12, 31), client)
    client.close()

    assert [r.revid for r in revisions] == [1, 2]
    assert revisions[1].timestamp == datetime.fromisoformat("2022-02-01T00:00:00+00:00")


def test_fetch_revision_content_returns_wikitext() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {"revisions": [{"revid": 1, "slots": {"main": {"content": "WIKITEXT"}}}]}
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert universe_gics.fetch_revision_content(1, client) == "WIKITEXT"
    client.close()


def test_fetch_revision_contents_batches_fifty_per_call() -> None:
    seen_batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(httpx.QueryParams(request.url.query))
        revids = params["revids"].split("|")
        seen_batches.append(revids)
        pages = [
            {
                "revisions": [
                    {"revid": int(revid), "slots": {"main": {"content": f"c{revid}"}}}
                    for revid in revids
                ]
            }
        ]
        return httpx.Response(200, json={"query": {"pages": pages}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    contents = universe_gics.fetch_revision_contents(list(range(1, 61)), client)
    client.close()

    assert [len(b) for b in seen_batches] == [50, 10]
    assert contents[1] == "c1"
    assert contents[60] == "c60"


def test_get_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = universe_gics._get(client, {})
    client.close()

    assert response.json() == {"ok": True}
    assert calls["n"] == 2


# ---------------------------------------------------------------------
# Revision picking
# ---------------------------------------------------------------------


def test_pick_revision_walks_back_to_last_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions = [
        _rev(1, "2022-01-01T00:00:00Z"),
        _rev(2, "2022-02-01T00:00:00Z"),
        _rev(3, "2022-02-15T00:00:00Z"),
    ]
    contents = {
        1: _table([GPS], nine_col=False),
        2: _table([], nine_col=False),
        3: _table([], nine_col=False),
    }
    monkeypatch.setattr(
        universe_gics, "fetch_revision_content", lambda revid, client=None: contents[revid]
    )
    stint = {
        "symbol": "GPS",
        "cik": "0000039911",
        "valid_from": "2021-06-01",
        "valid_to": "2022-02-20",
    }

    picked = universe_gics._pick_revision(
        stint, revisions, client=None, cache={}, stats=universe_gics.ParseStats()
    )

    assert picked["revision"] is not None and picked["revision"].revid == 1
    assert picked["depth"] == 3
    # The two newer candidates did not list the CIK at all.
    assert [s["outcome"] for s in picked["skipped"]] == ["cik_absent", "cik_absent"]
    assert all(s["cik_present"] is False for s in picked["skipped"])
    assert [s["revid"] for s in picked["skipped"]] == [3, 2]


def test_pick_revision_is_bounded_by_valid_from(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions = [
        _rev(1, "2022-01-01T00:00:00Z"),
        _rev(2, "2022-02-01T00:00:00Z"),
        _rev(3, "2022-02-15T00:00:00Z"),
    ]
    contents = {
        1: _table([GPS], nine_col=False),
        2: _table([], nine_col=False),
        3: _table([], nine_col=False),
    }
    monkeypatch.setattr(
        universe_gics, "fetch_revision_content", lambda revid, client=None: contents[revid]
    )
    # valid_from is after rev 1, so the walk-back must stop before reaching it.
    stint = {
        "symbol": "GPS",
        "cik": "0000039911",
        "valid_from": "2022-01-15",
        "valid_to": "2022-02-20",
    }

    picked = universe_gics._pick_revision(
        stint, revisions, client=None, cache={}, stats=universe_gics.ParseStats()
    )

    assert picked["revision"] is None
    assert picked["depth"] == 2


def test_pick_revision_reports_no_revision_in_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # The newest revision at or before valid_to predates valid_from.
    revisions = [_rev(1, "2023-06-19T00:00:00Z")]
    contents = {1: _table([GPS], nine_col=False)}
    monkeypatch.setattr(
        universe_gics, "fetch_revision_content", lambda revid, client=None: contents[revid]
    )
    stint = {
        "symbol": "GPS",
        "cik": "0000039911",
        "valid_from": "2023-07-03",
        "valid_to": "2023-07-05",
    }

    picked = universe_gics._pick_revision(
        stint, revisions, client=None, cache={}, stats=universe_gics.ParseStats()
    )

    assert picked["revision"] is None
    assert picked["depth"] == 0
    assert picked["reason"] == "no revision dated within valid_from..valid_to"


def test_pick_revision_reports_symbol_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions = [_rev(1, "2022-04-10T12:00:00Z")]
    contents = {1: _table([WBD], nine_col=False)}
    monkeypatch.setattr(
        universe_gics, "fetch_revision_content", lambda revid, client=None: contents[revid]
    )
    stint = {
        "symbol": "DISCK",
        "cik": "0001437107",
        "valid_from": "2014-08-06",
        "valid_to": "2022-04-10",
    }

    picked = universe_gics._pick_revision(
        stint, revisions, client=None, cache={}, stats=universe_gics.ParseStats()
    )

    assert picked["revision"] is None
    assert picked["reason"] == "symbol_mismatch"
    assert picked["other_symbols"] == ("WBD",)
    assert picked["skipped"][0]["outcome"] == "symbol_differs"
    assert picked["skipped"][0]["symbols_for_cik"] == ["WBD"]


# ---------------------------------------------------------------------
# fill_gics -- end to end on a tmp DB
# ---------------------------------------------------------------------

_REVISIONS = [
    _rev(0, "2021-12-30T00:00:00Z"),
    _rev(1, "2022-02-01T00:00:00Z"),
    _rev(2, "2022-02-07T00:00:00Z"),
    _rev(3, "2022-03-01T00:00:00Z"),
    _rev(4, "2022-04-06T00:00:00Z"),
    _rev(5, "2022-04-11T00:00:00Z"),
]
_CONTENTS = {
    0: _table([GPS, DISCK], nine_col=True),
    1: _table([GPS, DISCK], nine_col=True),
    2: _table([DISCK], nine_col=True),
    3: _table([DISCK], nine_col=True),
    4: _table([DISCK, DISCA], nine_col=True),
    5: _table([WBD], nine_col=False),
}


@pytest.fixture(autouse=True)
def _isolated_universe_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(tmp_path / "universe.db"))
    monkeypatch.setenv("NAME", "Test Name")
    monkeypatch.setenv("EMAIL", "test@example.org")
    # Keep the hermetic suite fast: no real pacing between mocked requests.
    monkeypatch.setattr(universe_gics, "REQUEST_DELAY_SECONDS", 0.0)


def _seed(db) -> None:
    queries.write_intervals(
        db,
        [
            _row(
                "CUR",
                "0000000009",
                sector="Industrials",
                sub_industry="Widgets",
                source="live_snapshot",
            ),
            _row("DISCK", "0001437107", valid_from="2014-08-06", valid_to="2022-04-10"),
            _row("GPS", "0000039911", valid_to="2022-02-02"),
            _row("OLD", "0000000008", valid_to="2010-01-01"),
        ],
    )


def _fresh_db():
    """A tmp DB with the GICS citation columns added, the way the builder's
    output copy gets them (the read path never migrates)."""
    db = queries._connect()
    queries.migrate_gics_columns(db)
    return db


def _patch_fetches(
    monkeypatch: pytest.MonkeyPatch, contents: dict[int, str] | None = None
) -> dict[str, int]:
    contents = _CONTENTS if contents is None else contents
    calls = {"n": 0}

    monkeypatch.setattr(
        universe_gics, "fetch_revision_list", lambda start, end, client=None: _REVISIONS
    )

    def batched(revids: list[int] | set[int], client: object = None) -> dict[int, str]:
        calls["n"] += 1
        return {revid: contents[revid] for revid in revids if revid in contents}

    def single(revid: int, client: object = None) -> str:
        calls["n"] += 1
        return contents[revid]

    monkeypatch.setattr(universe_gics, "fetch_revision_contents", batched)
    monkeypatch.setattr(universe_gics, "fetch_revision_content", single)
    return calls


def test_fill_gics_fills_targets_and_cites_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _fresh_db()
    _seed(db)
    calls = _patch_fetches(monkeypatch)

    report = universe_gics.fill_gics(db)

    assert report["targets"] == 2
    assert {f["symbol"] for f in report["filled"]} == {"DISCK", "GPS"}
    assert report["unfilled"] == []
    by_symbol = {f["symbol"]: f for f in report["filled"]}
    assert by_symbol["GPS"]["gics_revision_id"] == "1"
    assert by_symbol["GPS"]["gics_revision_timestamp"] == "2022-02-01T00:00:00Z"
    assert by_symbol["GPS"]["gics_sector"] == "Consumer Discretionary"
    assert by_symbol["GPS"]["walk_back_depth"] == 1
    assert by_symbol["DISCK"]["gics_revision_id"] == "4"
    assert by_symbol["DISCK"]["matched_symbol"] == "DISCK"

    rows = {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM universe_membership")}
    assert rows["GPS"]["gics_sector"] == "Consumer Discretionary"
    assert rows["GPS"]["gics_revision_id"] == "1"
    assert rows["DISCK"]["gics_sub_industry"] == "Broadcasting"
    # Rows the fill did not target keep NULL citation columns.
    assert rows["CUR"]["gics_revision_id"] is None
    assert rows["CUR"]["gics_revision_timestamp"] is None
    assert rows["CUR"]["gics_sector"] == "Industrials"
    assert rows["OLD"]["gics_revision_id"] is None
    assert rows["OLD"]["gics_sector"] is None
    assert calls["n"] > 0
    db.close()


def test_fill_gics_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _fresh_db()
    _seed(db)
    _patch_fetches(monkeypatch)
    universe_gics.fill_gics(db)
    before = {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM universe_membership")}
    calls = _patch_fetches(monkeypatch)

    second = universe_gics.fill_gics(db)

    after = {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM universe_membership")}
    assert second["filled"] == []
    assert second["already_filled"] == 2
    assert calls["n"] == 0  # no network on a filled file
    assert before == after
    db.close()


def test_fill_gics_reports_stability_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _fresh_db()
    _seed(db)
    contents = dict(_CONTENTS)
    # 30 days before GPS's chosen revision (rev 1) is rev 0; give it a
    # different label so the report flags the change without altering the fill.
    contents[0] = _table([{**GPS, "sector": "Industrials"}, DISCK], nine_col=True)
    _patch_fetches(monkeypatch, contents)

    report = universe_gics.fill_gics(db)

    assert [d["symbol"] for d in report["stability_differences"]] == ["GPS"]
    diff = report["stability_differences"][0]
    assert diff["older_sector"] == "Industrials"
    assert diff["chosen_sector"] == "Consumer Discretionary"
    # The fill keeps the revision's own label.
    rows = {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM universe_membership")}
    assert rows["GPS"]["gics_sector"] == "Consumer Discretionary"
    db.close()


# ---------------------------------------------------------------------
# validate_paths / queries
# ---------------------------------------------------------------------


def test_validate_paths_rejects_same_source_and_out(tmp_path: Path) -> None:
    path = tmp_path / "u.db"
    with pytest.raises(ValueError, match="must differ"):
        universe_gics.validate_paths(path, path)


def test_validate_paths_rejects_protected_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"universe_history\.db"):
        universe_gics.validate_paths(tmp_path / "src.db", tmp_path / "universe_history.db")
    with pytest.raises(ValueError, match=r"universe\.db"):
        universe_gics.validate_paths(tmp_path / "src.db", tmp_path / "universe.db")


def test_validate_paths_accepts_a_new_name(tmp_path: Path) -> None:
    universe_gics.validate_paths(tmp_path / "src.db", tmp_path / "out.db")


def test_gics_columns_are_migrated_and_exposed_in_public_dict() -> None:
    db = _fresh_db()
    _seed(db)
    columns = set(db.table_columns("universe_membership"))
    assert {"gics_revision_id", "gics_revision_timestamp"} <= columns
    row = db.execute("SELECT * FROM universe_membership WHERE symbol = 'CUR'").fetchone()
    public = queries._row_to_public_dict(row)
    assert public["gics_revision_id"] is None
    assert public["gics_revision_timestamp"] is None
    db.close()


def test_fetch_gics_targets_excludes_members_with_an_open_stint() -> None:
    db = _fresh_db()
    _seed(db)
    # CUR has an open stint, so it is not a target even if it later closes;
    # OLD predates the cutoff.
    targets = queries.fetch_gics_targets(db, "2022-01-01")
    assert {t["symbol"] for t in targets} == {"DISCK", "GPS"}
    db.close()


# ---------------------------------------------------------------------
# Read path must never mutate the file it opens
# ---------------------------------------------------------------------

_OLD_SCHEMA_DDL = """
CREATE TABLE universe_membership (
    symbol            TEXT NOT NULL,
    security          TEXT NOT NULL,
    gics_sector       TEXT,
    gics_sub_industry TEXT,
    hq_location       TEXT,
    date_added        TEXT,
    cik               TEXT,
    founded           TEXT,
    valid_from        TEXT NOT NULL,
    valid_to          TEXT,
    source            TEXT NOT NULL
);
CREATE INDEX idx_membership_symbol ON universe_membership (symbol);
CREATE INDEX idx_membership_valid   ON universe_membership (valid_from, valid_to);
"""


def _make_old_schema_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(_OLD_SCHEMA_DDL)
    con.execute(
        "INSERT INTO universe_membership VALUES "
        "('AAA','A','Industrials','Widgets','Nowhere','2000-01-01','0000000001',"
        "'1990','2000-01-01',NULL,'live_snapshot')"
    )
    con.commit()
    con.close()


def test_read_path_leaves_old_schema_file_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway's `/universe` route opens `$UNIVERSE_DB_PATH` for reading;
    that file is sha1-tracked and must not change. A file predating the GICS
    citation columns is read with those fields as None, not migrated."""
    path = tmp_path / "old.db"
    _make_old_schema_db(path)
    before = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()

    monkeypatch.setenv("UNIVERSE_DB_PATH", str(path))
    db = queries._connect()
    try:
        rows = queries.query_membership_as_of(db, "2024-01-01")
    finally:
        db.close()

    after = hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()
    assert before == after
    assert rows and rows[0]["Symbol"] == "AAA"
    assert rows[0]["gics_revision_id"] is None
    assert rows[0]["gics_revision_timestamp"] is None


def test_read_path_does_not_add_gics_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.db"
    _make_old_schema_db(path)
    monkeypatch.setenv("UNIVERSE_DB_PATH", str(path))

    db = queries._connect()
    try:
        columns = set(db.table_columns("universe_membership"))
    finally:
        db.close()

    assert "gics_revision_id" not in columns
    assert "gics_revision_timestamp" not in columns


# A revision whose table has a malformed row (cell count != header).
_MALFORMED_TABLE = """
{| class="wikitable sortable" id="constituents"
|-
![[Ticker symbol|Symbol]]
! Security !! [[GICS Sector|GICS]] Sector !! GICS Sub-Industry !! Headquarters Location !! Date added !! [[Central Index Key|CIK]] !! Founded
|-
|{{NasdaqSymbol|GPS}}
|[[Gap]]|| Consumer Discretionary || Apparel Retail || [[X]] || 1986-08-31 || 0000039911
|}
"""


def test_fill_gics_raises_when_the_parser_drops_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _fresh_db()
    _seed(db)
    contents = dict.fromkeys(range(6), _MALFORMED_TABLE)
    _patch_fetches(monkeypatch, contents)

    with pytest.raises(ValueError, match="parser dropped"):
        universe_gics.fill_gics(db)
    db.close()


# ---------------------------------------------------------------------
# build_gics_db: idempotent re-run, atomic output, source checks
# ---------------------------------------------------------------------


def _source_db(tmp_path: Path) -> Path:
    path = tmp_path / "source.db"
    db = queries.connect(path)
    _seed(db)
    db.close()
    return path


def test_build_gics_db_rerun_keeps_the_filled_file_and_skips_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_db(tmp_path)
    out = tmp_path / "out_gics.db"
    _patch_fetches(monkeypatch)
    first = universe_gics.build_gics_db(source, out)
    assert len(first["filled"]) == 2
    before = out.read_bytes()
    calls = _patch_fetches(monkeypatch)

    second = universe_gics.build_gics_db(source, out)

    assert second["filled"] == []
    assert second["already_filled"] == 2
    assert calls["n"] == 0
    assert out.read_bytes() == before
    assert not out.with_name(out.name + ".tmp").exists()


def test_build_gics_db_failure_leaves_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_db(tmp_path)
    out = tmp_path / "out_gics.db"
    _patch_fetches(monkeypatch, dict.fromkeys(range(6), _MALFORMED_TABLE))

    with pytest.raises(ValueError, match="parser dropped"):
        universe_gics.build_gics_db(source, out)

    assert not out.exists()
    assert not out.with_name(out.name + ".tmp").exists()


def test_build_gics_db_rejects_a_missing_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        universe_gics.build_gics_db(tmp_path / "nope.db", tmp_path / "out.db")


# ---------------------------------------------------------------------
# Wikimedia self-identification
# ---------------------------------------------------------------------


def test_mediawiki_headers_use_name_and_email_from_env() -> None:
    agent = universe_gics.mediawiki_headers()["User-Agent"]
    assert "Test Name" in agent
    assert "test@example.org" in agent
    assert "example.com" not in agent


@pytest.mark.parametrize("missing", ["NAME", "EMAIL"])
def test_mediawiki_headers_require_a_real_contact(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match="NAME and EMAIL"):
        universe_gics.mediawiki_headers()
