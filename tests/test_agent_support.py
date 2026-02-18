"""Tests for Support Agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ortobahn.agents.support import SupportAgent
from ortobahn.models import SupportReport


class TestSupportAgent:
    def test_support_agent_basic(self, test_db, mock_llm_response):
        """Mock the DB and LLM, verify the agent runs and returns a SupportReport."""
        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "tickets": [],
                    "health_summary": "All clients are healthy",
                    "at_risk_clients": [],
                    "recommendations": ["Continue monitoring"],
                }
            )
        )

        agent = SupportAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert isinstance(report, SupportReport)
        assert report.total_clients_checked >= 1  # default client exists
        assert report.health_summary == "All clients are healthy"
        assert len(report.tickets) == 0
        assert "Continue monitoring" in report.recommendations

    def test_support_agent_detects_missing_credentials(self, test_db, mock_llm_response):
        """Set up a client with no credentials, verify a ticket is generated."""
        # Create a client with no platform credentials
        test_db.create_client(
            {
                "name": "NoCreds Corp",
                "industry": "SaaS",
                "brand_voice": "Professional",
                "email": "nocreds@test.com",
                "status": "active",
            }
        )

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "tickets": [
                        {
                            "client_id": "nocreds",
                            "severity": "critical",
                            "category": "credentials",
                            "summary": "No platform credentials configured",
                            "recommendation": "Guide client through credential setup",
                        }
                    ],
                    "health_summary": "One client needs credentials",
                    "at_risk_clients": ["nocreds"],
                    "recommendations": ["Improve onboarding flow for credentials"],
                }
            )
        )

        agent = SupportAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert isinstance(report, SupportReport)
        assert len(report.tickets) == 1
        assert report.tickets[0].severity == "critical"
        assert report.tickets[0].category == "credentials"
        assert len(report.at_risk_clients) == 1

    def test_support_agent_detects_trial_expiry(self, test_db, mock_llm_response):
        """Set up a trialing client near expiry, verify at-risk detection."""
        # Create a trialing client that expires in 2 days
        trial_end = datetime.now(timezone.utc) + timedelta(days=2)
        cid = test_db.create_client(
            {
                "name": "TrialCorp",
                "industry": "E-commerce",
                "brand_voice": "Friendly",
                "email": "trial@test.com",
                "status": "active",
            }
        )
        test_db.execute(
            "UPDATE clients SET subscription_status='trialing', trial_ends_at=? WHERE id=?",
            (trial_end.isoformat(), cid),
            commit=True,
        )

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "tickets": [
                        {
                            "client_id": cid,
                            "severity": "critical",
                            "category": "billing",
                            "summary": "Trial expiring in 2 days with low engagement",
                            "recommendation": "Reach out to discuss conversion to paid plan",
                        }
                    ],
                    "health_summary": "One trial client at risk of churning",
                    "at_risk_clients": [cid],
                    "recommendations": ["Implement trial expiry email reminders"],
                }
            )
        )

        agent = SupportAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert isinstance(report, SupportReport)
        assert len(report.at_risk_clients) >= 1
        assert cid in report.at_risk_clients
        assert len(report.tickets) >= 1
        assert report.tickets[0].severity == "critical"
        assert report.tickets[0].category == "billing"

    def test_support_agent_handles_bad_llm_response(self, test_db, mock_llm_response):
        """Verify graceful fallback when LLM returns invalid JSON."""
        llm_resp = mock_llm_response("not valid json at all")

        agent = SupportAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert isinstance(report, SupportReport)
        assert "not valid json" in report.health_summary
        assert report.total_clients_checked >= 1

    def test_support_agent_no_clients(self, test_db, mock_llm_response):
        """Verify empty report when no active clients exist."""
        # Remove all clients
        test_db.execute("DELETE FROM clients", commit=True)

        agent = SupportAgent(db=test_db, api_key="sk-ant-test", model="test")
        report = agent.run(run_id="run-1")

        assert isinstance(report, SupportReport)
        assert report.total_clients_checked == 0
        assert len(report.tickets) == 0
