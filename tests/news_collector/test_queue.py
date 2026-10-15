"""Tests for SQLite URL queue."""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from news_collector.models import DiscoveredURL
from news_collector.storage.queue import URLQueue


@pytest.fixture
def queue(tmp_path: Path) -> URLQueue:
    q = URLQueue(str(tmp_path / "test.db"))
    q.initialize()
    return q


def _make_url(url: str, ticker: str = "AAPL", domain: str = "cnbc.com") -> DiscoveredURL:
    return DiscoveredURL(
        url=url,
        domain=domain,
        company="Apple Inc.",
        ticker=ticker,
        source="ddg",
        discovered_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


def test_enqueue_and_get_pending(queue: URLQueue) -> None:
    urls = [
        _make_url("https://cnbc.com/2024/01/01/apple-story.html"),
        _make_url("https://cnbc.com/2024/01/02/apple-story2.html"),
    ]
    inserted = queue.enqueue_batch(urls)
    assert inserted == 2

    pending = queue.get_pending()
    assert len(pending) == 2


def test_deduplication(queue: URLQueue) -> None:
    url = _make_url("https://cnbc.com/2024/01/01/apple-story.html")
    first = queue.enqueue_batch([url])
    second = queue.enqueue_batch([url])
    assert first == 1
    assert second == 0  # duplicate ignored


def test_mark_fetched(queue: URLQueue) -> None:
    url = _make_url("https://cnbc.com/2024/01/01/apple-story.html")
    queue.enqueue_batch([url])
    queue.mark_fetched(url.url, 200)

    pending = queue.get_pending()
    assert len(pending) == 0


def test_enqueue_batch_sets_id_in_place(queue: URLQueue) -> None:
    urls = [
        _make_url("https://cnbc.com/2024/01/01/apple-story.html"),
        _make_url("https://cnbc.com/2024/01/02/apple-story2.html"),
    ]
    assert all(u.id is None for u in urls)
    queue.enqueue_batch(urls)
    assert all(isinstance(u.id, int) for u in urls)
    assert urls[0].id != urls[1].id


def test_enqueue_batch_sets_id_for_ignored_duplicates_too(queue: URLQueue) -> None:
    first = _make_url("https://cnbc.com/2024/01/01/apple-story.html")
    queue.enqueue_batch([first])

    duplicate = _make_url("https://cnbc.com/2024/01/01/apple-story.html")
    queue.enqueue_batch([duplicate])
    # duplicate was ignored (not a new row) but should still resolve to the
    # existing row's id, not stay None
    assert duplicate.id == first.id


def test_get_pending_includes_id(queue: URLQueue) -> None:
    queue.enqueue_batch([_make_url("https://cnbc.com/2024/01/01/apple-story.html")])
    pending = queue.get_pending()
    assert len(pending) == 1
    assert isinstance(pending[0].id, int)


def test_get_by_id(queue: URLQueue) -> None:
    url = _make_url("https://cnbc.com/2024/01/01/apple-story.html")
    queue.enqueue_batch([url])
    assert url.id is not None

    fetched = queue.get_by_id(url.id)
    assert fetched is not None
    assert fetched.url == url.url
    assert fetched.id == url.id

    assert queue.get_by_id(999999) is None


def test_mark_fetched_by_id_updates_only_that_row(queue: URLQueue) -> None:
    same_url_different_ticker = [
        _make_url("https://cnbc.com/2024/01/01/shared-story.html", ticker="AAPL"),
        _make_url("https://cnbc.com/2024/01/01/shared-story.html", ticker="MSFT"),
    ]
    queue.enqueue_batch(same_url_different_ticker)
    aapl_id = same_url_different_ticker[0].id
    assert aapl_id is not None

    queue.mark_fetched_by_id(aapl_id, 200)

    pending = queue.get_pending()
    assert len(pending) == 1
    assert pending[0].ticker == "MSFT"


def test_mark_skipped_by_id(queue: URLQueue) -> None:
    url = _make_url("https://cnbc.com/2024/01/01/apple-story.html")
    queue.enqueue_batch([url])
    assert url.id is not None
    queue.mark_skipped_by_id(url.id, reason="paywalled")

    fetched = queue.get_by_id(url.id)
    assert fetched is not None
    assert fetched.status == "skipped"
    assert fetched.title == "paywalled"


def test_stats(queue: URLQueue) -> None:
    urls = [
        _make_url("https://cnbc.com/2024/01/01/story1.html"),
        _make_url("https://nasdaq.com/articles/story-2024-01-01", domain="nasdaq.com"),
    ]
    queue.enqueue_batch(urls)
    s = queue.stats()
    assert s["total"] == 2
    assert s["by_status"]["pending"] == 2


# ---------------------------------------------------------------------------
# Checkpoint / resume
# ---------------------------------------------------------------------------


def test_completed_pairs_empty_initially(queue: URLQueue) -> None:
    assert queue.completed_pairs(["cnbc.com"], date(2024, 1, 1), date(2024, 1, 31)) == set()


def test_mark_pair_completed_then_visible_in_completed_pairs(queue: URLQueue) -> None:
    queue.mark_pair_completed(
        "AAPL", "cnbc.com", date(2024, 1, 1), date(2024, 1, 31), inserted_count=5
    )

    pairs = queue.completed_pairs(["cnbc.com", "nasdaq.com"], date(2024, 1, 1), date(2024, 1, 31))
    assert pairs == {("AAPL", "cnbc.com")}


def test_completed_pairs_scoped_to_exact_date_range(queue: URLQueue) -> None:
    queue.mark_pair_completed("AAPL", "cnbc.com", date(2024, 1, 1), date(2024, 1, 31))

    # A different date range must not be treated as already completed.
    pairs = queue.completed_pairs(["cnbc.com"], date(2024, 2, 1), date(2024, 2, 29))
    assert pairs == set()


def test_completed_pairs_filtered_by_requested_domains(queue: URLQueue) -> None:
    queue.mark_pair_completed("AAPL", "cnbc.com", date(2024, 1, 1), date(2024, 1, 31))
    queue.mark_pair_completed("AAPL", "nasdaq.com", date(2024, 1, 1), date(2024, 1, 31))

    pairs = queue.completed_pairs(["cnbc.com"], date(2024, 1, 1), date(2024, 1, 31))
    assert pairs == {("AAPL", "cnbc.com")}


def test_mark_pair_completed_is_idempotent_upsert(queue: URLQueue) -> None:
    queue.mark_pair_completed(
        "AAPL", "cnbc.com", date(2024, 1, 1), date(2024, 1, 31), inserted_count=1
    )
    queue.mark_pair_completed(
        "AAPL", "cnbc.com", date(2024, 1, 1), date(2024, 1, 31), inserted_count=2
    )

    pairs = queue.completed_pairs(["cnbc.com"], date(2024, 1, 1), date(2024, 1, 31))
    assert pairs == {("AAPL", "cnbc.com")}  # re-marking doesn't duplicate the row


def test_get_pending_filter_by_domain(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [
            _make_url("https://cnbc.com/2024/01/01/story.html", domain="cnbc.com"),
            _make_url("https://nasdaq.com/articles/story-2024-01-01", domain="nasdaq.com"),
        ]
    )
    cnbc_pending = queue.get_pending(domain="cnbc.com")
    assert len(cnbc_pending) == 1
    assert cnbc_pending[0].domain == "cnbc.com"


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@given(
    urls=st.lists(
        st.from_regex(r"https://cnbc\.com/\d{4}/\d{2}/\d{2}/[a-z0-9-]+\.html", fullmatch=True),
        min_size=1,
        max_size=50,
        unique=True,
    )
)
@settings(max_examples=50)
def test_enqueue_inserts_at_most_len_input(urls: list[str]) -> None:
    """Inserted rows must never exceed the number of input URLs."""
    # Use a fresh temp dir per generated example rather than pytest's
    # function-scoped tmp_path fixture, which hypothesis's health check
    # correctly flags as unsafe to share across @given examples.
    with tempfile.TemporaryDirectory() as tmp_dir:
        q = URLQueue(str(Path(tmp_dir) / "prop_test.db"))
        q.initialize()
        discovered = [_make_url(u) for u in urls]
        inserted = q.enqueue_batch(discovered)
        assert 0 <= inserted <= len(urls)


# ---------------------------------------------------------------------------
# Matching class (T-056b)
# ---------------------------------------------------------------------------


def _classed(url: str, match_class: str, **kw: object) -> DiscoveredURL:
    u = _make_url(url)
    u.match_class = match_class  # type: ignore[assignment]
    for key, value in kw.items():
        setattr(u, key, value)
    return u


def _stored(queue: URLQueue, url: str) -> DiscoveredURL:
    rows, _ = queue.query(url_contains=url)
    assert len(rows) == 1
    return rows[0]


def test_reject_is_stored_skipped_and_never_pending(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [_classed("https://x.com/rej", "reject"), _classed("https://x.com/acc", "accept")]
    )
    assert [u.url for u in queue.get_pending()] == ["https://x.com/acc"]
    row = _stored(queue, "rej")
    assert (row.status, row.match_class) == ("skipped", "reject")


def test_reject_insert_keeps_the_title(queue: URLQueue) -> None:
    queue.enqueue_batch([_classed("https://x.com/rej", "reject", title="Some headline")])
    assert _stored(queue, "rej").title == "Some headline"


def test_review_is_pending_and_keeps_class_reason_and_query(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [_classed("https://x.com/r", "review", match_reason="weak_first_word", query="apple news")]
    )
    row = queue.get_pending()[0]
    assert (row.match_class, row.match_reason, row.query) == (
        "review",
        "weak_first_word",
        "apple news",
    )


def test_mark_skipped_leaves_match_class_alone(queue: URLQueue) -> None:
    queue.enqueue_batch([_classed("https://x.com/p", "review")])
    queue.mark_skipped("https://x.com/p", "paywall")
    row = _stored(queue, "/p")
    assert (row.status, row.match_class, row.title) == ("skipped", "review", "paywall")


def test_rediscovery_with_a_higher_class_promotes_and_resets_status(queue: URLQueue) -> None:
    first = _classed("https://x.com/a", "reject", match_reason="none")
    queue.enqueue_batch([first])
    again = _classed("https://x.com/a", "accept", match_reason="name", query="apple stock")
    assert queue.enqueue_batch([again]) == 0  # not a new row
    row = _stored(queue, "/a")
    assert (row.match_class, row.match_reason, row.query, row.status) == (
        "accept",
        "name",
        "apple stock",
        "pending",
    )
    assert again.id == first.id
    assert [u.url for u in queue.get_pending()] == ["https://x.com/a"]


def test_rediscovery_promotes_to_review_and_lifts_the_skip(queue: URLQueue) -> None:
    queue.enqueue_batch([_classed("https://x.com/a", "reject")])
    queue.enqueue_batch([_classed("https://x.com/a", "review", match_reason="weak_ticker")])
    row = _stored(queue, "/a")
    assert (row.match_class, row.status) == ("review", "pending")


def test_rediscovery_never_demotes(queue: URLQueue) -> None:
    queue.enqueue_batch([_classed("https://x.com/a", "accept", match_reason="name")])
    queue.enqueue_batch([_classed("https://x.com/a", "reject", match_reason="none")])
    row = _stored(queue, "/a")
    assert (row.match_class, row.match_reason, row.status) == ("accept", "name", "pending")


def test_rediscovery_fills_null_query_and_reason_without_changing_the_class(
    queue: URLQueue,
) -> None:
    queue.enqueue_batch([_classed("https://x.com/a", "review")])
    queue.enqueue_batch(
        [_classed("https://x.com/a", "review", match_reason="weak_ticker", query="q1")]
    )
    queue.enqueue_batch([_classed("https://x.com/a", "review", match_reason="other", query="q2")])
    row = _stored(queue, "/a")
    assert (row.match_class, row.match_reason, row.query) == ("review", "weak_ticker", "q1")


def test_promotion_does_not_reset_a_paywall_skip(queue: URLQueue) -> None:
    queue.enqueue_batch([_classed("https://x.com/a", "review")])
    queue.mark_skipped("https://x.com/a", "paywall")
    queue.enqueue_batch([_classed("https://x.com/a", "accept", match_reason="name")])
    row = _stored(queue, "/a")
    assert (row.match_class, row.status) == ("accept", "skipped")


def test_two_entries_for_one_pair_in_a_batch_compose(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [
            _classed("https://x.com/a", "reject"),
            _classed("https://x.com/a", "accept", match_reason="name"),
        ]
    )
    row = _stored(queue, "/a")
    assert (row.match_class, row.status) == ("accept", "pending")


def test_query_filters_by_match_class(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [
            _classed("https://x.com/1", "accept"),
            _classed("https://x.com/2", "review"),
            _classed("https://x.com/3", "reject"),
        ]
    )
    rows, total = queue.query(match_class="reject")
    assert total == 1
    assert rows[0].url == "https://x.com/3"
    assert queue.query(status="skipped")[1] == 1


def test_invalid_match_class_is_refused_before_any_write(queue: URLQueue) -> None:
    good = _classed("https://x.com/good", "accept")
    bad = _classed("https://x.com/bad", "accept")
    bad.match_class = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="bogus"):
        queue.enqueue_batch([good, bad])
    assert queue.stats()["total"] == 0


