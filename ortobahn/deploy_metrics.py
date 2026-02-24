"""Post-deploy metric validation -- checks CloudWatch for error rate, latency, and resource usage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("ortobahn.deploy_metrics")


@dataclass
class MetricCheck:
    name: str
    current_value: float
    baseline_value: float
    threshold_pct: float  # max acceptable % increase
    passed: bool
    detail: str


@dataclass
class DeployValidationResult:
    passed: bool
    checks: list[MetricCheck]
    summary: str


def fetch_cloudwatch_metric(
    namespace: str,
    metric_name: str,
    dimensions: list[dict],
    stat: str = "Average",
    period_minutes: int = 5,
    region: str = "us-west-2",
) -> float | None:
    """Fetch a single CloudWatch metric value. Returns None if unavailable."""
    try:
        import boto3

        client = boto3.client("cloudwatch", region_name=region)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=period_minutes)
        response = client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start,
            EndTime=end,
            Period=period_minutes * 60,
            Statistics=[stat],
        )
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return None
        latest = sorted(datapoints, key=lambda d: d["Timestamp"])[-1]
        return latest.get(stat)
    except Exception as e:
        logger.warning("CloudWatch fetch failed for %s: %s", metric_name, e)
        return None


def validate_deploy(
    cluster: str = "ortobahn",
    service: str = "ortobahn-web-v2",
    region: str = "us-west-2",
    error_rate_threshold: float = 5.0,  # max 5% error rate
    latency_p99_threshold_ms: float = 3000.0,  # max 3s p99
    cpu_threshold_pct: float = 80.0,  # max 80% CPU
) -> DeployValidationResult:
    """Run post-deploy metric validation against CloudWatch."""
    checks = []

    # Check 1: ALB 5xx error rate
    error_count = fetch_cloudwatch_metric(
        "AWS/ApplicationELB",
        "HTTPCode_Target_5XX_Count",
        [{"Name": "TargetGroup", "Value": f"targetgroup/{service}"}],
        stat="Sum",
        region=region,
    )
    request_count = fetch_cloudwatch_metric(
        "AWS/ApplicationELB",
        "RequestCount",
        [{"Name": "TargetGroup", "Value": f"targetgroup/{service}"}],
        stat="Sum",
        region=region,
    )
    error_rate = 0.0
    if error_count is not None and request_count and request_count > 0:
        error_rate = (error_count / request_count) * 100
    checks.append(
        MetricCheck(
            name="error_rate_5xx",
            current_value=error_rate,
            baseline_value=0.0,
            threshold_pct=error_rate_threshold,
            passed=error_rate <= error_rate_threshold,
            detail=f"{error_rate:.1f}% 5xx error rate (threshold: {error_rate_threshold}%)",
        )
    )

    # Check 2: Response latency p99
    latency = fetch_cloudwatch_metric(
        "AWS/ApplicationELB",
        "TargetResponseTime",
        [{"Name": "TargetGroup", "Value": f"targetgroup/{service}"}],
        stat="p99",
        region=region,
    )
    latency_ms = (latency or 0) * 1000
    checks.append(
        MetricCheck(
            name="latency_p99",
            current_value=latency_ms,
            baseline_value=0.0,
            threshold_pct=latency_p99_threshold_ms,
            passed=latency_ms <= latency_p99_threshold_ms,
            detail=f"{latency_ms:.0f}ms p99 latency (threshold: {latency_p99_threshold_ms}ms)",
        )
    )

    # Check 3: ECS CPU utilization
    cpu = fetch_cloudwatch_metric(
        "AWS/ECS",
        "CPUUtilization",
        [
            {"Name": "ClusterName", "Value": cluster},
            {"Name": "ServiceName", "Value": service},
        ],
        stat="Average",
        region=region,
    )
    cpu_val = cpu or 0.0
    checks.append(
        MetricCheck(
            name="cpu_utilization",
            current_value=cpu_val,
            baseline_value=0.0,
            threshold_pct=cpu_threshold_pct,
            passed=cpu_val <= cpu_threshold_pct,
            detail=f"{cpu_val:.1f}% CPU (threshold: {cpu_threshold_pct}%)",
        )
    )

    # Check 4: ECS Memory utilization
    memory = fetch_cloudwatch_metric(
        "AWS/ECS",
        "MemoryUtilization",
        [
            {"Name": "ClusterName", "Value": cluster},
            {"Name": "ServiceName", "Value": service},
        ],
        stat="Average",
        region=region,
    )
    mem_val = memory or 0.0
    checks.append(
        MetricCheck(
            name="memory_utilization",
            current_value=mem_val,
            baseline_value=0.0,
            threshold_pct=85.0,
            passed=mem_val <= 85.0,
            detail=f"{mem_val:.1f}% memory (threshold: 85%)",
        )
    )

    all_passed = all(c.passed for c in checks)
    failed = [c for c in checks if not c.passed]
    summary = (
        "All metrics within thresholds."
        if all_passed
        else f"{len(failed)} metric(s) exceeded threshold: {', '.join(c.name for c in failed)}"
    )

    return DeployValidationResult(passed=all_passed, checks=checks, summary=summary)


def format_validation_report(result: DeployValidationResult) -> str:
    """Format validation result as human-readable report."""
    lines = [f"Deploy Validation: {'PASSED' if result.passed else 'FAILED'}"]
    for c in result.checks:
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{status}] {c.detail}")
    lines.append(f"Summary: {result.summary}")
    return "\n".join(lines)
