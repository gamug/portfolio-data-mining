# News article category taxonomy

## Purpose

`news_nlp`'s category stage classifies every article into one of 10 fixed
categories representing a *dimension of company performance* (earnings,
M&A, leadership, etc.), so that sentiment produced by the sentiment stage
can be attributed to the right dimension rather than treated as one
undifferentiated "sentiment about the company" number. This is intended as
a reusable controlled vocabulary for the not-yet-built knowledge-graph
module downstream of `news_nlp` (see `README.md`'s pipeline diagram), not
just an internal detail of the category stage — hence living at the top
level of `docs/`, not nested under `docs/modules/`.

Zero-shot NLI classification (the technique used here — see
`src/news_nlp/pipeline.py`'s `run_category_stage`) needs a fixed
`candidate_labels` list; it doesn't invent categories, it only picks the
best-fitting one(s) from a list supplied at inference time. So this
taxonomy had to be designed up front, from real published sources, rather
than left for the model to discover.

## Sources

The 9 substantive categories below were triangulated across four
independent, real-world financial-news/company-event taxonomies, so no
single source's idiosyncrasies dominate the result:

1. **RavenPack News Analytics taxonomy** —
   https://www.ravenpack.com/technology/classification — a financial-news
   analytics vendor's proprietary event taxonomy. Categories drawn from:
   acquisitions & mergers, analyst ratings, credit, credit ratings,
   dividends, earnings, equity actions, insider trading, labor issues,
   legal, marketing, partnerships, price targets, products/services,
   regulatory, revenues, sanctions, stock picks, stock price.
2. **SASB Materiality Map** — https://sasb.ifrs.org — the Sustainability
   Accounting Standards Board's 5 primary company-performance dimensions
   (Environment, Social Capital, Human Capital, Business Model &
   Innovation, Leadership & Governance) and their 26 underlying General
   Issue Categories. Used for the categories with no direct RavenPack
   analogue: Leadership & Governance, Business Model & Innovation, Human
   Capital.
3. **IPTC Media Topics** —
   https://www.iptc.org/std/NewsCodes/treeview/mediatopic/mediatopic-en-GB.html —
   the news industry's standard story-classification taxonomy, maintained
   by the International Press Telecommunications Council. Its "Economy,
   Business and Finance" branch covers corporate earnings, stock buyback,
   corporate dividends, bankruptcy, business restructuring,
   layoffs/downsizing, mergers/acquisitions — used to corroborate category
   boundaries against how the news industry itself already classifies this
   content.
4. **Refinitiv/Thomson Reuters News Analytics (TRNA) topic codes** — a
   financial-data vendor's real-time news topic codes, e.g. `MRG` (Mergers
   & Acquisitions), `RES`/`RESF` (Results/Results Forecast), `DIV`
   (Dividends), `RCH` (Research/analyst) — used as a cross-check that the
   category boundaries match how a major financial-news feed already tags
   stories.

## The taxonomy

`src/news_nlp/categories.py`'s `CATEGORY_LABELS` is the canonical
machine-readable source for this table — keep this doc in sync with it if
the labels ever change.

| slug | display name | scope | sources |
|---|---|---|---|
| `earnings_performance` | Earnings & Financial Performance | Quarterly/annual results, guidance, revenue/profit figures | RavenPack (earnings, revenues), Refinitiv (RES/RESF), IPTC (corporate earnings) |
| `mergers_acquisitions` | Mergers & Acquisitions | Deals, takeovers, divestitures | RavenPack (acquisitions & mergers), Refinitiv (MRG), IPTC (mergers/acquisitions) |
| `leadership_governance` | Corporate Leadership & Governance | Executive appointments/departures, board actions, insider trading | SASB (Leadership & Governance), RavenPack (insider trading) |
| `legal_regulatory` | Legal & Regulatory | Litigation, regulatory action, sanctions, bankruptcy | RavenPack (legal, regulatory, sanctions), IPTC (bankruptcy, business restructuring) |
| `product_innovation` | Product & Innovation | Product launches, R&D, technology | SASB (Business Model & Innovation), RavenPack (products/services) |
| `capital_shareholder_returns` | Capital Actions & Shareholder Returns | Dividends, buybacks, debt/credit actions | RavenPack (dividends, equity actions, credit/credit ratings), IPTC (stock buyback, corporate dividends) |
| `labor_human_capital` | Labor & Human Capital | Layoffs, hiring, workforce/labor relations | SASB (Human Capital), RavenPack (labor issues), IPTC (layoffs and downsizing) |
| `market_analyst_sentiment` | Market & Analyst Sentiment | Analyst ratings/price targets, stock price moves | RavenPack (analyst ratings, price targets, stock picks, stock price), Refinitiv (RCH) |
| `partnerships_business_dev` | Partnerships & Business Development | Strategic alliances, joint ventures, distribution deals | RavenPack (partnerships, marketing) |
| `other` | Other | Catch-all — anything below the confidence threshold against every category above | n/a — the below-threshold fallback |

## The "other" fallback and confidence threshold

`other` is not itself an NLI candidate label — there is no hypothesis text
for it. The classifier runs the article against the 9 substantive labels,
takes the highest-scoring one via softmax over NLI entailment logits, and
only assigns that label if its score clears
`pipeline.CATEGORY_CONFIDENCE_THRESHOLD` (currently **0.4**). Below that,
the article is labeled `other`.

Rationale for 0.4: with 9 mutually-exclusive labels, a uniform/no-signal
distribution puts every label at ~0.11. Requiring the winner to clear 0.4
(~3.6x that baseline) is a middle ground — strict enough to route genuinely
ambiguous or generic articles (market-wrap roundups, listicles, tangential
mentions) to `other`, without being so strict that legitimately on-topic
articles with modest lexical overlap to their hypothesis get
miscategorized.

This threshold is a reasoned starting point, not a validated one. The
`article_category` table stores the full 9-way score distribution for
every article specifically so it can be retuned later: query for articles
labeled `other` whose winning-slug score was just under 0.4 (near-misses)
versus those with a flat distribution (genuinely ambiguous), and adjust the
constant in `src/news_nlp/pipeline.py` accordingly.

## Classification input

The classifier runs on the article's title plus the lead chunk of its body
(not the full article, and not the opt-in `article_summary` — see
`docs/modules/news-nlp.md` for why). News articles are inverted-pyramid, so
the opening sentences almost always establish the dominant topic.
