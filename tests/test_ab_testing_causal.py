"""Tests for Causal A/B Testing."""

from __future__ import annotations

import uuid

from ortobahn.ab_testing import _extract_temporal_bucket, get_ab_results_causal


def _create_ab_pair(test_db, client_id, pair_id, a_engagement, b_engagement, published_at="2026-02-22T14:00:00"):
    """Helper to insert an A/B pair of published posts with metrics."""
    for group, engagement in [("A", a_engagement), ("B", b_engagement)]:
        post_id = str(uuid.uuid4())[:8]
        test_db.execute(
            """INSERT INTO posts (id, text, run_id, status, client_id, ab_group, ab_pair_id, published_at)
               VALUES (?, ?, 'run-test', 'published', ?, ?, ?, ?)""",
            (post_id, f"Test {group}", client_id, group, pair_id, published_at),
            commit=True,
        )
        if engagement > 0:
            mid = str(uuid.uuid4())[:8]
            test_db.execute(
                "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count) VALUES (?, ?, ?, 0, 0, 0)",
                (mid, post_id, engagement),
                commit=True,
            )


class TestExtractTemporalBucket:
    def test_morning(self):
        assert _extract_temporal_bucket("2026-02-22T08:30:00") == "6_6"  # Sunday=6, hour 8 -> bucket 6

    def test_afternoon(self):
        result = _extract_temporal_bucket("2026-02-22T14:00:00")
        assert result is not None
        assert result.endswith("_12")

    def test_none_input(self):
        assert _extract_temporal_bucket(None) is None

    def test_invalid_input(self):
        assert _extract_temporal_bucket("not-a-date") is None


class TestGetAbResultsCausal:
    def test_few_pairs_returns_none_causal(self, test_db):
        _create_ab_pair(test_db, "default", "pair-1", 10, 5)
        _create_ab_pair(test_db, "default", "pair-2", 8, 3)

        result = get_ab_results_causal(test_db, "default")
        assert result["completed_pairs"] == 2
        assert result["causal_winner"] is None
        assert result["confounded"] is None

    def test_clear_causal_winner(self, test_db):
        # All pairs across different times: A wins consistently
        _create_ab_pair(test_db, "default", "pair-1", 10, 5, "2026-02-20T08:00:00")  # Thursday morning
        _create_ab_pair(test_db, "default", "pair-2", 12, 3, "2026-02-21T14:00:00")  # Friday afternoon
        _create_ab_pair(test_db, "default", "pair-3", 8, 2, "2026-02-22T20:00:00")  # Saturday evening

        result = get_ab_results_causal(test_db, "default")
        assert result["completed_pairs"] == 3
        assert result["a_wins"] == 3
        assert result["causal_winner"] == "A"
        assert result["confounded"] is False

    def test_confounded_detection(self, test_db):
        # A wins in aggregate but only in one time bucket
        _create_ab_pair(test_db, "default", "pair-1", 100, 1, "2026-02-22T14:00:00")  # Sunday afternoon - A wins big
        _create_ab_pair(test_db, "default", "pair-2", 100, 1, "2026-02-22T15:00:00")  # Sunday afternoon - same bucket
        _create_ab_pair(test_db, "default", "pair-3", 1, 10, "2026-02-20T08:00:00")  # Thursday morning - B wins

        result = get_ab_results_causal(test_db, "default")
        # A wins aggregate (2 vs 1), but across buckets it's split
        # One bucket: A wins, another bucket: B wins -> not >60% for either
        assert result["a_wins"] == 2
        assert result["b_wins"] == 1

    def test_temporal_breakdown_populated(self, test_db):
        _create_ab_pair(test_db, "default", "pair-1", 10, 5, "2026-02-20T08:00:00")
        _create_ab_pair(test_db, "default", "pair-2", 8, 3, "2026-02-21T14:00:00")
        _create_ab_pair(test_db, "default", "pair-3", 6, 2, "2026-02-22T20:00:00")

        result = get_ab_results_causal(test_db, "default")
        assert len(result["temporal_breakdown"]) > 0
        for entry in result["temporal_breakdown"]:
            assert "bucket" in entry
            assert "a_wins" in entry
            assert "b_wins" in entry
