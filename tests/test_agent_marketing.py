"""Tests for Marketing Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.marketing import MarketingAgent


class TestMarketingAgent:
    def test_empty_report_with_no_data(self, test_db):
        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0  # disable for tests

        llm_resp_text = json.dumps(
            {
                "content_ideas": [],
                "draft_posts": [],
                "metrics_highlights": ["No posts yet"],
                "recommendations": ["Start publishing"],
                "summary": "Platform is new, no data to market yet",
            }
        )
        from ortobahn.llm import LLMResponse

        llm_resp = LLMResponse(text=llm_resp_text, input_tokens=100, output_tokens=200, model="test")

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.summary == "Platform is new, no data to market yet"
        assert len(report.content_ideas) == 0

    def test_report_with_data(self, test_db, mock_llm_response):
        # Seed some pipeline data
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=3, total_input_tokens=1000, total_output_tokens=500)

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [
                        {
                            "angle": "Proof of autonomy",
                            "hook": "We published 3 posts without a human touching anything",
                            "target_platform": "bluesky",
                            "content_type": "social_post",
                        },
                    ],
                    "draft_posts": [
                        "Ortobahn just published 3 posts autonomously. No human reviewed, edited, or scheduled them."
                    ],
                    "metrics_highlights": ["3 posts published", "100% success rate"],
                    "recommendations": ["Highlight autonomous publishing angle"],
                    "summary": "Strong proof point with real published posts",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.content_ideas) == 1
        assert report.content_ideas[0].angle == "Proof of autonomy"
        assert len(report.draft_posts) == 1
        assert len(report.metrics_highlights) == 2

    def test_handles_bad_llm_response(self, test_db, mock_llm_response):
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response("not valid json")

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert "not valid json" in report.summary
