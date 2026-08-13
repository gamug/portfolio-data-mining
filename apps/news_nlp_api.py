"""
FastAPI app: trigger/monitor the news-nlp pipeline (FinBERT sentiment +
SEC-BERT NER) and query/correct its results.

Source: news-nlp/app.py + news-nlp/main.py, merged. See docs/modules/news-nlp.md.

Run:
    .venv\\Scripts\\python.exe apps\\news_nlp_api.py
    -> http://127.0.0.1:8003/docs
"""
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from news_nlp import db, pipeline, corrections

app = FastAPI(title="news-nlp")


class RunStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.status = "idle"
            self.stage = None
            self.processed = 0
            self.total = 0
            self.started_at = None
            self.finished_at = None
            self.error = None

    def to_dict(self):
        with self._lock:
            return {
                "status": self.status,
                "stage": self.stage,
                "processed": self.processed,
                "total": self.total,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
            }

    def start(self) -> bool:
        with self._lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.stage = None
            self.processed = 0
            self.total = 0
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.finished_at = None
            self.error = None
            return True

    def update_progress(self, stage, processed, total):
        with self._lock:
            self.stage = stage
            self.processed = processed
            self.total = total

    def finish(self, error=None):
        with self._lock:
            self.status = "error" if error else "done"
            self.error = error
            self.finished_at = datetime.now(timezone.utc).isoformat()


pipeline_status = RunStatus()


def get_db():
    conn = db.connect(db.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


class PipelineRunRequest(BaseModel):
    limit: int | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/pipeline/run", status_code=202)
def start_pipeline(req: PipelineRunRequest):
    if not pipeline_status.start():
        raise HTTPException(status_code=409, detail=pipeline_status.to_dict())

    def worker():
        try:
            pipeline.run_pipeline(limit=req.limit, on_progress=pipeline_status.update_progress)
            pipeline_status.finish()
        except Exception as exc:
            pipeline_status.finish(error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return pipeline_status.to_dict()


@app.get("/pipeline/status")
def get_pipeline_status():
    return pipeline_status.to_dict()


@app.get("/articles")
def get_articles(
    company: str | None = None,
    ticker: str | None = None,
    sentiment: Literal["positive", "negative", "neutral"] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    return db.list_articles(
        conn, company=company, ticker=ticker, sentiment=sentiment,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )


@app.get("/articles/{article_id}")
def get_article(article_id: int, conn=Depends(get_db)):
    detail = db.get_article_detail(conn, article_id)
    if detail is None or detail["sentiment"] is None:
        raise HTTPException(status_code=404, detail="Article not found or not yet processed")
    return detail


@app.get("/stats/sentiment")
def get_sentiment_stats(
    company: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: Literal["company", "year", "month"] | None = None,
    conn=Depends(get_db),
):
    return db.sentiment_stats(conn, company=company, date_from=date_from, date_to=date_to, group_by=group_by)


@app.get("/stats/entities")
def get_entity_stats(
    company: str | None = None,
    entity_type: Literal["ORG", "PER", "LOC"] | None = None,
    top: int = Query(20, ge=1, le=200),
    conn=Depends(get_db),
):
    return db.entity_stats(conn, company=company, entity_type=entity_type, top=top)


@app.get("/sectors/summary")
def get_sector_summaries(
    sector: str | None = None,
    sub_industry: str | None = None,
    week_start: str | None = None,
    conn=Depends(get_db),
):
    return db.list_sector_summaries(conn, sector=sector, sub_industry=sub_industry, week_start=week_start)


class SentimentUpdateRequest(BaseModel):
    label: Literal["positive", "negative", "neutral"] | None = None
    score: float | None = Field(None, ge=0, le=1)
    positive: float | None = Field(None, ge=0, le=1)
    negative: float | None = Field(None, ge=0, le=1)
    neutral: float | None = Field(None, ge=0, le=1)


class EntityUpdateRequest(BaseModel):
    entity_type: Literal["ORG", "PER", "LOC"] | None = None
    text: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    score: float | None = Field(None, ge=0, le=1)


@app.patch("/articles/{article_id}/sentiment")
def patch_sentiment(article_id: int, req: SentimentUpdateRequest, conn=Depends(get_db)):
    fields = req.model_dump(exclude_unset=True)
    updated = corrections.update_sentiment(conn, article_id, **fields)
    conn.commit()
    if updated is None:
        raise HTTPException(status_code=404, detail="Sentiment not found for article")
    return updated


@app.delete("/articles/{article_id}/sentiment", status_code=204)
def remove_sentiment(article_id: int, conn=Depends(get_db)):
    deleted = corrections.delete_sentiment(conn, article_id)
    conn.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Sentiment not found for article")


@app.patch("/entities/{entity_id}")
def patch_entity(entity_id: int, req: EntityUpdateRequest, conn=Depends(get_db)):
    fields = req.model_dump(exclude_unset=True)
    updated = corrections.update_entity(conn, entity_id, **fields)
    conn.commit()
    if updated is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return updated


@app.delete("/entities/{entity_id}", status_code=204)
def remove_entity(entity_id: int, conn=Depends(get_db)):
    deleted = corrections.delete_entity(conn, entity_id)
    conn.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")


@app.delete("/articles/{article_id}/entities")
def remove_article_entities(article_id: int, conn=Depends(get_db)):
    count = corrections.delete_entities_for_article(conn, article_id)
    conn.commit()
    return {"deleted": count}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8003, reload=False)
