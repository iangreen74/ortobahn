"""Tests for SRE Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

from ortobahn.agents.sre import SREAgent
from ortobahn.models import SREAlert


def _llm_json(health_status="healthy", trend="stable", alerts=None, recommendations=None):
    """Helper to build a valid SRE LLM JSON response string."""
    return json.dumps(
        {
            "health_status": health_status,
            "avg_confidence_trend": trend,
            "alerts": alerts or [],
            "recommendations": recommendations or [],
        }
    )


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

    # --- Health status determination ---

    def test_fallback_healthy_when_success_rate_above_80(self, test_db, mock_llm_response):
        """When LLM JSON is invalid but success rate > 80%, fallback is 'healthy'."""
        # 5 successful runs, 0 failures -> 100% success
        for i in range(5):
            test_db.start_pipeline_run(f"run-{i}", mode="single")
            test_db.complete_pipeline_run(f"run-{i}", posts_published=1)

        llm_resp = mock_llm_response("garbage response")
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.health_status == "healthy"

    def test_fallback_degraded_when_success_rate_at_or_below_80(self, test_db, mock_llm_response):
        """When LLM JSON is invalid and success rate <= 80%, fallback is 'degraded'."""
        # 4 success, 1 failure -> 80% exactly -> degraded (not >0.8)
        for i in range(4):
            test_db.start_pipeline_run(f"run-ok-{i}", mode="single")
            test_db.complete_pipeline_run(f"run-ok-{i}", posts_published=1)
        test_db.start_pipeline_run("run-fail", mode="single")
        test_db.fail_pipeline_run("run-fail", ["timeout"])

        llm_resp = mock_llm_response("{invalid json!")
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.health_status == "degraded"
        assert report.pipeline_success_rate == 0.8

    def test_critical_health_from_llm(self, test_db, mock_llm_response):
        """LLM can report critical health status."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.fail_pipeline_run("run-a", ["crash"])

        llm_resp = mock_llm_response(
            _llm_json(
                health_status="critical",
                alerts=[{"severity": "critical", "component": "pipeline", "message": "Total failure"}],
            )
        )
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.health_status == "critical"
        assert report.alerts[0].severity == "critical"

    # --- Platform health detection ---

    def test_platform_health_published_is_healthy(self, test_db, mock_llm_response):
        """A platform with a recent 'published' post is marked 'healthy'."""
        test_db.save_post(text="Hello", run_id="r1", status="published", platform="bluesky")

        llm_resp = mock_llm_response(_llm_json())
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        # Seed a run so the agent doesn't bail early
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.platform_health.get("bluesky") == "healthy"

    def test_platform_health_failed_is_failing(self, test_db, mock_llm_response):
        """A platform with a recent 'failed' post is marked 'failing'."""
        test_db.save_post(text="Fail post", run_id="r1", status="failed", platform="twitter")

        llm_resp = mock_llm_response(_llm_json())
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.platform_health.get("twitter") == "failing"

    def test_platform_health_no_data(self, test_db, mock_llm_response):
        """A platform with no published/failed posts is marked 'no_data'."""
        llm_resp = mock_llm_response(_llm_json())
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.platform_health.get("bluesky") == "no_data"
        assert report.platform_health.get("twitter") == "no_data"
        assert report.platform_health.get("linkedin") == "no_data"

    def test_all_platforms_failing(self, test_db, mock_llm_response):
        """When all platforms have failed posts, all are marked 'failing'."""
        for platform in ["bluesky", "twitter", "linkedin"]:
            test_db.save_post(text=f"Fail on {platform}", run_id="r1", status="failed", platform=platform)

        llm_resp = mock_llm_response(_llm_json(health_status="critical"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=0)

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        for platform in ["bluesky", "twitter", "linkedin"]:
            assert report.platform_health[platform] == "failing"

    # --- Cost estimation accuracy ---

    def test_cost_estimation_sonnet_pricing(self, test_db, mock_llm_response):
        """Cost is estimated at $3/M input + $15/M output tokens (Sonnet pricing)."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run(
            "run-a",
            posts_published=1,
            total_input_tokens=1_000_000,
            total_output_tokens=1_000_000,
        )

        llm_resp = mock_llm_response(_llm_json())
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        # 1M input * $3/M + 1M output * $15/M = $18
        assert abs(report.estimated_cost_24h - 18.0) < 0.01

    def test_cost_zero_when_no_tokens(self, test_db, mock_llm_response):
        """Cost is zero when runs have no token data."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(_llm_json())
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.estimated_cost_24h == 0.0

    # --- Token usage aggregation ---

    def test_token_aggregation_across_multiple_runs(self, test_db, mock_llm_response):
        """Tokens are summed across all recent runs."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1, total_input_tokens=100, total_output_tokens=50)
        test_db.start_pipeline_run("run-b", mode="single")
        test_db.complete_pipeline_run("run-b", posts_published=1, total_input_tokens=200, total_output_tokens=100)
        test_db.start_pipeline_run("run-c", mode="single")
        test_db.complete_pipeline_run("run-c", posts_published=1, total_input_tokens=300, total_output_tokens=150)

        llm_resp = mock_llm_response(_llm_json())
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        # 100+200+300 input + 50+100+150 output = 900 total
        assert report.total_tokens_24h == 900

    # --- Slack alert dedup integration ---

    def test_slack_alert_sent_when_degraded(self, test_db, mock_llm_response):
        """Slack alert is sent via send_slack_message_deduped when health is degraded."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(_llm_json(health_status="degraded", recommendations=["Fix pipeline"]))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")

        with (
            patch.object(agent, "call_llm", return_value=llm_resp),
            patch("ortobahn.integrations.slack.send_slack_message_deduped") as mock_slack,
            patch("ortobahn.integrations.slack.format_sre_alert", return_value="alert message"),
        ):
            agent.run(run_id="sre-1", slack_webhook_url="https://hooks.slack.com/test")

        mock_slack.assert_called_once()
        call_args = mock_slack.call_args
        assert call_args[0][0] == "https://hooks.slack.com/test"
        assert "sre:degraded" in str(call_args)

    def test_slack_alert_sent_when_critical(self, test_db, mock_llm_response):
        """Slack alert is also sent for critical health."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.fail_pipeline_run("run-a", ["crash"])

        llm_resp = mock_llm_response(_llm_json(health_status="critical"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")

        with (
            patch.object(agent, "call_llm", return_value=llm_resp),
            patch("ortobahn.integrations.slack.send_slack_message_deduped") as mock_slack,
            patch("ortobahn.integrations.slack.format_sre_alert", return_value="critical alert"),
        ):
            agent.run(run_id="sre-1", slack_webhook_url="https://hooks.slack.com/test")

        mock_slack.assert_called_once()

    def test_no_slack_alert_when_healthy(self, test_db, mock_llm_response):
        """No Slack alert when health is 'healthy'."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(_llm_json(health_status="healthy"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")

        with (
            patch.object(agent, "call_llm", return_value=llm_resp),
            patch("ortobahn.integrations.slack.send_slack_message_deduped") as mock_slack,
            patch("ortobahn.integrations.slack.format_sre_alert"),
        ):
            agent.run(run_id="sre-1", slack_webhook_url="https://hooks.slack.com/test")

        mock_slack.assert_not_called()

    def test_no_slack_alert_when_no_webhook_url(self, test_db, mock_llm_response):
        """No Slack alert when slack_webhook_url is not provided."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(_llm_json(health_status="degraded"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")

        with (
            patch.object(agent, "call_llm", return_value=llm_resp),
            patch("ortobahn.integrations.slack.send_slack_message_deduped") as mock_slack,
            patch("ortobahn.integrations.slack.format_sre_alert"),
        ):
            agent.run(run_id="sre-1")  # No slack_webhook_url kwarg

        mock_slack.assert_not_called()

    # --- LLM response parsing edge cases ---

    def test_llm_response_with_markdown_fences(self, test_db, mock_llm_response):
        """LLM response wrapped in ```json fences is still parsed."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        fenced = "```json\n" + _llm_json(health_status="healthy", trend="rising") + "\n```"
        llm_resp = mock_llm_response(fenced)
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.health_status == "healthy"
        assert report.avg_confidence_trend == "rising"

    def test_llm_response_missing_optional_fields(self, test_db, mock_llm_response):
        """LLM response with only health_status (missing alerts/recommendations)."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        minimal = json.dumps({"health_status": "healthy"})
        llm_resp = mock_llm_response(minimal)
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")

        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.health_status == "healthy"
        assert report.avg_confidence_trend == "stable"  # default
        assert report.alerts == []
        assert report.recommendations == []

    def test_multiple_alerts_parsed(self, test_db, mock_llm_response):
        """Multiple alerts from LLM are all parsed as SREAlert objects."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(
            _llm_json(
                health_status="degraded",
                alerts=[
                    {"severity": "warning", "component": "pipeline", "message": "High failure rate"},
                    {"severity": "critical", "component": "tokens", "message": "Cost spike detected"},
                    {"severity": "info", "component": "platform_api", "message": "Twitter latency elevated"},
                ],
            )
        )
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert len(report.alerts) == 3
        assert all(isinstance(a, SREAlert) for a in report.alerts)
        severities = [a.severity for a in report.alerts]
        assert "warning" in severities
        assert "critical" in severities
        assert "info" in severities

    # --- Confidence trend analysis ---

    def test_confidence_trend_rising(self, test_db, mock_llm_response):
        """LLM can report rising confidence trend."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(_llm_json(trend="rising"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.avg_confidence_trend == "rising"

    def test_confidence_trend_falling(self, test_db, mock_llm_response):
        """LLM can report falling confidence trend."""
        test_db.start_pipeline_run("run-a", mode="single")
        test_db.complete_pipeline_run("run-a", posts_published=1)

        llm_resp = mock_llm_response(_llm_json(trend="falling"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.avg_confidence_trend == "falling"

    # --- Edge case: all runs failed ---

    def test_all_runs_failed_zero_success_rate(self, test_db, mock_llm_response):
        """When every run failed, success rate is 0.0."""
        for i in range(5):
            test_db.start_pipeline_run(f"run-{i}", mode="single")
            test_db.fail_pipeline_run(f"run-{i}", [f"error-{i}"])

        llm_resp = mock_llm_response(_llm_json(health_status="critical"))
        agent = SREAgent(db=test_db, api_key="sk-ant-test", model="test")
        with patch.object(agent, "call_llm", return_value=llm_resp):
            report = agent.run(run_id="sre-1")

        assert report.pipeline_success_rate == 0.0
        assert report.health_status == "critical"
