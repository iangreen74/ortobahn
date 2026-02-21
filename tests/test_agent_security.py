"""Tests for Security Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.security import SecurityAgent
from ortobahn.models import SecurityReport

VALID_SECURITY_JSON = json.dumps(
    {
        "threat_level": "medium",
        "threats_detected": [
            {
                "threat_type": "path_scan",
                "severity": "warning",
                "source_ip": "1.2.3.4",
                "details": "Multiple .env probe attempts",
                "count": 5,
            }
        ],
        "recommendations": [
            {
                "area": "waf",
                "priority": "high",
                "recommendation": "Enable AWS WAF with managed rule groups",
            }
        ],
        "actions_taken": ["Cleaned up old access logs"],
        "credential_health": {"bluesky": "configured (2 clients)", "twitter": "no credentials stored"},
        "summary": "Medium threat level due to .env probing. WAF recommended.",
    }
)


class TestSecurityAgent:
    def test_returns_security_report(self, test_db, mock_llm_response):
        agent = SecurityAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_SECURITY_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1")

        assert isinstance(result, SecurityReport)
        assert result.threat_level == "medium"
        assert len(result.threats_detected) == 1
        assert result.threats_detected[0].threat_type == "path_scan"

    def test_fallback_on_bad_json(self, test_db, mock_llm_response):
        agent = SecurityAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text="This is not JSON at all")

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1")

        assert isinstance(result, SecurityReport)
        assert result.threat_level in ("low", "medium")

    def test_logs_decision(self, test_db, mock_llm_response):
        agent = SecurityAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_SECURITY_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(run_id="run-1")

        logs = test_db.get_recent_agent_logs(limit=5)
        assert any(log["agent_name"] == "security" for log in logs)

    def test_credential_health_populated(self, test_db, mock_llm_response):
        agent = SecurityAgent(db=test_db, api_key="sk-ant-test")
        # Return report without credential_health so the agent populates it
        minimal_json = json.dumps({"threat_level": "low", "summary": "All clear"})
        fake = mock_llm_response(text=minimal_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1")

        assert isinstance(result.credential_health, dict)
