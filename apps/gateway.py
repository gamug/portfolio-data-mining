"""
Gateway: mounts all five service apps under one running process/port, for
browsing/demo convenience. See docs/modules/gateway.md.

IMPORTANT — this is NOT a single merged OpenAPI schema. Starlette's
`app.mount()` keeps each sub-app's OpenAPI/`/docs` fully separate; this
process just serves all five at once, path-prefixed, instead of you having
to start five separate `uvicorn` processes on five separate ports. Each
service's own Swagger UI still lives at its own `/docs` (e.g.
`/pricing/docs`), just reachable through one port now.

Getting a genuinely single flattened Swagger page would require rewriting
each service's routes as an `APIRouter` and `include_router()`-ing them all
into one FastAPI instance -- a real refactor of all five apps, not a
5th file on top of them. Not done here; flag it if you want that instead.

Each sub-app is imported defensively: if one fails to import (e.g.
FINNHUB_API_KEY missing from .env, or torch not installed for news_nlp),
the gateway logs it and mounts everything else rather than refusing to
start.

Run:
    .venv\\Scripts\\python.exe apps\\gateway.py
    -> http://127.0.0.1:8000/  (landing page listing what mounted)
"""

import logging
import sys
from pathlib import Path

# Repo root (not src/) -- needed so `apps.news_collector_api` etc. are
# importable below. Each sub-app bootstraps src/ onto sys.path itself the
# moment it's imported, so we don't need to do that here too.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Portfolio Data Mining — Gateway",
    description=(
        "Single-process entrypoint that mounts all five services. Each "
        "keeps its own /docs -- see the landing page at / for links. "
        "Production/independent-scaling deploys should still run "
        "apps/{news_collector,news_crawler,news_nlp,pricing,sec_edgar}_api.py "
        "separately instead of this."
    ),
    version="1.0.0",
)

# (url prefix, module:attr, import path, human label)
_SERVICES = [
    ("/collector", "news_collector_api", "News Collector (URL discovery)"),
    ("/crawler", "news_crawler_api", "News Crawler / extractor (full-text extraction)"),
    ("/nlp", "news_nlp_api", "News NLP (sentiment + NER)"),
    ("/pricing", "pricing_api", "Pricing (Finnhub OHLCV/news/market data)"),
    ("/edgar", "sec_edgar_api", "SEC EDGAR (filings/financials)"),
]

_mounted: list[tuple[str, str]] = []
_skipped: list[tuple[str, str]] = []

for prefix, module_name, label in _SERVICES:
    try:
        module = __import__(f"apps.{module_name}", fromlist=["app"])
        app.mount(prefix, module.app)
        _mounted.append((prefix, label))
        log.info("Mounted %s at %s", label, prefix)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any one
        # service's missing env var / missing dependency shouldn't take
        # down the other four.
        _skipped.append((prefix, f"{label} -- {exc.__class__.__name__}: {exc}"))
        log.warning("Skipped %s (%s): %s", label, prefix, exc)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing() -> str:
    """What mounted, what didn't, and links to each service's own /docs."""
    mounted_items = "".join(
        f'<li><a href="{prefix}/docs">{label}</a> — <code>{prefix}/docs</code></li>'
        for prefix, label in _mounted
    )
    skipped_items = "".join(f"<li>{label}</li>" for _, label in _skipped)
    skipped_block = (
        f"<h2>Not mounted</h2><ul>{skipped_items}</ul>"
        if _skipped
        else ""
    )
    return (
        "<html><body style='font-family: sans-serif; max-width: 640px; margin: 3rem auto;'>"
        "<h1>Portfolio Data Mining — Gateway</h1>"
        "<p>Each service keeps its own Swagger UI (this does not merge them into one schema).</p>"
        f"<h2>Mounted</h2><ul>{mounted_items}</ul>"
        f"{skipped_block}"
        "</body></html>"
    )


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {
        "mounted": [prefix for prefix, _ in _mounted],
        "skipped": [prefix for prefix, _ in _skipped],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
