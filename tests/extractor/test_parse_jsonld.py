from extractor.parse import extract_jsonld_article


def test_extracts_headline_date_and_string_author_from_newsarticle_jsonld():
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "NewsArticle",
      "headline": "3M (MMM) Q4 Earnings Lag Estimates",
      "datePublished": "2023-01-24T17:31:05+00:00",
      "author": "Zacks Equity Research"
    }
    </script>
    </head><body>ignored</body></html>
    """

    result = extract_jsonld_article(html)

    assert result["title"] == "3M (MMM) Q4 Earnings Lag Estimates"
    assert result["pub_date"] == "2023-01-24T17:31:05+00:00"
    assert result["author"] == "Zacks Equity Research"


def test_extracts_author_name_from_object_and_from_graph_wrapper():
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@graph": [
        {"@type": "WebPage", "name": "irrelevant"},
        {
          "@type": "NewsArticle",
          "headline": "CNBC Exclusive",
          "datePublished": "2024-03-01T10:00:00Z",
          "author": {"@type": "Person", "name": "Jane Reporter"}
        }
      ]
    }
    </script>
    </head><body>ignored</body></html>
    """

    result = extract_jsonld_article(html)

    assert result["title"] == "CNBC Exclusive"
    assert result["author"] == "Jane Reporter"


def test_falls_back_to_meta_tags_when_no_jsonld_present():
    html = """
    <html><head>
    <meta property="og:title" content="Apple beats estimates" />
    <meta name="author" content="Staff Writer" />
    <meta property="article:published_time" content="2024-05-02T08:00:00Z" />
    </head><body>ignored</body></html>
    """

    result = extract_jsonld_article(html)

    assert result["title"] == "Apple beats estimates"
    assert result["author"] == "Staff Writer"
    assert result["pub_date"] == "2024-05-02T08:00:00Z"


def test_malformed_jsonld_does_not_crash_and_falls_back_to_meta():
    html = """
    <html><head>
    <script type="application/ld+json">{ not valid json </script>
    <meta property="og:title" content="Fallback Title" />
    </head><body>ignored</body></html>
    """

    result = extract_jsonld_article(html)

    assert result["title"] == "Fallback Title"
