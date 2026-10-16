"""The read-only article-quality audit (T-060)."""

from __future__ import annotations

import sys
from pathlib import Path

from data_mining.db import connect
from data_mining.schema import apply_schema

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from audit_article_quality import audit, by_status, main, wall_ids

_PROSE = "Shares rose as analysts weighed the results. " * 30


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "urls.db"
    con = connect(str(path))
    apply_schema(con)
    rows = [
        (1, _PROSE, "ok"),
        (2, _PROSE, "ok"),  # the same body twice
        (3, "Just a moment... " + _PROSE, "ok"),  # a wall stored as ok
        (4, "short", "ok"),
        (5, "Just a moment...", "failed"),  # not ok: ignored
        (6, "Just a moment... " + _PROSE, "quarantined"),  # held, was ok: comes back on release
        (7, "Just a moment... " + _PROSE, "quarantined"),  # held, was failed: stays out
    ]
    for i, body, status in rows:
        con.execute(
            "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at)"
            " VALUES (?, ?, 'cnbc.com', 'Apple', 'AAPL', 'sitemap', '2026-01-01')",
            (i, f"https://cnbc.com/{i}"),
        )
        con.execute(
            "INSERT INTO articles (id, source_domain, body_text, fetch_status)"
            " VALUES (?, 'cnbc.com', ?, ?)",
            (i, body, status),
        )
    con.execute("UPDATE articles SET prev_fetch_status = 'ok' WHERE id = 6")
    con.execute("UPDATE articles SET prev_fetch_status = 'failed' WHERE id = 7")
    con.commit()
    con.close()
    return path


def test_audit_counts_ok_rows_per_domain(tmp_path: Path) -> None:
    assert audit(_db(tmp_path))["cnbc.com"] == {
        "ok": 4,
        "walls": 1,
        "under_floor": 1,
        "duplicate_bodies": 1,
        "held": 1,
        "held_walls": 1,
    }


def test_main_exits_1_when_a_wall_is_stored_as_ok(tmp_path: Path) -> None:
    assert main(["--db", str(_db(tmp_path))]) == 1


def test_main_exits_1_when_an_ok_row_is_under_the_floor(tmp_path: Path) -> None:
    path = _db(tmp_path)
    con = connect(str(path))
    con.execute("UPDATE articles SET body_text = ? WHERE id = 3", (_PROSE,))  # no wall left
    con.commit()
    con.close()

    assert audit(path)["cnbc.com"]["walls"] == 0
    assert main(["--db", str(path)]) == 1  # row 4 is still "short"


def test_wall_ids_lists_only_ok_walls(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = _db(tmp_path)

    assert wall_ids(path) == [3, 6]  # 5 is `failed`, 7 was `failed` before quarantine
    assert main(["--db", str(path), "--list"]) == 1
    assert capsys.readouterr().out.split() == ["3", "6"]


def test_a_held_wall_alone_fails_the_audit(tmp_path: Path) -> None:
    path = _db(tmp_path)
    con = connect(str(path))
    con.execute("UPDATE articles SET body_text = ? WHERE id IN (3, 4)", (_PROSE,))
    con.commit()
    con.close()

    assert audit(path)["cnbc.com"]["walls"] == 0
    assert main(["--db", str(path)]) == 1  # row 6 would be a wall once released


def test_by_status_counts_the_detector_over_every_row(tmp_path: Path) -> None:
    result = by_status(_db(tmp_path))

    assert result["failed"] == {"failed": 1, "paywalled": 0, "none": 0}
    assert result["quarantined"] == {"failed": 2, "paywalled": 0, "none": 0}
    assert result["ok"] == {"failed": 1, "paywalled": 0, "none": 3}
