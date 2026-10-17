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
  or a common abbreviation. A cashtag (``$X``) of an ordinary-word ticker or an
  abbreviation is only ``review``; only an exchange form (``NYSE: X``) is ``accept``.
- ``review``: only a weaker sign: a non-common ticker or a distinctive first word
  somewhere in the path or title, or an abbreviation-like 2-letter ticker in upper
  case in the title. Quarantined and decided downstream; never excluded.
- ``reject``: nothing else.

The rule is monotone: more text can only add evidence, so classifying again with
a longer title never lowers the class. A ticker never matches as a raw URL
substring, and a lone ordinary-word ticker or generic first word is never enough
(only an exchange form, ``NYSE: ON``, makes such a ticker ``accept``; a cashtag is ``review``).

Design and evidence: ``.specify/memory/PLAN.md`` Work item 10 (T-055), reproduced
by ``scripts/score_ticker_rules.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

MatchClass = Literal["accept", "review", "reject"]

# `reject` < `review` < `accept`: a stored class is only ever promoted (T-056b/c).
CLASS_RANK: dict[MatchClass, int] = {"reject": 0, "review": 1, "accept": 2}

# Ordinary English words that are also tickers (PLAN Work item 10, failure mode 1).
WORD_TICKERS = ("ON", "A", "ARE", "IT", "TECH", "HAS", "NOW", "ALL")

MIN_NAME = 4
MIN_WORDS = 2  # a loose name match spans two words or more, or the name is distinctive
MIN_WORD_CHARS = 2  # ... and each of its words has at least two characters
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


# Names whose plural the slug spells like a possessive but is an ordinary phrase (read by hand in the
# real data, PLAN Work item 12: `best-buys` is a list of picks, not Best Buy, and
# `on-semiconductors` is a plural, not ON Semiconductor, and `target-corporations` is a noun phrase, not
# Target Corporation). Owner: whoever changes the company universe re-scans for new names of this shape.
PLURAL_PHRASES = frozenset({"best buy", "on semiconductor", "target corporation"})
_CAMEL_TAIL = re.compile(
    r"(?<=[a-z])(?:Corp|Co)$"
)  # PepsiCo, KeyCorp: a capital tail on a lower-case letter


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


_EXCHANGE = r"(?:NYSE American|NYSE MKT|NYSEARCA|NYSE|NASDAQ|Nasdaq)"
_END = r"(?![A-Za-z0-9&-])"  # same right edge as `word`, plus no hyphen ("NYSE: A-list")


def explicit_ticker(ticker: str, *texts: str, cashtag: bool = True) -> bool:
    """An explicit ticker form (``$X``, ``(NYSE: X)``, ``NASDAQ: X``) in any of ``texts``.

    The exchange is mandatory: a bare ``(X)`` is not explicit (``(A)``, ``(ON)``,
    ``(PM)`` are list markers and abbreviations). ``cashtag=False`` leaves ``$X`` out;
    `classify` does that for ordinary-word tickers and abbreviations, where ``$X``
    is only `review` evidence.
    """
    t = re.escape(ticker)
    forms = [
        rf"\({_EXCHANGE}\s*:\s*{t}\)",
        rf"{_EXCHANGE}\s*:\s*{t}{_END}",
    ]
    if cashtag:
        forms.append(rf"\${t}{_END}")
    pattern = re.compile("|".join(forms))
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


def _kept_name(company: str) -> str:
    """The lower-case name with its corporate suffix kept and ``(Class B)`` gone."""
    return " ".join(flat(re.sub(r"\([^)]*\)", " ", company)).split())


def _spelled_enough(form: str) -> bool:
    """A form is safe to look for on its own: two words or more, or one distinctive word.

    Every word needs at least two characters: the ``&``-dropped form of ``AT&T`` is ``at t``, which is
    two words but not a spelling of the company (``…fund-manager-at-t-rowe-price…``)."""
    words = form.split()
    if any(len(w) < MIN_WORD_CHARS for w in words):
        return False
    return len(words) >= MIN_WORDS or (len(form) >= MIN_NAME and form not in STOPWORDS)


def _loose_name(company: str, text: str) -> bool:
    """The company's name as a URL spells it, a weaker sign than the exact name (``review``).

    Spellings the exact check misses (PLAN Work item 12): the name with its corporate suffix kept
    (``kla-corporation``, ``southern-company``, ``news-corp``), the name with the ``and`` of an ``&``
    dropped (``flavors-fragrances``), and the brand of a camel-case name (``pepsi`` for ``PepsiCo``;
    only a capital ``Co``/``Corp`` tail counts, so ``Costco`` and ``Cisco`` are not cut). A trailing
    ``s`` of a possessive is read (``news-corps-dual-class``, ``bank-of-americas-ceo``; 45 of the 433
    known false rejects) only for a name of two words or more, because a slug writes a plural the same
    way (``apples`` and ``visas`` are nouns), and not for a name in `PLURAL_PHRASES`, the phrases read
    by hand in the real data. A single word
    is never enough on its own: it must be distinctive, or the match must span two words or more.
    """
    flat_text = flat(text)
    name = full_name(company)
    brand = flat(_CAMEL_TAIL.sub("", re.sub(r"\([^)]*\)", " ", company).strip())).strip()
    forms = {name, name.replace(" and ", " "), _kept_name(company), brand}
    for form in forms:
        if not form or not _spelled_enough(form):
            continue
        if f" {form} " in flat_text:
            return True
        words = form.split()
        possessive_safe = len(words) >= MIN_WORDS and form not in PLURAL_PHRASES
        if possessive_safe and f" {form}s " in flat_text:
            return True
    return False


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
    if explicit_ticker(ticker, title, cashtag=ticker not in common):
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
    if ticker in common and explicit_ticker(ticker, title):
        return Match("review", "cashtag")
    if len(ticker) >= MIN_TICKER and ticker not in common and word(ticker, text):
        return Match("review", "weak_ticker")
    if _loose_name(company, text):
        return Match("review", "loose_name")
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
