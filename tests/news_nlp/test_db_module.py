from pathlib import Path

from news_nlp import db


def test_db_path_points_to_project_root_data_dir():
    project_root = Path(__file__).resolve().parents[1]
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
