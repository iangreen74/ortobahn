"""Tests for Strategist Agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from ortobahn.agents.strategist import StrategistAgent
from ortobahn.models import ContentPlan, Strategy, TrendingTopic

VALID_PLAN_JSON = json.dumps(
    {
        "posts": [
            {
                "topic": "AI agents in production",
                "angle": "most companies aren't ready",
                "hook": "Your AI agent is only as good as your error handling",
                "content_type": "hot_take",
                "priority": 1,
                "trending_source": "rss",
            },
            {
                "topic": "Open source AI",
                "angle": "the real moat is community",
                "hook": "Open weights aren't enough",
                "content_type": "insight",
                "priority": 2,
                "trending_source": None,
            },
        ]
    }
)


def _make_strategy():
    return Strategy(
        themes=["AI", "tech"],
        tone="bold",
        goals=["grow"],
        content_guidelines="be real",
        posting_frequency="3x/day",
        valid_until=datetime.utcnow() + timedelta(days=7),
    )


class TestStrategistAgent:
    def test_creates_content_plan(self, test_db, mock_llm_response):
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(
                run_id="run-1",
                strategy=_make_strategy(),
                trending=[TrendingTopic(title="AI news", source="rss")],
            )

        assert isinstance(result, ContentPlan)
        assert len(result.posts) == 2
        # Should be sorted by priority
        assert result.posts[0].priority <= result.posts[1].priority

    def test_works_without_trends(self, test_db, mock_llm_response):
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy(), trending=[])

        assert isinstance(result, ContentPlan)
