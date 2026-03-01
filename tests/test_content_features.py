"""Tests for ortobahn.content_features — zero-LLM feature extraction."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ortobahn.content_features import build_content_brief, extract_features


class TestExtractFeatures:
    """Tests for extract_features()."""

    def test_short_post(self) -> None:
        """Post with len < 100 should be bucketed as 'short'."""
        text = "Hello world"
        features = extract_features(text)
        assert features["length_bucket"] == "short"

    def test_question_detected(self) -> None:
        """A '?' in the text should set has_question=True."""
        features = extract_features("What do you think?")
        assert features["has_question"] is True

    def test_cta_detected(self) -> None:
        """'Check out' should trigger has_cta=True."""
        features = extract_features("Check out our new product launch today")
        assert features["has_cta"] is True

    def test_emoji_detected(self) -> None:
        """Post containing emoji should set has_emoji=True."""
        features = extract_features("Great news! \U0001f600")
        assert features["has_emoji"] is True

    def test_time_of_day_morning(self) -> None:
        """09:00 UTC should be classified as 'morning'."""
        published = "2025-06-15T09:00:00+00:00"
        features = extract_features("Morning post", published_at=published)
        assert features["time_of_day"] == "morning"
        assert features["day_of_week"] == "Sunday"

    def test_no_published_at_skips_timing(self) -> None:
        """When published_at is None, time_of_day should not be present."""
        features = extract_features("No timestamp post", published_at=None)
        assert "time_of_day" not in features
        assert "day_of_week" not in features


class TestBuildContentBrief:
    """Tests for build_content_brief() using the test_db fixture."""

    @staticmethod
    def _insert_post(
        db,
        client_id: str,
        text: str,
        likes: int = 0,
        reposts: int = 0,
        replies: int = 0,
        published_at: str | None = None,
    ) -> str:
        """Helper to insert a published post with metrics."""
        post_id = str(uuid.uuid4())[:8]
        if published_at is None:
            published_at = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        db.execute(
            "INSERT INTO posts (id, text, status, client_id, run_id, published_at, platform, confidence) "
            "VALUES (?, ?, 'published', ?, 'r1', ?, 'bluesky', 0.8)",
            (post_id, text, client_id, published_at),
            commit=True,
        )
        db.execute(
            "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4())[:8],
                post_id,
                likes,
                reposts,
                replies,
                datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            ),
            commit=True,
        )
        return post_id

    def test_returns_empty_with_insufficient_data(self, test_db) -> None:
        """Fewer than 10 posts should return empty string."""
        for i in range(5):
            self._insert_post(test_db, "default", f"Post number {i}")

        result = build_content_brief(test_db, client_id="default")
        assert result == ""

    def test_returns_brief_with_sufficient_data(self, test_db) -> None:
        """15 posts with varying engagement should produce a brief."""
        for i in range(15):
            # Vary engagement: first 5 get high engagement, rest get low
            likes = 50 if i < 5 else 1
            self._insert_post(
                test_db,
                "default",
                f"Post number {i} with some content to analyze",
                likes=likes,
                reposts=i,
                replies=0,
            )

        result = build_content_brief(test_db, client_id="default")
        assert "Content Performance Brief" in result
        assert "Analyzed 15 published posts" in result

    def test_isolates_by_client_id(self, test_db) -> None:
        """Posts for clientA should not appear in brief for clientB."""
        for i in range(15):
            self._insert_post(test_db, "clientA", f"Post {i} for client A", likes=i)

        result = build_content_brief(test_db, client_id="clientB")
        assert result == ""

    def test_zero_llm_calls(self, test_db) -> None:
        """Verify the function runs without any LLM mocking."""
        # Insert enough posts for a brief
        for i in range(12):
            self._insert_post(
                test_db,
                "default",
                f"Testing post {i} content",
                likes=i * 3,
                reposts=i,
            )

        # Should not raise — no LLM calls needed
        result = build_content_brief(test_db, client_id="default")
        assert isinstance(result, str)
