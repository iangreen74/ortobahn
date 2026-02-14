"""Tests for Ops Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.ops import OpsAgent


class TestOpsAgent:
    def test_no_pending_clients(self, test_db, mock_llm_response):
        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "recommendations": ["All systems operational"],
                    "summary": "No pending work",
                }
            )
        )

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.pending_clients == 0
        assert report.active_clients >= 1  # default client exists

    def test_auto_activates_complete_pending_client(self, test_db, mock_llm_response):
        # Create a pending client with complete profile
        test_db.create_client(
            {
                "name": "TestCorp",
                "industry": "SaaS",
                "brand_voice": "Professional",
                "email": "test@testcorp.com",
                "status": "pending",
            }
        )

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "recommendations": ["Monitor new client"],
                    "summary": "Auto-activated TestCorp",
                }
            )
        )

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.actions_taken) == 1
        assert report.actions_taken[0].action == "activate_client"
        assert "TestCorp" in report.actions_taken[0].target

        # Verify client was activated in DB
        clients = test_db.conn.execute("SELECT status FROM clients WHERE name='TestCorp'").fetchone()
        assert clients["status"] == "active"

    def test_does_not_activate_incomplete_profile(self, test_db, mock_llm_response):
        # Create pending client without brand_voice
        test_db.create_client(
            {
                "name": "IncompleteCorp",
                "industry": "",
                "brand_voice": "",
                "email": "inc@test.com",
                "status": "pending",
            }
        )

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "recommendations": ["Request more info from IncompleteCorp"],
                    "summary": "One client needs profile completion",
                }
            )
        )

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # Should not auto-activate incomplete profiles
        activated = [a for a in report.actions_taken if a.target == "IncompleteCorp"]
        assert len(activated) == 0

    def test_handles_bad_llm_response(self, test_db, mock_llm_response):
        llm_resp = mock_llm_response("invalid json")

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert "invalid json" in report.summary
