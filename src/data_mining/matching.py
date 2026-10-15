"""Company matching for discovery: which `(url, ticker)` pairs are about that company.

One pure, dependency-free rule shared by the news-collector connectors
(`BaseConnector.classify_match`) and, later, the extractor's post-fetch
re-evaluation. It looks only at the URL **path and query** (never the host) and
the title, and returns one of three classes:

- ``accept``: evidence about this company. The full normalized company name
  (``&`` read as ``and``; a name that is itself an ordinary word, such as ``News``
  for News Corp, never counts alone) or a hand-kept alias is in the path or title,
  or an explicit ticker form (``$X``, ``(NYSE: X)``, ``NASDAQ: X``), or the ticker
  as an upper-case token of 2+ letters in the title that is not an ordinary word
  or a common abbreviation.
- ``review``: only a weaker sign: a non-common ticker or a distinctive first word
  somewhere in the path or title, or an abbreviation-like 2-letter ticker in upper
  case in the title. Quarantined and decided downstream; never excluded.
- ``reject``: nothing else.

The rule is monotone: more text can only add evidence, so classifying again with
a longer title never lowers the class. A ticker never matches as a raw URL
substring, and a lone ordinary-word ticker or generic first word is never enough.

Design and evidence: ``.specify/memory/PLAN.md`` Work item 10 (T-055), reproduced
by ``scripts/score_ticker_rules.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

MatchClass = Literal["accept", "review", "reject"]

# Ordinary English words that are also tickers (PLAN Work item 10, failure mode 1).
WORD_TICKERS = ("ON", "A", "ARE", "IT", "TECH", "HAS", "NOW", "ALL")

MIN_NAME = 4
MIN_TICKER = 3
MIN_SHORT_TICKER = 2

# Lone first words too ordinary to identify a company by themselves.
STOPWORDS = frozenset(
    {
        *("dollar", "american", "trade", "bank", "first", "news", "best", "global", "home"),
        *("target", "public", "live", "state", "federal", "capital", "texas", "digital"),
        *("extra", "union", "equity", "international", "general", "united", "national"),
        *("new", "pacific", "southern", "western", "norfolk"),
    }
)

# Ordinary words that are also tickers or look like them in a headline; an
# upper-case ticker among these never counts as evidence by itself.
COMMON_TICKER_WORDS = frozenset(WORD_TICKERS) | frozenset(
    {
        *("AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN", "IS", "ME", "MY"),
        *("NO", "OF", "OR", "SO", "TO", "UP", "US", "WE", "I", "CAN", "FOR", "ONE", "OUT"),
        *("NEW", "BIG", "FAST", "KEY", "APP", "FIX"),
    }
)

# 2-letter tickers that are also upper-case abbreviations in a headline ("Egypt PM
# says"). Never `accept` on their own, but never excluded either: an upper-case
# hit in the title is `review`, so the list cannot hide a pair.
ABBREVIATIONS = frozenset({"PM", "MA", "ED", "EL", "IP", "CI", "HD", "PH", "MS", "NI", "DD"})
BASELINE_ABBREVIATIONS: frozenset[str] = frozenset()  # the first draft had none

# Names a company is also known by, matched like the full name (hand-kept, tested).
# `coke` for KO was rejected: it is also a fuel and a steel input.
ALIASES: dict[str, tuple[str, ...]] = {
    "GOOGL": ("google",),
    "GOOG": ("google",),
    "PG": ("p and g",),
    "META": ("facebook",),
    "BRK.B": ("berkshire",),
    "BRK.A": ("berkshire",),
    "JPM": ("jpmorgan",),
    "WMT": ("wal mart",),
}

_SUFFIX = re.compile(
    r"\b(inc|corp|corporation|co|company|ltd|plc|llc|holdings|group|the|class [a-c])\b\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Match:
    """A class and the signal that decided it (``reason``)."""

    cls: MatchClass
    reason: str


def normalized_name(company: str) -> str:
    """Company name lowercased, corporate suffixes dropped, punctuation as spaces."""
    base = _SUFFIX.sub(" ", company.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", base).split())


def flat(text: str) -> str:
    """Lower-case words of ``text`` between spaces; ``&`` is read as ``and``."""
    words = re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and ")).split()
    return " " + " ".join(words) + " "


def full_name(company: str) -> str:
    """`normalized_name` with ``&`` read as ``and`` (URLs spell it out)."""
    return normalized_name(company.replace("&", " and "))


def word(token: str, text: str, flags: int = re.IGNORECASE) -> bool:
    """``token`` as a whole word of ``text`` (not part of a longer word, not around ``&``)."""
    pattern = rf"(?<![A-Za-z0-9&]){re.escape(token)}(?![A-Za-z0-9&])"
    return re.search(pattern, text, flags) is not None


def explicit_ticker(ticker: str, *texts: str) -> bool:
    """An explicit ticker form (``$X``, ``(NYSE: X)``, ``NASDAQ: X``) in any of ``texts``."""
    t = re.escape(ticker)
    pattern = re.compile(
        rf"(\((?:NYSE|NASDAQ|NYSEARCA|Nasdaq|NYSE American)?:?\s*{t}\)"
        rf"|(?:NYSE|NASDAQ)\s*:\s*{t}\b|\${t}\b)"
    )
    return any(pattern.search(text) for text in texts)


def _name_reason(
    company: str,
    ticker: str,
    text: str,
    aliases: dict[str, tuple[str, ...]],
) -> str | None:
    """``'name'`` or ``'alias'`` when the full name / an alias is in ``text``."""
    name = full_name(company)
    distinctive = (len(name) >= MIN_NAME or any(c.isdigit() for c in name)) and (
        name not in STOPWORDS
    )  # "3M" yes; "News" (News Corp) no
    flat_text = flat(text)
    if distinctive and f" {name} " in flat_text:
        return "name"
    if any(f" {a} " in flat_text for a in aliases.get(ticker, ())):
        return "alias"
    return None


def name_in(
    company: str,
    ticker: str,
    text: str,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    """True when the full name or an alias is in ``text``."""
    return _name_reason(company, ticker, text, ALIASES if aliases is None else aliases) is not None


def _strong(
    title: str,
    text: str,
    company: str,
    ticker: str,
    aliases: dict[str, tuple[str, ...]],
    common: frozenset[str],
) -> Match | None:
    found = _name_reason(company, ticker, text, aliases)
    if found:
        return Match("accept", found)
    if explicit_ticker(ticker, title):
        return Match("accept", "explicit_ticker")
    if len(ticker) >= MIN_SHORT_TICKER and ticker not in common and word(ticker, title, 0):
        return Match("accept", "upper_ticker")
    return None


def _weak(
    title: str,
    text: str,
    company: str,
    ticker: str,
    abbreviations: frozenset[str],
    common: frozenset[str],
) -> Match | None:
    if len(ticker) >= MIN_TICKER and ticker not in common and word(ticker, text):
        return Match("review", "weak_ticker")
    words = company.split(maxsplit=1)
    first = words[0].strip(",.") if words else ""
    if len(first) >= MIN_NAME and first.lower() not in STOPWORDS and word(first, text):
        return Match("review", "weak_first_word")
    if ticker in abbreviations and word(ticker, title, 0):
        return Match("review", "abbreviation")
    return None


def classify(
    url: str,
    title: str,
    company: str,
    ticker: str,
    *,
    abbreviations: frozenset[str] = ABBREVIATIONS,
    with_host: bool = False,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> Match:
    """Class a pair as ``accept`` / ``review`` / ``reject`` from the URL path and title.

    ``with_host`` and the other keywords exist only so the scorer can measure what
    an earlier version of the rule did; production callers pass none.
    """
    title = title or ""
    parts = urlsplit(url)
    host = f"{parts.netloc} " if with_host else ""
    text = f"{host}{parts.path} {parts.query} {title}"
    common = COMMON_TICKER_WORDS | abbreviations
    alias_map = ALIASES if aliases is None else aliases
    return (
        _strong(title, text, company, ticker, alias_map, common)
        or _weak(title, text, company, ticker, abbreviations, common)
        or Match("reject", "none")
    )
