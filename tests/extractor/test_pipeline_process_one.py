import httpx

from extractor.pipeline import process_one
from extractor.scheduler import DomainScheduler

ROW = {
    "id": 1,
    "url": "https://cnbc.com/a",
    "domain": "cnbc.com",
    "company": "3M",
    "ticker": "MMM",
    "title": "fallback title",
}

OK_HTML = "<html><body><article><p>" + " ".join(["word"] * 100) + "</p></article></body></html>"


async def test_process_one_returns_ok_article_for_successful_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=OK_HTML)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        article = await process_one(
            client,
            DomainScheduler(),
            ROW,
            gics_sector="Industrials",
            gics_sub_industry="Industrial Conglomerates",
            fetched_at="2026-08-10T00:00:00",
        )

    assert article["fetch_status"] == "ok"
    assert article["id"] == 1


async def test_process_one_marks_failed_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        article = await process_one(
            client,
            DomainScheduler(),
            ROW,
            gics_sector=None,
            gics_sub_industry=None,
            fetched_at="2026-08-10T00:00:00",
        )

    assert article["fetch_status"] == "failed"
    assert article["http_status_code"] == 599
