from extractor.parse import (
    detect_language,
    extract_body_text,
    is_probably_paywalled,
    is_thin_content,
    word_count,
)

ARTICLE_HTML = """
<html><head><title>3M beats estimates</title></head>
<body>
<nav><a href="/">Home</a><a href="/markets">Markets</a><a href="/watchlist">Watchlist</a></nav>
<header>Subscribe to our newsletter for more market news.</header>
<article>
<h1>3M beats estimates</h1>
<p>3M Company reported fourth-quarter earnings on Tuesday that topped Wall Street
expectations, sending shares higher in early trading. The industrial conglomerate
posted adjusted earnings per share of 2.28 dollars, ahead of the 2.07 dollars
analysts had forecast, according to data compiled by Refinitiv.</p>
<p>Revenue for the quarter came in at 8.1 billion dollars, roughly in line with
estimates. Chief Executive Officer Mike Roman said the company continued to see
strong demand across its safety and industrial segments despite ongoing supply
chain pressures affecting the broader manufacturing sector this year.</p>
<p>The company also reaffirmed its full-year guidance, projecting adjusted
earnings per share between 8.50 and 9.00 dollars, citing resilient demand and
cost discipline initiatives that were implemented earlier in the fiscal year.</p>
</article>
<footer>Copyright 2023. All rights reserved. Terms of use. Privacy policy.</footer>
</body></html>
"""


def test_extract_body_text_returns_article_paragraphs_without_nav_or_footer() -> None:
    text = extract_body_text(ARTICLE_HTML)

    assert "3M Company reported fourth-quarter earnings" in text
    assert "reaffirmed its full-year guidance" in text
    assert "Home" not in text
    assert "Copyright 2023" not in text


def test_word_count_counts_words_in_text() -> None:
    assert word_count("one two three four") == 4
    assert word_count("") == 0


def test_is_thin_content_true_below_threshold_false_above() -> None:
    short_text = "Breaking news update."
    long_text = " ".join(["word"] * 100)

    assert is_thin_content(short_text) is True
    assert is_thin_content(long_text) is False


def test_is_probably_paywalled_flags_known_domain_with_marker_text() -> None:
    html = "<p>Subscribe to continue reading this article.</p>"

    assert is_probably_paywalled(html, "seekingalpha.com") is True


def test_is_probably_paywalled_false_for_normal_domain_and_content() -> None:
    assert is_probably_paywalled(ARTICLE_HTML, "cnbc.com") is False


def test_detect_language_identifies_english() -> None:
    text = (
        "The company reported strong quarterly earnings today, beating "
        "analyst expectations across every major business segment this year."
    )

    assert detect_language(text) == "en"


def test_detect_language_returns_none_for_empty_text() -> None:
    assert detect_language("") is None
