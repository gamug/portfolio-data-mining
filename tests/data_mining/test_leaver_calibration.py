"""Tests for data_mining/leaver_calibration.py -- the private Tiingo fetch for
the 32 pre-2022 leaver lines.

Hermetic (NR-004): every Tiingo/EDGAR/gateway response is a hand-made synthetic
response served by an ``httpx.MockTransport``; the production-DB reads, the
environment checks and EDGAR verification are monkeypatched. No test carries a
real Tiingo value, and no test touches a real ``/workspaces/thesis`` file.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from cli import pricing_cli
from data_mining import leaver_calibration as lc
from data_mining.leaver_calibration import CalibrationLine, LeaverPricesError

TINY = CalibrationLine("TINY", "0000000001", "TINY", "2019-02-01", "2019-02-08", ("tiny corp",))

TINY_SESSIONS = ["2019-02-01", "2019-02-04", "2019-02-05", "2019-02-06", "2019-02-07", "2019-02-08"]


def tiingo_row(
    day: str, close: float, volume: int, *, split: float = 1.0, div: float = 0.0
) -> dict[str, object]:
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


def meta(name: str, start: str, end: str) -> dict[str, object]:
    return {
        "ticker": "TINY",
        "name": name,
        "startDate": start,
        "endDate": end,
        "exchangeCode": "NYSE",
    }


def tiny_rows() -> list[dict[str, object]]:
    return [tiingo_row(day, 100.0 + i, 1_000_000 + i) for i, day in enumerate(TINY_SESSIONS)]


def build(line: CalibrationLine = TINY, rows: list[dict[str, object]] | None = None):
    return lc._build_line(
        line,
        rows if rows is not None else tiny_rows(),
        meta("Tiny Corp", "1976-07-01", "2019-02-08"),
        {"method": "test"},
        {"count": 0, "note": ""},
        0,
    )


# ---------------------------------------------------------------------
# calendar / spans
# ---------------------------------------------------------------------


def test_expected_days_uses_the_bms_two_session_stint() -> None:
    bms = next(line for line in lc.CALIBRATION_LINES if line.ask == "BMS")
    assert lc.expected_days(bms) == ["2019-06-07", "2019-06-10"]


def test_last_session_skips_a_weekend() -> None:
    assert lc.last_session("2019-02-10") == "2019-02-08"


def test_line_table_has_32_lines_and_padded_ciks() -> None:
    assert len(lc.CALIBRATION_LINES) == 32
    assert all(len(line.cik) == 10 and line.cik.isdigit() for line in lc.CALIBRATION_LINES)


def test_validate_lines_stops_on_a_changed_valid_to() -> None:
    spans = {"TINY": [{"cik": "", "valid_from": "1976-07-01", "valid_to": "2019-02-09"}]}
    with pytest.raises(LeaverPricesError, match="latest valid_to"):
        lc._validate_lines((TINY,), spans)


# ---------------------------------------------------------------------
# line building
# ---------------------------------------------------------------------


def test_kept_series_records_split_cum_and_dividends() -> None:
    rows = [
        tiingo_row("2019-02-01", 400.0, 1_000_000),
        tiingo_row("2019-02-04", 400.0, 1_000_000, div=0.5),
        tiingo_row("2019-02-05", 100.0, 4_000_000, split=4.0),
        *[tiingo_row(day, 100.0, 4_000_000) for day in TINY_SESSIONS[3:]],
    ]
    result = build(rows=rows)
    assert result is not None
    assert result.rows[0]["split_cum"] == 4.0
    assert result.rows[0]["adj_close"] == pytest.approx(100.0)
    assert result.rows[2]["split_cum"] == 1.0
    label = next(row for row in result.rows if row["date"] == "2019-02-04")
    assert label["divCash"] == 0.5
    assert result.split_events == [{"date": "2019-02-05", "factor": 4.0, "kind": "stock split"}]


def test_a_carried_row_is_trimmed_and_reported_missing() -> None:
    rows = tiny_rows()
    rows[3] = tiingo_row("2019-02-06", 102.0, 0)  # repeats 02-05's close, zero volume
    result = build(rows=rows)
    assert result is not None
    assert "2019-02-06" in result.trimmed
    assert result.trimmed["2019-02-06"] == "carried"
    assert "2019-02-06" in result.missing
    assert result.rows[-1]["date"] == "2019-02-08"


def test_a_short_tail_sets_last_equals_exit_false_with_a_reason() -> None:
    result = build(rows=tiny_rows()[:3])  # ends 2019-02-05
    assert result is not None
    assert result.last_equals_exit is False
    assert "trading ended 2019-02-05" in lc._missing_reason(result)
    assert "before the index exit 2019-02-08" in lc._exit_reason(result)


def test_a_full_span_sets_last_equals_exit_true() -> None:
    result = build()
    assert result is not None
    assert result.last_equals_exit is True
    assert result.missing == []
    assert lc._exit_reason(result) == ""


def test_an_empty_series_is_reported_not_delivered_even_if_the_symbol_was_reused() -> None:
    apc = next(line for line in lc.CALIBRATION_LINES if line.ask == "APC")
    result = lc._build_line(
        apc,
        [],
        meta("ARKO Petroleum Corp - Class A", "2026-02-12", "2026-10-28"),
        {"method": "test"},
        {"count": 0, "note": ""},
        0,
    )
    assert result is None


def test_identity_mismatch_stops() -> None:
    with pytest.raises(LeaverPricesError, match="possibly the symbol's new owner"):
        lc._build_line(
            TINY,
            tiny_rows(),
            meta("Some Other Corp", "1976-07-01", "2019-02-08"),
            {"method": "test"},
            {"count": 0, "note": ""},
            0,
        )


def test_dwdp_is_reported_missing_when_dd_does_not_cover_the_span() -> None:
    dwdp = next(line for line in lc.CALIBRATION_LINES if line.ask == "DWDP")
    rows = [
        tiingo_row("2019-05-29", 30.0, 1_000_000),
        tiingo_row("2019-05-30", 30.5, 1_000_000),
        tiingo_row("2019-05-31", 31.0, 1_000_000),
    ]
    missing = lc._build_line(
        dwdp,
        rows,
        meta("DuPont de Nemours, Inc.", "2017-08-03", "2019-05-01"),
        {"method": "test"},
        {"count": 0, "note": ""},
        0,
    )
    assert missing is None
    accepted = lc._build_line(
        dwdp,
        rows,
        meta("DuPont de Nemours, Inc.", "2017-08-03", "2019-05-31"),
        {"method": "test"},
        {"count": 0, "note": ""},
        0,
    )
    assert accepted is not None


# ---------------------------------------------------------------------
# reused-ticker override (LLL/TSS)
# ---------------------------------------------------------------------


def test_shadowed_metadata_is_accepted_only_on_dates_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        lc.SHADOWED_METADATA,
        "TINY",
        {"last": "2019-02-08", "supported_tickers_range": "x..y", "reason": "test"},
    )
    result = lc._build_line(
        TINY,
        tiny_rows(),
        meta("Some 2026 ETF", "2026-07-01", "2026-10-28"),
        {"method": "test"},
        {"count": 0, "note": ""},
        0,
    )
    assert result is not None
    assert result.identity_override is not None
    assert result.identity_override["first_date"] == "2019-02-01"
    assert result.identity_override["last_date"] == "2019-02-08"
    assert result.identity_override["rows"] == 6


def test_shadowed_metadata_rejects_a_wrong_last_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        lc.SHADOWED_METADATA,
        "TINY",
        {"last": "2019-02-07", "supported_tickers_range": "x..y", "reason": "test"},
    )
    with pytest.raises(LeaverPricesError, match="override series ends"):
        lc._build_line(
            TINY,
            tiny_rows(),
            meta("Some 2026 ETF", "2026-07-01", "2026-10-28"),
            {"method": "test"},
            {"count": 0, "note": ""},
            0,
        )


def test_shadowed_metadata_rejects_rows_outside_the_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        lc.SHADOWED_METADATA,
        "TINY",
        {"last": "2019-02-08", "supported_tickers_range": "x..y", "reason": "test"},
    )
    rows = [*tiny_rows(), tiingo_row("2019-02-11", 110.0, 1_000_000)]
    with pytest.raises(LeaverPricesError, match="rows outside"):
        lc._build_line(
            TINY,
            rows,
            meta("Some 2026 ETF", "2026-07-01", "2026-10-28"),
            {"method": "test"},
            {"count": 0, "note": ""},
            0,
        )


# ---------------------------------------------------------------------
# jumps
# ---------------------------------------------------------------------


def test_a_known_jump_is_reported() -> None:
    rows = [
        tiingo_row("2019-02-01", 100.0, 1_000_000),
        tiingo_row("2019-02-04", 40.0, 1_000_000),
        tiingo_row("2019-02-05", 41.0, 1_000_000),
        tiingo_row("2019-02-06", 42.0, 1_000_000),
        tiingo_row("2019-02-07", 43.0, 1_000_000),
        tiingo_row("2019-02-08", 44.0, 1_000_000),
    ]
    line = replace(TINY, known_jumps=(("2019-02-04", lc.CORPORATE_EVENT, "test distribution"),))
    result = build(line, rows)
    assert result is not None
    assert result.jumps == [
        {
            "date": "2019-02-04",
            "abs_return": 0.6,
            "cause_type": "corporate event",
            "cause": "test distribution",
        }
    ]


def test_a_market_wide_jump_carries_spy_same_day() -> None:
    rows = [
        tiingo_row("2019-02-01", 100.0, 1_000_000),
        tiingo_row("2019-02-04", 70.0, 1_000_000),
        *[tiingo_row(day, 71.0, 1_000_000) for day in TINY_SESSIONS[2:]],
    ]
    line = replace(TINY, known_jumps=(("2019-02-04", lc.MARKET_WIDE, "test crash"),))
    result = lc._build_line(
        line,
        rows,
        meta("Tiny Corp", "1976-07-01", "2019-02-08"),
        {"method": "test"},
        {"count": 0, "note": ""},
        0,
        {"2019-02-04": -0.109},
    )
    assert result is not None
    assert result.jumps[0]["cause_type"] == "market-wide move"
    assert result.jumps[0]["spy_same_day_return"] == -0.109


def test_an_unexplained_jump_stops() -> None:
    rows = [
        tiingo_row("2019-02-01", 100.0, 1_000_000),
        tiingo_row("2019-02-04", 40.0, 1_000_000),
        *[tiingo_row(day, 41.0, 1_000_000) for day in TINY_SESSIONS[2:]],
    ]
    with pytest.raises(LeaverPricesError, match="unexplained adjusted close-to-close jump"):
        build(rows=rows)


# ---------------------------------------------------------------------
# FOX/FOXA classes
# ---------------------------------------------------------------------


def test_fox_classes_stop_when_the_metadata_is_indistinguishable() -> None:
    metas = {
        "FOX": meta("Twenty-First Century Fox, Inc.", "1987-12-30", "2019-03-20"),
        "FOXA": meta("Twenty-First Century Fox, Inc.", "1987-12-30", "2019-03-20"),
    }
    with pytest.raises(LeaverPricesError, match="cannot tell the two classes apart"):
        lc._check_fox_classes(metas)


def test_fox_classes_accept_distinct_series() -> None:
    metas = {
        "FOX": meta("Twenty-First Century Fox, Inc.", "1987-12-30", "2019-03-20"),
        "FOXA": meta("Twenty-First Century Fox, Inc.", "1996-03-11", "2019-03-20"),
    }
    evidence = lc._check_fox_classes(metas)
    assert evidence["TFCF"]["start_date"] == "1987-12-30"
    assert evidence["TFCFA"]["start_date"] == "1996-03-11"


# ---------------------------------------------------------------------
# phases and outputs
# ---------------------------------------------------------------------


def _patch_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lc, "machine_check", lambda *a, **k: None)
    monkeypatch.setattr(lc, "assert_not_in_git_worktree", lambda *a, **k: None)
    monkeypatch.setattr(
        lc,
        "read_membership_spans",
        lambda symbols, db: {
            "TINY": [{"cik": "", "valid_from": "1976-07-01", "valid_to": "2019-02-08"}]
        },
    )
    monkeypatch.setattr(lc, "production_stored_count", lambda *a, **k: 0)
    monkeypatch.setattr(lc, "_gateway_count", lambda *a, **k: {"count": 0, "note": ""})
    monkeypatch.setattr(lc, "_spy_returns", lambda *a, **k: {})
    monkeypatch.setattr(
        lc,
        "verify_cik",
        lambda verifier, line: {
            "method": "test",
            "sec_name": "Tiny Corp",
            "former_names": [],
            "submissions_tickers": ["TINY"],
            "filing": {"form": "10-K", "filed": "2019-02-04", "accession": "0", "document": "a"},
            "filing_in_span": True,
            "ask_symbol_on_cover": True,
            "cover_error": None,
        },
    )


def _tiingo(rows: list[dict[str, object]], meta_obj: dict[str, object], counter: dict[str, int]):
    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if parts[-1] == "prices":
            counter["prices"] = counter.get("prices", 0) + 1
            return httpx.Response(200, json=rows)
        counter["meta"] = counter.get("meta", 0) + 1
        return httpx.Response(200, json=meta_obj)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_phase_a_and_b_write_the_csv_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_environment(monkeypatch)
    counter: dict[str, int] = {}
    client = _tiingo(tiny_rows(), meta("Tiny Corp", "1976-07-01", "2019-02-08"), counter)
    out = tmp_path / "cal"
    summary_a = lc.fetch_calibration_phase_a(
        output_dir=out,
        tiingo_client=client,
        lines=(TINY,),
        pace_seconds=0,
    )
    assert summary_a["prices_fetched"] == 1
    manifest = lc.fetch_calibration_phase_b(
        output_dir=out,
        tiingo_client=client,
        gateway_client=client,
        sec_client=client,
        production_dbs=(),
        lines=(TINY,),
        pace_seconds=0,
    )
    assert counter == {"prices": 1, "meta": 1}
    assert manifest["request_count"]["this_run"] == 2
    assert manifest["dividend_formula"].startswith("divCash / split_cum")
    line = manifest["lines"][0]
    assert line["rows_kept"] == 6
    assert line["last_equals_exit"] is True
    assert line["last_date"] == "2019-02-08"
    header = (out / "prices.csv").read_text().splitlines()[0]
    assert header.endswith("split_cum")
    assert "adj_close" in header and "raw_close" in header
    assert not (out / lc.CACHE_DIRNAME).exists()
    # No price field in the manifest's per-line record.
    assert not {"adj_close", "raw_close", "close"} & set(line)


def test_manifest_marks_a_short_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_environment(monkeypatch)
    client = _tiingo(tiny_rows()[:3], meta("Tiny Corp", "1976-07-01", "2019-02-08"), {})
    out = tmp_path / "cal"
    lc.fetch_calibration_phase_a(
        output_dir=out, tiingo_client=client, lines=(TINY,), pace_seconds=0
    )
    manifest = lc.fetch_calibration_phase_b(
        output_dir=out,
        tiingo_client=client,
        gateway_client=client,
        sec_client=client,
        production_dbs=(),
        lines=(TINY,),
        pace_seconds=0,
    )
    line = manifest["lines"][0]
    assert line["last_equals_exit"] is False
    assert line["missing_sessions"] == ["2019-02-06", "2019-02-07", "2019-02-08"]
    assert "trading ended 2019-02-05" in line["missing_reason"]


def test_cli_routes_the_calibration_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["pricing_cli.py", "leaver-prices-calibration", "--phase", "a"]
    )
    args = pricing_cli.parse_args()
    assert args.command == "leaver-prices-calibration"
    assert args.phase == "a"
    with pytest.raises(SystemExit):
        monkeypatch.setattr(sys, "argv", ["pricing_cli.py", "leaver-prices-calibration"])
        pricing_cli.parse_args()


def test_cache_is_kept_when_a_check_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_environment(monkeypatch)
    monkeypatch.setattr(
        lc, "verify_cik", lambda verifier, line: (_ for _ in ()).throw(LeaverPricesError("stop"))
    )
    client = _tiingo(tiny_rows(), meta("Tiny Corp", "1976-07-01", "2019-02-08"), {})
    out = tmp_path / "cal"
    lc.fetch_calibration_phase_a(
        output_dir=out, tiingo_client=client, lines=(TINY,), pace_seconds=0
    )
    with pytest.raises(LeaverPricesError, match="stop"):
        lc.fetch_calibration_phase_b(
            output_dir=out,
            tiingo_client=client,
            gateway_client=client,
            sec_client=client,
            production_dbs=(),
            lines=(TINY,),
            pace_seconds=0,
        )
    assert (out / lc.CACHE_DIRNAME / "prices_TINY.json").exists()
    assert not (out / "manifest.json").exists()


def test_outputs_are_not_written_inside_a_git_worktree(tmp_path: Path) -> None:
    # The real check is exercised by the module; this keeps the helper honest.
    with pytest.raises(LeaverPricesError, match="inside a git work tree"):
        lc.assert_not_in_git_worktree(Path("/workspaces/portfolio-data-mining"))


def test_no_real_prices_in_the_committed_module() -> None:
    text = Path(lc.__file__).read_text()
    assert "adjClose" in text  # the formula discussion, not a value
    assert not any(token in text for token in ('"close": 1', "194.92", "374.30"))
