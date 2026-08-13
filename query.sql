WITH news AS (
	SELECT
		id, ticker, company, gics_sector,
		gics_sub_industry, body_text, title
	FROM articles
	WHERE
		http_status_code=200
),
entities AS (
	SELECT
		article_id,
		GROUP_CONCAT(text, ', ') AS entities
	FROM article_entities
	WHERE
		score>0.8 AND
		text NOT GLOB '[0-9]'
	GROUP BY article_id
),
sentiment AS (
	SELECT
		article_id, label,
		score AS confidence
	FROM article_sentiment
),
source_text AS (
	SELECT
		COALESCE(n.id, s.article_id, e.article_id) AS article_id,
		n.ticker, n.company, n.gics_sector, n.gics_sub_industry,
		n.body_text, n.title, s.label, s.confidence, e.entities
	FROM news n
	INNER JOIN sentiment s
		ON n.id=s.article_id
	INNER JOIN entities e
		ON n.id = e.article_id
)
SELECT
	article_id,
	"METADATA:\nTicker-"||s.ticker||"\nCompany-"||s.company||
	"\n\nNLP FEATURES:\nSentiment-"||s.label||" Confidence-"||CAST(s.confidence AS VARCHAR)||
	"\nEntities-"||s.entities||"\n\nTEXT BODY:\nTitle-"||s.title||
	"\nBody-"||s.body_text  AS company_text
FROM source_text s