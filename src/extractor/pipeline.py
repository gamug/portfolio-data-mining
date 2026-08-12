"""Orchestrates fetch -> parse -> classify -> (fallback) -> db write.

See CLAUDE.md for the hybrid strategy this implements: httpx + trafilatura
is the default path; Crawl4AI is only invoked as a fallback for URLs whose
default-path result comes back thin/empty.
"""
import httpx

from extractor.parse import (
    detect_language,
    extract_body_text,
    extract_jsonld_article,
    is_probably_paywalled,
    is_thin_content,
    word_count,
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sp500-news-extractor/0.1 "
        "(+https://github.com/; research/dataset-building use)"
    )
}


def classify_and_build_article(
    row: dict,
    html: str,
    http_status: int,
    gics_sector: str | None,
    gics_sub_industry: str | None,
    fetched_at: str,
    extraction_method: str = "jsonld+trafilatura",
) -> dict:
    """Pure function: turn a fetched page + its discovery row into an
    `articles` table row, including the fetch_status classification.
    """
    domain = row["domain"]
    jsonld = extract_jsonld_article(html)
    body_text = extract_body_text(html)

    title = jsonld["title"] or row.get("title")

    if http_status is not None and http_status >= 400:
        fetch_status = "failed"
    elif is_probably_paywalled(html, domain):
        fetch_status = "paywalled"
    elif is_thin_content(body_text):
        fetch_status = "thin_content"
    else:
        fetch_status = "ok"

    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "company": row["company"],
        "gics_sector": gics_sector,
        "gics_sub_industry": gics_sub_industry,
        "title": title,
        "author": jsonld["author"],
        "pub_date": jsonld["pub_date"],
        "body_text": body_text,
        "word_count": word_count(body_text),
        "language": detect_language(body_text),
        "source_domain": domain,
        "extraction_method": extraction_method,
        "fetch_status": fetch_status,
        "http_status_code": http_status,
        "fetched_at": fetched_at,
    }


async def fetch_html(client: httpx.AsyncClient, url: str) -> tuple[str, int]:
    """Thin I/O wrapper: GET a URL, return (html, status_code).

    Network/timeout errors are not caught here -- the caller (process_one)
    decides how to record a failed fetch.
    """
    response = await client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True)
    return response.text, response.status_code


async def process_one(
    client: httpx.AsyncClient,
    scheduler,
    row: dict,
    gics_sector: str | None,
    gics_sub_industry: str | None,
    fetched_at: str,
) -> dict:
    """Fetch one discovered URL and return its classified article dict.

    On a network-level failure (timeout, connection error) the URL is
    recorded as fetch_status='failed' rather than raising, so a batch run
    over many URLs doesn't abort on the first bad one.
    """
    async with scheduler.slot(row["domain"]):
        try:
            html, status = await fetch_html(client, row["url"])
        except httpx.HTTPError:
            return classify_and_build_article(
                row, "", http_status=599,
                gics_sector=gics_sector, gics_sub_industry=gics_sub_industry,
                fetched_at=fetched_at,
            )

    return classify_and_build_article(
        row, html, http_status=status,
        gics_sector=gics_sector, gics_sub_industry=gics_sub_industry,
        fetched_at=fetched_at,
    )

