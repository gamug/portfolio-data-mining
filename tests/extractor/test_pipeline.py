from extractor.pipeline import classify_and_build_article

ROW = {
    "id": 1,
    "url": "https://cnbc.com/a",
    "domain": "cnbc.com",
    "company": "3M",
    "ticker": "MMM",
    "title": "3M (MMM) Q4 Earnings Lag Estimates",
}

FULL_ARTICLE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "NewsArticle", "headline": "3M beats estimates",
 "datePublished": "2023-01-24T17:31:05+00:00", "author": "Jane Reporter"}
</script>
</head><body><article>
<p>3M Company reported fourth-quarter earnings on Tuesday that topped Wall
Street expectations, sending shares higher in early trading on strong demand.</p>
<p>Revenue for the quarter came in at 8.1 billion dollars, roughly in line
with estimates, as the industrial conglomerate navigated ongoing supply
chain pressures across its safety and industrial product segments this year.</p>
<p>The company also reaffirmed full-year guidance between 8.50 and 9.00
dollars per share, citing resilient demand and cost discipline initiatives
implemented earlier in the fiscal year across its global operations.</p>
</article></body></html>
"""

THIN_HTML = "<html><body><article><p>Breaking news update.</p></article></body></html>"

PAYWALL_HTML = "<html><body><p>Subscribe to continue reading this article.</p></body></html>"


def test_classify_ok_article_uses_jsonld_fields_and_body_text() -> None:
    article = classify_and_build_article(
        ROW,
        FULL_ARTICLE_HTML,
        http_status=200,
        gics_sector="Industrials",
        gics_sub_industry="Industrial Conglomerates",
        fetched_at="2026-08-10T12:00:00",
    )

    assert article["fetch_status"] == "ok"
    assert article["title"] == "3M beats estimates"
    assert article["author"] == "Jane Reporter"
    assert article["ticker"] == "MMM"
    assert article["gics_sector"] == "Industrials"
    assert article["gics_sub_industry"] == "Industrial Conglomerates"
    assert "reaffirmed full-year guidance" in article["body_text"]
    assert article["word_count"] > 0
    assert article["extraction_method"] == "jsonld+trafilatura"


def test_classify_falls_back_to_discovered_title_when_no_jsonld_or_meta_title() -> None:
    html = "<html><body><article><p>" + " ".join(["word"] * 100) + "</p></article></body></html>"

    article = classify_and_build_article(
        ROW,
        html,
        http_status=200,
        gics_sector=None,
        gics_sub_industry=None,
        fetched_at="2026-08-10T12:00:00",
    )

    assert article["title"] == "3M (MMM) Q4 Earnings Lag Estimates"


def test_classify_thin_content() -> None:
    article = classify_and_build_article(
        ROW,
        THIN_HTML,
        http_status=200,
        gics_sector=None,
        gics_sub_industry=None,
        fetched_at="2026-08-10T12:00:00",
    )

    assert article["fetch_status"] == "thin_content"


def test_classify_paywalled_domain_with_marker_text() -> None:
    row = {**ROW, "domain": "seekingalpha.com"}

    article = classify_and_build_article(
        row,
        PAYWALL_HTML,
        http_status=200,
        gics_sector=None,
        gics_sub_industry=None,
        fetched_at="2026-08-10T12:00:00",
    )

    assert article["fetch_status"] == "paywalled"


def test_classify_http_error_status_is_failed_regardless_of_content() -> None:
    article = classify_and_build_article(
        ROW,
        FULL_ARTICLE_HTML,
        http_status=404,
        gics_sector=None,
        gics_sub_industry=None,
        fetched_at="2026-08-10T12:00:00",
    )

    assert article["fetch_status"] == "failed"
    assert article["http_status_code"] == 404
