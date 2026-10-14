"""Hermetic tests for scripts/audit_ticker_tagging.py (T-053)."""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_ticker_tagging.py"
_spec = importlib.util.spec_from_file_location("audit_ticker_tagging", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
audit_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_mod)

_ROWS = [
    # (url, title, domain, company, ticker, source)
    (
        "https://cnbc.com/x/china-has-best-week-in-a-month.html",
        None,
        "cnbc.com",
        "Agilent Technologies",
        "A",
        "sitemap",
    ),
    (
        "https://cnbc.com/x/agilent-a-earnings.html",
        None,
        "cnbc.com",
        "Agilent Technologies",
        "A",
        "sitemap",
    ),
    (
        "https://cnbc.com/x/agilent-beats.html",
        None,
        "cnbc.com",
        "Agilent Technologies",
        "A",
        "sitemap",
    ),
    (
        "https://www.nasdaq.com/articles/season-opens",
        None,
        "nasdaq.com",
        "ON Semiconductor",
        "ON",
        "ddg",
    ),
    ("https://cnbc.com/x/unrelated.html", None, "cnbc.com", "Agilent Technologies", "A", "sitemap"),
]


@pytest.mark.parametrize(
    ("row", "mode"),
    [
        (_ROWS[0], "ticker_only"),
        (_ROWS[1], "both"),
        (_ROWS[2], "name_only"),
        (_ROWS[3], "shortcut_only"),
        (_ROWS[4], "unmatched"),
    ],
)
def test_classify_modes(row: tuple[str, ...], mode: str) -> None:
    url, title, domain, company, ticker, _ = row
    assert audit_mod.classify(url, title, domain, company, ticker, {}) == mode


def test_shortcut_only_applies_to_shortcut_connectors_only() -> None:
    # Same URL on a connector without the shortcut is rejected by the real rule.
    url = "https://www.cnbc.com/articles/season-opens"
    assert audit_mod.classify(url, None, "cnbc.com", "ON Semiconductor", "ON", {}) == "unmatched"


def test_title_counts_as_match_text() -> None:
    mode = audit_mod.classify(
        "https://cnbc.com/x/y.html", "Agilent profit", "cnbc.com", "Agilent Technologies", "A", {}
    )
    assert mode == "name_only"


def test_audit_counts_and_read_only(tmp_path: Path) -> None:
    db = tmp_path / "urls.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (url TEXT, title TEXT, domain TEXT, company TEXT, ticker TEXT, source TEXT)"
        )
        con.executemany("INSERT INTO discovered_urls VALUES (?,?,?,?,?,?)", _ROWS)
    before = db.read_bytes()
    result = audit_mod.audit(db, min_rows=1, generic_share=0.2)
    assert result["total_rows"] == len(_ROWS)
    cnbc = next(r for r in result["by_connector"] if r["domain"] == "cnbc.com")
    assert (cnbc["ticker_only"], cnbc["both"], cnbc["name_only"], cnbc["unmatched"]) == (1, 1, 1, 1)
    word = next(r for r in result["word_tickers"] if r["ticker"] == "A")
    assert word["rows"] == 4
    per_conn = {(c["domain"], c["source"]): c["ticker_only"] for c in word["by_connector"]}
    assert set(per_conn) == {("cnbc.com", "sitemap")}
    assert sum(per_conn.values()) == word["ticker_only"] == 1
    generic = next(r for r in result["generic_first_words"] if r["first_word"] == "Agilent")
    assert [c["company"] for c in generic["companies"]] == ["Agilent Technologies"]
    assert db.read_bytes() == before


def test_main_reads_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "urls.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (url TEXT, title TEXT, domain TEXT, company TEXT, ticker TEXT, source TEXT)"
        )
        con.executemany("INSERT INTO discovered_urls VALUES (?,?,?,?,?,?)", _ROWS)
    monkeypatch.setenv("DATABASE_URL", str(db))
    assert audit_mod.main(["--min-rows", "1"]) == 0
    assert "rows scanned: 5" in capsys.readouterr().out


def test_main_missing_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert audit_mod.main(["--db", str(tmp_path / "nope.db")]) == 1
    assert "not found" in capsys.readouterr().err


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "urls.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (url TEXT, title TEXT, domain TEXT, company TEXT, ticker TEXT, source TEXT)"
        )
        con.executemany("INSERT INTO discovered_urls VALUES (?,?,?,?,?,?)", _ROWS)
    return db


def test_shortcut_domains_match_connector_sources() -> None:
    """SHORTCUT_DOMAINS must equal the connectors that still run the raw substring shortcut."""
    connectors = Path(audit_mod.__file__).resolve().parents[1] / "src/news_collector/connectors"
    found = set()
    for path in connectors.glob("*.py"):
        text = path.read_text()
        if "ticker.lower() in url.lower()" in text:
            found.add(re.search(r'domain="([^"]+)"', text).group(1))  # type: ignore[union-attr]
    assert found == set(audit_mod.SHORTCUT_DOMAINS)


def test_render_includes_per_connector_rows_and_header(tmp_path: Path) -> None:
    result = audit_mod.audit(_make_db(tmp_path), min_rows=1, generic_share=0.2)
    text = audit_mod.render(result)
    assert "word tickers: ON, A, ARE" in text
    assert "thresholds: min_rows=1, generic_share=20%" in text
    assert "    cnbc.com            sitemap" in text
    assert "Agilent Technologies" in text


def test_json_output_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert audit_mod.main(["--db", str(_make_db(tmp_path)), "--json", "--min-rows", "1"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_rows"] == len(_ROWS)
    assert data["thresholds"]["min_rows"] == 1


def test_min_rows_filters_lists(tmp_path: Path) -> None:
    result = audit_mod.audit(_make_db(tmp_path), min_rows=100, generic_share=0.0)
    assert result["word_tickers"] == []
    assert result["generic_first_words"] == []
    assert result["total_rows"] == len(_ROWS)
