"""Tests for CEO Agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from ortobahn.agents.ceo import CEOAgent
from ortobahn.models import AnalyticsReport, Strategy

VALID_STRATEGY_JSON = json.dumps(
    {
        "themes": ["AI autonomy", "tech culture", "startup life"],
        "tone": "authoritative but approachable",
        "goals": ["grow followers", "spark discussions"],
        "content_guidelines": "Be specific, avoid generic takes",
        "posting_frequency": "3-4 posts per day",
        "valid_until": (datetime.utcnow() + timedelta(days=7)).isoformat(),
    }
)


class TestCEOAgent:
    def test_creates_strategy_first_run(self, test_db, mock_llm_response):
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_STRATEGY_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", analytics_report=AnalyticsReport(), trending=[])

        assert isinstance(result, Strategy)
        assert len(result.themes) == 3
        assert test_db.get_active_strategy() is not None

    def test_reuses_active_strategy(self, test_db):
        valid_until = (datetime.utcnow() + timedelta(days=7)).isoformat()
        test_db.save_strategy(
            {
                "themes": ["existing"],
                "tone": "existing",
                "goals": ["existing"],
                "content_guidelines": "existing",
                "posting_frequency": "daily",
                "valid_until": valid_until,
            },
            run_id="prev",
        )

        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        # Should not call LLM at all
        result = agent.run(run_id="run-2")

        assert result.themes == ["existing"]

    def test_saves_to_db(self, test_db, mock_llm_response):
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_STRATEGY_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(run_id="run-1")

        saved = test_db.get_active_strategy()
        assert saved is not None
        assert "AI autonomy" in saved["themes"]

        logs = test_db.get_recent_agent_logs(limit=5)
        assert any(log["agent_name"] == "ceo" for log in logs)