def test_stats_counts_rows_by_match_class(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [
            _classed("https://x.com/1", "accept"),
            _classed("https://x.com/2", "review"),
            _classed("https://x.com/3", "reject"),
            _classed("https://x.com/4", "reject"),
        ]
    )
    queue.mark_skipped("https://x.com/1", "paywall")
    stats = queue.stats()
    assert stats["by_match_class"] == {"accept": 1, "review": 1, "reject": 2}
    assert stats["by_status"] == {"pending": 1, "skipped": 3}


def _snapshot(queue: URLQueue, url: str) -> dict:
    assert queue._conn is not None
    row = queue._conn.execute("SELECT * FROM discovered_urls WHERE url=?", (url,)).fetchone()
    return dict(row)


def test_promotion_never_writes_back_a_status_the_extractor_changed(queue: URLQueue) -> None:
    first = _classed("https://x.com/a", "review")
    queue.enqueue_batch([first])
    stale = {("https://x.com/a", "AAPL"): _snapshot(queue, "https://x.com/a")}  # status pending
    queue.mark_fetched_by_id(first.id or 0, 200)  # the extractor gets there first

    again = _classed("https://x.com/a", "accept", match_reason="name", query="q")
    queue._promote_duplicates([again], stale)

    row = _stored(queue, "/a")
    assert (row.match_class, row.match_reason, row.query) == ("accept", "name", "q")
    assert row.status == "fetched"


