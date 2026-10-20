"""Reclaim of the never-fetched sitemap rejects (T-073): the selection, the deletion, the proof."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from sitemap_helpers import RANGE, ROOT, Clock, Site, leaf
from test_reclass_cache import APPLE, DOMAIN, PLAIN, TITLE, TITLED, connector

from news_collector import reclaim_sitemap as rs
from news_collector.sitemap_cache import SitemapCache

OTHER = "plain.test"
NINE = 9


def put(
    conn: sqlite3.Connection,
    n: int,
    *,
    url: str | None = None,
    cls: str = "reject",
    status: str = "skipped",
    source: str = "sitemap",
    domain: str = DOMAIN,
    ticker: str = "AAPL",
    pub: str | None = "2023-05-01",
    article: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO discovered_urls (id, url, domain, company, ticker, source, discovered_at, "
        "pub_date, status, match_class) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (n, url or f"https://{domain}/2023/05/01/p{n}.html", domain, "Apple Inc.", ticker, source,
         "2026-10-18", pub, status, cls),
    )  # fmt: skip
    if article:
        conn.execute("INSERT INTO articles (id, ticker) VALUES (?, ?)", (n, ticker))


@pytest.fixture
def filled(db: str) -> str:
    """A queue file with one row of each kind and a cache crawl for `cnbc.com` only."""
    conn = sqlite3.connect(db)
    put(conn, 1)  # qualifies
    put(conn, 2, source="rss")  # qualifies
    put(conn, 3, article=True)  # kept: it has an article
    put(conn, 4, cls="accept")  # kept: not a reject
    put(conn, 5, status="pending")  # kept: it may still be fetched
    put(conn, 6, source="ddg")  # kept: not a sitemap
    put(conn, 7, domain=OTHER)  # kept: no cache for this domain
    put(conn, 8)  # kept below: a hand-labeled id
    put(conn, NINE, cls="review", status="ok", article=True)
    conn.execute(
        "INSERT INTO sitemap_crawls (domain, started_at, finished_at, range_start, signature, "
        "complete, entries, failures) VALUES ('cnbc.com','2026-10-20T10:00:00',"
        "'2026-10-20T10:00:05','2022-01-01','x',1,1,0)"
    )
    conn.commit()
    conn.close()
    return db


def data_dir(tmp_path: Path, *ids: int) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "ticker_x_sample.csv").write_text("id,url\n" + "".join(f"{i},u\n" for i in ids))
    (folder / "ticker_y_manual.csv").write_text("id,label\n8,yes\n")
    (folder / "other.csv").write_text("id\n1\n")  # not a sample file: ignored
    return folder


def ids_left(db: str) -> list[int]:
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT id FROM discovered_urls ORDER BY id")]
    finally:
        conn.close()


def backup_of(db: str, tmp_path: Path) -> Path:
    out = tmp_path / "backup.db"
    shutil.copy(db, out)
    return out


def test_kept_ids_reads_the_sample_files_only(tmp_path: Path) -> None:
    assert rs.kept_ids(data_dir(tmp_path, 3, 8)) == {3, 8}


def test_only_the_never_fetched_rejects_of_a_filled_cache_qualify(
    filled: str, tmp_path: Path
) -> None:
    conn = sqlite3.connect(filled)
    rs._load_kept(conn, rs.kept_ids(data_dir(tmp_path)))
    where, params = rs.candidate_where(rs.cache_domains(conn))
    sql = f"SELECT id FROM discovered_urls WHERE {where} ORDER BY id"  # noqa: S608
    got = [r[0] for r in conn.execute(sql, params)]
    conn.close()
    assert got == [
        1,
        2,
    ]  # 8 is a labeled id; 3 has an article; 7 has no cache; 4, 5, 6 are not rejects


def test_reclaim_deletes_the_candidates_and_nothing_else(filled: str, tmp_path: Path) -> None:
    backup = backup_of(filled, tmp_path)
    done = rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert done.deleted == 2
    assert ids_left(filled) == [3, 4, 5, 6, 7, 8, NINE]
    assert (done.before.discovered, done.after.discovered) == (NINE, NINE - 2)
    assert done.after.articles == done.before.articles == 2
    assert done.after.orphans == 0
    assert done.after.kept_rows() == done.before.kept_rows()
    assert done.after.user_version == done.before.user_version
    assert (
        done.after.schema_hash == done.before.schema_hash
    )  # tables and indexes survive VACUUM INTO
    assert done.after.quick_check == "ok"
    assert ids_left(str(backup)) == list(range(1, NINE + 1))  # the backup is untouched
    assert not Path(filled + ".compact").exists()
    # the file keeps its journal mode (VACUUM INTO writes a rollback-journal file; the queue's is WAL)
    assert Path(filled).read_bytes()[18:20] == Path(str(backup)).read_bytes()[18:20] == b"\x02\x02"


def test_a_different_expected_count_deletes_nothing(filled: str, tmp_path: Path) -> None:
    backup = backup_of(filled, tmp_path)
    with pytest.raises(rs.ReclaimError, match="names 3 rows, 2 qualify"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=3, check_processes=False)
    assert ids_left(filled) == list(range(1, NINE + 1))


def test_a_domain_without_a_filled_cache_deletes_nothing(db: str, tmp_path: Path) -> None:
    conn = sqlite3.connect(db)
    put(conn, 1)
    conn.commit()
    conn.close()
    done = rs.reclaim(
        Path(db), backup_of(db, tmp_path), data_dir(tmp_path), expect=0, check_processes=False
    )
    assert done.deleted == 0
    assert ids_left(db) == [1]


def test_the_backup_must_exist_differ_from_the_file_and_match_it(
    filled: str, tmp_path: Path
) -> None:
    folder = data_dir(tmp_path)
    with pytest.raises(rs.ReclaimError, match="no backup"):
        rs.reclaim(Path(filled), tmp_path / "none.db", folder, expect=2, check_processes=False)
    with pytest.raises(rs.ReclaimError, match="the database itself"):
        rs.reclaim(Path(filled), Path(filled), folder, expect=2, check_processes=False)
    stale = backup_of(filled, tmp_path)
    conn = sqlite3.connect(filled)
    put(conn, 20)  # the live file moved on after the backup
    conn.commit()
    conn.close()
    with pytest.raises(rs.ReclaimError, match="differs from the database"):
        rs.reclaim(Path(filled), stale, folder, expect=2, check_processes=False)
    assert 20 in ids_left(filled)  # nothing deleted


def test_it_refuses_while_a_collector_runs_and_when_the_disk_is_short(
    filled: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup, folder = backup_of(filled, tmp_path), data_dir(tmp_path)
    monkeypatch.setattr(rs, "running_collectors", lambda: ["123 apps/gateway.py"])
    with pytest.raises(rs.ReclaimError, match="stop the gateway"):
        rs.reclaim(Path(filled), backup, folder, expect=2)
    monkeypatch.setattr(
        rs.shutil,
        "disk_usage",
        lambda _p: shutil._ntuple_diskusage(10, 10, 0),  # type: ignore[attr-defined]
    )
    with pytest.raises(rs.ReclaimError, match="not enough disk"):
        rs.reclaim(Path(filled), backup, folder, expect=2, check_processes=False)
    assert ids_left(filled) == list(range(1, NINE + 1))


def test_a_stale_compact_file_stops_it(filled: str, tmp_path: Path) -> None:
    Path(filled + ".compact").write_text("left over")
    with pytest.raises(rs.ReclaimError, match="exists"):
        rs.reclaim(
            Path(filled),
            backup_of(filled, tmp_path),
            data_dir(tmp_path),
            expect=2,
            check_processes=False,
        )


def test_a_failed_fingerprint_rolls_the_delete_back(filled: str, tmp_path: Path) -> None:
    backup = backup_of(filled, tmp_path)
    conn = sqlite3.connect(filled)
    conn.execute(  # a bad actor: deleting a candidate also deletes an accept row
        "CREATE TRIGGER evil AFTER DELETE ON discovered_urls BEGIN "
        "DELETE FROM discovered_urls WHERE id = 4; END"
    )
    conn.commit()
    conn.close()
    shutil.copy(filled, backup)  # the backup has the trigger too, so only the check can catch it
    with pytest.raises(rs.ReclaimError, match=r"rolled back"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert ids_left(filled) == list(range(1, NINE + 1))


# --- the proof ------------------------------------------------------------------------------------


async def evidence_for(db: str, tmp_path: Path, site: Site, tickers=("AAPL",)) -> rs.Evidence:  # type: ignore[no-untyped-def]
    conn = connector()
    cache = SitemapCache(DOMAIN, [ROOT], db, site.fetch, conn.is_article_url, Clock())
    await cache.begin(RANGE, force=True)
    return await rs.gather_evidence(db, {DOMAIN: conn}, data_dir(tmp_path), RANGE, tickers)


@pytest.fixture
def site() -> Site:
    return Site({ROOT: leaf((TITLED, "2024-05-01", TITLE), (PLAIN, "2024-05-02"))})


async def test_the_proof_finds_every_in_range_pair_and_splits_the_rest(
    db: str, site: Site, tmp_path: Path
) -> None:
    conn = sqlite3.connect(db)
    put(conn, 1, url=TITLED, cls="accept", status="ok", pub="2024-05-01")  # in the cache: found
    put(
        conn,
        2,
        url="https://www.cnbc.com/2019/01/02/old.html",
        cls="accept",
        status="ok",
        pub="2019-01-02",
    )
    put(
        conn,
        3,
        url="https://www.cnbc.com/2024/06/02/gone.html",
        cls="review",
        status="ok",
        pub="2024-06-02",
    )
    put(conn, 4, url=PLAIN)  # a qualifying reject, in the cache
    put(
        conn, 5, url="https://www.cnbc.com/2018/01/01/older.html", pub="2018-01-01"
    )  # before the range
    conn.commit()
    conn.close()
    ev = await evidence_for(db, tmp_path, site)
    assert ev.qualifying == 2
    assert (ev.split.in_cache, sum(ev.split.before_range.values()), ev.split.left_sitemap) == (
        1,
        1,
        0,
    )
    assert ev.proof.stored == 3
    assert ev.proof.found == 1
    assert ev.proof.rejected_now == []
    assert sum(ev.proof.not_in_cache.before_range.values()) == 1
    assert ev.proof.not_in_cache.left_sitemap == 1
    text = rs.format_evidence(ev, RANGE)
    assert "in cache, NOW REJECTED    : 0" in text


async def test_a_stored_pair_the_cache_no_longer_derives_is_reported_as_now_rejected(
    db: str, site: Site, tmp_path: Path
) -> None:
    conn = sqlite3.connect(db)
    put(
        conn, 1, url=PLAIN, cls="accept", status="ok", pub="2024-05-02"
    )  # the matcher rejects it now
    conn.commit()
    conn.close()
    ev = await evidence_for(db, tmp_path, site)
    assert [(t, u, c) for t, u, c in ev.proof.rejected_now] == [("AAPL", PLAIN, "reject")]
    assert ev.proof.found == 0


async def test_the_evidence_never_writes_the_file(db: str, site: Site, tmp_path: Path) -> None:
    conn = connector()
    cache = SitemapCache(DOMAIN, [ROOT], db, site.fetch, conn.is_article_url, Clock())
    await cache.begin(RANGE, force=True)
    import hashlib  # noqa: PLC0415

    before = hashlib.sha256(Path(db).read_bytes()).hexdigest()
    await rs.gather_evidence(db, {DOMAIN: conn}, data_dir(tmp_path), RANGE, [APPLE.ticker])
    assert hashlib.sha256(Path(db).read_bytes()).hexdigest() == before


def test_a_delete_that_touches_articles_rolls_back(filled: str, tmp_path: Path) -> None:
    conn = sqlite3.connect(filled)
    conn.execute(
        "CREATE TRIGGER evil AFTER DELETE ON discovered_urls BEGIN "
        "DELETE FROM articles WHERE id = 9; END"
    )
    conn.commit()
    conn.close()
    backup = backup_of(filled, tmp_path)
    with pytest.raises(rs.ReclaimError, match="articles changed"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert ids_left(filled) == list(range(1, NINE + 1))
