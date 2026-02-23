"""Tests for Post Feedback Loop module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ortobahn.memory import MemoryStore
from ortobahn.post_feedback import PostFeedbackLoop


@pytest.fixture
def memory_store(test_db):
    return MemoryStore(db=test_db)


@pytest.fixture
def feedback(test_db, memory_store):
    return PostFeedbackLoop(db=test_db, memory_store=memory_store)


def _publish_post(test_db, run_id, client_id, text="Test post", platform="bluesky"):
    """Helper to create a published post with a platform URI."""
    post_id = test_db.save_post(
        text=text,
        run_id=run_id,
        status="draft",
        client_id=client_id,
        platform=platform,
    )
    test_db.execute(
        "UPDATE posts SET status='published', platform_uri=?, published_at=CURRENT_TIMESTAMP WHERE id=?",
        (f"at://did:plc:test/app.bsky.feed.post/{post_id[:8]}", post_id),
        commit=True,
    )
    return post_id


class TestCheckRecentPosts:
    def test_returns_zeros_for_empty_run(self, feedback):
        result = feedback.check_recent_posts("run-empty", "default")
        assert result == {"posts_checked": 0, "resonating": 0, "silent": 0, "viral": 0}

    def test_classifies_silent_post(self, test_db, memory_store):
        """A post with 0 engagement should be classified as silent."""
        mock_bsky = MagicMock()
        metrics = MagicMock()
        metrics.like_count = 0
        metrics.repost_count = 0
        metrics.reply_count = 0
        mock_bsky.get_post_metrics.return_value = metrics

        fb = PostFeedbackLoop(db=test_db, memory_store=memory_store, bluesky_client=mock_bsky)
        _publish_post(test_db, "run-1", "default")

        result = fb.check_recent_posts("run-1", "default")
        assert result["posts_checked"] == 1
        assert result["silent"] == 1
        assert result["resonating"] == 0

    def test_classifies_resonating_post(self, test_db, memory_store):
        """A post with some engagement should be classified as resonating."""
        mock_bsky = MagicMock()
        metrics = MagicMock()
        metrics.like_count = 3
        metrics.repost_count = 1
        metrics.reply_count = 0
        mock_bsky.get_post_metrics.return_value = metrics

        fb = PostFeedbackLoop(db=test_db, memory_store=memory_store, bluesky_client=mock_bsky)
        _publish_post(test_db, "run-2", "default")

        result = fb.check_recent_posts("run-2", "default")
        assert result["posts_checked"] == 1
        assert result["resonating"] == 1

    def test_classifies_viral_post(self, test_db, memory_store):
        """A post with >5x historical avg should be classified as viral."""
        mock_bsky = MagicMock()
        metrics = MagicMock()
        metrics.like_count = 100
        metrics.repost_count = 50
        metrics.reply_count = 20
        mock_bsky.get_post_metrics.return_value = metrics

        fb = PostFeedbackLoop(db=test_db, memory_store=memory_store, bluesky_client=mock_bsky)

        # Create historical posts with low engagement to establish baseline
        for i in range(5):
            pid = _publish_post(test_db, f"hist-{i}", "default")
            test_db.save_metrics(pid, like_count=2, repost_count=1, reply_count=0)

        # Now check a new high-engagement post
        _publish_post(test_db, "run-viral", "default")
        result = fb.check_recent_posts("run-viral", "default")

        assert result["posts_checked"] == 1
        assert result["viral"] == 1

    def test_no_platform_client_returns_none_metrics(self, feedback, test_db):
        """Posts without matching platform clients should be skipped."""
        _publish_post(test_db, "run-no-client", "default")
        result = feedback.check_recent_posts("run-no-client", "default")
        assert result["posts_checked"] == 0

    def test_creates_memory_for_silent_post(self, test_db, memory_store):
        mock_bsky = MagicMock()
        metrics = MagicMock()
        metrics.like_count = 0
        metrics.repost_count = 0
        metrics.reply_count = 0
        mock_bsky.get_post_metrics.return_value = metrics

        fb = PostFeedbackLoop(db=test_db, memory_store=memory_store, bluesky_client=mock_bsky)
        _publish_post(test_db, "run-mem", "default")
        fb.check_recent_posts("run-mem", "default")

        memories = memory_store.recall("creator", "default")
        assert len(memories) >= 1
        assert any("silent" in (m.content.get("signal", "") or "") for m in memories)

    def test_creates_memory_for_resonating_post(self, test_db, memory_store):
        """A resonating post should create a memory with signal='resonating'."""
        mock_bsky = MagicMock()
        metrics = MagicMock()
        metrics.like_count = 5
        metrics.repost_count = 2
        metrics.reply_count = 1
        mock_bsky.get_post_metrics.return_value = metrics

        fb = PostFeedbackLoop(db=test_db, memory_store=memory_store, bluesky_client=mock_bsky)
        _publish_post(test_db, "run-res-mem", "default")
        fb.check_recent_posts("run-res-mem", "default")

        memories = memory_store.recall("creator", "default")
        assert len(memories) >= 1
        assert any("resonating" in (m.content.get("signal", "") or "") for m in memories)

    def test_handles_platform_api_error_gracefully(self, test_db, memory_store):
        """If the platform API raises, the post should be skipped (not crash)."""
        mock_bsky = MagicMock()
        mock_bsky.get_post_metrics.side_effect = RuntimeError("API down")

        fb = PostFeedbackLoop(db=test_db, memory_store=memory_store, bluesky_client=mock_bsky)
        _publish_post(test_db, "run-err", "default")
        result = fb.check_recent_posts("run-err", "default")

        assert result["posts_checked"] == 0
        assert result["silent"] == 0


class TestGetHistoricalEarlyAvg:
    def test_returns_zero_with_no_history(self, feedback):
        assert feedback._get_historical_early_avg("default") == 0.0

    def test_returns_average_from_recent_posts(self, test_db, feedback):
        for i in range(3):
            pid = test_db.save_post(text=f"Post {i}", run_id="hist", status="draft", client_id="default")
            test_db.execute(
                "UPDATE posts SET status='published', published_at=CURRENT_TIMESTAMP WHERE id=?",
                (pid,),
                commit=True,
            )
            test_db.save_metrics(pid, like_count=6, repost_count=2, reply_count=1, quote_count=1)

        avg = feedback._get_historical_early_avg("default")
        assert avg == 10.0  # 6+2+1+1=10 per post