def test_a_stale_read_cannot_demote_a_concurrent_promotion(queue: URLQueue) -> None:
    first = _classed("https://x.com/a", "reject")
    queue.enqueue_batch([first])
    stale = {("https://x.com/a", "AAPL"): _snapshot(queue, "https://x.com/a")}  # reject
    queue.enqueue_batch([_classed("https://x.com/a", "accept", match_reason="name")])  # wins

    queue._promote_duplicates([_classed("https://x.com/a", "review", match_reason="weak")], stale)

    row = _stored(queue, "/a")
    assert (row.match_class, row.match_reason, row.status) == ("accept", "name", "pending")


def test_last_promotion_counts_promoted_and_lifted_rows(queue: URLQueue) -> None:
    queue.enqueue_batch(
        [
            _classed("https://x.com/1", "reject"),
            _classed("https://x.com/2", "reject"),
            _classed("https://x.com/3", "review"),
        ]
    )
    assert queue.last_promotion == (0, 0)
    queue.mark_skipped("https://x.com/3", "paywall")
    queue.enqueue_batch(
        [
            _classed("https://x.com/1", "accept"),
            _classed("https://x.com/2", "review"),
            _classed("https://x.com/3", "accept"),  # promoted but a paywall skip stays
        ]
    )
    assert queue.last_promotion == (3, 2)
    queue.enqueue_batch([_classed("https://x.com/1", "accept")])
    assert queue.last_promotion == (0, 0)


def test_last_promotion_is_zero_when_the_guard_skipped_the_write(queue: URLQueue) -> None:
    queue.enqueue_batch([_classed("https://x.com/a", "reject")])
    stale = {("https://x.com/a", "AAPL"): _snapshot(queue, "https://x.com/a")}  # reject
    queue.enqueue_batch([_classed("https://x.com/a", "accept", match_reason="name")])  # wins

    result = queue._promote_duplicates([_classed("https://x.com/a", "review")], stale)

    assert result == (0, 0)


def test_promoted_but_not_lifted_when_the_status_already_moved_on(queue: URLQueue) -> None:
    first = _classed("https://x.com/a", "reject")
    queue.enqueue_batch([first])
    stale = {("https://x.com/a", "AAPL"): _snapshot(queue, "https://x.com/a")}  # skipped, reject
    queue.mark_fetched_by_id(first.id or 0, 200)  # e.g. fetched by hand meanwhile

    result = queue._promote_duplicates([_classed("https://x.com/a", "accept")], stale)

    assert result == (1, 0)
    assert _stored(queue, "/a").status == "fetched"
