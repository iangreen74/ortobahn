"""Tests for the Smart Timing optimizer."""

from __future__ import annotations

import pytest

from ortobahn.smart_timing import MIN_POSTS_FOR_TIMING, SmartTimingOptimizer


@pytest.fixture()
def optimizer(test_db):
    return SmartTimingOptimizer(test_db)


@pytest.fixture()
def _seed_client(test_db):
    """Seed a client with preferred_posting_hours column."""
    test_db.create_client(
        {
            "id": "timing-test",
            "name": "Timing Test Co",
            "industry": "tech",
            "target_audience": "developers",
            "brand_voice": "professional",
        }
    )


@pytest.fixture()
def _seed_posts(test_db, _seed_client):
    """Seed enough published posts at different hours for timing analysis."""
    from datetime import datetime, timezone

    for i in range(10):
        hour = 9 + (i % 4)  # hours 9, 10, 11, 12 repeating
        published = datetime(2025, 1, 15, hour, 30, 0, tzinfo=timezone.utc).isoformat()
        post_id = f"timing-post-{i}"
        test_db.save_post(
            text=f"Post at hour {hour} #{i}",
            run_id=f"run-{i}",
            status="published",
            confidence=0.8,
            client_id="timing-test",
            platform="bluesky",
        )
        # Update published_at to the desired hour
        test_db.execute(
            "UPDATE posts SET published_at=? WHERE text=?",
            (published, f"Post at hour {hour} #{i}"),
            commit=True,
        )

    # Add metrics for engagement (higher engagement at hours 9 and 10)
    posts = test_db.fetchall(
        "SELECT id, published_at FROM posts WHERE client_id='timing-test' ORDER BY published_at"
    )
    for post in posts:
        pa = post["published_at"] or ""
        # Extract hour from the ISO string
        try:
            hour = int(pa[11:13])
        except (ValueError, IndexError):
            hour = 0
        # Hour 9 gets high engagement, hour 10 medium, others low
        engagement = {9: 50, 10: 30, 11: 5, 12: 2}.get(hour, 1)
        test_db.execute(
            "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at)"
            " VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (f"m-{post['id']}", post["id"], engagement, engagement // 2, engagement // 5),
            commit=True,
        )


class TestCalculateOptimalHours:
    def test_returns_empty_with_no_posts(self, optimizer, _seed_client):
        result = optimizer.calculate_optimal_hours("timing-test")
        assert result == []

    def test_returns_empty_with_too_few_posts(self, test_db, optimizer, _seed_client):
        # Add only 3 posts (below MIN_POSTS_FOR_TIMING)
        for i in range(3):
            test_db.save_post(
                text=f"Few post {i}",
                run_id=f"few-run-{i}",
                status="published",
                confidence=0.8,
                client_id="timing-test",
                platform="bluesky",
            )
        result = optimizer.calculate_optimal_hours("timing-test")
        assert result == []

    def test_returns_sorted_hours(self, optimizer, _seed_posts):
        result = optimizer.calculate_optimal_hours("timing-test")
        assert result == sorted(result)
        assert len(result) > 0

    def test_highest_engagement_hours_first(self, optimizer, _seed_posts):
        result = optimizer.calculate_optimal_hours("timing-test")
        # Hours 9 and 10 should be in the result (highest engagement)
        assert 9 in result
        assert 10 in result

    def test_returns_empty_for_nonexistent_client(self, optimizer):
        result = optimizer.calculate_optimal_hours("does-not-exist")
        assert result == []


class TestUpdateClientPostingHours:
    def test_updates_when_data_available(self, test_db, optimizer, _seed_posts):
        result = optimizer.update_client_posting_hours("timing-test")
        assert result is True

        client = test_db.fetchone(
            "SELECT preferred_posting_hours FROM clients WHERE id='timing-test'"
        )
        assert client is not None
        hours_str = client["preferred_posting_hours"]
        assert hours_str != ""
        hours = [int(h) for h in hours_str.split(",")]
        assert 9 in hours
        assert 10 in hours

    def test_returns_false_with_insufficient_data(self, optimizer, _seed_client):
        result = optimizer.update_client_posting_hours("timing-test")
        assert result is False

    def test_returns_false_when_hours_unchanged(self, test_db, optimizer, _seed_posts):
        # First call updates
        assert optimizer.update_client_posting_hours("timing-test") is True
        # Second call finds no change
        assert optimizer.update_client_posting_hours("timing-test") is False

    def test_preserves_existing_hours_on_insufficient_data(self, test_db, _seed_client):
        # Set existing hours
        test_db.execute(
            "UPDATE clients SET preferred_posting_hours='8,9,10' WHERE id='timing-test'",
            commit=True,
        )
        opt = SmartTimingOptimizer(test_db)
        result = opt.update_client_posting_hours("timing-test")
        assert result is False

        # Hours should be unchanged
        client = test_db.fetchone(
            "SELECT preferred_posting_hours FROM clients WHERE id='timing-test'"
        )
        assert client["preferred_posting_hours"] == "8,9,10"
