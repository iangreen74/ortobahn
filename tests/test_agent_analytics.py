"""Tests for Analytics Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.analytics import AnalyticsAgent
from ortobahn.models import AnalyticsReport

VALID_ANALYTICS_JSON = json.dumps(
    {
        "top_themes": ["AI autonomy"],
        "summary": "Performance is growing. AI-related posts perform best.",
        "recommendations": ["Post more about AI agents", "Try question-format posts"],
    }
)


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
