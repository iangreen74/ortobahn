"""Tests for Dynamic Posting Cadence module."""

from __future__ import annotations

import time
import uuid

import pytest

from ortobahn.cadence import CadenceOptimizer


@pytest.fixture
def cadence(test_db):
    return CadenceOptimizer(db=test_db)


def _insert_cycle(test_db, run_id, client_id, posts_with_engagement):
    """Helper: create a pipeline run + published posts with metrics."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    test_db.execute(
        "INSERT INTO pipeline_runs (id, mode, started_at, status, client_id) VALUES (?, 'single', ?, 'completed', ?)",
        (run_id, now, client_id),
        commit=True,
    )
    for i, engagement in enumerate(posts_with_engagement):
        post_id = f"{run_id}-post-{i}"
        test_db.execute(
            "INSERT INTO posts (id, text, run_id, status, client_id) VALUES (?, ?, ?, 'published', ?)",
            (post_id, f"Post {i}", run_id, client_id),
            commit=True,
        )
        if engagement > 0:
            mid = str(uuid.uuid4())[:8]
            test_db.execute(
                "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count) VALUES (?, ?, ?, 0, 0, 0)",
                (mid, post_id, engagement),
                commit=True,
            )


class TestCalculateOptimalPosts:
    def test_no_history_returns_current(self, cadence):
        assert cadence.calculate_optimal_posts("default", current_max=4) == 4

    def test_zero_engagement_cools_down(self, test_db, cadence):
        _insert_cycle(test_db, "run-1", "default", [0, 0, 0])
        assert cadence.calculate_optimal_posts("default", current_max=4) == 1

    def test_falling_trend_reduces_by_one(self, test_db, cadence):
        # Two cycles with falling engagement (insert older first so ordering works)
        _insert_cycle(test_db, "run-old", "default", [10, 10])
        time.sleep(0.01)  # Ensure different timestamps
        _insert_cycle(test_db, "run-new", "default", [3, 3])
        assert cadence.calculate_optimal_posts("default", current_max=4) == 3

    def test_falling_floor_at_one(self, test_db, cadence):
        _insert_cycle(test_db, "run-old", "default", [10, 10])
        time.sleep(0.01)
        _insert_cycle(test_db, "run-new", "default", [2, 2])
        assert cadence.calculate_optimal_posts("default", current_max=1) == 1

    def test_rising_strong_increases_by_one(self, test_db, cadence):
        # We need 3 cycles so historical avg is pulled down enough for last > 2x avg.
        # Cycle 1 (oldest): engagement=1, Cycle 2: engagement=3, Cycle 3 (newest): engagement=100
        # avg of [1, 3, 100] = 34.67, 100 > 69.33? Yes → increase
        _insert_cycle(test_db, "run-1", "default", [1])
        time.sleep(0.01)
        _insert_cycle(test_db, "run-2", "default", [3])
        time.sleep(0.01)
        _insert_cycle(test_db, "run-3", "default", [100])
        result = cadence.calculate_optimal_posts("default", current_max=4)
        assert result == 5

    def test_rising_caps_at_six(self, test_db, cadence):
        _insert_cycle(test_db, "run-1", "default", [1])
        time.sleep(0.01)
        _insert_cycle(test_db, "run-2", "default", [5])
        time.sleep(0.01)
        _insert_cycle(test_db, "run-3", "default", [100])
        result = cadence.calculate_optimal_posts("default", current_max=6)
        assert result <= 6

    def test_stable_keeps_current(self, test_db, cadence):
        _insert_cycle(test_db, "run-1", "default", [5, 5])
        time.sleep(0.01)
        _insert_cycle(test_db, "run-2", "default", [5, 5])
        assert cadence.calculate_optimal_posts("default", current_max=4) == 4


class TestDetectTrend:
    def test_single_cycle_is_stable(self, cadence):
        history = [{"avg_engagement": 5.0, "post_count": 3, "run_id": "r1"}]
        assert cadence._detect_trend(history) == "stable"

    def test_rising(self, cadence):
        history = [
            {"avg_engagement": 10.0, "post_count": 3, "run_id": "r2"},
            {"avg_engagement": 3.0, "post_count": 3, "run_id": "r1"},
        ]
        assert cadence._detect_trend(history) == "rising"

    def test_falling(self, cadence):
        history = [
            {"avg_engagement": 2.0, "post_count": 3, "run_id": "r2"},
            {"avg_engagement": 10.0, "post_count": 3, "run_id": "r1"},
        ]
        assert cadence._detect_trend(history) == "falling"


class TestGetCadenceContext:
    def test_empty_when_no_history(self, cadence):
        assert cadence.get_cadence_context("default", 4) == ""

    def test_includes_recommendation(self, test_db, cadence):
        _insert_cycle(test_db, "run-1", "default", [5, 5])
        ctx = cadence.get_cadence_context("default", 3)
        assert "Recommended posts this cycle: 3" in ctx
        assert "Dynamic Cadence" in ctx
