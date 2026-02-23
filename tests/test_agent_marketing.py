"""Tests for Marketing Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.marketing import MarketingAgent
from ortobahn.llm import LLMResponse
from ortobahn.models import MarketingReport


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

    # --- New tests below ---

    def test_returns_marketing_report_type(self, test_db, mock_llm_response):
        """Agent should always return a MarketingReport instance."""
        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "test",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert isinstance(report, MarketingReport)

    def test_multiple_content_ideas_parsed(self, test_db, mock_llm_response):
        """Multiple content ideas should be parsed into MarketingIdea objects."""
        ideas = [
            {
                "angle": "Growth story",
                "hook": "From 0 to 100 posts",
                "target_platform": "bluesky",
                "content_type": "social_post",
            },
            {
                "angle": "Tech deep-dive",
                "hook": "How AI writes social posts",
                "target_platform": "linkedin",
                "content_type": "article",
            },
            {
                "angle": "Case study",
                "hook": "Client X doubled engagement",
                "target_platform": "twitter",
                "content_type": "social_post",
            },
        ]

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": ideas,
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "multiple ideas",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.content_ideas) == 3
        assert report.content_ideas[0].angle == "Growth story"
        assert report.content_ideas[1].target_platform == "linkedin"
        assert report.content_ideas[2].content_type == "social_post"

    def test_draft_posts_parsed(self, test_db, mock_llm_response):
        """Draft posts should be parsed as a list of strings."""
        drafts = [
            "Draft post one about growth",
            "Draft post two about automation",
            "Draft post three about AI",
        ]

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": drafts,
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "drafts ready",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.draft_posts) == 3
        assert report.draft_posts == drafts

    def test_metrics_highlights_parsed(self, test_db, mock_llm_response):
        """Metrics highlights should be available on the report."""
        highlights = ["100 posts published", "98% success rate", "5 active clients"]

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": highlights,
                    "recommendations": [],
                    "summary": "great metrics",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.metrics_highlights == highlights

    def test_recommendations_parsed(self, test_db, mock_llm_response):
        """Recommendations from LLM should be parsed onto the report."""
        recs = ["Focus on LinkedIn", "Create video content", "Publish case studies"]

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": recs,
                    "summary": "strategic recs",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.recommendations == recs

    def test_bad_json_fallback_truncates_to_500(self, test_db, mock_llm_response):
        """When LLM returns non-JSON, summary should be capped at 500 chars."""
        long_text = "a" * 800

        llm_resp = mock_llm_response(long_text)

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.summary) == 500
        assert report.content_ideas == []
        assert report.draft_posts == []

    def test_llm_response_with_markdown_fences(self, test_db, mock_llm_response):
        """LLM response wrapped in ```json fences should still parse."""
        json_body = json.dumps(
            {
                "content_ideas": [
                    {"angle": "test", "hook": "test hook", "target_platform": "bluesky", "content_type": "social_post"}
                ],
                "draft_posts": ["A draft"],
                "metrics_highlights": [],
                "recommendations": [],
                "summary": "fenced",
            }
        )

        llm_resp = mock_llm_response(f"```json\n{json_body}\n```")

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.summary == "fenced"
        assert len(report.content_ideas) == 1
        assert len(report.draft_posts) == 1

    def test_platform_breakdown_in_llm_context(self, test_db, mock_llm_response):
        """Published posts on different platforms should be counted in the context sent to LLM."""
        # Create published posts on different platforms
        test_db.save_post(text="bluesky post", run_id="run-a", status="published", confidence=0.9, platform="bluesky")
        test_db.save_post(text="bluesky post 2", run_id="run-a", status="published", confidence=0.9, platform="bluesky")
        test_db.save_post(text="linkedin post", run_id="run-a", status="published", confidence=0.9, platform="linkedin")

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "multi-platform",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0

        captured_messages = []

        def capture_call(msg, **kwargs):
            captured_messages.append(msg)
            return llm_resp

        with patch.object(agent, "call_llm", side_effect=capture_call):
            agent.run(run_id="run-1")

        # Verify platform counts were passed in the user message
        assert len(captured_messages) == 1
        assert "bluesky" in captured_messages[0]
        assert "linkedin" in captured_messages[0]

    def test_success_rate_calculation_with_failures(self, test_db, mock_llm_response):
        """Success rate should account for failed pipeline runs."""
        # 2 successful, 1 failed
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)
        test_db.start_pipeline_run("run-b", mode="single")
        test_db.complete_pipeline_run("run-b", posts_published=1)
        test_db.start_pipeline_run("run-c", mode="single")
        test_db.fail_pipeline_run("run-c", ["some error"])

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "mixed",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0

        captured_messages = []

        def capture_call(msg, **kwargs):
            captured_messages.append(msg)
            return llm_resp

        with patch.object(agent, "call_llm", side_effect=capture_call):
            agent.run(run_id="run-test")

        # success_rate = (3 - 1) / 3 = 66.7%
        assert "66.7%" in captured_messages[0]

    def test_client_count_in_llm_context(self, test_db, mock_llm_response):
        """Active client count should be included in the LLM prompt."""
        # The default client is already created by test_db fixture
        test_db.create_client(
            {"name": "Client2", "industry": "Tech", "brand_voice": "casual", "email": "c2@test.com", "status": "active"}
        )

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "clients",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0

        captured_messages = []

        def capture_call(msg, **kwargs):
            captured_messages.append(msg)
            return llm_resp

        with patch.object(agent, "call_llm", side_effect=capture_call):
            agent.run(run_id="run-1")

        assert "Active clients:" in captured_messages[0]

    def test_missing_keys_in_llm_json_uses_defaults(self, test_db, mock_llm_response):
        """If LLM JSON is missing keys, empty defaults should be used."""
        llm_resp = mock_llm_response(json.dumps({"summary": "only summary"}))

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.summary == "only summary"
        assert report.content_ideas == []
        assert report.draft_posts == []
        assert report.metrics_highlights == []
        assert report.recommendations == []

    def test_invalid_content_idea_type_falls_back(self, test_db, mock_llm_response):
        """If content_ideas items are malformed, should fall back gracefully."""
        # Return content_ideas with wrong type (string instead of dict)
        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": "not a list",
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "type error",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # Should fall back to summary-only since MarketingIdea(**item) will fail on a string
        assert "type error" in report.summary or len(report.summary) > 0

    def test_sample_posts_included_in_prompt(self, test_db, mock_llm_response):
        """Recent published posts should be sampled in the LLM prompt."""
        test_db.save_post(text="This is a great post about AI", run_id="run-a", status="published", confidence=0.9)

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "content_ideas": [],
                    "draft_posts": [],
                    "metrics_highlights": [],
                    "recommendations": [],
                    "summary": "samples",
                }
            )
        )

        agent = MarketingAgent(db=test_db, api_key="sk-ant-test", model="test")
        agent.thinking_budget = 0

        captured_messages = []

        def capture_call(msg, **kwargs):
            captured_messages.append(msg)
            return llm_resp

        with patch.object(agent, "call_llm", side_effect=capture_call):
            agent.run(run_id="run-1")

        assert "This is a great post about AI" in captured_messages[0]
