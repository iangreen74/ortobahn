"""Tests for CEO Agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from ortobahn.agents.ceo import CEOAgent
from ortobahn.memory import MemoryStore
from ortobahn.models import (
    AgentMemory,
    AnalyticsReport,
    CEOReport,
    MemoryCategory,
    MemoryType,
    ReflectionReport,
)

VALID_CEO_REPORT_JSON = json.dumps(
    {
        "strategy": {
            "themes": ["AI autonomy", "tech culture", "startup life"],
            "tone": "authoritative but approachable",
            "goals": ["grow followers", "spark discussions"],
            "content_guidelines": "Be specific, avoid generic takes",
            "posting_frequency": "3-4 posts per day",
            "valid_until": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        },
        "directives": [
            {
                "priority": "high",
                "category": "legal",
                "directive": "Generate Terms of Service",
                "target_agent": "legal",
                "reasoning": "No legal documents exist yet",
            }
        ],
        "business_assessment": "Strong growth trajectory with healthy engagement metrics",
        "risk_flags": ["Missing legal documents"],
    }
)

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
    def test_creates_ceo_report_first_run(self, test_db, mock_llm_response):
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_CEO_REPORT_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", analytics_report=AnalyticsReport(), trending=[])

        assert isinstance(result, CEOReport)
        assert len(result.strategy.themes) == 3
        assert len(result.directives) == 1
        assert result.directives[0].category == "legal"
        assert result.business_assessment != ""
        assert test_db.get_active_strategy() is not None

    def test_backward_compat_plain_strategy(self, test_db, mock_llm_response):
        """If LLM returns plain Strategy JSON, it should be wrapped in CEOReport."""
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_STRATEGY_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", analytics_report=AnalyticsReport(), trending=[])

        assert isinstance(result, CEOReport)
        assert len(result.strategy.themes) == 3
        assert len(result.directives) == 0  # No directives from plain strategy

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

        assert isinstance(result, CEOReport)
        assert result.strategy.themes == ["existing"]

    def test_saves_to_db(self, test_db, mock_llm_response):
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_CEO_REPORT_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(run_id="run-1")

        saved = test_db.get_active_strategy()
        assert saved is not None
        assert "AI autonomy" in saved["themes"]

        logs = test_db.get_recent_agent_logs(limit=5)
        assert any(log["agent_name"] == "ceo" for log in logs)

    def test_fallback_on_bad_json(self, test_db, mock_llm_response):
        """If LLM returns garbage, a minimal fallback report should be returned."""
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text="This is completely not JSON")

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1")

        assert isinstance(result, CEOReport)
        assert result.strategy.themes == ["general marketing"]

    def test_ceo_receives_content_pattern_memories(self, test_db, mock_llm_response):
        """When creator has CONTENT_PATTERN memories, they appear in the CEO prompt."""
        store = MemoryStore(test_db)
        store.remember(
            AgentMemory(
                agent_name="creator",
                client_id="default",
                memory_type=MemoryType.OBSERVATION,
                category=MemoryCategory.CONTENT_PATTERN,
                content={"summary": "Short posts get 3x more engagement"},
                confidence=0.8,
                source_run_id="prev-run",
            )
        )

        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_CEO_REPORT_JSON)

        captured_messages: list[str] = []
        original_call_llm = agent.call_llm

        def _capture_call(user_message, system_prompt=None):
            captured_messages.append(user_message)
            return original_call_llm(user_message, system_prompt=system_prompt)

        with patch.object(agent, "call_llm", side_effect=_capture_call):
            with patch("ortobahn.agents.base.call_llm", return_value=fake):
                agent.run(run_id="run-1")

        assert len(captured_messages) == 1
        assert "Content Patterns" in captured_messages[0]
        assert "Short posts get 3x more engagement" in captured_messages[0]

    def test_ceo_calibration_alert_overconfident(self, test_db, mock_llm_response):
        """When reflection_report says overconfident, prompt includes narrowing advice."""
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_CEO_REPORT_JSON)

        reflection = ReflectionReport(
            confidence_accuracy=0.3,
            confidence_bias="overconfident",
        )

        captured_messages: list[str] = []
        original_call_llm = agent.call_llm

        def _capture_call(user_message, system_prompt=None):
            captured_messages.append(user_message)
            return original_call_llm(user_message, system_prompt=system_prompt)

        with patch.object(agent, "call_llm", side_effect=_capture_call):
            with patch("ortobahn.agents.base.call_llm", return_value=fake):
                agent.run(run_id="run-1", reflection_report=reflection)

        assert len(captured_messages) == 1
        assert "OVERCONFIDENT" in captured_messages[0]
        assert "narrowing" in captured_messages[0].lower() or "proven themes" in captured_messages[0].lower()

    def test_ceo_calibration_alert_underconfident(self, test_db, mock_llm_response):
        """When reflection_report says underconfident, prompt includes broadening advice."""
        agent = CEOAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_CEO_REPORT_JSON)

        reflection = ReflectionReport(
            confidence_accuracy=0.3,
            confidence_bias="underconfident",
        )

        captured_messages: list[str] = []
        original_call_llm = agent.call_llm

        def _capture_call(user_message, system_prompt=None):
            captured_messages.append(user_message)
            return original_call_llm(user_message, system_prompt=system_prompt)

        with patch.object(agent, "call_llm", side_effect=_capture_call):
            with patch("ortobahn.agents.base.call_llm", return_value=fake):
                agent.run(run_id="run-1", reflection_report=reflection)

        assert len(captured_messages) == 1
        assert "UNDERCONFIDENT" in captured_messages[0]
        assert "broadening" in captured_messages[0].lower() or "untapped" in captured_messages[0].lower()
