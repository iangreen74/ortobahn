"""Tests for Ops Agent."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ortobahn.agents.ops import OpsAgent
from ortobahn.models import OpsReport


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
        clients = test_db.fetchone("SELECT status FROM clients WHERE name='TestCorp'")
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

    # --- New tests below ---

    def test_returns_ops_report_type(self, test_db, mock_llm_response):
        """Agent should always return an OpsReport instance."""
        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "typed"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert isinstance(report, OpsReport)

    def test_active_clients_counted(self, test_db, mock_llm_response):
        """Active clients should be counted in the report."""
        test_db.create_client(
            {
                "name": "ActiveOne",
                "industry": "Tech",
                "brand_voice": "casual",
                "email": "a1@test.com",
                "status": "active",
            }
        )
        test_db.create_client(
            {
                "name": "ActiveTwo",
                "industry": "Finance",
                "brand_voice": "formal",
                "email": "a2@test.com",
                "status": "active",
            }
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "active clients counted"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # default client + 2 new active clients
        assert report.active_clients >= 3

    def test_pending_clients_counted(self, test_db, mock_llm_response):
        """Pending clients should be counted in the report."""
        # Create 2 pending clients (incomplete so they won't be auto-activated)
        test_db.create_client(
            {"name": "Pending1", "industry": "", "brand_voice": "", "email": "p1@test.com", "status": "pending"}
        )
        test_db.create_client(
            {"name": "Pending2", "industry": "", "brand_voice": "", "email": "p2@test.com", "status": "pending"}
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "pending counted"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.pending_clients == 2

    def test_multiple_pending_clients_auto_activated(self, test_db, mock_llm_response):
        """Multiple pending clients with complete profiles should all be auto-activated."""
        test_db.create_client(
            {"name": "FullCorp1", "industry": "Tech", "brand_voice": "fun", "email": "f1@test.com", "status": "pending"}
        )
        test_db.create_client(
            {
                "name": "FullCorp2",
                "industry": "Health",
                "brand_voice": "caring",
                "email": "f2@test.com",
                "status": "pending",
            }
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "both activated"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        activated = [a for a in report.actions_taken if a.action == "activate_client"]
        assert len(activated) == 2
        activated_names = {a.target for a in activated}
        assert "FullCorp1" in activated_names
        assert "FullCorp2" in activated_names

        # Both should be active in DB
        c1 = test_db.fetchone("SELECT status FROM clients WHERE name='FullCorp1'")
        c2 = test_db.fetchone("SELECT status FROM clients WHERE name='FullCorp2'")
        assert c1["status"] == "active"
        assert c2["status"] == "active"

    def test_recommendations_from_llm(self, test_db, mock_llm_response):
        """Recommendations from LLM should be on the report."""
        recs = ["Onboard more clients", "Improve response time", "Add monitoring"]

        llm_resp = mock_llm_response(json.dumps({"recommendations": recs, "summary": "recs"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.recommendations == recs

    def test_summary_from_llm(self, test_db, mock_llm_response):
        """Summary should be parsed from LLM JSON."""
        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "Everything is running smoothly"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.summary == "Everything is running smoothly"

    def test_bad_json_fallback_truncates_to_500(self, test_db, mock_llm_response):
        """When LLM returns non-JSON, summary should be truncated to 500 chars."""
        long_text = "z" * 1000
        llm_resp = mock_llm_response(long_text)

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.summary) == 500

    def test_llm_response_with_markdown_fences(self, test_db, mock_llm_response):
        """LLM response wrapped in markdown code fences should still parse."""
        json_body = json.dumps({"recommendations": ["Monitor closely"], "summary": "fenced response"})
        llm_resp = mock_llm_response(f"```json\n{json_body}\n```")

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.summary == "fenced response"
        assert report.recommendations == ["Monitor closely"]

    def test_missing_keys_in_llm_json_uses_defaults(self, test_db, mock_llm_response):
        """If LLM JSON is missing some keys, defaults should be used."""
        llm_resp = mock_llm_response(json.dumps({"summary": "partial"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.summary == "partial"
        assert report.recommendations == []

    def test_client_run_counts_in_llm_context(self, test_db, mock_llm_response):
        """Per-client run counts should be passed in the LLM prompt."""
        # Create some runs for the default client
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)
        test_db.start_pipeline_run("run-b", mode="single")
        test_db.complete_pipeline_run("run-b", posts_published=2)

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "run counts"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")

        captured_messages = []

        def capture_call(msg, **kwargs):
            captured_messages.append(msg)
            return llm_resp

        with patch.object(agent, "call_llm", side_effect=capture_call):
            agent.run(run_id="run-test")

        assert len(captured_messages) == 1
        # Should mention runs in the context
        assert "runs:" in captured_messages[0]

    def test_auto_actions_in_llm_context(self, test_db, mock_llm_response):
        """Auto-actions taken should be reported in the LLM prompt."""
        test_db.create_client(
            {
                "name": "AutoCorp",
                "industry": "Retail",
                "brand_voice": "friendly",
                "email": "auto@corp.com",
                "status": "pending",
            }
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "actions reported"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")

        captured_messages = []

        def capture_call(msg, **kwargs):
            captured_messages.append(msg)
            return llm_resp

        with patch.object(agent, "call_llm", side_effect=capture_call):
            agent.run(run_id="run-test")

        assert len(captured_messages) == 1
        # The auto-activation action should be mentioned in the context
        assert "activate_client" in captured_messages[0]
        assert "AutoCorp" in captured_messages[0]

    def test_mixed_pending_and_active_clients(self, test_db, mock_llm_response):
        """Report should correctly distinguish pending from active clients."""
        # One active client (default) already exists.
        # Add one pending incomplete and one active
        test_db.create_client(
            {"name": "PendingCo", "industry": "", "brand_voice": "", "email": "pend@test.com", "status": "pending"}
        )
        test_db.create_client(
            {
                "name": "ActiveCo",
                "industry": "Media",
                "brand_voice": "bold",
                "email": "act@test.com",
                "status": "active",
            }
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "mixed"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.pending_clients == 1
        assert report.active_clients >= 2  # default + ActiveCo

    def test_action_has_correct_fields(self, test_db, mock_llm_response):
        """Auto-activation action should have correct action, target, status, and detail."""
        test_db.create_client(
            {
                "name": "FieldCheck",
                "industry": "Education",
                "brand_voice": "inspiring",
                "email": "fc@test.com",
                "status": "pending",
            }
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "field check"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        action = report.actions_taken[-1]  # last action should be the activate
        assert action.action == "activate_client"
        assert action.target == "FieldCheck"
        assert action.status == "completed"
        assert "FieldCheck" in action.detail

    def test_enrichment_failure_still_activates(self, test_db, mock_llm_response):
        """If enrichment fails, the client should still be activated (enrichment is best-effort)."""
        test_db.create_client(
            {
                "name": "EnrichFail",
                "industry": "Logistics",
                "brand_voice": "direct",
                "email": "ef@test.com",
                "status": "pending",
                # No products or content_pillars, so enrichment will be attempted
            }
        )

        llm_resp = mock_llm_response(json.dumps({"recommendations": [], "summary": "enrichment skipped"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")

        # Mock the enrichment agent import to raise an error
        with (
            patch.object(agent, "call_llm", return_value=llm_resp),
            patch.dict(
                "sys.modules", {"ortobahn.agents.enrichment": MagicMock(side_effect=ImportError("no enrichment"))}
            ),
        ):
            report = agent.run(run_id="run-1")

        # Client should still have been activated even if enrichment import failed
        activated = [a for a in report.actions_taken if a.action == "activate_client" and a.target == "EnrichFail"]
        assert len(activated) == 1

        # Verify in DB
        client = test_db.fetchone("SELECT status FROM clients WHERE name='EnrichFail'")
        assert client["status"] == "active"

    def test_no_clients_at_all(self, test_db, mock_llm_response):
        """If no clients exist (edge case), report should handle it gracefully."""
        # Remove the default client
        test_db.execute("DELETE FROM clients", commit=True)

        llm_resp = mock_llm_response(json.dumps({"recommendations": ["Create first client"], "summary": "empty"}))

        agent = OpsAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.pending_clients == 0
        assert report.active_clients == 0
        assert report.actions_taken == []
