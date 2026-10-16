"""How often is a `loose_name` pair really the company? (T-062 decision for the maintainer)

`reclass_rejects.py` promotes the pairs the old rule rejected to `review`/`loose_name`; `review` stays
quarantined, so `portfolio-nlp` reads none of them. Whether a subset can be released to `accept` is a
decision that needs a number: this draws a small stratified sample of the planned promotions
(News Corp, which is most of them, and everything else) for a person to label, and scores the labels.

``sample`` is read-only on the database and writes two files: the sample (same columns as the other
check sheets) and the labeling sheet (title and excerpt, label `yes` / `no` / `unclear`), which
``ticker_tagging_sample.py manual-apply`` merges back. The strata are counted in *distinct URLs*: News
Corp's `NWS` and `NWSA` are the same articles under two tickers, so a URL is drawn once. ``summary``
reports, per stratum, how many are the company, `unclear` and unlabeled rows, the share and its 95% lower
bound, and for the whole a conservative lower bound (the stratum bounds weighted by stratum size), never
a bare point estimate; ``--what-if ID=LABEL`` re-scores with a contested label changed. ``refresh`` keeps
the labeled rows that are still promoted by the shipped matcher and replaces only the others.

Usage:
    PYTHONPATH=src uv run python scripts/loose_name_precision.py sample --db urls.db
    PYTHONPATH=src uv run python scripts/ticker_tagging_sample.py manual-apply \\
        --sample data/ticker_loose_name_sample.csv --manual data/ticker_loose_name_manual.csv
    PYTHONPATH=src uv run python scripts/loose_name_precision.py summary [--what-if ID=yes]
    PYTHONPATH=src uv run python scripts/loose_name_precision.py refresh --db urls.db
"""

from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from reclass_rejects import plan
from ticker_tagging_sample import FIELDS, MANUAL_FIELDS, manual_export, wilson

from data_mining.db import resolve_db_path

DEFAULT_SAMPLE = "data/ticker_loose_name_sample.csv"
DEFAULT_MANUAL = "data/ticker_loose_name_manual.csv"
SEED = 62
NEWS_CORP = frozenset({"NWS", "NWSA"})
ALLOCATION = {"news_corp": 10, "other": 20}


def _pools(
    con: sqlite3.Connection,
) -> tuple[dict[int, tuple[str, str]], dict[str, list[sqlite3.Row]]]:
    """The planned promotions and, per stratum, one pair per distinct URL (the lowest id)."""
    promoted = {i: (cls, reason) for i, cls, reason in plan(con)}
    con.row_factory = sqlite3.Row
    pool: dict[str, dict[str, sqlite3.Row]] = {"news_corp": {}, "other": {}}
    for r in con.execute(
        "SELECT id, url, ticker, company, domain, source FROM discovered_urls ORDER BY id"
    ):
        if r["id"] in promoted:
            pool["news_corp" if r["ticker"] in NEWS_CORP else "other"].setdefault(r["url"], r)
    return promoted, {k: list(v.values()) for k, v in pool.items()}


def _sample_row(
    r: sqlite3.Row, promoted: dict[int, tuple[str, str]], stratum: str, size: int
) -> dict[str, object]:
    return {
        **{k: r[k] for k in ("id", "url", "ticker", "company", "domain", "source")},
        "match_mode": promoted[r["id"]][0],
        "stratum": stratum,
        "stratum_size": size,
        "label": "",
        "labeled_by": "",
        "assistant_label": "",
    }


def draw(con: sqlite3.Connection, seed: int = SEED) -> list[dict[str, object]]:
    """The sampled promoted pairs (distinct URLs), label columns empty, deterministic for a seed."""
    promoted, pool = _pools(con)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    out: list[dict[str, object]] = []
    for stratum, want in ALLOCATION.items():
        for r in rng.sample(pool[stratum], min(want, len(pool[stratum]))):
            out.append(_sample_row(r, promoted, stratum, len(pool[stratum])))
    rng.shuffle(out)  # the labeling order must not reveal the stratum
    return out


def refresh(
    con: sqlite3.Connection, rows: list[dict[str, str]], seed: int = SEED
) -> tuple[list[dict[str, str]], list[str]]:
    """The labeled sample kept as it is, except the rows the shipped matcher no longer promotes, or
    whose URL an earlier row already covers: each is replaced, in place, by a fresh distinct URL of
    the same stratum with a blank label. Returns the new rows and the replaced ids."""
    promoted, pool = _pools(con)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    used: set[str] = set()
    keep: list[bool] = []
    for r in rows:
        ok = int(r["id"]) in promoted and r["url"] not in used
        used.add(r["url"])
        keep.append(ok)
    out: list[dict[str, str]] = []
    dropped: list[str] = []
    for row, kept in zip(rows, keep, strict=True):
        size = len(pool[row["stratum"]])
        if kept:
            out.append({**row, "stratum_size": str(size)})
            continue
        dropped.append(row["id"])
        pick = rng.choice([r for r in pool[row["stratum"]] if r["url"] not in used])
        used.add(pick["url"])
        fresh = _sample_row(pick, promoted, row["stratum"], size)
        out.append({k: str(v) for k, v in fresh.items()})
    return out, dropped


