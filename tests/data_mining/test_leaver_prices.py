"""Tests for data_mining/leaver_prices.py -- the private Tiingo fetch for the
33 delivered S&P 500 leaver lines.

Hermetic (NR-004): every Tiingo/gateway response is a hand-made synthetic
response served by an ``httpx.MockTransport``; the production DB reads and the
environment checks are monkeypatched. No test carries a real Tiingo value, and
no test touches a real ``/workspaces/thesis`` file.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from cli import pricing_cli
from data_mining import leaver_prices as lp
from data_mining.leaver_prices import LeaverPricesError, LineSpec

# ---------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------


def tiingo_row(
    day: str, close: float, volume: int, *, split: float = 1.0, div: float = 0.0
) -> dict[str, object]:
    """One synthetic Tiingo EOD row (only the fields the module reads)."""
    return {
        "date": f"{day}T00:00:00.000Z",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "adjClose": close,
        "adjHigh": close,
        "adjLow": close,
        "adjOpen": close,
        "adjVolume": volume,
        "divCash": div,
        "splitFactor": split,
    }


def _tiingo_client(by_symbol: dict[str, list[dict[str, object]]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        # /tiingo/daily/{symbol}/prices
        symbol = request.url.path.split("/")[-2]
        return httpx.Response(200, json=by_symbol[symbol])

    return httpx.Client(transport=httpx.MockTransport(handler))


def _gateway_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def _expectations(tmp_path: Path, last_closes: dict[str, float]) -> Path:
    path = tmp_path / "expectations.json"
    path.write_text(json.dumps({"last_real_close": last_closes}))
    return path


# A minimal synthetic line: three sessions, the last one a carried close.
TINY_RAW = {
    "TINY": [
        tiingo_row("2019-01-02", 10.0, 1000),
        tiingo_row("2019-01-03", 11.0, 1000),
        tiingo_row("2019-01-04", 11.0, 0),
    ]
}
TINY = LineSpec(
    "TINY",
    "0000000001",
    "TINY",
    "2019-01-04",
    3,
    2,
    "2019-01-02",
    "2019-01-03",
    (("2019-01-04", "carried"),),
)


@pytest.fixture(autouse=True)
def _set_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "test-key")


# ---------------------------------------------------------------------
# Split formula (report §4) -- made-up numbers, never a real price
# ---------------------------------------------------------------------


def test_split_cum_applies_only_to_dates_before_the_ex_date() -> None:
    rows = [
        tiingo_row("2021-06-15", 500.0, 1_000_000),
        tiingo_row("2021-06-16", 505.0, 1_000_000),
        tiingo_row("2021-06-17", 50.0, 10_000_000, split=10.0),
    ]
    factors = lp.split_cum_factors(rows)
    assert factors["2021-06-15"] == 10.0
    assert factors["2021-06-16"] == 10.0
    assert factors["2021-06-17"] == 1.0


def test_build_series_adjusts_price_and_volume_by_split() -> None:
    rows = [
        tiingo_row("2021-06-15", 500.0, 1_000_000),
        tiingo_row("2021-06-17", 50.0, 10_000_000, split=10.0),
    ]
    series = lp.build_series("TTD", rows, "2021-06-17")
    assert series[0]["adj_close"] == pytest.approx(50.0)
    assert series[0]["adj_volume"] == 10_000_000
    assert series[0]["raw_close"] == 500.0
    assert series[1]["adj_close"] == pytest.approx(50.0)
    assert series[1]["adj_volume"] == 10_000_000


def test_build_series_keeps_divcash_and_splitfactor_as_returned() -> None:
    rows = [
        tiingo_row("2020-08-07", 100.0, 1000, div=0.30),
        tiingo_row("2020-08-31", 25.0, 4000, split=4.0),
    ]
    series = lp.build_series("AAPL", rows, "2020-08-31")
    assert series[0]["divCash"] == 0.30
    assert series[1]["splitFactor"] == 4.0


def test_no_real_prices_in_committed_sources() -> None:
    """Fix 5: the committed module and this test file must use made-up numbers,
    never the real TTD/AAPL values (built here by concatenation so this guard
    does not itself contain them)."""
    forbidden = ["585" + ".76", "58" + ".576", "0." + "205"]
    sources = Path(lp.__file__).read_text() + Path(__file__).read_text()
    for value in forbidden:
        assert value not in sources


# ---------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------


def test_parse_tiingo_date_takes_the_date_part_only() -> None:
    assert lp._parse_tiingo_date("2019-01-02T00:00:00.000Z") == "2019-01-02"


# ---------------------------------------------------------------------
# Calendar check (fix 1)
# ---------------------------------------------------------------------


def test_assert_on_calendar_stops_on_a_non_session_date() -> None:
    rows = [tiingo_row("2019-01-05", 10.0, 1000)]  # a Saturday
    with pytest.raises(LeaverPricesError, match="not an NYSE session"):
        lp._assert_on_calendar(TINY, rows)


def test_assert_on_calendar_accepts_a_session_date() -> None:
    rows = [tiingo_row("2019-01-04", 10.0, 1000)]  # a Friday
    lp._assert_on_calendar(TINY, rows)  # no raise


def test_fetch_stops_on_an_off_calendar_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {"TINY": [*TINY_RAW["TINY"], tiingo_row("2019-01-05", 11.0, 5)]}  # Saturday row
    _patch_run(monkeypatch, TINY, raw)
    out = tmp_path / "out"
    with pytest.raises(LeaverPricesError, match="not an NYSE session"):
        lp.fetch_leaver_prices(
            output_dir=out,
            expectations_path=_expectations(tmp_path, {"TINY": 11.0}),
            tiingo_client=_tiingo_client(raw),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )
    assert not (out / lp.DATA_FILENAME).exists()


# ---------------------------------------------------------------------
# Trim rule (report §3)
# ---------------------------------------------------------------------


def test_detect_non_real_flags_a_carried_close() -> None:
    rows = [
        tiingo_row("2022-05-02", 50.0, 1_000_000),
        tiingo_row("2022-05-03", 50.0, 0),  # carried
        tiingo_row("2022-05-04", 51.0, 1_000_000),
    ]
    assert lp.detect_non_real("XYZ", rows) == {"2022-05-03": "carried"}


def test_detect_non_real_does_not_flag_a_real_same_close_row() -> None:
    rows = [
        tiingo_row("2022-05-02", 50.0, 1_000_000),
        tiingo_row("2022-05-03", 50.0, 800_000),  # same close, real volume
    ]
    assert lp.detect_non_real("XYZ", rows) == {}


def test_detect_non_real_flags_otc_after_delisting() -> None:
    # SIVBQ: the delisting day does not reprice later -> the day itself is otc.
    rows = [
        tiingo_row("2023-03-09", 40.0, 1_000_000),
        tiingo_row("2023-03-10", 41.5, 11_000_000),
        tiingo_row("2023-03-13", 41.5, 2_000),
    ]
    assert lp.detect_non_real("SIVBQ", rows) == {
        "2023-03-10": "otc",
        "2023-03-13": "otc",
    }


def test_detect_non_real_flags_halt_when_the_line_reprices_later() -> None:
    # FRCB: the delisting day is followed by an OTC reprice -> halt; the
    # zero-volume day after it is carried.
    rows = [
        tiingo_row("2023-04-28", 3.5, 1_000_000),
        tiingo_row("2023-05-01", 3.5, 18_000_000),
        tiingo_row("2023-05-02", 3.5, 0),
        tiingo_row("2023-05-03", 0.33, 200_000_000),
    ]
    assert lp.detect_non_real("FRCB", rows) == {
        "2023-05-01": "halt",
        "2023-05-02": "carried",
        "2023-05-03": "otc",
    }


def test_detect_non_real_empty_series() -> None:
    assert lp.detect_non_real("XYZ", []) == {}


# ---------------------------------------------------------------------
# _check_line (report §2/§3 reproduction)
# ---------------------------------------------------------------------


def _tiny_series() -> list[dict[str, object]]:
    return lp.build_series("TINY", TINY_RAW["TINY"], TINY.valid_to)


def test_check_line_passes_when_it_reproduces_the_report() -> None:
    series = [row for row in _tiny_series() if row["date"] != "2019-01-04"]
    detected = {"2019-01-04": "carried"}
    assert lp._check_line(TINY, series, detected, {"last_real_close": {"TINY": 11.0}}) == []


def test_check_line_reports_a_row_count_mismatch() -> None:
    spec = LineSpec(
        "TINY", "0000000001", "TINY", "2019-01-04", 3, 99, "2019-01-02", "2019-01-03", ()
    )
    series = [row for row in _tiny_series() if row["date"] != "2019-01-04"]
    mismatches = lp._check_line(
        spec, series, {"2019-01-04": "carried"}, {"last_real_close": {"TINY": 11.0}}
    )
    assert any("rows kept" in m for m in mismatches)


def test_check_line_reports_a_trim_set_mismatch() -> None:
    series = [row for row in _tiny_series() if row["date"] != "2019-01-04"]
    mismatches = lp._check_line(TINY, series, {}, {"last_real_close": {"TINY": 11.0}})
    assert any("trim set differs" in m for m in mismatches)


def test_check_line_reports_a_trim_reason_mismatch() -> None:
    # Fix 7: the date matches, but the detected class does not.
    series = [row for row in _tiny_series() if row["date"] != "2019-01-04"]
    mismatches = lp._check_line(
        TINY, series, {"2019-01-04": "otc"}, {"last_real_close": {"TINY": 11.0}}
    )
    assert any("report §3 says" in m for m in mismatches)


def test_check_line_reports_a_last_close_mismatch() -> None:
    series = [row for row in _tiny_series() if row["date"] != "2019-01-04"]
    mismatches = lp._check_line(
        TINY, series, {"2019-01-04": "carried"}, {"last_real_close": {"TINY": 12.0}}
    )
    assert any("last real close" in m for m in mismatches)


# ---------------------------------------------------------------------
# Convention / dividend / WBD controls
# ---------------------------------------------------------------------


def test_compare_convention_matches_shared_dates() -> None:
    series = lp.build_series("TTD", TINY_RAW["TINY"], "2019-01-04")
    gateway = [
        {"date": "2019-01-02", "close": 10.0, "volume": 1000},
        {"date": "2019-01-03", "close": 11.0, "volume": 900},
    ]
    result = lp._compare_convention("TTD", series, gateway)
    assert result["shared_dates"] == 2
    assert result["max_abs_close_diff"] == 0.0
    assert result["max_abs_volume_diff"] == 100.0


def test_dividend_formula_detects_split_adjusted_dividends() -> None:
    # A 4:1 split; the pre-split dividend is divided by 4.
    series = lp.build_series(
        "AAPL",
        [
            tiingo_row("2020-08-07", 100.0, 1000, div=0.30),
            tiingo_row("2020-08-31", 25.0, 4000, split=4.0),
        ],
        "2020-08-31",
    )
    gateway = [{"date": "2020-08-07", "value": 0.30 / 4}]
    result = lp._dividend_formula(series, gateway)
    assert result["formula"] == "divCash / split_cum"
    assert result["max_abs_diff_adjusted"] < result["max_abs_diff_raw"]


def test_deletion_step_covers_every_copy() -> None:
    steps = lp._deletion_step(Path("/workspaces/thesis/data/private/tiingo"))
    text = " ".join(step["copy"] + " " + step["path"] for step in steps)
    assert "private folder" in text
    assert "T-100" in text
    assert "backup" in text
    assert lp.FEASIBILITY_SCRATCH in text
    for step in steps:
        assert step["command"] and step["check"]
        # Fix 7: no relative rm -rf -- every path is rooted (absolute or a
        # placeholder-rooted absolute).
        assert step["path"].startswith(("/", "<"))


def test_deletion_step_t100_has_an_absolute_placeholder() -> None:
    steps = {step["copy"]: step for step in lp._deletion_step(Path("/x/data/private/tiingo"))}
    t100 = steps["T-100's machine, if different"]
    assert t100["path"].startswith("<T-100 thesis folder>/")
    assert "/data/private/tiingo" in t100["path"]
    assert "Users/dova/thesis" in t100.get("example", "")


# ---------------------------------------------------------------------
# Manifest fields (fix 6)
# ---------------------------------------------------------------------


def test_k_line_carries_the_wk_kellogg_spinoff_note() -> None:
    k = next(spec for spec in lp.LINES if spec.ask == "K")
    note = " ".join(k.notes)
    assert "WK Kellogg" in note and "1.065" in note
    assert "spin-off" in note


def test_declared_exception_has_label_and_accession() -> None:
    assert lp.DECLARED_EXCEPTIONS["ANSS"]["label"] == (
        "real final-day trade below deal value, kept as traded"
    )
    assert lp.DECLARED_EXCEPTIONS["ANSS"]["accession"] == "0001193125-24-008112"


def test_all_trim_reasons_are_one_of_the_three_classes() -> None:
    for spec in lp.LINES:
        for _day, reason in spec.trims:
            assert reason in {"carried", "halt", "otc"}


# ---------------------------------------------------------------------
# Production count by CIK + ticker (fix 3)
# ---------------------------------------------------------------------


def _financial_db(tmp_path: Path) -> Path:
    path = tmp_path / "fin.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE assets (id INTEGER PRIMARY KEY, ticker TEXT, cik TEXT);"
        "CREATE TABLE price_daily (id INTEGER PRIMARY KEY, asset_id INTEGER);"
    )
    con.execute("INSERT INTO assets VALUES (484, 'WBD', '0001437107')")
    con.executemany("INSERT INTO price_daily (asset_id) VALUES (?)", [(484,)] * 5)
    con.commit()
    con.close()
    return path


def test_production_stored_count_matches_cik_and_ticker(tmp_path: Path) -> None:
    db = _financial_db(tmp_path)
    assert lp.production_stored_count("0001437107", "WBD", db) == 5
    assert lp.production_stored_count("0001437107", "DISCK", db) == 0


# ---------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------


def test_machine_check_passes_on_the_expected_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "financial.db"
    db.write_bytes(b"not really a db")
    monkeypatch.setattr(lp, "FINANCIAL_DB_SHA1", lp.sha1_file(db))
    brief = tmp_path / "brief.md"
    brief.write_text("brief")
    lp.machine_check(db, brief)  # no raise


def test_machine_check_stops_on_a_wrong_sha1(tmp_path: Path) -> None:
    db = tmp_path / "financial.db"
    db.write_bytes(b"wrong")
    brief = tmp_path / "brief.md"
    brief.write_text("brief")
    with pytest.raises(LeaverPricesError, match="wrong machine"):
        lp.machine_check(db, brief)


def test_machine_check_stops_when_the_brief_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "financial.db"
    db.write_bytes(b"x")
    monkeypatch.setattr(lp, "FINANCIAL_DB_SHA1", lp.sha1_file(db))
    with pytest.raises(LeaverPricesError, match="brief not found"):
        lp.machine_check(db, tmp_path / "missing.md")


def test_assert_not_in_git_worktree_rejects_a_repo_dir(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603 -- fixed, trusted argv; no shell, no user input
        ["git", "init", "--quiet", str(tmp_path)],  # noqa: S607
        check=True,
    )
    with pytest.raises(LeaverPricesError, match="git work tree"):
        lp.assert_not_in_git_worktree(tmp_path / "out")


def test_assert_not_in_git_worktree_allows_a_plain_dir(tmp_path: Path) -> None:
    lp.assert_not_in_git_worktree(tmp_path / "out")  # no raise


# ---------------------------------------------------------------------
# Request counter (fix 4)
# ---------------------------------------------------------------------


def test_rate_limiter_stops_at_headroom() -> None:
    limiter = lp.RateLimiter(max_per_hour=3, headroom=2)
    limiter.check()
    limiter.check()
    with pytest.raises(LeaverPricesError, match="budget reached"):
        limiter.check()


def test_rate_limiter_persists_and_reloads(tmp_path: Path) -> None:
    log = tmp_path / "request_log.json"
    first = lp.RateLimiter(path=log, max_per_hour=5, headroom=4)
    first.check()
    first.check()
    assert log.exists()
    assert len(json.loads(log.read_text())) == 2

    second = lp.RateLimiter(path=log, max_per_hour=5, headroom=4)
    assert second.total_all_runs() == 2
    second.check()
    second.check()
    with pytest.raises(LeaverPricesError, match="budget reached"):
        second.check()
    assert second.total_all_runs() == 4


def test_rate_limiter_largest_rolling_hour(tmp_path: Path) -> None:
    log = tmp_path / "request_log.json"
    limiter = lp.RateLimiter(path=log)
    limiter._times = [
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 59, tzinfo=UTC),
        datetime(2026, 1, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 3, 10, tzinfo=UTC),
    ]
    assert limiter.largest_rolling_hour() == 3
    assert limiter.total_all_runs() == 5


# ---------------------------------------------------------------------
# End to end (tiny synthetic run)
# ---------------------------------------------------------------------


def _patch_run(
    monkeypatch: pytest.MonkeyPatch, spec: LineSpec, raw: dict[str, list[dict[str, object]]]
) -> None:
    monkeypatch.setattr(lp, "LINES", (spec,))
    monkeypatch.setattr(lp, "machine_check", lambda *a, **k: None)
    monkeypatch.setattr(
        lp,
        "read_membership_spans",
        lambda symbols, universe_db=None: {
            spec.ask: [{"cik": spec.cik, "valid_from": "2019-01-02", "valid_to": spec.valid_to}]
        },
    )
    monkeypatch.setattr(lp, "production_stored_count", lambda cik, ticker, financial_db=None: 0)
    monkeypatch.setattr(lp, "_gateway_candles", lambda *a, **k: [])
    monkeypatch.setattr(
        lp,
        "_run_controls",
        lambda *a, **k: {
            "convention": [],
            "dividend": None,
            "wbd_vs_production": {"shared_dates": 0, "max_abs_diff_cents": 0},
        },
    )


def test_fetch_writes_csv_and_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, TINY, TINY_RAW)
    out = tmp_path / "out"
    expectations = _expectations(tmp_path, {"TINY": 11.0})

    manifest = lp.fetch_leaver_prices(
        output_dir=out,
        expectations_path=expectations,
        tiingo_client=_tiingo_client(TINY_RAW),
        gateway_client=_gateway_client(),
        production_dbs=(),
        pace_seconds=0.0,
    )

    csv_text = (out / lp.DATA_FILENAME).read_text()
    assert csv_text.startswith("ask_ticker,cik,tiingo_symbol,date,")
    assert csv_text.count("\n") == 3  # header + 2 kept rows
    assert manifest["data_file_sha256"] == lp.sha256_file(out / lp.DATA_FILENAME)
    assert manifest["lines"][0]["rows_kept"] == 2
    assert manifest["lines"][0]["trimmed_rows"] == [
        {"date": "2019-01-04", "reason": "carried", "detected_by": "carried"}
    ]
    # fix 6: every stint, not only the last valid_to
    assert manifest["lines"][0]["membership_span"] == [
        {"cik": "0000000001", "valid_from": "2019-01-02", "valid_to": "2019-01-04"}
    ]
    assert manifest["request_count"]["this_run"] == 1
    assert manifest["request_count"]["log_file"] == lp.REQUEST_LOG_FILENAME
    assert (out / lp.REQUEST_LOG_FILENAME).exists()
    # fix 6: ANSS declared exception
    anss = next(e for e in manifest["declared_exception"] if e["ask"] == "ANSS")
    assert anss["accession"] == "0001193125-24-008112"
    assert "kept as traded" in anss["label"]
    assert manifest["not_delivered"] and {d["ask"] for d in manifest["not_delivered"]} == {
        "INFO",
        "WRK",
    }
    assert (out / lp.MANIFEST_FILENAME).exists()


def test_fetch_stops_when_the_gateway_serves_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fix 2: one vendor per line -- a non-zero gateway count stops the run.
    _patch_run(monkeypatch, TINY, TINY_RAW)
    monkeypatch.setattr(lp, "_gateway_candles", lambda *a, **k: [{"date": "2019-01-02"}])
    out = tmp_path / "out"
    with pytest.raises(LeaverPricesError, match="one vendor per line"):
        lp.fetch_leaver_prices(
            output_dir=out,
            expectations_path=_expectations(tmp_path, {"TINY": 11.0}),
            tiingo_client=_tiingo_client(TINY_RAW),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )
    assert not (out / lp.DATA_FILENAME).exists()


def test_fetch_writes_nothing_on_a_report_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = LineSpec(
        "TINY", "0000000001", "TINY", "2019-01-04", 3, 99, "2019-01-02", "2019-01-03", ()
    )
    _patch_run(monkeypatch, bad, TINY_RAW)
    out = tmp_path / "out"
    with pytest.raises(LeaverPricesError, match="report reproduction failed"):
        lp.fetch_leaver_prices(
            output_dir=out,
            expectations_path=_expectations(tmp_path, {"TINY": 11.0}),
            tiingo_client=_tiingo_client(TINY_RAW),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )
    assert not (out / lp.DATA_FILENAME).exists()


def test_fetch_stops_when_valid_to_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, TINY, TINY_RAW)
    monkeypatch.setattr(
        lp,
        "read_membership_spans",
        lambda symbols, universe_db=None: {
            "TINY": [{"cik": "0000000001", "valid_from": "2019-01-02", "valid_to": "2019-01-05"}]
        },
    )
    with pytest.raises(LeaverPricesError, match="valid_to"):
        lp.fetch_leaver_prices(
            output_dir=tmp_path / "out",
            expectations_path=_expectations(tmp_path, {"TINY": 11.0}),
            tiingo_client=_tiingo_client(TINY_RAW),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )


def test_fetch_stops_when_expectations_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, TINY, TINY_RAW)
    with pytest.raises(LeaverPricesError, match="expectations file not found"):
        lp.fetch_leaver_prices(
            output_dir=tmp_path / "out",
            expectations_path=tmp_path / "nope.json",
            tiingo_client=_tiingo_client(TINY_RAW),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )


def test_fetch_stops_when_key_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, TINY, TINY_RAW)
    monkeypatch.delenv("TIINGO_API_KEY")
    with pytest.raises(LeaverPricesError, match="TIINGO_API_KEY is not set"):
        lp.fetch_leaver_prices(
            output_dir=tmp_path / "out",
            expectations_path=_expectations(tmp_path, {"TINY": 11.0}),
            tiingo_client=_tiingo_client(TINY_RAW),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )


def test_fetch_writes_nothing_when_a_control_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, TINY, TINY_RAW)

    def boom(*a: object, **k: object) -> dict[str, object]:
        raise LeaverPricesError("control failed")

    monkeypatch.setattr(lp, "_run_controls", boom)
    out = tmp_path / "out"
    with pytest.raises(LeaverPricesError, match="control failed"):
        lp.fetch_leaver_prices(
            output_dir=out,
            expectations_path=_expectations(tmp_path, {"TINY": 11.0}),
            tiingo_client=_tiingo_client(TINY_RAW),
            gateway_client=_gateway_client(),
            production_dbs=(),
            pace_seconds=0.0,
        )
    assert not (out / lp.DATA_FILENAME).exists()


# ---------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------


def test_cli_registers_leaver_prices_without_finnhub(tmp_path: Path) -> None:
    out = str(tmp_path / "out")
    argv = sys.argv
    sys.argv = ["pricing_cli.py", "leaver-prices", "--out", out]
    try:
        args = pricing_cli.parse_args()
    finally:
        sys.argv = argv
    assert args.command == "leaver-prices"
    assert args.func is pricing_cli.cmd_leaver_prices
    assert args.out == out
