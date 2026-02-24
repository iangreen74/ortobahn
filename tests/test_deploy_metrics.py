"""Tests for post-deploy metric validation module."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.deploy_metrics import (
    DeployValidationResult,
    MetricCheck,
    fetch_cloudwatch_metric,
    format_validation_report,
    validate_deploy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cloudwatch():
    """Mock boto3 CloudWatch client with configurable metric responses."""
    mock_client = MagicMock()

    # Default: all metrics return healthy values
    def _make_response(value, stat="Average"):
        return {
            "Datapoints": [
                {
                    "Timestamp": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                    stat: value,
                }
            ]
        }

    def default_get_metric_statistics(**kwargs):
        metric = kwargs.get("MetricName", "")
        stat = kwargs.get("Statistics", ["Average"])[0]

        if metric == "HTTPCode_Target_5XX_Count":
            return _make_response(2.0, stat)
        elif metric == "RequestCount":
            return _make_response(1000.0, stat)
        elif metric == "TargetResponseTime":
            return _make_response(0.5, stat)  # 500ms in seconds
        elif metric == "CPUUtilization":
            return _make_response(45.0, stat)
        elif metric == "MemoryUtilization":
            return _make_response(60.0, stat)
        return {"Datapoints": []}

    mock_client.get_metric_statistics.side_effect = default_get_metric_statistics

    with patch("boto3.client", return_value=mock_client):
        yield mock_client


@pytest.fixture
def mock_boto3():
    """Lower-level mock for testing fetch_cloudwatch_metric directly."""
    with patch("boto3.client") as mock_client_factory:
        yield mock_client_factory


# ---------------------------------------------------------------------------
# MetricCheck dataclass
# ---------------------------------------------------------------------------


class TestMetricCheck:
    def test_metric_check_passed(self):
        """MetricCheck with value under threshold passes."""
        check = MetricCheck(
            name="cpu_utilization",
            current_value=45.0,
            baseline_value=0.0,
            threshold_pct=80.0,
            passed=True,
            detail="45.0% CPU (threshold: 80%)",
        )
        assert check.passed is True
        assert check.name == "cpu_utilization"
        assert check.current_value == 45.0

    def test_metric_check_failed(self):
        """MetricCheck with value over threshold fails."""
        check = MetricCheck(
            name="error_rate_5xx",
            current_value=12.0,
            baseline_value=0.0,
            threshold_pct=5.0,
            passed=False,
            detail="12.0% 5xx error rate (threshold: 5%)",
        )
        assert check.passed is False
        assert check.current_value > check.threshold_pct


# ---------------------------------------------------------------------------
# validate_deploy
# ---------------------------------------------------------------------------


class TestValidateDeploy:
    def test_all_metrics_pass(self, mock_cloudwatch):
        """All metrics within thresholds returns passed=True."""
        result = validate_deploy()

        assert result.passed is True
        assert len(result.checks) == 4
        assert all(c.passed for c in result.checks)
        assert "All metrics within thresholds" in result.summary

    def test_error_rate_exceeds_threshold(self, mock_cloudwatch):
        """High 5xx rate fails validation."""
        original_side_effect = mock_cloudwatch.get_metric_statistics.side_effect

        def high_error_rate(**kwargs):
            metric = kwargs.get("MetricName", "")
            stat = kwargs.get("Statistics", ["Average"])[0]
            if metric == "HTTPCode_Target_5XX_Count":
                return {
                    "Datapoints": [
                        {
                            "Timestamp": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                            stat: 100.0,
                        }
                    ]
                }
            elif metric == "RequestCount":
                return {
                    "Datapoints": [
                        {
                            "Timestamp": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                            stat: 500.0,
                        }
                    ]
                }
            return original_side_effect(**kwargs)

        mock_cloudwatch.get_metric_statistics.side_effect = high_error_rate

        result = validate_deploy()

        assert result.passed is False
        error_check = next(c for c in result.checks if c.name == "error_rate_5xx")
        assert error_check.passed is False
        assert error_check.current_value == pytest.approx(20.0)
        assert "error_rate_5xx" in result.summary

    def test_latency_exceeds_threshold(self, mock_cloudwatch):
        """High p99 latency fails validation."""
        original_side_effect = mock_cloudwatch.get_metric_statistics.side_effect

        def high_latency(**kwargs):
            metric = kwargs.get("MetricName", "")
            stat = kwargs.get("Statistics", ["Average"])[0]
            if metric == "TargetResponseTime":
                return {
                    "Datapoints": [
                        {
                            "Timestamp": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                            stat: 5.0,  # 5 seconds = 5000ms
                        }
                    ]
                }
            return original_side_effect(**kwargs)

        mock_cloudwatch.get_metric_statistics.side_effect = high_latency

        result = validate_deploy()

        assert result.passed is False
        latency_check = next(c for c in result.checks if c.name == "latency_p99")
        assert latency_check.passed is False
        assert latency_check.current_value == pytest.approx(5000.0)

    def test_cloudwatch_unavailable(self, mock_cloudwatch):
        """Missing CloudWatch data still returns result (no crash)."""
        mock_cloudwatch.get_metric_statistics.side_effect = None
        mock_cloudwatch.get_metric_statistics.return_value = {"Datapoints": []}

        result = validate_deploy()

        # With no data, all values default to 0 which is within thresholds
        assert result.passed is True
        assert len(result.checks) == 4
        for check in result.checks:
            assert check.current_value == 0.0

    def test_format_validation_report(self):
        """Report formatting includes all checks."""
        result = DeployValidationResult(
            passed=False,
            checks=[
                MetricCheck(
                    name="error_rate_5xx",
                    current_value=10.0,
                    baseline_value=0.0,
                    threshold_pct=5.0,
                    passed=False,
                    detail="10.0% 5xx error rate (threshold: 5%)",
                ),
                MetricCheck(
                    name="cpu_utilization",
                    current_value=45.0,
                    baseline_value=0.0,
                    threshold_pct=80.0,
                    passed=True,
                    detail="45.0% CPU (threshold: 80%)",
                ),
            ],
            summary="1 metric(s) exceeded threshold: error_rate_5xx",
        )

        report = format_validation_report(result)

        assert "FAILED" in report
        assert "[FAIL]" in report
        assert "[PASS]" in report
        assert "error_rate" in report
        assert "CPU" in report
        assert "Summary:" in report


# ---------------------------------------------------------------------------
# fetch_cloudwatch_metric
# ---------------------------------------------------------------------------


class TestFetchCloudwatchMetric:
    def test_returns_latest_datapoint(self, mock_boto3):
        """Should return the value from the most recent datapoint."""
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        mock_client.get_metric_statistics.return_value = {
            "Datapoints": [
                {
                    "Timestamp": datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
                    "Average": 30.0,
                },
                {
                    "Timestamp": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                    "Average": 50.0,
                },
                {
                    "Timestamp": datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
                    "Average": 20.0,
                },
            ]
        }

        value = fetch_cloudwatch_metric(
            "AWS/ECS",
            "CPUUtilization",
            [{"Name": "ClusterName", "Value": "ortobahn"}],
        )

        assert value == 50.0

    def test_returns_none_on_empty(self, mock_boto3):
        """Should return None when no datapoints are available."""
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        mock_client.get_metric_statistics.return_value = {"Datapoints": []}

        value = fetch_cloudwatch_metric(
            "AWS/ECS",
            "CPUUtilization",
            [{"Name": "ClusterName", "Value": "ortobahn"}],
        )

        assert value is None

    def test_handles_exception(self, mock_boto3):
        """Should return None and log warning on exception."""
        mock_boto3.side_effect = Exception("AWS connection failed")

        value = fetch_cloudwatch_metric(
            "AWS/ECS",
            "CPUUtilization",
            [{"Name": "ClusterName", "Value": "ortobahn"}],
        )

        assert value is None
