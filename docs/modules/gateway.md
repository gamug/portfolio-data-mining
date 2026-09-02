# gateway — all four services, one process

**Source:** new (not derived from any of the four projects) — `apps/gateway.py`

Mounts all four service apps under one running process/port, purely for browsing/demo
convenience: instead of starting four `uvicorn` processes on four ports, start one.

**What this is not:** a single merged OpenAPI schema. `Starlette`'s `app.mount()` keeps
each sub-app's `/docs` and `openapi.json` fully separate — this process serves all four at
once, path-prefixed, but you still browse four distinct Swagger UIs (linked from the
landing page at `/`), not one flattened page. Getting a genuinely single combined `/docs`
would mean rewriting each service's routes as an `APIRouter` and `include_router()`-ing
all four into one `FastAPI()` instance — a real refactor of all four apps, not a 5th file
on top of them. Not done here.

Each sub-app is imported defensively — if one fails (missing `FINNHUB_API_KEY`, etc.) the
gateway logs it and mounts everything else rather than refusing to start. `GET /` shows
what mounted and what didn't; `GET /health` gives the same as JSON.

| Prefix | Service |
|---|---|
| `/collector` | news_collector |
| `/crawler` | extractor (news_crawler) |
| `/pricing` | pricing |
| `/edgar` | sec_edgar |

## Running

```bash
.venv\Scripts\python.exe apps\gateway.py
# -> http://127.0.0.1:8000/  (landing page)
# -> http://127.0.0.1:8000/pricing/docs, /edgar/docs, /collector/docs, /crawler/docs
```

Production deploys, or anywhere you want independent scaling/failure isolation, should
still run the four `apps/*_api.py` separately instead of this.
