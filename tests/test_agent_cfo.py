"""Tests for CFO Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.cfo import CFOAgent
from ortobahn.models import CFOReport


class TestCFOAgent:
    def test_empty_report_with_no_runs(self, test_db):
        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        report = agent.run(run_id="run-1")
        assert report.total_spend_24h == 0.0
        assert report.cost_per_post == 0.0

    def test_calculates_costs_correctly(self, test_db, mock_llm_response):
        # Seed pipeline runs with token usage
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=2,
            total_input_tokens=100_000,
            total_output_tokens=10_000,
        )

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "budget_status": "within_budget",
                    "recommendations": ["Cost efficiency is good"],
                    "summary": "Low cost per post, good ROI",
                }
            )
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # $3/M input + $15/M output
        expected_cost = (100_000 / 1_000_000 * 3) + (10_000 / 1_000_000 * 15)
        assert abs(report.total_spend_24h - expected_cost) < 0.001
        assert report.cost_per_post > 0
        assert report.budget_status == "within_budget"

    def test_handles_bad_llm_response(self, test_db, mock_llm_response):
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1, total_input_tokens=1000, total_output_tokens=500)

        llm_resp = mock_llm_response("not json")

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.total_spend_24h > 0
        assert "not json" in report.summary

    # --- New tests below ---

    def test_cost_per_post_calculation(self, test_db, mock_llm_response):
        """Cost per post should be total cost divided by total posts."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=5,
            total_input_tokens=200_000,
            total_output_tokens=20_000,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "ok"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        expected_total = (200_000 / 1_000_000 * 3) + (20_000 / 1_000_000 * 15)
        expected_cpp = expected_total / 5
        assert abs(report.cost_per_post - expected_cpp) < 0.0001

    def test_cost_per_post_zero_when_no_posts(self, test_db, mock_llm_response):
        """If posts_published is 0, cost_per_post should be 0 (no division by zero)."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=0,
            total_input_tokens=10_000,
            total_output_tokens=1_000,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "no posts"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.cost_per_post == 0.0
        assert report.total_spend_24h > 0

    def test_cache_token_cost_calculation(self, test_db, mock_llm_response):
        """Cache write costs $3.75/M and cache read costs $0.30/M."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=1,
            total_input_tokens=500_000,
            total_output_tokens=10_000,
            total_cache_creation_tokens=200_000,
            total_cache_read_tokens=100_000,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "cached"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # uncached_input = max(0, 500k - 200k - 100k) = 200k
        # input_cost = 200k / 1M * 3 = 0.6
        # cache_write_cost = 200k / 1M * 3.75 = 0.75
        # cache_read_cost = 100k / 1M * 0.30 = 0.03
        # output_cost = 10k / 1M * 15 = 0.15
        # total = 0.6 + 0.75 + 0.03 + 0.15 = 1.53
        expected_total = 0.6 + 0.75 + 0.03 + 0.15
        assert abs(report.total_spend_24h - expected_total) < 0.001

    def test_aggregates_multiple_runs(self, test_db, mock_llm_response):
        """Costs should be summed across multiple pipeline runs."""
        for i in range(3):
            run_id = f"run-{i}"
            test_db.start_pipeline_run(run_id, mode="single")
            test_db.complete_pipeline_run(
                run_id,
                posts_published=1,
                total_input_tokens=100_000,
                total_output_tokens=5_000,
            )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "aggregated"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-test")

        # 3 runs * 100k input = 300k, 3 runs * 5k output = 15k
        expected_total = (300_000 / 1_000_000 * 3) + (15_000 / 1_000_000 * 15)
        assert abs(report.total_spend_24h - expected_total) < 0.001
        assert report.cost_per_post > 0

    def test_roi_calculation_with_engagements(self, test_db, mock_llm_response):
        """ROI = total_engagements / total_cost."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=1,
            total_input_tokens=100_000,
            total_output_tokens=10_000,
        )

        # Create a published post with metrics
        post_id = test_db.save_post(
            text="test post",
            run_id="run-a",
            status="published",
            confidence=0.9,
        )
        test_db.execute(
            "INSERT INTO metrics (post_id, like_count, repost_count, reply_count, quote_count, measured_at) "
            "VALUES (?, 10, 5, 3, 0, datetime('now'))",
            (post_id,),
            commit=True,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "good roi"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-test")

        total_cost = (100_000 / 1_000_000 * 3) + (10_000 / 1_000_000 * 15)
        total_engagements = 10 + 5 + 3  # likes + reposts + replies
        expected_roi = total_engagements / total_cost
        assert report.total_engagements_24h == total_engagements
        assert abs(report.roi_estimate - expected_roi) < 0.1
        assert report.cost_per_engagement > 0

    def test_cost_per_engagement_zero_when_no_engagements(self, test_db, mock_llm_response):
        """If there are no engagements, cost_per_engagement should be 0."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=1,
            total_input_tokens=50_000,
            total_output_tokens=5_000,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "no engagement"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-test")

        assert report.cost_per_engagement == 0.0
        assert report.total_engagements_24h == 0
        assert report.roi_estimate == 0.0

    def test_budget_status_from_llm(self, test_db, mock_llm_response):
        """Budget status should be parsed from LLM JSON response."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1, total_input_tokens=1000, total_output_tokens=500)

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "budget_status": "over_budget",
                    "recommendations": ["Reduce spend"],
                    "summary": "Costs are too high",
                }
            )
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.budget_status == "over_budget"
        assert "Reduce spend" in report.recommendations
        assert report.summary == "Costs are too high"

    def test_recommendations_parsed_from_llm(self, test_db, mock_llm_response):
        """Multiple recommendations should be parsed correctly from LLM response."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=2, total_input_tokens=5000, total_output_tokens=2000)

        recommendations = [
            "Optimize prompt lengths",
            "Increase cache utilization",
            "Consider batch processing",
        ]
        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "budget_status": "within_budget",
                    "recommendations": recommendations,
                    "summary": "Multiple suggestions",
                }
            )
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.recommendations) == 3
        assert report.recommendations == recommendations

    def test_llm_response_with_markdown_fences(self, test_db, mock_llm_response):
        """LLM response wrapped in markdown code fences should still parse."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1, total_input_tokens=1000, total_output_tokens=500)

        json_body = json.dumps(
            {"budget_status": "under_utilized", "recommendations": ["Increase posting"], "summary": "Low usage"}
        )
        llm_resp = mock_llm_response(f"```json\n{json_body}\n```")

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.budget_status == "under_utilized"
        assert report.summary == "Low usage"

    def test_bad_json_fallback_truncates_to_500_chars(self, test_db, mock_llm_response):
        """When LLM returns non-JSON, summary should be truncated to 500 chars."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1, total_input_tokens=1000, total_output_tokens=500)

        long_text = "x" * 1000
        llm_resp = mock_llm_response(long_text)

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert len(report.summary) == 500
        assert report.budget_status == "within_budget"  # default value

    def test_client_budget_enforcement_pauses_over_budget(self, test_db, mock_llm_response):
        """Clients exceeding monthly budget should be paused."""
        # Create a client with a low monthly budget
        test_db.create_client(
            {
                "name": "BudgetCorp",
                "industry": "SaaS",
                "brand_voice": "professional",
                "email": "budget@corp.com",
                "status": "active",
            }
        )
        # Get the client ID
        client = test_db.fetchone("SELECT id FROM clients WHERE name='BudgetCorp'")
        client_id = client["id"]

        # Set a monthly budget
        test_db.execute("UPDATE clients SET monthly_budget=0.01, active=1 WHERE id=?", (client_id,), commit=True)

        # Create a pipeline run with significant token usage for this client
        test_db.start_pipeline_run("run-budget", mode="single", client_id=client_id)
        test_db.complete_pipeline_run(
            "run-budget",
            posts_published=1,
            total_input_tokens=1_000_000,
            total_output_tokens=100_000,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "over_budget", "recommendations": [], "summary": "over"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-cfo")

        # Check recommendations mention paused clients
        paused_recs = [r for r in (report.recommendations or []) if "Paused over-budget" in r]
        assert len(paused_recs) == 1
        assert "BudgetCorp" in paused_recs[0]

        # Verify client was actually paused in DB
        updated = test_db.fetchone("SELECT status FROM clients WHERE id=?", (client_id,))
        assert updated["status"] == "paused"

    def test_client_within_budget_not_paused(self, test_db, mock_llm_response):
        """Clients within budget should NOT be paused."""
        client_id = test_db.create_client(
            {
                "name": "RichCorp",
                "industry": "Finance",
                "brand_voice": "formal",
                "email": "rich@corp.com",
                "status": "active",
            }
        )

        # Set a very high budget
        test_db.execute("UPDATE clients SET monthly_budget=10000, active=1 WHERE id=?", (client_id,), commit=True)

        # Minimal token usage
        test_db.start_pipeline_run("run-cheap", mode="single", client_id=client_id)
        test_db.complete_pipeline_run("run-cheap", posts_published=1, total_input_tokens=100, total_output_tokens=50)

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "fine"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-cfo")

        # Should not have paused-client recommendations
        paused_recs = [r for r in (report.recommendations or []) if "Paused over-budget" in r]
        assert len(paused_recs) == 0

        # Verify client is still active
        updated = test_db.fetchone("SELECT status FROM clients WHERE id=?", (client_id,))
        assert updated["status"] == "active"

    def test_uncached_input_floor_at_zero(self, test_db, mock_llm_response):
        """uncached_input = max(0, input - cache_create - cache_read), should not go negative."""
        test_db.start_pipeline_run("run-a", mode="single")
        # Cache tokens exceed input tokens (edge case)
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=1,
            total_input_tokens=100_000,
            total_output_tokens=5_000,
            total_cache_creation_tokens=80_000,
            total_cache_read_tokens=50_000,
        )

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "edge case"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # uncached = max(0, 100k - 80k - 50k) = max(0, -30k) = 0
        # cost = 0 + 80k/1M*3.75 + 50k/1M*0.30 + 5k/1M*15
        expected = 0 + (80_000 / 1_000_000 * 3.75) + (50_000 / 1_000_000 * 0.30) + (5_000 / 1_000_000 * 15)
        assert abs(report.total_spend_24h - expected) < 0.001

    def test_null_token_values_treated_as_zero(self, test_db, mock_llm_response):
        """Runs with None/null token values should be treated as 0."""
        test_db.start_pipeline_run("run-a", mode="single")
        # complete_pipeline_run defaults all token fields to 0, but let's ensure
        # the agent handles rows where values might be null by completing without tokens
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(
            json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "no tokens"})
        )

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.total_spend_24h == 0.0
        assert report.cost_per_post == 0.0

    def test_empty_report_returns_cfo_report_type(self, test_db):
        """Even with no data, the agent should return a CFOReport instance."""
        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        report = agent.run(run_id="run-1")
        assert isinstance(report, CFOReport)

    def test_llm_json_missing_keys_uses_defaults(self, test_db, mock_llm_response):
        """If LLM JSON is missing some keys, defaults should be used."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1, total_input_tokens=1000, total_output_tokens=500)

        # Return valid JSON but missing budget_status and recommendations
        llm_resp = mock_llm_response(json.dumps({"summary": "partial response"}))

        agent = CFOAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.budget_status == "within_budget"  # default from .get()
        assert report.recommendations == []  # default from .get()
        assert report.summary == "partial response"
