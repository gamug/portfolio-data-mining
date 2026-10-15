"""Hermetic tests for scripts/score_ticker_rules.py (T-054 evidence for T-055)."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "score_ticker_rules", _SCRIPTS / "score_ticker_rules.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
from ticker_tagging_sample import FIELDS  # noqa: E402

# (id, ticker, company, url, title, body, status, label)
CASES = [
    (1, "AAPL", "Apple Inc.", "https://x/apple-earnings.html", "Apple earnings", "", "ok", "yes"),
    (
        2,
        "ON",
        "ON Semiconductor",
        "https://x/turn-on-the-tv.html",
        "Turn on the TV",
        "",
        "ok",
        "no",
    ),
    (
        3,
        "XOM",
        "Exxon Mobil",
        "https://x/oil.html",
        "Oil rises",
        "Exxon Mobil said. Exxon Mobil cut.",
        "ok",
        "yes",
    ),
    (4, "BZ", "Bank Zeta", "https://x/n.html", "A bank said today", "", "failed", "no"),
    (5, "TSLA", "Tesla, Inc.", "https://x/ev.html", "TSLA jumps on deliveries", "", "ok", "yes"),
]


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    db, sample = tmp_path / "urls.db", tmp_path / "s.csv"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute(
            "CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, body_text TEXT, fetch_status TEXT)"
        )
        for i, _t, _c, _u, title, body, status, _l in CASES:
            con.execute("INSERT INTO discovered_urls VALUES (?, ?)", (i, title))
            con.execute("INSERT INTO articles VALUES (?, ?, ?, ?)", (i, title, body, status))
    with sample.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for i, t, c, u, _title, _b, _s, lab in CASES:
            writer.writerow(
                {"id": i, "url": u, "ticker": t, "company": c, "label": lab}
                | {k: "" for k in FIELDS if k not in ("id", "url", "ticker", "company", "label")}
            )
    return db, sample


def test_rules_keep_and_drop(tmp_path: Path) -> None:
    db, sample = _fixture(tmp_path)
    data = mod.load(db, sample)
    kept = {r["id"]: mod.rule_plus_title_signals(r, t, b) for r, _s, t, b in data}
    assert kept == {"1": True, "2": False, "3": True, "4": False, "5": True}
    assert not mod.rule_name_or_explicit(data[2][0], data[2][2], data[2][3])  # needs the body rule
    assert mod.rule_plus_body_name(data[2][0], data[2][2], data[2][3])


def test_score_counts_and_ok_filter(tmp_path: Path) -> None:
    db, sample = _fixture(tmp_path)
    data = mod.load(db, sample)
    s = mod.score(data, mod.rule_plus_title_signals)
    assert (s["kept_correct"], s["kept_wrong"], s["lost_correct"], s["dropped_wrong"]) == (
        3,
        0,
        0,
        2,
    )
    assert s["precision"] == 1.0
    assert mod.score(data, mod.rule_current, only_ok=True)["kept_wrong"] == 1  # id 2; id 4 failed


def test_main_exclude_and_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, sample = _fixture(tmp_path)
    assert mod.main(["--db", str(db), "--sample", str(sample), "--exclude-ids", "1"]) == 0
    out = capsys.readouterr().out
    assert "rows scored: 4; excluded (unclear or unlabeled): 0" in out
    assert "current (accepts all)" in out
    assert mod.main(["--db", str(tmp_path / "no.db"), "--sample", str(sample)]) == 1
