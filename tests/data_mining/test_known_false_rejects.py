"""The T-057 false rejects: pairs with the full company name in the URL that the frozen rule rejects.

`data/retag_known_false_rejects.csv` lists them (found after the real re-tag, a lower bound; see PLAN
Work item 10). The test pins today's behaviour so a matcher fix shows up here: when it lands, the
pairs stop being `reject`, this test fails, and the fix re-classes them and releases their articles.
"""

import csv
from pathlib import Path

from data_mining.matching import classify

_SHEET = Path(__file__).resolve().parents[2] / "data" / "retag_known_false_rejects.csv"


def _rows() -> list[dict[str, str]]:
    with _SHEET.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_the_sheet_lists_the_28_pairs() -> None:
    rows = _rows()

    assert len(rows) == 28
    assert len({r["article_id"] for r in rows}) == 28
    assert {r["ticker"] for r in rows} == {"KLAC", "SO", "DG", "IFF"}


def test_the_frozen_rule_still_rejects_them() -> None:
    classes = {classify(r["url"], "", r["company"], r["ticker"]).cls for r in _rows()}

    assert classes == {"reject"}  # a matcher fix changes this: re-class and release the articles
