"""URL normalization and validation utilities."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from news_collector.models import _TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """
    Return a canonical, normalized URL suitable for deduplication.

    Steps:
    1. Parse the URL
    2. Lowercase scheme and host
    3. Remove tracking query parameters
    4. Sort remaining query parameters for stable representation
    5. Remove URL fragment
    6. Strip trailing slash from path (except bare '/')

    Idempotent: normalize_url(normalize_url(u)) == normalize_url(u)
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url.strip()

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"

    # Filter tracking params, sort remaining for stability
    qs = parse_qs(parsed.query, keep_blank_values=False)
    clean_qs = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    query = urlencode(sorted(clean_qs.items()), doseq=True)

    normalized = urlunparse((scheme, netloc, path, "", query, ""))
    return normalized


def extract_domain(url: str) -> str:
    """Return the registered domain (host without www. prefix)."""
    netloc = urlparse(url.strip()).netloc.lower()
    # Strip port
    netloc = netloc.split(":")[0]
    # Strip leading www.
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def is_valid_http_url(url: str) -> bool:
    """Return True if url is a syntactically valid http/https URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False
