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
