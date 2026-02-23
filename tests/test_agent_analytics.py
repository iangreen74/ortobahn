"""Tests for Analytics Agent."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.analytics import AnalyticsAgent
from ortobahn.models import AnalyticsReport

VALID_ANALYTICS_JSON = json.dumps(
    {
        "top_themes": ["AI autonomy"],
        "summary": "Performance is growing. AI-related posts perform best.",
        "recommendations": ["Post more about AI agents", "Try question-format posts"],
    }
)


def _setup_published_post(db, text="Test post", likes=5, reposts=2, replies=1, platform="bluesky"):
    """Helper to create a published post with metrics in the test DB."""
    pid = db.save_post(text=text, run_id="r1", status="published", platform=platform)
    db.update_post_published(pid, f"at://test/{pid[:8]}", f"bafy{pid[:8]}")
    db.save_metrics(pid, like_count=likes, repost_count=reposts, reply_count=replies)
    return pid


class TestAnalyticsAgent:
    def test_empty_report_first_run(self, test_db, mock_bluesky_client):
        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        result = agent.run(run_id="run-1")

        assert isinstance(result, AnalyticsReport)
        assert result.total_posts == 0
        assert result.summary == "No data yet."

    def test_generates_report_with_posts(self, test_db, mock_bluesky_client, mock_llm_response):
        # Set up a published post with metrics
        pid = test_db.save_post(text="Test post", run_id="r1", status="published")
        test_db.update_post_published(pid, "at://test/1", "bafy1")
        test_db.save_metrics(pid, like_count=5, repost_count=2, reply_count=1)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-2")

        assert result.total_posts >= 1
        assert "AI autonomy" in result.top_themes
        assert len(result.recommendations) == 2

    def test_handles_bad_llm_json(self, test_db, mock_bluesky_client, mock_llm_response):
        pid = test_db.save_post(text="Test", run_id="r1", status="published")
        test_db.update_post_published(pid, "at://test/1", "bafy1")
        test_db.save_metrics(pid, like_count=1)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text="This is not JSON at all, just text analysis")

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-3")

        # Should fallback to raw text
        assert isinstance(result, AnalyticsReport)
        assert "not JSON" in result.summary or len(result.summary) > 0

    # --- Empty / null data handling ---

    def test_empty_report_no_platform_clients(self, test_db):
        """No platform clients at all should still return an empty report."""
        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test")
        result = agent.run(run_id="run-empty")

        assert isinstance(result, AnalyticsReport)
        assert result.total_posts == 0
        assert result.total_likes == 0
        assert result.avg_engagement_per_post == 0.0

    def test_empty_report_logs_decision(self, test_db, mock_bluesky_client):
        """Empty report (first run) should still log a decision."""
        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        agent.run(run_id="run-log-empty")

        logs = test_db.fetchall(
            "SELECT * FROM agent_logs WHERE run_id=? AND agent_name=?",
            ("run-log-empty", "analytics"),
        )
        assert len(logs) == 1
        assert "No posts" in logs[0]["input_summary"]

    def test_posts_with_zero_metrics(self, test_db, mock_bluesky_client, mock_llm_response):
        """Posts with zero engagement should produce a valid report with zero averages."""
        _setup_published_post(test_db, text="Zero engagement post", likes=0, reposts=0, replies=0)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(agent, "_refresh_metrics"),
        ):
            result = agent.run(run_id="run-zero")

        assert result.total_posts == 1
        assert result.total_likes == 0
        assert result.total_reposts == 0
        assert result.total_replies == 0
        assert result.avg_engagement_per_post == 0.0

    # --- Engagement calculation edge cases ---

    def test_avg_engagement_calculation(self, test_db, mock_bluesky_client, mock_llm_response):
        """Average engagement should be (total likes + reposts + replies) / post count."""
        _setup_published_post(test_db, text="Post A", likes=10, reposts=5, replies=3)
        _setup_published_post(test_db, text="Post B", likes=4, reposts=2, replies=0)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(agent, "_refresh_metrics"),
        ):
            result = agent.run(run_id="run-avg")

        assert result.total_posts == 2
        assert result.total_likes == 14
        assert result.total_reposts == 7
        assert result.total_replies == 3
        # (14 + 7 + 3) / 2 = 12.0
        assert result.avg_engagement_per_post == 12.0

    def test_best_and_worst_post_identified(self, test_db, mock_bluesky_client, mock_llm_response):
        """Report should identify the best and worst performing posts."""
        _setup_published_post(test_db, text="Best post ever", likes=50, reposts=20, replies=10)
        _setup_published_post(test_db, text="Worst post ever", likes=0, reposts=0, replies=0)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(agent, "_refresh_metrics"),
        ):
            result = agent.run(run_id="run-bestworst")

        assert result.best_post is not None
        assert result.worst_post is not None
        assert result.best_post.total_engagement == 80
        assert result.worst_post.total_engagement == 0
        assert "Best post ever" in result.best_post.text

    def test_single_post_is_both_best_and_worst(self, test_db, mock_bluesky_client, mock_llm_response):
        """When there is only one post, it should be both best and worst."""
        _setup_published_post(test_db, text="Only post", likes=7, reposts=3, replies=1)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-single")

        assert result.best_post is not None
        assert result.worst_post is not None
        assert result.best_post.total_engagement == result.worst_post.total_engagement

    # --- LLM response parsing ---

    def test_llm_json_with_markdown_fences(self, test_db, mock_bluesky_client, mock_llm_response):
        """LLM wrapping JSON in ```json fences should still parse correctly."""
        fenced = f"```json\n{VALID_ANALYTICS_JSON}\n```"
        _setup_published_post(test_db)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=fenced)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-fenced")

        assert "AI autonomy" in result.top_themes

    def test_llm_json_partial_fields(self, test_db, mock_bluesky_client, mock_llm_response):
        """LLM returning JSON with only some fields should merge what is available."""
        partial_json = json.dumps({"summary": "Things look good.", "top_themes": ["automation"]})
        _setup_published_post(test_db)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=partial_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-partial")

        assert result.summary == "Things look good."
        assert "automation" in result.top_themes
        # recommendations should remain default (empty list) since not provided
        assert result.recommendations == []

    def test_llm_summary_truncated_to_500(self, test_db, mock_bluesky_client, mock_llm_response):
        """When LLM returns non-JSON, fallback summary should be truncated to 500 chars."""
        long_text = "x" * 1000
        _setup_published_post(test_db)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=long_text)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-long")

        assert len(result.summary) <= 500

    # --- _refresh_metrics ---

    def test_refresh_metrics_called_with_bluesky(self, test_db, mock_bluesky_client, mock_llm_response):
        """When bluesky client exists and posts > 0, metrics should be refreshed."""
        _setup_published_post(test_db)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(agent, "_refresh_metrics") as mock_refresh,
        ):
            agent.run(run_id="run-refresh")

        mock_refresh.assert_called_once()

    def test_no_refresh_without_platform_clients(self, test_db, mock_llm_response):
        """Without any platform client, _refresh_metrics should not be called."""
        _setup_published_post(test_db)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(agent, "_refresh_metrics") as mock_refresh,
        ):
            agent.run(run_id="run-no-refresh")

        mock_refresh.assert_not_called()

    def test_refresh_skips_posts_without_uri(self, test_db, mock_bluesky_client):
        """Posts without a platform_uri or bluesky_uri should be skipped during refresh."""
        # Create a post with no URI set (save_post but don't call update_post_published)
        test_db.save_post(text="No URI post", run_id="r1", status="published", platform="bluesky")

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        # _refresh_metrics should not crash, and bluesky.get_post_metrics should not be called
        agent._refresh_metrics()

        mock_bluesky_client.get_post_metrics.assert_not_called()

    def test_refresh_handles_platform_api_error(self, test_db, mock_bluesky_client):
        """If platform API raises, _refresh_metrics should silently continue."""
        _setup_published_post(test_db, platform="bluesky")
        mock_bluesky_client.get_post_metrics.side_effect = ConnectionError("API down")

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        # Should not raise
        agent._refresh_metrics()

    # --- Platform-specific analytics ---

    def test_twitter_metrics_refresh(self, test_db):
        """Twitter client metrics should be fetched when platform is twitter."""
        mock_twitter = MagicMock()
        mock_metrics = MagicMock()
        mock_metrics.like_count = 10
        mock_metrics.retweet_count = 3
        mock_metrics.reply_count = 2
        mock_twitter.get_post_metrics.return_value = mock_metrics

        pid = test_db.save_post(text="Twitter post", run_id="r1", status="published", platform="twitter")
        test_db.execute(
            "UPDATE posts SET platform_id=? WHERE id=?",
            ("tw-12345", pid),
            commit=True,
        )
        # Also set published_at so get_recent_published_posts returns it
        from datetime import datetime, timezone

        test_db.execute(
            "UPDATE posts SET published_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), pid),
            commit=True,
        )

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", twitter_client=mock_twitter)
        agent._refresh_metrics()

        mock_twitter.get_post_metrics.assert_called_once_with("tw-12345")

    def test_linkedin_metrics_refresh(self, test_db):
        """LinkedIn client metrics should be fetched when platform is linkedin."""
        mock_linkedin = MagicMock()
        mock_metrics = MagicMock()
        mock_metrics.like_count = 15
        mock_metrics.comment_count = 4
        mock_linkedin.get_post_metrics.return_value = mock_metrics

        pid = test_db.save_post(text="LinkedIn post", run_id="r1", status="published", platform="linkedin")
        test_db.execute(
            "UPDATE posts SET platform_id=? WHERE id=?",
            ("li-67890", pid),
            commit=True,
        )
        from datetime import datetime, timezone

        test_db.execute(
            "UPDATE posts SET published_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), pid),
            commit=True,
        )

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", linkedin_client=mock_linkedin)
        agent._refresh_metrics()

        mock_linkedin.get_post_metrics.assert_called_once_with("li-67890")

    # --- Decision logging ---

    def test_report_logs_decision_with_posts(self, test_db, mock_bluesky_client, mock_llm_response):
        """When posts exist, log_decision should record the analysis summary."""
        _setup_published_post(test_db, text="Logged post", likes=3, reposts=1, replies=0)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(run_id="run-log-posts")

        logs = test_db.fetchall(
            "SELECT * FROM agent_logs WHERE run_id=? AND agent_name=?",
            ("run-log-posts", "analytics"),
        )
        assert len(logs) == 1
        assert "posts analyzed" in logs[0]["input_summary"]
        assert "Avg engagement" in logs[0]["output_summary"]

    # --- Multiple posts, engagement variation ---

    def test_many_posts_aggregation(self, test_db, mock_bluesky_client, mock_llm_response):
        """Multiple posts should have their metrics aggregated correctly."""
        _setup_published_post(test_db, text="Post 1", likes=10, reposts=2, replies=1)
        _setup_published_post(test_db, text="Post 2", likes=20, reposts=5, replies=3)
        _setup_published_post(test_db, text="Post 3", likes=5, reposts=0, replies=0)

        agent = AnalyticsAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_ANALYTICS_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(agent, "_refresh_metrics"),
        ):
            result = agent.run(run_id="run-many")

        assert result.total_posts == 3
        assert result.total_likes == 35
        assert result.total_reposts == 7
        assert result.total_replies == 4
        # (35 + 7 + 4) / 3 = 15.33
        assert result.avg_engagement_per_post == pytest.approx(15.33, abs=0.01)
