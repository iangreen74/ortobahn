"""Tests for CFO Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.cfo import CFOAgent


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
