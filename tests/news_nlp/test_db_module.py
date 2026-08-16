from pathlib import Path

from news_nlp import db


def test_db_path_points_to_project_root_data_dir():
    # parents[0]=tests/news_nlp, [1]=tests, [2]=repo root -- one level
    # deeper than the original news-nlp repo's tests/test_db_module.py
    # (tests/ directly at repo root there) since this suite is namespaced
    # under tests/news_nlp/ alongside the other migrated modules' tests.
    project_root = Path(__file__).resolve().parents[2]
    assert db.DB_PATH == project_root / "data" / "urls.db"


def test_fetch_pending_articles_unpacks_as_two_tuple(conn):
    from conftest import seed_article
    seed_article(conn, id=1, body_text="Body text.")
    conn.commit()

    rows = db.fetch_pending_articles(conn, "article_sentiment")
    assert len(rows) == 1
    article_id, body_text = rows[0]
    assert article_id == 1
    assert body_text == "Body text."
