import pytest

from extractor.parse import (
    detect_language,
    extract_body_text,
    is_probably_paywalled,
    is_thin_content,
    is_wall_page,
    wall_kind,
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


# --- wall detector (T-060) ---

_PROSE = "Shares rose as analysts weighed the results. " * 30  # well over 200 words


def test_wall_phrase_at_the_start_of_a_long_body_is_a_wall() -> None:
    assert is_wall_page("Just a moment... " + _PROSE)
    assert is_wall_page("Security Verification\nVerify you are human. " + _PROSE)


def test_wall_phrase_deep_in_a_long_article_is_not_a_wall() -> None:
    prose = _PROSE + "Wait just a moment, the CEO said, before the unusual traffic of buyers."
    assert not is_wall_page(prose)  # the CNBC false positives: normal prose, not a challenge page


def test_wall_phrase_deep_in_a_short_article_is_not_a_wall() -> None:
    body = ("Headlines of the day. " * 30) + "Access denied to the plant, a spokesman said."
    assert not is_wall_page(body)  # 90+ words, so it is not thin, and the phrase is not at the head


def test_wall_kind_separates_a_challenge_from_a_login_prompt() -> None:
    assert wall_kind("Just a moment... " + _PROSE) == "failed"  # transient: retried
    assert (
        wall_kind("Subscribe to continue reading. " + _PROSE) == "paywalled"
    )  # gated: not retried
    assert wall_kind(_PROSE) is None


def test_an_ordinary_article_is_not_a_wall() -> None:
    assert not is_wall_page(_PROSE)
    assert not is_wall_page("")


# --- real excerpts from the 2026-10 urls.db (T-060): what is and is not a wall ---

_REAL_WALL = (  # ft.com, 578 `failed` + 198 quarantined rows look like this
    "Security Verification\nFor help please visit help.ft.com. We apologise for any inconvenience.\n"
    "The following information can help our support team to resolve this issue.\n- Reason\n- Challenge\n"
    "- Request ID\n- a2b869f5af68f78e\n- Status Code\n- 403"
)
_REAL_PROSE = {
    "momentary blip (cnbc, head)": (
        "The markets are losing steam today as the one-month anniversary of the summer rally comes "
        "and goes. Is this just a momentary blip on the radar or is it the start of the 2009 summer sell-off?"
    ),
    "unusual traffic (cnbc, head)": (
        "Twitter shares fell almost 7 percent on Monday after the company said it was investigating "
        "unusual traffic that might be from state-sponsored hackers and a security firm said hackers "
        "used the platform to try to steal user data."
    ),
    "just a moment (cnbc, deep)": (
        "ANDY JASSY: A lot of them. Thanks for having me.\nSORKIN: It's very nice to have you here and "
        "I want to talk about your letter in just a moment because it is a major piece of the news."
    ),
    "access denied (cnbc, deep)": (
        'But before I could, my screen said "Access denied" alongside an "error code 1020" message. '
        "Care Access denied the allegations of quality violations."
    ),
    "captcha (cnbc)": (
        'some retail websites use what\'s known as a "CAPTCHA" typically a distorted string of letters'
    ),
}


def test_a_real_ft_challenge_page_is_a_wall() -> None:
    assert wall_kind(_REAL_WALL) == "failed"


@pytest.mark.parametrize("text", _REAL_PROSE.values(), ids=_REAL_PROSE.keys())
def test_real_cnbc_prose_with_a_wall_phrase_is_not_a_wall(text: str) -> None:
    # as in the real pages, the "deep" phrases sit well past the first 300 characters
    assert wall_kind(_PROSE + text) is None


@pytest.mark.parametrize("key", [k for k in _REAL_PROSE if "deep" not in k], ids=lambda k: k)
def test_real_cnbc_prose_with_a_phrase_in_its_head_is_not_a_wall(key: str) -> None:
    assert wall_kind(_REAL_PROSE[key]) is None  # the phrase is inside the first 300 characters
