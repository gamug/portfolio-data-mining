"""Reclaim of the never-fetched sitemap rejects (T-073): the selection, the deletion, the proof."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sitemap_helpers import RANGE, ROOT, Clock, Site, leaf
from test_reclass_cache import APPLE, DOMAIN, PLAIN, TITLE, TITLED, connector

from news_collector import reclaim_sitemap as rs
from news_collector import sitemap_cache as sc
from news_collector import sitemap_fill
from news_collector.sitemap_cache import SitemapCache

OTHER = "plain.test"
NINE = 9
LAST = 10  # the highest id of the `filled` file


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
    put(conn, 2)  # qualifies too
    put(conn, 3, article=True)  # kept: it has an article
    put(conn, 4, cls="accept")  # kept: not a reject
    put(conn, 5, status="pending")  # kept: it may still be fetched
    put(conn, 6, source="ddg")  # kept: not a sitemap
    put(conn, 7, domain=OTHER)  # kept: no cache for this domain
    put(conn, 8)  # kept below: a hand-labeled id
    put(conn, NINE, cls="review", status="ok", article=True)
    put(conn, LAST, source="rss")  # kept: nothing in the cache can re-derive an rss pair
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
    ]  # 8 is a labeled id; 3 has an article; 7 has no cache; 4, 5, 6 are not rejects; 10 is rss


def test_reclaim_deletes_the_candidates_and_nothing_else(filled: str, tmp_path: Path) -> None:
    backup = backup_of(filled, tmp_path)
    done = rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert done.deleted == 2
    assert ids_left(filled) == [3, 4, 5, 6, 7, 8, NINE, LAST]
    assert (done.before.discovered, done.after.discovered) == (LAST, LAST - 2)
    assert done.after.articles == done.before.articles == 2
    assert done.after.orphans == 0
    assert done.after.kept_rows() == done.before.kept_rows()
    assert done.after.user_version == done.before.user_version
    assert (
        done.after.schema_hash == done.before.schema_hash
    )  # tables and indexes survive VACUUM INTO
    assert done.after.quick_check == "ok"
    assert ids_left(str(backup)) == list(range(1, LAST + 1))  # the backup is untouched
    assert not Path(filled + ".compact").exists()
    # the file keeps its journal mode (VACUUM INTO writes a rollback-journal file; the queue's is WAL)
    assert Path(filled).read_bytes()[18:20] == Path(str(backup)).read_bytes()[18:20] == b"\x02\x02"


def test_a_different_expected_count_deletes_nothing(filled: str, tmp_path: Path) -> None:
    backup = backup_of(filled, tmp_path)
    with pytest.raises(rs.ReclaimError, match="names 3 rows, 2 qualify"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=3, check_processes=False)
    assert ids_left(filled) == list(range(1, LAST + 1))


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
    assert ids_left(filled) == list(range(1, LAST + 1))


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
    assert ids_left(filled) == list(range(1, LAST + 1))


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
    assert ids_left(filled) == list(range(1, LAST + 1))


# --- the review of PR #95 -----------------------------------------------------------------------


def test_kept_ids_refuses_missing_or_empty_sample_files(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(rs.ReclaimError, match="the labeled ids are not protected"):
        rs.kept_ids(empty)
    (empty / "ticker_a_sample.csv").write_text("id,url\n")
    (empty / "ticker_b_manual.csv").write_text("id,label\n")
    with pytest.raises(rs.ReclaimError, match="name no id"):
        rs.kept_ids(empty)


def test_a_wrong_data_dir_deletes_nothing(filled: str, tmp_path: Path) -> None:
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    with pytest.raises(rs.ReclaimError, match="not protected"):
        rs.reclaim(
            Path(filled), backup_of(filled, tmp_path), nowhere, expect=2, check_processes=False
        )
    assert ids_left(filled) == list(range(1, LAST + 1))


def test_the_number_of_protected_ids_can_be_pinned(filled: str, tmp_path: Path) -> None:
    folder = data_dir(tmp_path, 3, 8)  # {3, 8}
    with pytest.raises(rs.ReclaimError, match="names 593 protected ids, 2 found"):
        rs.reclaim(
            Path(filled),
            backup_of(filled, tmp_path),
            folder,
            expect=2,
            expect_kept=593,
            check_processes=False,
        )
    done = rs.reclaim(
        Path(filled),
        backup_of(filled, tmp_path),
        folder,
        expect=2,
        expect_kept=2,
        check_processes=False,
    )
    assert done.kept == 2


def test_it_refuses_while_another_connection_has_the_file_open(
    filled: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rs, "LOCK_WAIT", 0.2)
    other = sqlite3.connect(filled)  # an idle connection, as a gateway keeps one
    other.execute("SELECT COUNT(*) FROM discovered_urls").fetchone()
    try:
        with pytest.raises(rs.ReclaimError, match="another connection has"):
            rs.reclaim(
                Path(filled),
                backup_of(filled, tmp_path),
                data_dir(tmp_path),
                expect=2,
                check_processes=False,
            )
    finally:
        other.close()
    assert ids_left(filled) == list(range(1, LAST + 1))


def test_open_handles_sees_a_process_that_holds_the_file(filled: str, tmp_path: Path) -> None:
    code = (
        "import sqlite3, sys, time; c = sqlite3.connect(sys.argv[1]); "
        "c.execute('SELECT 1 FROM sqlite_master').fetchall(); print('up', flush=True); time.sleep(30)"
    )
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code, filled], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "up"
        found = rs.open_handles(Path(filled))
        assert any(f"pid {proc.pid} " in line for line in found), found
        with pytest.raises(rs.ReclaimError, match="stop the gateway"):
            rs.reclaim(
                Path(filled),
                Path(filled + ".none"),
                data_dir(tmp_path),
                expect=2,
                check_processes=True,
            )
    finally:
        proc.kill()
        proc.wait()
    assert rs.open_handles(Path(filled)) == []  # this process is not reported


def test_a_missing_pgrep_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_pgrep(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(rs.subprocess, "run", no_pgrep)
    assert rs.running_collectors() == []


def test_a_failure_after_the_commit_says_so_and_compact_finishes_it(
    filled: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = backup_of(filled, tmp_path)

    def disk_full(_conn: sqlite3.Connection, compact: Path) -> None:
        compact.write_bytes(b"half a file")
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(rs, "_write_compact", disk_full)
    with pytest.raises(rs.ReclaimIncomplete, match=r"COMMITTED.*disk is full.*compact") as err:
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert "2 rows" in str(err.value)
    assert ids_left(filled) == [3, 4, 5, 6, 7, 8, NINE, LAST]  # the rows are gone
    assert not Path(filled + ".compact").exists()  # the partial file is removed
    assert ids_left(str(backup)) == list(range(1, LAST + 1))  # the backup is intact
    monkeypatch.undo()

    done = rs.compact(Path(filled), check_processes=False)

    assert done.after.discovered == LAST - 2
    assert ids_left(filled) == [3, 4, 5, 6, 7, 8, NINE, LAST]
    assert not Path(filled + ".compact").exists()
    assert Path(filled).read_bytes()[18:20] == b"\x02\x02"  # the journal mode survives


def test_a_compacted_file_that_differs_is_not_swapped_in_and_is_reported(
    filled: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = backup_of(filled, tmp_path)
    real = rs._write_compact

    def corrupting(conn: sqlite3.Connection, compact: Path) -> None:
        real(conn, compact)
        other = sqlite3.connect(compact)
        other.execute("DELETE FROM discovered_urls WHERE id = 4")  # a row the delete spared
        other.commit()
        other.close()

    monkeypatch.setattr(rs, "_write_compact", corrupting)
    with pytest.raises(rs.ReclaimIncomplete, match=r"COMMITTED.*differs from the database"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert 4 in ids_left(filled)  # the original file was not replaced
    assert not Path(filled + ".compact").exists()


def test_compact_with_nothing_committed_leaves_the_file_as_it_was_on_failure(
    filled: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = hashlib.sha256(Path(filled).read_bytes()).hexdigest()

    def broken(_conn: sqlite3.Connection, compact: Path) -> None:
        compact.write_bytes(b"x")
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(rs, "_write_compact", broken)
    with pytest.raises(rs.ReclaimError, match=r"the file is as it was \(disk is full\)"):
        rs.compact(Path(filled), check_processes=False)
    assert not Path(filled + ".compact").exists()
    assert ids_left(filled) == list(range(1, LAST + 1))
    assert hashlib.sha256(Path(filled).read_bytes()).hexdigest() == before


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="no owner on Windows")
def test_the_swap_keeps_the_mode_bits_and_reports_the_owner(filled: str, tmp_path: Path) -> None:
    os.chmod(filled, 0o640)
    done = rs.reclaim(
        Path(filled),
        backup_of(filled, tmp_path),
        data_dir(tmp_path),
        expect=2,
        check_processes=False,
    )
    assert (Path(filled).stat().st_mode & 0o777) == 0o640
    assert Path(filled).stat().st_uid == os.getuid()
    assert done.owner_kept is True


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="no owner on Windows")
def test_the_owner_that_cannot_be_kept_is_reported(
    filled: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object) -> None:
        raise PermissionError("not permitted")

    monkeypatch.setattr(rs.os, "chown", refuse)
    monkeypatch.setattr(rs.os, "getuid", lambda: Path(filled).stat().st_uid + 1)
    done = rs.reclaim(
        Path(filled),
        backup_of(filled, tmp_path),
        data_dir(tmp_path),
        expect=2,
        check_processes=False,
    )
    assert done.owner_kept is False


def test_a_quote_in_the_file_name_is_not_sql(filled: str, tmp_path: Path) -> None:
    folder = tmp_path / "o'brien"
    folder.mkdir()
    odd = folder / "it's.db"
    shutil.copy(filled, odd)
    done = rs.reclaim(
        odd, backup_of(str(odd), tmp_path), data_dir(tmp_path), expect=2, check_processes=False
    )
    assert done.deleted == 2


def test_a_domain_named_by_the_caller_must_have_a_complete_crawl(
    filled: str, tmp_path: Path
) -> None:
    with pytest.raises(rs.ReclaimError, match="no complete cache crawl for"):
        rs.reclaim(
            Path(filled),
            backup_of(filled, tmp_path),
            data_dir(tmp_path),
            expect=1,
            domains=[OTHER],
            check_processes=False,
        )
    assert ids_left(filled) == list(range(1, LAST + 1))


def test_a_backup_of_other_rows_with_the_same_counts_is_refused(
    filled: str, tmp_path: Path
) -> None:
    backup = backup_of(filled, tmp_path)
    other = sqlite3.connect(backup)
    other.execute("UPDATE discovered_urls SET id = 1001 WHERE id = 1")  # same counts, other ids
    other.commit()
    other.close()
    with pytest.raises(rs.ReclaimError, match="differs from the database"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)


async def test_a_read_only_cache_reads_and_never_writes_or_crawls(
    db: str, site: Site, tmp_path: Path
) -> None:
    conn = connector()
    await SitemapCache(DOMAIN, [ROOT], db, site.fetch, conn.is_article_url, Clock()).begin(
        RANGE, force=True
    )
    before = hashlib.sha256(Path(db).read_bytes()).hexdigest()

    async def never(_url: str) -> str:
        raise AssertionError("a read-only cache never fetches")

    reader = SitemapCache(DOMAIN, [ROOT], db, never, conn.is_article_url, read_only=True)
    run = await reader.begin(RANGE, force=True, allow_crawl=True)  # asks to crawl: still reads

    assert run.crawl is not None
    assert len(run.rows) == 2
    assert hashlib.sha256(Path(db).read_bytes()).hexdigest() == before


# --- the fill command (the cache of the real file is filled before G3) ----------------------------


@pytest.fixture
def offline_site(site: Site, monkeypatch: pytest.MonkeyPatch) -> Site:
    async def fetch_text(_client: object, url: str) -> str:
        return await site.fetch(url)

    monkeypatch.setattr(sc, "fetch_text", fetch_text)
    return site


async def test_fill_cache_crawls_once_writes_only_the_cache_and_a_second_run_reuses_it(
    db: str, offline_site: Site, tmp_path: Path
) -> None:
    conn = sqlite3.connect(db)
    put(conn, 1, url=PLAIN)
    conn.commit()
    conn.close()

    first = await sitemap_fill.fill_cache(Path(db), connector(), object(), RANGE, backup=None)  # type: ignore[arg-type]
    second = await sitemap_fill.fill_cache(Path(db), connector(), object(), RANGE, backup=None)  # type: ignore[arg-type]

    assert first.crawled is True
    assert first.complete is True
    assert len(first.run.rows) == 2
    assert first.pipeline_unchanged is True
    assert second.crawled is False  # a complete, fresh crawl was already there
    assert offline_site.crawls() == 1
    assert ids_left(db) == [1]


async def test_fill_cache_needs_a_matching_backup_and_a_current_schema(
    db: str, offline_site: Site, tmp_path: Path
) -> None:
    stale = backup_of(db, tmp_path)
    conn = sqlite3.connect(db)
    put(conn, 1, url=PLAIN)
    conn.commit()
    conn.close()
    with pytest.raises(rs.ReclaimError, match="differs from the database"):
        await sitemap_fill.fill_cache(Path(db), connector(), object(), RANGE, backup=stale)  # type: ignore[arg-type]
    assert offline_site.crawls() == 0  # nothing was fetched or written

    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("CREATE TABLE discovered_urls (id INTEGER)")
    old.commit()
    old.close()
    with pytest.raises(rs.ReclaimError, match="schema version 4"):
        await sitemap_fill.fill_cache(
            tmp_path / "old.db",
            connector(),
            object(),  # type: ignore[arg-type]
            RANGE,
            backup=None,
        )
    with pytest.raises(rs.ReclaimError, match="no database"):
        await sitemap_fill.fill_cache(
            tmp_path / "none.db",
            connector(),
            object(),  # type: ignore[arg-type]
            RANGE,
            backup=None,
        )


async def test_fill_cache_reports_an_incomplete_crawl(
    db: str, offline_site: Site, tmp_path: Path
) -> None:
    offline_site.down.add(ROOT)
    report = await sitemap_fill.fill_cache(
        Path(db),
        connector(),
        object(),  # type: ignore[arg-type]
        RANGE,
        backup=None,
    )
    assert report.complete is False
    assert report.pipeline_unchanged is True


# --- the review of PR #95, round 2 --------------------------------------------------------------


def test_a_write_between_the_backup_check_and_the_lock_refuses(
    filled: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = backup_of(filled, tmp_path)
    real = rs._open_exclusive

    def writer_first(db_path: Path) -> sqlite3.Connection:
        other = sqlite3.connect(db_path)  # a program the checks did not see
        put(other, 11, cls="accept", status="ok")
        other.commit()
        other.close()
        return real(db_path)

    monkeypatch.setattr(rs, "_open_exclusive", writer_first)
    with pytest.raises(rs.ReclaimError, match="changed after the backup was checked"):
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)
    assert ids_left(filled) == [*range(1, LAST + 1), 11]  # nothing deleted


def test_an_sqlite_error_before_the_commit_is_a_refusal_that_says_it_rolled_back(
    filled: str, tmp_path: Path
) -> None:
    conn = sqlite3.connect(filled)
    conn.execute(
        "CREATE TRIGGER nope BEFORE DELETE ON discovered_urls BEGIN "
        "SELECT RAISE(ABORT, 'no deletes allowed'); END"
    )
    conn.commit()
    conn.close()
    backup = backup_of(filled, tmp_path)  # the backup has the trigger too

    with pytest.raises(rs.ReclaimError, match=r"rolled back \(no deletes allowed\)") as err:
        rs.reclaim(Path(filled), backup, data_dir(tmp_path), expect=2, check_processes=False)

    assert not isinstance(err.value, rs.ReclaimIncomplete)
    assert ids_left(filled) == list(range(1, LAST + 1))


def test_an_unreadable_backup_is_a_refusal_not_a_traceback(filled: str, tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 100)
    with pytest.raises(rs.ReclaimError, match="cannot be read"):
        rs.reclaim(Path(filled), junk, data_dir(tmp_path), expect=2, check_processes=False)


def _crawl(conn: sqlite3.Connection, domain: str, started: str, entries: int | None) -> None:
    conn.execute(
        "INSERT INTO sitemap_crawls (domain, started_at, finished_at, range_start, signature, "
        "complete, entries, failures) VALUES (?,?,?,'2022-01-01','x',1,?,0)",
        (domain, started, started, entries),
    )


def test_a_complete_crawl_with_no_entries_is_not_a_filled_cache(db: str) -> None:
    conn = sqlite3.connect(db)
    _crawl(conn, "empty.test", "2026-10-20T10:00:00", 0)  # an empty urlset, HTTP 200
    _crawl(conn, "full.test", "2026-10-20T10:00:00", 5)
    _crawl(conn, "turned-empty.test", "2026-10-01T10:00:00", 5)
    _crawl(conn, "turned-empty.test", "2026-10-20T10:00:00", 0)  # the latest one decides
    conn.commit()
    assert rs.cache_domains(conn) == ["full.test"]
    conn.close()


def test_an_empty_cache_deletes_nothing(db: str, tmp_path: Path) -> None:
    conn = sqlite3.connect(db)
    put(conn, 1)
    _crawl(conn, DOMAIN, "2026-10-20T10:00:00", 0)
    conn.commit()
    conn.close()
    with pytest.raises(rs.ReclaimError, match="names 1 rows, 0 qualify"):
        rs.reclaim(
            Path(db), backup_of(db, tmp_path), data_dir(tmp_path), expect=1, check_processes=False
        )
    assert ids_left(db) == [1]


def test_compact_wants_a_backup_that_exists_and_passes_quick_check(
    filled: str, tmp_path: Path
) -> None:
    with pytest.raises(rs.ReclaimError, match="no backup at"):
        rs.compact(Path(filled), backup=tmp_path / "none.db", check_processes=False)
    with pytest.raises(rs.ReclaimError, match="itself"):
        rs.compact(Path(filled), backup=Path(filled), check_processes=False)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"x" * 5000)
    with pytest.raises(rs.ReclaimError, match=r"cannot be read|fails quick_check"):
        rs.compact(Path(filled), backup=junk, check_processes=False)
    done = rs.compact(Path(filled), backup=backup_of(filled, tmp_path), check_processes=False)
    assert done.after.discovered == LAST  # nothing deleted; the backup need not match


def test_a_sample_file_without_an_id_column_or_with_a_bad_id_is_refused(tmp_path: Path) -> None:
    folder = data_dir(tmp_path, 3)
    (folder / "ticker_z_sample.csv").write_text("ID,url\n5,u\n")  # the wrong header
    with pytest.raises(rs.ReclaimError, match=r"ticker_z_sample.csv has no id column"):
        rs.kept_ids(folder)
    (folder / "ticker_z_sample.csv").write_text("id,url\n5.0,u\n")
    with pytest.raises(rs.ReclaimError, match="not a number"):
        rs.kept_ids(folder)
