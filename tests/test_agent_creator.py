"""Tests for Creator Agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from ortobahn.agents.creator import CreatorAgent
from ortobahn.models import ContentPlan, ContentType, DraftPosts, Platform, PostIdea, PostType, Strategy

VALID_DRAFTS_JSON = json.dumps(
    {
        "posts": [
            {
                "text": "Your AI agent is only as good as your worst edge case. Most teams learn this at 3am.",
                "source_idea": "AI agents in production",
                "reasoning": "Relatable pain point for developers",
                "confidence": 0.92,
                "platform": "twitter",
                "content_type": "social_post",
            },
            {
                "text": "Open weights are table stakes. The real moat is the community that builds on top.",
                "source_idea": "Open source AI",
                "reasoning": "Contrarian take on open source",
                "confidence": 0.85,
                "platform": "linkedin",
                "content_type": "social_post",
            },
        ]
    }
)


def _make_plan():
    return ContentPlan(
        posts=[
            PostIdea(topic="AI agents", angle="edge cases", hook="3am", content_type=PostType.HOT_TAKE, priority=1),
        ]
    )


def _make_strategy():
    return Strategy(
        themes=["AI"],
        tone="bold",
        goals=["grow"],
        content_guidelines="be real",
        posting_frequency="3x/day",
        valid_until=datetime.utcnow() + timedelta(days=7),
    )


class TestCreatorAgent:
    def test_creates_drafts(self, test_db, mock_llm_response):
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_DRAFTS_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", content_plan=_make_plan(), strategy=_make_strategy())

        assert isinstance(result, DraftPosts)
        assert len(result.posts) == 2
        assert result.posts[0].platform == Platform.TWITTER
        assert result.posts[1].platform == Platform.LINKEDIN

    def test_truncates_long_twitter_posts(self, test_db, mock_llm_response):
        long_json = json.dumps(
            {
                "posts": [
                    {
                        "text": "x" * 290,
                        "source_idea": "test",
                        "reasoning": "test",
                        "confidence": 0.9,
                        "platform": "twitter",
                        "content_type": "social_post",
                    }
                ]
            }
        )
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=long_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(
                run_id="run-1",
                content_plan=_make_plan(),
                strategy=_make_strategy(),
                target_platforms=[Platform.TWITTER],
            )

        assert len(result.posts[0].text) <= 280
        assert result.posts[0].confidence <= 0.5

    def test_truncates_long_generic_posts(self, test_db, mock_llm_response):
        long_json = json.dumps(
            {
                "posts": [
                    {
                        "text": "x" * 510,
                        "source_idea": "test",
                        "reasoning": "test",
                        "confidence": 0.9,
                        "platform": "generic",
                        "content_type": "social_post",
                    }
                ]
            }
        )
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=long_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", content_plan=_make_plan(), strategy=_make_strategy())

        assert len(result.posts[0].text) <= 500
        assert result.posts[0].confidence <= 0.5

    def test_ad_headline_truncation(self, test_db, mock_llm_response):
        long_json = json.dumps(
            {
                "posts": [
                    {
                        "text": "This headline is way too long for Google Ads",
                        "source_idea": "test",
                        "reasoning": "test",
                        "confidence": 0.9,
                        "platform": "google_ads",
                        "content_type": "ad_headline",
                    }
                ]
            }
        )
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=long_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(
                run_id="run-1",
                content_plan=_make_plan(),
                strategy=_make_strategy(),
                target_platforms=[Platform.GOOGLE_ADS],
            )

        assert len(result.posts[0].text) <= 30
        assert result.posts[0].content_type == ContentType.AD_HEADLINE

    def test_ad_description_limit(self, test_db, mock_llm_response):
        long_json = json.dumps(
            {
                "posts": [
                    {
                        "text": "x" * 100,
                        "source_idea": "test",
                        "reasoning": "test",
                        "confidence": 0.9,
                        "platform": "google_ads",
                        "content_type": "ad_description",
                    }
                ]
            }
        )
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=long_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(
                run_id="run-1",
                content_plan=_make_plan(),
                strategy=_make_strategy(),
                target_platforms=[Platform.GOOGLE_ADS],
            )

        assert len(result.posts[0].text) <= 90

    def test_linkedin_allows_long_text(self, test_db, mock_llm_response):
        long_json = json.dumps(
            {
                "posts": [
                    {
                        "text": "x" * 2000,
                        "source_idea": "test",
                        "reasoning": "test",
                        "confidence": 0.9,
                        "platform": "linkedin",
                        "content_type": "social_post",
                    }
                ]
            }
        )
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=long_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(
                run_id="run-1",
                content_plan=_make_plan(),
                strategy=_make_strategy(),
                target_platforms=[Platform.LINKEDIN],
            )

        # 2000 chars is within LinkedIn's 3000 limit, should not truncate
        assert len(result.posts[0].text) == 2000
        assert result.posts[0].confidence == 0.9

    def test_client_context_passed(self, test_db, mock_llm_response):
        from ortobahn.models import Client

        client = Client(
            id="vs",
            name="Vaultscaler",
            description="Autonomous engineering",
            brand_voice="direct",
            target_audience="CTOs",
        )
        agent = CreatorAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_DRAFTS_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(
                run_id="run-1",
                content_plan=_make_plan(),
                strategy=_make_strategy(),
                client=client,
                target_platforms=[Platform.TWITTER],
                enable_self_critique=False,
            )

        # Verify the system prompt was parameterized with client context
        call_args = mock_call.call_args
        system_prompt = call_args.kwargs.get("system_prompt") or call_args[1].get("system_prompt") or call_args[0][0]
        assert "Vaultscaler" in system_prompt
