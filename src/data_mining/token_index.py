"""The token index that preselects the candidate sitemap entries of one company (T-068, T-071).

Today (before `T-071`) every ticker classed every entry of a domain's sitemap, about 4.6 s a ticker
for `cnbc.com`. The design of Work item 15 keeps the entries once per domain in the cache and, per
ticker, classes only the *candidates* of this index. **The index preselects; it never decides**: every
candidate is classed by `data_mining.matching.classify`, and the result must equal the full scan's
`accept`/`review` pairs, class, reason and `pub_date` (guardrail 2 of `PLAN.md` Work item 15).

Why the candidate set is a superset (the argument, tested by `tests/scripts/test_sitemap_preselect_check.py`
and by the equality tests of `T-071`): every signal of `classify` needs one *form* (the name, an alias,
the ticker, a loose-name spelling, the first word) to appear in `flat(path + query + title)` as
consecutive whole tokens, so each token of the form is a token of the entry; the index holds those
tokens, and any one token of the form is a valid anchor (the code takes the rarest). Two forms change a
token and are handled: the possessive `form + "s"` of a loose name changes only the last token (the
anchor is a token other than the last, or the postings of the last token and of the last token plus
`s`), and the title-only signals (`NYSE: X`, `$X`, an upper-case ticker, an abbreviation) need the
ticker as a token of the *title*, which has its own index. **The argument stops holding if a signal is
added to `classify` that does not need a token of its form (a fuzzy or a prefix match): the equality
tests then fail, which is their purpose.**

The index is built in memory per run (1.6 s for 109,493 entries, 33,843 distinct tokens); it is not
stored.
"""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlsplit

from data_mining import matching as M

Row = tuple[str, str | None, str | None]  # normalized url, lastmod (ISO), title


def entry_text(url: str, title: str | None) -> str:
    """The text `classify` reads (the host is never read)."""
    parts = urlsplit(url)
    return f"{parts.path} {parts.query} {title or ''}"


class TokenIndex:
    """`flat()` tokens of the entry text -> row ids; a second index for the title alone.

    ``skip`` names anchor families to leave out: only the mutation check uses it, to prove that
    the equality check fails when an anchor is missing.
    """

    def __init__(self, rows: list[Row], skip: frozenset[str] = frozenset()) -> None:
        self.skip = skip
        self.size = len(rows)  # how many entries the index was built over
        self.text: dict[str, list[int]] = defaultdict(list)
        self.title: dict[str, list[int]] = defaultdict(list)
        for i, (url, _lastmod, title) in enumerate(rows):
            for token in set(M.flat(entry_text(url, title)).split()):
                self.text[token].append(i)
            if title:
                for token in set(M.flat(title).split()):
                    self.title[token].append(i)

    @staticmethod
    def _rarest(tokens: list[str], index: dict[str, list[int]]) -> list[int]:
        """Postings of the rarest token: a form needs *all* its tokens, so any one is an anchor."""
        postings = [index.get(token, []) for token in tokens]
        return min(postings, key=len) if postings else []

    def _form(self, tokens: list[str]) -> set[int]:
        """Anchor of a loose-name form. The possessive (`form` + "s") changes only the last token,
        so a form of 2+ words is anchored on a token other than the last, or on the entries that
        have the last token or the last token plus `s` (both are valid; the smaller wins)."""
        if len(tokens) < M.MIN_WORDS:
            return set(self._rarest(tokens, self.text))
        head = self._rarest(tokens[:-1], self.text)
        tail = set(self.text.get(tokens[-1], ())) | set(self.text.get(tokens[-1] + "s", ()))
        return set(head) if len(head) <= len(tail) else tail

    def candidates(self, company: str, ticker: str) -> set[int]:
        common = M.COMMON_TICKER_WORDS | M.ABBREVIATIONS
        found: set[int] = set()
        name = M.full_name(company)
        distinctive = (len(name) >= M.MIN_NAME or any(c.isdigit() for c in name)) and (
            name not in M.STOPWORDS
        )
        if distinctive and "name" not in self.skip:
            found.update(self._rarest(name.split(), self.text))
        if "alias" not in self.skip:
            for alias in M.ALIASES.get(ticker, ()):
                found.update(self._rarest(alias.split(), self.text))
        if len(ticker) >= M.MIN_TICKER and ticker not in common and "weak_ticker" not in self.skip:
            found.update(self._rarest(M.flat(ticker).split(), self.text))
        if "loose_name" not in self.skip:
            brand = M.flat(
                M._CAMEL_TAIL.sub("", re.sub(r"\([^)]*\)", " ", company).strip())
            ).strip()
            kept = M._kept_name(company)
            legal = re.sub(r" corp$", " corporation", kept)
            for form in {name, name.replace(" and ", " "), kept, legal, brand}:
                if form and M._spelled_enough(form):
                    found.update(self._form(form.split()))
        words = company.split(maxsplit=1)
        first = words[0].strip(",.") if words else ""
        first_ok = len(first) >= M.MIN_NAME and first.lower() not in M.STOPWORDS
        if first_ok and "weak_first_word" not in self.skip:
            found.update(self._rarest(M.flat(first).split(), self.text))
        if "title" not in self.skip:  # explicit_ticker, upper_ticker, cashtag, abbreviation
            found.update(self._rarest(M.flat(ticker).split(), self.title))
        return found
