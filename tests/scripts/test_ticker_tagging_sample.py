"""Hermetic tests for scripts/ticker_tagging_sample.py (T-054)."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))  # the script imports its sibling audit script
_spec = importlib.util.spec_from_file_location(
    "ticker_tagging_sample", _SCRIPTS / "ticker_tagging_sample.py"
)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "urls.db"
    rows = []
    for i in range(30):
        rows.append(
            (
                f"https://cnbc.com/x/a-{i}-in-a-month.html",
                "cnbc.com",
                "Agilent Technologies",
                "A",
                "sitemap",
            )
        )
    for i in range(12):
        rows.append(
            (
                f"https://cnbc.com/x/agilent-technologies-{i}.html",
                "cnbc.com",
                "Agilent Technologies",
                "A",
                "sitemap",
            )
        )
    rows.append(
        (
            "https://cnbc.com/x/agilent-technologies-results.html",
            "cnbc.com",
            "Agilent Technologies",
            "A",
            "ddg",
        )
    )
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE discovered_urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, domain TEXT,"
            " company TEXT, ticker TEXT, source TEXT)"
        )
        con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, body_text TEXT)")
        for url, domain, company, ticker, source in rows:
            con.execute(
                "INSERT INTO discovered_urls (url, title, domain, company, ticker, source) VALUES (?,?,?,?,?,?)",
                (url, None, domain, company, ticker, source),
            )
    return db


def test_normalized_name_and_full_name_presence() -> None:
    assert mod.normalized_name("Alphabet Inc. (Class C)") == "alphabet"
    assert mod.full_name_present(
        "Agilent Technologies", "https://x/agilent-technologies-q1.html", None
    )
    assert not mod.full_name_present("Agilent Technologies", "https://x/agilent-q1.html", None)
    assert not mod.full_name_present("3M", "https://x/3m-profit.html", None)  # too short


def test_stratum_splits_word_tickers() -> None:
    assert mod.stratum_of("d", "s", "ticker_only", "A") == "d/s/ticker_only/word_ticker"
    assert mod.stratum_of("d", "s", "ticker_only", "AAPL") == "d/s/ticker_only/other_ticker"
    assert mod.stratum_of("d", "s", "both", "A") == "d/s/both"


def test_draw_is_deterministic_and_reports_population(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first = mod.draw(db, seed=1)
    assert first == mod.draw(db, seed=1)
    by_stratum = {r["stratum"]: r for r in first}
    word = by_stratum["cnbc.com/sitemap/ticker_only/word_ticker"]
    assert word["stratum_size"] == 30
    assert sum(r["stratum"] == word["stratum"] for r in first) == mod.SMALL_STRATUM_N
    assert len({r["id"] for r in first}) == len(first)
    assert all(r["label"] == "" for r in first)
    assert mod.KNOWN_CORRECT in by_stratum


def test_wilson_bounds() -> None:
    assert mod.wilson(0, 0) == (0.0, 0.0)
    lo, hi = mod.wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8


def _write(path: Path, labels: list[tuple[str, str, int, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=mod.FIELDS)
        writer.writeheader()
        for i, (stratum, mode, size, label) in enumerate(labels):
            writer.writerow(
                {"id": i, "url": f"u{i}", "ticker": "T", "company": "C", "domain": "d.com", "source": "s",
                 "match_mode": mode, "stratum": stratum, "stratum_size": size, "label": label, "labeled_by": "hand",
                 "assistant_label": ""}
            )  # fmt: skip


def test_report_weights_strata_and_excludes_unclear(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    # big stratum all wrong, small stratum all right: weighted rate follows the big one.
    rows = [("d/s/ticker_only", "ticker_only", 900, "no")] * 5 + [
        ("d/s/both", "both", 100, "yes")
    ] * 5
    rows += [("d/s/ticker_only", "ticker_only", 900, "unclear")]
    rows += [(mod.KNOWN_CORRECT, "both", 50, "yes")] * 4 + [(mod.KNOWN_CORRECT, "both", 50, "no")]
    _write(path, rows)
    text = mod.report(path)
    assert "unclear rows excluded from every rate: 1" in text
    assert "ticker_only" in text
    assert "known-correct retention (labeled yes): 4/5 = 80%" in text
    mode_rates = {
        name: rate
        for name, _, _, rate, _ in mod._rate_rows(
            [
                r
                for r in csv.DictReader(path.open())
                if r["label"] != "unclear" and r["stratum"] != mod.KNOWN_CORRECT
            ],
            "match_mode",
        )
    }
    assert mode_rates == {"both": 0.0, "ticker_only": 1.0}


def test_report_rejects_unlabeled(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    _write(path, [("d/s/both", "both", 10, "")])
    with pytest.raises(ValueError, match="no yes/no/unclear label"):
        mod.report(path)


def test_main_sample_and_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out" / "sample.csv"
    assert mod.main(["sample", "--db", str(_db(tmp_path)), "--out", str(out)]) == 0
    assert out.is_file()
    assert mod.main(["sample", "--db", str(tmp_path / "nope.db")]) == 1
    assert mod.main(["report", "--sample", str(tmp_path / "nope.csv")]) == 1
    assert "not found" in capsys.readouterr().err


def test_manual_export_hides_assistant_labels_and_apply_merges(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sample = tmp_path / "sample.csv"
    assert mod.main(["sample", "--db", str(db), "--out", str(sample)]) == 0
    rows = list(csv.DictReader(sample.open()))
    for r in rows:  # pretend the assistant labeled everything "yes"
        r["label"], r["labeled_by"] = "yes", "assistant"
    with sample.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=mod.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    sheet = tmp_path / "manual.csv"
    assert (
        mod.main(["manual-export", "--db", str(db), "--sample", str(sample), "--out", str(sheet)])
        == 0
    )
    sheet_rows = list(csv.DictReader(sheet.open()))
    assert len(sheet_rows) == len(rows)
    assert {r["label"] for r in sheet_rows} == {""}
    assert "assistant_label" not in sheet_rows[0]
    sheet_rows[0]["label"], sheet_rows[1]["label"] = "no", "YES"
    with sheet.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=mod.MANUAL_FIELDS)
        writer.writeheader()
        writer.writerows(sheet_rows)
    summary = mod.manual_apply(sample, sheet)
    assert "applied 2 hand labels" in summary
    assert "agreement with the assistant on 2 rows: 1/2" in summary
    merged = {r["id"]: r for r in csv.DictReader(sample.open())}
    first = merged[sheet_rows[0]["id"]]
    assert (first["label"], first["labeled_by"], first["assistant_label"]) == ("no", "hand", "yes")
    assert merged[sheet_rows[1]["id"]]["label"] == "yes"


def test_manual_apply_rejects_bad_label(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sample = tmp_path / "s.csv"
    _write(sample, [("d/s/both", "both", 10, "yes")])
    sheet = tmp_path / "m.csv"
    sheet.write_text("id,label\n0,maybe\n")
    assert mod.main(["manual-apply", "--sample", str(sample), "--manual", str(sheet)]) == 1
    assert "yes/no/unclear" in capsys.readouterr().err


def _sheet(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text("id,label\n" + "".join(f"{i},{lab}\n" for i, lab in rows))


def test_manual_export_refuses_to_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _db(tmp_path)
    sample = tmp_path / "sample.csv"
    assert mod.main(["sample", "--db", str(db), "--out", str(sample)]) == 0
    sheet = tmp_path / "manual.csv"
    sheet.write_text("hand labels")
    args = ["manual-export", "--db", str(db), "--sample", str(sample), "--out", str(sheet)]
    assert mod.main(args) == 1
    assert "--force" in capsys.readouterr().err
    assert sheet.read_text() == "hand labels"
    assert mod.main([*args, "--force"]) == 0
    assert sheet.read_text().startswith("id,ticker,company")


def test_manual_apply_partial_sheet_and_relabel(tmp_path: Path) -> None:
    sample = tmp_path / "s.csv"
    _write(sample, [("d/s/both", "both", 10, "yes"), ("d/s/both", "both", 10, "no")])
    rows = list(csv.DictReader(sample.open()))
    for r in rows:
        r["labeled_by"] = "assistant"
    with sample.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=mod.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    sheet = tmp_path / "m.csv"
    _sheet(sheet, [("0", "no"), ("1", "")])  # row 1 left blank: untouched
    assert "applied 1 hand labels" in mod.manual_apply(sample, sheet)
    _sheet(sheet, [("0", "yes"), ("1", "")])  # flip after the first apply
    mod.manual_apply(sample, sheet)
    merged = {r["id"]: r for r in csv.DictReader(sample.open())}
    assert (merged["0"]["label"], merged["0"]["assistant_label"]) == ("yes", "yes")
    assert (merged["1"]["label"], merged["1"]["labeled_by"]) == ("no", "assistant")


def test_default_export_path_is_not_the_committed_sheet() -> None:
    assert mod.DEFAULT_EXPORT != mod.DEFAULT_MANUAL


def test_manual_apply_without_assistant_labels(tmp_path: Path) -> None:
    sample = tmp_path / "s.csv"
    _write(sample, [("d/s/both", "both", 10, "")])
    sheet = tmp_path / "m.csv"
    _sheet(sheet, [("0", "yes")])
    text = mod.manual_apply(sample, sheet)
    assert "applied 1 hand labels" in text
    assert "no assistant labels to compare with" in text
    assert "0%" not in text
