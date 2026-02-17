"""Tests for Reflection Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.reflection import ReflectionAgent
from ortobahn.models import ReflectionReport

VALID_REFLECTION_JSON = json.dumps(
    {
        "confidence_accuracy": 0.15,
        "confidence_bias": "overconfident",
        "strategy_effectiveness": {
            "AI trends": {"engagement_level": "high", "recommendation": "continue"},
        },
        "content_patterns": {
            "high_performers": [],
            "low_performers": [],
            "winning_attributes": ["contrarian"],
            "losing_attributes": ["generic"],
        },
        "new_memories": [],
        "recommendations": ["CEO: focus on AI trends"],
        "summary": "Test reflection summary",
    }
)


def _insert_published_post(db, post_id: str, confidence: float, client_id: str = "default", run_id: str = "run-1"):
    """Insert a published post with metrics for testing."""
    db.execute(
        """INSERT INTO posts (id, text, status, confidence, run_id, client_id, platform, published_at)
           VALUES (?, ?, 'published', ?, ?, ?, 'bluesky', CURRENT_TIMESTAMP)""",
        (post_id, f"Post {post_id}", confidence, run_id, client_id),
        commit=True,
    )


def _insert_metrics(db, post_id: str, likes: int = 0, reposts: int = 0, replies: int = 0):
    """Insert metrics for a given post."""
    import uuid

    db.execute(
        "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4())[:8], post_id, likes, reposts, replies),
        commit=True,
    )


class TestReflectionAgent:
    def test_empty_report_when_no_posts(self, test_db):
        agent = ReflectionAgent(db=test_db, api_key="sk-ant-test")
        report = agent.run(run_id="run-1", client_id="default")

        assert isinstance(report, ReflectionReport)
        assert report.confidence_accuracy == 0.0
        assert report.confidence_bias == "neutral"
        assert report.strategy_effectiveness == {}
        assert report.content_patterns is None
        assert report.ab_test_updates == []
        assert report.goal_progress == []
        assert report.new_memories == []
        assert report.recommendations == []
        assert "No published posts" in report.summary

    def test_calibration_computation(self, test_db):
        """Test _compute_calibration with posts that have known confidence and engagement."""
        agent = ReflectionAgent(db=test_db, api_key="sk-ant-test")

        # Create 4 posts with known confidence and varying engagement.
        # Post A: confidence=0.9, engagement=1 (low)  -> actual percentile = 0.125 -> error = 0.775
        # Post B: confidence=0.8, engagement=3 (mid-low) -> actual percentile = 0.375 -> error = 0.425
        # Post C: confidence=0.3, engagement=5 (mid-high) -> actual percentile = 0.625 -> error = -0.325
        # Post D: confidence=0.2, engagement=10 (high) -> actual percentile = 0.875 -> error = -0.675
        posts = [
            {"id": "a", "confidence": 0.9, "like_count": 1, "repost_count": 0, "reply_count": 0},
            {"id": "b", "confidence": 0.8, "like_count": 2, "repost_count": 1, "reply_count": 0},
            {"id": "c", "confidence": 0.3, "like_count": 3, "repost_count": 1, "reply_count": 1},
            {"id": "d", "confidence": 0.2, "like_count": 5, "repost_count": 3, "reply_count": 2},
        ]

        calibration = agent._compute_calibration(posts, run_id="run-1", client_id="default")

        assert calibration["sample_size"] == 4
        assert calibration["bias"] == "neutral"  # mean_error = (0.775 + 0.425 - 0.325 - 0.675) / 4 = 0.05
        assert len(calibration["details"]) == 4

        # The mean absolute error should be (0.775 + 0.425 + 0.325 + 0.675) / 4 = 0.55
        assert abs(calibration["mean_absolute_error"] - 0.55) < 0.01

    def test_calibration_overconfident_bias(self, test_db):
        """Test that high confidence with low engagement yields overconfident bias."""
        agent = ReflectionAgent(db=test_db, api_key="sk-ant-test")

        # All posts have high confidence but sorted engagement creates low percentiles
        # for the high-confidence ones, resulting in positive mean_error > 0.1
        posts = [
            {"id": "a", "confidence": 0.95, "like_count": 1, "repost_count": 0, "reply_count": 0},
            {"id": "b", "confidence": 0.90, "like_count": 2, "repost_count": 0, "reply_count": 0},
            {"id": "c", "confidence": 0.85, "like_count": 3, "repost_count": 0, "reply_count": 0},
        ]

        calibration = agent._compute_calibration(posts, run_id="run-1", client_id="default")
        # Sorted by engagement: a(1), b(2), c(3)
        # Percentiles: a=0.167, b=0.5, c=0.833
        # Errors: 0.95-0.167=0.783, 0.90-0.5=0.4, 0.85-0.833=0.017
        # Mean error = (0.783 + 0.4 + 0.017) / 3 = 0.4 > 0.1
        assert calibration["bias"] == "overconfident"

    def test_calibration_fewer_than_two_posts(self, test_db):
        """With fewer than 2 scored posts, calibration returns neutral defaults."""
        agent = ReflectionAgent(db=test_db, api_key="sk-ant-test")

        posts = [{"id": "a", "confidence": 0.9, "like_count": 5, "repost_count": 0, "reply_count": 0}]
        calibration = agent._compute_calibration(posts, run_id="run-1", client_id="default")

        assert calibration["mean_absolute_error"] == 0.0
        assert calibration["bias"] == "neutral"
        assert calibration["sample_size"] == 1
        assert calibration["details"] == []

    def test_run_with_posts(self, test_db, mock_llm_response):
        """Full run with posts: mocks call_llm, verifies report structure and calibration overlay."""
        agent = ReflectionAgent(db=test_db, api_key="sk-ant-test")

        # Insert published posts with metrics so get_recent_posts_with_metrics returns data
        _insert_published_post(test_db, "p1", confidence=0.9, client_id="default")
        _insert_metrics(test_db, "p1", likes=2, reposts=0, replies=0)

        _insert_published_post(test_db, "p2", confidence=0.5, client_id="default")
        _insert_metrics(test_db, "p2", likes=10, reposts=3, replies=2)

        _insert_published_post(test_db, "p3", confidence=0.7, client_id="default")
        _insert_metrics(test_db, "p3", likes=5, reposts=1, replies=1)

        fake = mock_llm_response(text=VALID_REFLECTION_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            report = agent.run(run_id="run-1", client_id="default")

        assert isinstance(report, ReflectionReport)

        # The calibration is computed from actual posts, then overlaid onto the LLM report.
        # So confidence_accuracy and confidence_bias come from _compute_calibration, NOT the LLM JSON.
        assert report.confidence_accuracy != 0.15  # overridden by real calibration
        assert report.confidence_accuracy > 0  # should have a real value from 3 posts

        # LLM-provided fields should be present
        assert report.strategy_effectiveness == {
            "AI trends": {"engagement_level": "high", "recommendation": "continue"},
        }
        assert report.content_patterns is not None
        assert report.content_patterns.winning_attributes == ["contrarian"]
        assert report.content_patterns.losing_attributes == ["generic"]
        assert report.recommendations == ["CEO: focus on AI trends"]
        assert report.summary == "Test reflection summary"
