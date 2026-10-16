"""Lower bound of the T-057 false rejects: rejected pairs whose full company name is in the URL.

The frozen rule (`data_mining.matching`) rejects some pairs whose URL spells the company out, for
three reasons (PLAN Work item 10): a name that is short or an ordinary word once its suffix is
removed (``kla-corporation``, ``southern-company``), a possessive that URLs write as a trailing
``s`` (``bank-of-americas``, ``dollar-generals``), and an ``&`` that URLs drop
(``flavors-fragrances``). This scans the re-tag's ``removed_articles.csv`` for the company's name,
with and without the suffix, with and without ``&``, with and without a trailing ``s``, as a run
of whole words of the URL path. A trailing ``s`` cannot tell a possessive from a plural, so the
hits were read by hand once: ``NOISE`` holds the 19 article ids that are not the company
(``best-buys`` is a list of picks, ``on-semiconductors`` a plural, ``at-t-mobiles`` is T-Mobile,
``target-corporations`` a noun), keyed by id so only what was read is left out and a later hit for
the same ticker still counts. The committed ``data/retag_known_false_rejects.csv`` is the scan
without them. It is a lower bound: a name the URL abbreviates, reorders or leaves to the title is
not found.

Usage:
    PYTHONPATH=src uv run python scripts/known_false_rejects.py [--removed PATH] [--out PATH]
    PYTHONPATH=src uv run python scripts/known_false_rejects.py --check   # file == the scan
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from data_mining.matching import normalized_name  # the matcher's own suffix list

MIN_WORDS = 2  # one word (`news`, `target`) is too common to be the company in a URL
DEFAULT_REMOVED = "data/retag/removed_articles.csv"
DEFAULT_OUT = "data/retag_known_false_rejects.csv"
# Article ids whose slug hit was read and is not the company (see the docstring).
NOISE = {
    **dict.fromkeys(
        ("141371", "141732", "142721", "143035", "144014", "145143", "145565", "145687", "146107"),
        "BBY: `best-buys` is a list of picks",
    ),
    **dict.fromkeys(
        ("346261", "355636", "356296", "356335", "365958", "366517"),
        "ON: `on-semiconductors` is a plural",
    ),
    "127085": "T: `at-t-mobiles` is T-Mobile",
    "127098": "T: `at-t-rowe-price` is T. Rowe Price",
    "127178": "T: `light-at-t` is a phrase",
    "445050": "TGT: `target-corporations` is a noun",
}
FIELDS = [
    "article_id",
    "ticker",
    "company",
    "url",
    "match_class",
    "match_reason",
    "prev_fetch_status",
]


def _forms(company: str) -> set[str]:
    """Slugs of the name: with/without the suffix, with/without ``&``; two words or more."""
    out: set[str] = set()
    for amp in (" and ", " "):
        spelled = re.sub(r"\([^)]*\)", " ", company.replace("&", amp))  # "(Class B)", "(The)"
        for name in (spelled.lower(), normalized_name(spelled)):
            words = re.findall(r"[a-z0-9]+", name)
            if len(words) >= MIN_WORDS:
                out.add("-".join(words))
    return out


def in_url(company: str, url: str) -> bool:
    """True when a slug of the company name is a run of whole words of the URL path
    (a trailing ``s`` allowed: ``dollar-generals``)."""
    path = re.sub(r"^https?://[^/]+", "", url.lower().split("?")[0])
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(slug)}s?(?![a-z0-9])", path)
        for slug in _forms(company)
    )


def scan(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The rows whose company name is in the URL, minus the reviewed noise ids."""
    return [r for r in rows if r["article_id"] not in NOISE and in_url(r["company"], r["url"])]


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--removed", default=DEFAULT_REMOVED)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument(
        "--check", action="store_true", help="the committed file must equal the scan"
    )
    args = parser.parse_args(argv)
    removed, out = Path(args.removed), Path(args.out)
    if not removed.is_file():
        print(f"error: {removed} not found (run retag_urls.py first)", file=sys.stderr)
        return 1
    found = scan(_read(removed))
    if args.check:
        same = {r["article_id"] for r in found} == {r["article_id"] for r in _read(out)}
        print(f"{len(found)} rows in the scan: {'matches' if same else 'DIFFERS from'} {out}")
        return 0 if same else 1
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(found)
    print(f"wrote {len(found)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
