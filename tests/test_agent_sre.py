"""Tests for SRE Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.sre import SREAgent


class TestSREAgent:
    def test_empty_report_with_no_runs(self, test_db):
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        report = agent.run(run_id="run-1")
        assert report.health_status == "unknown"

    def test_healthy_report_with_data(self, test_db, mock_llm_response):
        # Seed some pipeline runs
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=3, total_input_tokens=1000, total_output_tokens=500)
        test_db.start_pipeline_run("run-b", mode="single")
        test_db.complete_pipeline_run("run-b", posts_published=2, total_input_tokens=800, total_output_tokens=400)

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "health_status": "healthy",
                    "avg_confidence_trend": "rising",
                    "alerts": [],
                    "recommendations": ["Keep up the good work"],
                }
            )
        )

        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.health_status == "healthy"
        assert report.pipeline_success_rate == 1.0
        assert report.total_tokens_24h == 2700
        assert report.estimated_cost_24h > 0
        assert len(report.alerts) == 0
        assert "Keep up the good work" in report.recommendations

    def test_degraded_with_failures(self, test_db, mock_llm_response):
        # 3 runs: 1 success, 2 failures
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)
        test_db.start_pipeline_run("run-b", mode="single")
        test_db.fail_pipeline_run("run-b", ["error1"])
        test_db.start_pipeline_run("run-c", mode="single")
        test_db.fail_pipeline_run("run-c", ["error2"])

        llm_resp = mock_llm_response(
            json.dumps(
                {
                    "health_status": "degraded",
                    "avg_confidence_trend": "falling",
                    "alerts": [{"severity": "warning", "component": "pipeline", "message": "High failure rate"}],
                    "recommendations": ["Investigate failures"],
                }
            )
        )

        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        assert report.health_status == "degraded"
        assert report.pipeline_success_rate < 0.5
        assert len(report.alerts) == 1
        assert report.alerts[0].severity == "warning"

    def test_handles_bad_llm_response(self, test_db, mock_llm_response):
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response("not valid json at all")

        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="run-1")

        # Falls back to heuristic-based health
        assert report.health_status in ("healthy", "degraded")
        assert report.pipeline_success_rate == 1.0