def score(rows: list[dict[str, str]], what_if: dict[str, str] | None = None) -> str:
    """Per stratum: pairs that are the company (`yes`), not (`no`), `unclear`, unlabeled, the share
    among the decided ones and its 95% lower bound; for the whole, a conservative lower bound (the
    stratum bounds weighted by stratum size; the point estimate is shown after it, never alone).
    `what_if` maps an id to a label that replaces the sheet's, to see what a contested label changes."""
    labels = {r["id"]: (what_if or {}).get(r["id"], r["label"]).strip().lower() for r in rows}
    lines: list[str] = []
    bound = total = point = 0.0
    unclear_all = 0
    for stratum in ALLOCATION:
        sub = [r for r in rows if r["stratum"] == stratum]
        if not sub:
            continue
        yes = sum(labels[r["id"]] == "yes" for r in sub)
        no = sum(labels[r["id"]] == "no" for r in sub)
        unclear = sum(labels[r["id"]] == "unclear" for r in sub)
        blank = len(sub) - yes - no - unclear
        decided = yes + no
        share = yes / decided if decided else 0.0
        low = 1 - wilson(no, decided)[1] if decided else 0.0
        size = int(sub[0]["stratum_size"])
        bound += low * size
        point += share * size
        total += size
        unclear_all += unclear
        lines.append(
            f"{stratum}: {len(sub)} distinct URLs sampled of {size}; {yes} the company, {no} not, "
            f"{unclear} unclear, {blank} unlabeled; share {share:.0%} (95% lower bound {low:.0%})"
        )
    if total:
        lines.append(
            f"overall: 95% lower bound {bound / total:.0%} of the {int(total)} URLs are the company "
            f"(weighted by stratum size; point estimate {point / total:.0%})"
        )
    if unclear_all:
        lines.append(f"{unclear_all} unclear row(s) are not counted as the company")
    return "\n".join(lines)


def _write_sample(path: Path, rows: list[dict[str, object]] | list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _splice_manual(db: Path, sample: Path, manual: Path) -> None:
    """Rewrite the labeling sheet for a refreshed sample: the rows already labeled stay as they are,
    the replacement rows are exported fresh (blank)."""
    with manual.open(newline="", encoding="utf-8") as fh:
        old = {r["id"]: r for r in csv.DictReader(fh)}
    tmp = manual.with_suffix(".tmp.csv")
    manual_export(db, sample, tmp)
    with tmp.open(newline="", encoding="utf-8") as fh:
        fresh = {r["id"]: r for r in csv.DictReader(fh)}
    tmp.unlink()
    ids = [r["id"] for r in csv.DictReader(sample.open(newline="", encoding="utf-8"))]
    with manual.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANUAL_FIELDS)
        writer.writeheader()
        writer.writerows(old[i] if i in old else fresh[i] for i in ids)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (
        ("sample", "draw the sample and the labeling sheet"),
        ("refresh", "keep the still-valid labeled rows, replace the others"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--db", default=None)
        p.add_argument(
            "--out" if name == "sample" else "--sample", dest="sample", default=DEFAULT_SAMPLE
        )
        p.add_argument("--manual", default=DEFAULT_MANUAL)
    p = sub.add_parser("summary", help="score the labeled sample")
    p.add_argument("--sample", default=DEFAULT_SAMPLE)
    p.add_argument("--what-if", action="append", default=[], metavar="ID=LABEL")
    args = ap.parse_args(argv)
    if args.cmd == "summary":
        what_if = dict(w.split("=", 1) for w in args.what_if)
        with Path(args.sample).open(newline="", encoding="utf-8") as fh:
            print(score(list(csv.DictReader(fh)), what_if))
        return 0
    db = Path(args.db or resolve_db_path())
    if not db.is_file():
        print(f"error: {db} not found", file=sys.stderr)
        return 1
    out = Path(args.sample)
    if args.cmd == "refresh":
        with out.open(newline="", encoding="utf-8") as fh:
            old_rows = list(csv.DictReader(fh))
        with closing(sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)) as con:
            new_rows, dropped = refresh(con, old_rows)
        _write_sample(out, new_rows)
        _splice_manual(db, out, Path(args.manual))
        print(
            f"kept {len(old_rows) - len(dropped)} labeled rows; replaced {len(dropped)}: {dropped}"
        )
        return 0
    with closing(sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)) as con:
        rows = draw(con)
    _write_sample(out, rows)
    manual_export(db, out, Path(args.manual))
    print(f"wrote {len(rows)} rows to {out} and the labeling sheet to {args.manual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
