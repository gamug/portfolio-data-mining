"""Deterministic parsing of article metadata/body from raw HTML.

Primary source of truth for title/author/pub_date is embedded NewsArticle
JSON-LD (or meta tags as fallback) -- see CLAUDE.md for why this is
preferred over trafilatura's own metadata guesses or an LLM.
"""

import json
import re
from collections.abc import Iterator
from typing import Any, Literal

import trafilatura
from bs4 import BeautifulSoup
from langdetect import LangDetectException, detect

# Domains known/expected to gate full articles behind a paywall (see CLAUDE.md).
KNOWN_PAYWALLED_DOMAINS = {"ft.com", "seekingalpha.com"}

PAYWALL_MARKERS = (
    "subscribe to continue reading",
    "subscribe to read",
    "this content is for subscribers",
    "to continue reading this article",
)

MIN_WORDS_FOR_FULL_CONTENT = 80

# Pages served with a 200 and a body long enough to pass the length check that are not the article
# (T-060). A wall starts with its own message, so only the first `WALL_HEAD_CHARS` characters are
# matched, case-insensitively and as whole words: real CNBC articles open with "just a momentary
# blip" and "investigating unusual traffic that might be from hackers". A bot challenge is
# transient (`failed`, retried by `--retry-failed`); a login or subscribe prompt is gated content a
# retry does not change (`paywalled`).
# "Just a moment..." and "Access denied" are common prose ("For just a moment, mentally fast-forward"),
# so they only count at the very start of the body, where a challenge page puts them.
CHALLENGE_START = ("just a moment", "access denied")
CHALLENGE_PHRASES = (
    "security verification",
    "unusual traffic from your",
    "verify you are a human",
    "verify you are human",
    "are you a robot",
    "checking your browser",
    "enable javascript and cookies",
)
GATE_PHRASES = (
    "sign in to continue",
    "log in to continue",
    "create a free account to continue",
    "subscribe to continue",
)
WALL_HEAD_CHARS = 300


def extract_body_text(html: str) -> str:
    """Extract main article text, stripped of nav/ads/boilerplate."""
    text = trafilatura.extract(html, favor_precision=True)
    return text or ""


def word_count(text: str) -> int:
    return len(text.split())


def is_thin_content(text: str, min_words: int = MIN_WORDS_FOR_FULL_CONTENT) -> bool:
    return word_count(text) < min_words


def _has_phrase(head: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", head) for phrase in phrases)


# a challenge page opens with its message, possibly after quote / markdown marks
_STARTS_WITH_CHALLENGE = re.compile(rf"[\s\"'#*]*(?:{'|'.join(map(re.escape, CHALLENGE_START))})\b")


def wall_kind(text: str) -> Literal["failed", "paywalled"] | None:
    """`fetch_status` for a wall page: `failed` (bot challenge), `paywalled` (login / subscribe
    prompt), or None when the body is an article."""
    head = text[:WALL_HEAD_CHARS].lower()
    if _has_phrase(head, CHALLENGE_PHRASES) or _STARTS_WITH_CHALLENGE.match(head):
        return "failed"
    if _has_phrase(head, GATE_PHRASES):
        return "paywalled"
    return None


def is_wall_page(text: str) -> bool:
    """True when the extracted body is a login wall / bot challenge / subscribe prompt."""
    return wall_kind(text) is not None


def is_probably_paywalled(html: str, domain: str) -> bool:
    if domain not in KNOWN_PAYWALLED_DOMAINS:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in PAYWALL_MARKERS)


def detect_language(text: str) -> str | None:
    if not text.strip():
        return None
    try:
        return str(detect(text))
    except LangDetectException:
        return None


def extract_jsonld_article(html: str) -> dict[str, str | None]:
    """Pull title/author/pub_date from the first Article-like JSON-LD block.

    Returns a dict with keys "title", "author", "pub_date", any of which
    may be None if not present.
    """
    result: dict[str, str | None] = {"title": None, "author": None, "pub_date": None}

    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        for node in _flatten_jsonld(data):
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if not any(t in ("NewsArticle", "Article") for t in types):
                continue

            result["title"] = node.get("headline")
            result["pub_date"] = node.get("datePublished")
            result["author"] = _author_name(node.get("author"))
            return result

    return _extract_meta_fallback(soup, result)


def _extract_meta_fallback(
    soup: BeautifulSoup, result: dict[str, str | None]
) -> dict[str, str | None]:
    result["title"] = result["title"] or _meta_content(
        soup, [("property", "og:title"), ("name", "title")]
    )
    result["author"] = result["author"] or _meta_content(soup, [("name", "author")])
    result["pub_date"] = result["pub_date"] or _meta_content(
        soup,
        [
            ("property", "article:published_time"),
            ("name", "pubdate"),
            ("name", "publish-date"),
        ],
    )
    return result


def _meta_content(soup: BeautifulSoup, attr_pairs: list[tuple[str, str]]) -> str | None:
    for attr, value in attr_pairs:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            return str(tag["content"])
    return None


def _flatten_jsonld(data: Any) -> Iterator[Any]:
    """Yield candidate nodes from a JSON-LD payload (object, list, or @graph)."""
    if isinstance(data, list):
        for item in data:
            yield from _flatten_jsonld(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten_jsonld(data["@graph"])
        yield data


def _author_name(author: Any) -> str | None:
    if author is None:
        return None
    if isinstance(author, str):
        return author
    if isinstance(author, dict):
        return author.get("name")
    if isinstance(author, list) and author:
        return _author_name(author[0])
    return None
