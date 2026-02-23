"""Tests for ArticleWriterAgent."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ortobahn.models import DraftArticle, Strategy


@pytest.fixture
def article_agent(test_db):
    from ortobahn.agents.article_writer import ArticleWriterAgent

    return ArticleWriterAgent(db=test_db, api_key="sk-ant-test")


@pytest.fixture
def strategy():
    from datetime import datetime, timedelta, timezone

    return Strategy(
        themes=["AI", "automation"],
        tone="professional",
        goals=["thought leadership"],
        content_guidelines="Write insightful articles",
        posting_frequency="weekly",
        valid_until=datetime.now(timezone.utc) + timedelta(days=7),
    )


@pytest.fixture
def mock_article_response():
    return json.dumps(
        {
            "title": "The Future of AI in Marketing",
            "subtitle": "How autonomous systems are reshaping content strategy",
            "body_markdown": "## Introduction\n\nAI is transforming marketing...\n\n## Key Trends\n\nHere are the trends...",
            "tags": ["AI", "marketing", "automation"],
            "meta_description": "Discover how AI-powered marketing automation is changing content strategy for modern businesses.",
            "topic_used": "AI in marketing",
            "confidence": 0.85,
            "word_count": 1500,
        }
    )


class TestArticleWriterAgent:
    def test_agent_attributes(self, article_agent):
        assert article_agent.name == "article_writer"
        assert article_agent.prompt_file == "article_writer.txt"
        assert article_agent.thinking_budget == 16_000
        assert article_agent.max_tokens == 8192

    @patch("ortobahn.agents.base.call_llm")
    def test_run_returns_draft_article(self, mock_call, article_agent, strategy, mock_article_response):
        from ortobahn.llm import LLMResponse

        mock_call.return_value = LLMResponse(
            text=mock_article_response,
            input_tokens=500,
            output_tokens=2000,
            model="claude-sonnet-4-5-20250929",
        )

        result = article_agent.run(run_id="test-run", strategy=strategy)

        assert isinstance(result, DraftArticle)
        assert result.title == "The Future of AI in Marketing"
        assert result.confidence == 0.85
        assert result.word_count == 1500
        assert "AI" in result.tags
        assert result.topic_used == "AI in marketing"

    @patch("ortobahn.agents.base.call_llm")
    def test_run_with_client(self, mock_call, article_agent, strategy, mock_article_response):
        from ortobahn.llm import LLMResponse
        from ortobahn.models import Client

        mock_call.return_value = LLMResponse(
            text=mock_article_response,
            input_tokens=500,
            output_tokens=2000,
            model="claude-sonnet-4-5-20250929",
        )

        client = Client(
            id="test-client",
            name="Test Corp",
            brand_voice="authoritative",
            target_audience="enterprise buyers",
            products="SaaS platform",
            content_pillars="AI, cloud, automation",
        )

        result = article_agent.run(run_id="test-run", strategy=strategy, client=client)
        assert isinstance(result, DraftArticle)
        assert result.title == "The Future of AI in Marketing"

    @patch("ortobahn.agents.base.call_llm")
    def test_run_with_recent_articles(self, mock_call, article_agent, strategy, mock_article_response):
        from ortobahn.llm import LLMResponse

        mock_call.return_value = LLMResponse(
            text=mock_article_response,
            input_tokens=500,
            output_tokens=2000,
            model="claude-sonnet-4-5-20250929",
        )

        recent = [
            {"topic_used": "cloud computing", "title": "Cloud article"},
            {"topic_used": "DevOps", "title": "DevOps article"},
        ]

        result = article_agent.run(run_id="test-run", strategy=strategy, recent_articles=recent)
        assert isinstance(result, DraftArticle)

        assert isinstance(result, DraftArticle)
        mock_call.assert_called_once()

    @patch("ortobahn.agents.base.call_llm")
    def test_run_with_top_social_posts(self, mock_call, article_agent, strategy, mock_article_response):
        from ortobahn.llm import LLMResponse

        mock_call.return_value = LLMResponse(
            text=mock_article_response,
            input_tokens=500,
            output_tokens=2000,
            model="claude-sonnet-4-5-20250929",
        )

        top_posts = [
            {"text": "AI is changing everything", "like_count": 50, "repost_count": 10, "reply_count": 5},
        ]

        result = article_agent.run(run_id="test-run", strategy=strategy, top_social_posts=top_posts)
        assert isinstance(result, DraftArticle)

    @patch("ortobahn.agents.base.call_llm")
    def test_word_count_fallback(self, mock_call, article_agent, strategy):
        """If LLM returns word_count=0, agent should calculate it from body."""
        from ortobahn.llm import LLMResponse

        response_data = {
            "title": "Test",
            "body_markdown": "Word " * 500,
            "tags": [],
            "meta_description": "test",
            "topic_used": "test",
            "confidence": 0.8,
            "word_count": 0,
        }
        mock_call.return_value = LLMResponse(
            text=json.dumps(response_data),
            input_tokens=100,
            output_tokens=500,
            model="claude-sonnet-4-5-20250929",
        )

        result = article_agent.run(run_id="test-run", strategy=strategy)
        assert result.word_count == 500

    def test_logs_decision(self, article_agent, strategy, mock_article_response, test_db):
        with patch("ortobahn.agents.base.call_llm") as mock_call:
            from ortobahn.llm import LLMResponse

            mock_call.return_value = LLMResponse(
                text=mock_article_response,
                input_tokens=500,
                output_tokens=2000,
                model="claude-sonnet-4-5-20250929",
            )

            article_agent.run(run_id="log-test-run", strategy=strategy)

            logs = test_db.get_recent_agent_logs(limit=5)
            article_logs = [entry for entry in logs if entry["agent_name"] == "article_writer"]
            assert len(article_logs) >= 1
