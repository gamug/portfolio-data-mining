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

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO)

# (url prefix, module:attr, import path, human label)
_SERVICES = [
    ("/collector", "news_collector_api", "News Collector (URL discovery)"),
    ("/crawler", "news_crawler_api", "News Crawler / extractor (full-text extraction)"),
    ("/nlp", "news_nlp_api", "News NLP (sentiment + NER)"),
    ("/pricing", "pricing_api", "Pricing (Finnhub OHLCV/news/market data)"),
    ("/edgar", "sec_edgar_api", "SEC EDGAR (filings/financials)"),
]

_mounted: list[tuple[str, str]] = []
_mounted_apps: list[FastAPI] = []
_skipped: list[tuple[str, str]] = []

for prefix, module_name, label in _SERVICES:
    try:
        module = __import__(f"apps.{module_name}", fromlist=["app"])
        _mounted.append((prefix, label))
        _mounted_apps.append(module.app)
        log.info("Will mount %s at %s", label, prefix)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any one
        # service's missing env var / missing dependency shouldn't take
        # down the other four.
        _skipped.append((prefix, f"{label} -- {exc.__class__.__name__}: {exc}"))
        log.warning("Skipped %s (%s): %s", label, prefix, exc)


@asynccontextmanager
async def gateway_lifespan(app: FastAPI):
    """Run every mounted sub-app's own `lifespan`.

    `app.mount()` only wires up HTTP routing -- it does NOT forward the ASGI
    lifespan protocol to the mounted sub-app. Only the outermost app that
    the server (uvicorn) actually talks to gets startup/shutdown events; a
    sub-app's own `lifespan=` context manager silently never runs. This bit
    `news_collector_api.py` specifically: its lifespan builds the shared
    `httpx.AsyncClient` into `app.state.client`, and every /collector/discover/*
    route depends on it, so under the gateway (unlike standalone, on its own
    port) those calls 500'd with `AttributeError: 'State' object has no
    attribute 'client'` -- health/openapi endpoints looked fine since they
    don't touch that dependency.

    Fix: explicitly enter each mounted sub-app's `router.lifespan_context`
    here via an AsyncExitStack, so their real startup/shutdown hooks run for
    the gateway's process lifetime. Cheap/no-op for the sub-apps that don't
    define a custom lifespan (Starlette falls back to a default no-op one),
    so this is safe for all five and needs no per-service special-casing.
    """
    async with AsyncExitStack() as stack:
        for sub_app in _mounted_apps:
            await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
        yield


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
    lifespan=gateway_lifespan,
)

for prefix, sub_app in zip((p for p, _ in _mounted), _mounted_apps):
    app.mount(prefix, sub_app)


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
