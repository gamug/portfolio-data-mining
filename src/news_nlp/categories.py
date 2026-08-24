"""The fixed 10-category taxonomy (9 substantive + "other") used by the
pipeline's zero-shot category-classification stage.

Kept separate from pipeline.py so it's importable without pulling in torch
(setup.py, docs generation, or future non-GPU code can use it directly).

This is the canonical machine-readable source for the taxonomy -- keep
docs/category-taxonomy.md in sync with it if the labels ever change. See that
doc for the sourcing behind each category (RavenPack, SASB, IPTC Media
Topics, Refinitiv/TRNA).
"""

# (slug, display_name, hypothesis_topic_phrase)
#
# `slug` is stored as the DB `label` value (machine-stable, matches how
# article_sentiment.label stores "positive"/"negative"/"neutral" slugs, not
# display strings) and becomes one article_category score column each.
#
# `hypothesis_topic_phrase` fills the NLI hypothesis template
# "This example is about {phrase}." used by pipeline.run_category_stage.
CATEGORY_LABELS = [
    (
        "earnings_performance",
        "Earnings & Financial Performance",
        "earnings and financial performance",
    ),
    ("mergers_acquisitions", "Mergers & Acquisitions", "mergers and acquisitions"),
    (
        "leadership_governance",
        "Corporate Leadership & Governance",
        "corporate leadership and governance",
    ),
    ("legal_regulatory", "Legal & Regulatory", "legal and regulatory matters"),
    ("product_innovation", "Product & Innovation", "products and innovation"),
    (
        "capital_shareholder_returns",
        "Capital Actions & Shareholder Returns",
        "capital actions and shareholder returns",
    ),
    ("labor_human_capital", "Labor & Human Capital", "labor and human capital"),
    ("market_analyst_sentiment", "Market & Analyst Sentiment", "market and analyst sentiment"),
    (
        "partnerships_business_dev",
        "Partnerships & Business Development",
        "partnerships and business development",
    ),
]

# Catch-all for anything that doesn't clear CATEGORY_CONFIDENCE_THRESHOLD
# against any of the 9 labels above. Not an NLI candidate itself -- there's
# no hypothesis for "other", it's the below-threshold fallback.
OTHER_LABEL = "other"

CATEGORY_SLUGS = tuple(slug for slug, _, _ in CATEGORY_LABELS)
