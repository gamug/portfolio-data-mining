"""The news_crawler_cli startup line must not count matcher-rejected pairs as processed."""

from __future__ import annotations

from cli.news_crawler_cli import progress_line


def test_progress_line_without_rejects_is_unchanged() -> None:
    assert (
        progress_line({"pending": 5, "fetched": 2, "failed": 1}, {"accept": 8}, "pending", 5)
        == "Already processed: 3. Pending this run: 5."
    )


def test_progress_line_reports_rejected_pairs_separately() -> None:
    # status counts already leave rejects out (`get_status_counts`)
    line = progress_line({"pending": 5, "skipped": 1}, {"accept": 6, "reject": 330000}, "failed", 0)
    assert line == (
        "Already processed: 1. Failed this run: 0. Matcher-rejected, not fetched: 330000."
    )
