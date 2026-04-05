"""Prometheus metrics collection and CloudWatch integration."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

try:
    from prometheus_client import Counter, Gauge, Histogram, push_to_gateway
except ImportError:
    Counter = Gauge = Histogram = None  # type: ignore
    push_to_gateway = None  # type: ignore

try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore


class MetricType(str, Enum):
    """Types of metrics collected."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class AgentMetrics(BaseModel):
    """Metrics data for an agent."""

    agent_id: str
    decision_latency_ms: float = Field(ge=0)
    api_errors: int = Field(ge=0, default=0)
    memory_size_bytes: int = Field(ge=0, default=0)
    timestamp: float = Field(default_factory=time.time)


class MetricsCollector:
    """Collects and exports agent metrics to Prometheus and CloudWatch."""

    def __init__(
        self,
        cloudwatch_enabled: bool = False,
        cloudwatch_namespace: str = "Ortobahn/Agents",
        prometheus_enabled: bool = True,
    ) -> None:
        """Initialize metrics collector.

        Args:
            cloudwatch_enabled: Whether to push metrics to CloudWatch
            cloudwatch_namespace: CloudWatch namespace for metrics
            prometheus_enabled: Whether to collect Prometheus metrics
        """
        self.cloudwatch_enabled = cloudwatch_enabled and boto3 is not None
        self.cloudwatch_namespace = cloudwatch_namespace
        self.prometheus_enabled = prometheus_enabled and Counter is not None

        if self.cloudwatch_enabled:
            self.cloudwatch_client = boto3.client("cloudwatch")
        else:
            self.cloudwatch_client = None

        if self.prometheus_enabled:
            self._init_prometheus_metrics()

    def _init_prometheus_metrics(self) -> None:
        """Initialize Prometheus metrics."""
        self.decision_latency = Histogram(
            "agent_decision_latency_seconds",
            "Time taken for agent decisions",
            ["agent_id"],
            buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
        )
        self.api_errors = Counter(
            "agent_api_errors_total",
            "Total API errors by agent",
            ["agent_id", "error_type"],
        )
        self.memory_size = Gauge(
            "agent_memory_size_bytes",
            "Agent memory size in bytes",
            ["agent_id"],
        )

    def record_metrics(self, metrics: AgentMetrics) -> None:
        """Record agent metrics.

        Args:
            metrics: Agent metrics to record
        """
        if self.prometheus_enabled:
            self.decision_latency.labels(agent_id=metrics.agent_id).observe(
                metrics.decision_latency_ms / 1000
            )
            self.memory_size.labels(agent_id=metrics.agent_id).set(
                metrics.memory_size_bytes
            )

        if self.cloudwatch_enabled:
            self._push_to_cloudwatch(metrics)

    def _push_to_cloudwatch(self, metrics: AgentMetrics) -> None:
        """Push metrics to CloudWatch.

        Args:
            metrics: Metrics to push
        """
        if not self.cloudwatch_client:
            return

        metric_data = [
            {
                "MetricName": "DecisionLatency",
                "Value": metrics.decision_latency_ms,
                "Unit": "Milliseconds",
                "Dimensions": [{"Name": "AgentId", "Value": metrics.agent_id}],
            },
            {
                "MetricName": "MemorySize",
                "Value": metrics.memory_size_bytes,
                "Unit": "Bytes",
                "Dimensions": [{"Name": "AgentId", "Value": metrics.agent_id}],
            },
        ]

        if metrics.api_errors > 0:
            metric_data.append(
                {
                    "MetricName": "APIErrors",
                    "Value": metrics.api_errors,
                    "Unit": "Count",
                    "Dimensions": [{"Name": "AgentId", "Value": metrics.agent_id}],
                }
            )

        self.cloudwatch_client.put_metric_data(
            Namespace=self.cloudwatch_namespace, MetricData=metric_data
        )

    @asynccontextmanager
    async def track_decision(self, agent_id: str):
        """Context manager to track decision latency.

        Args:
            agent_id: ID of the agent making the decision

        Yields:
            Dictionary to store additional metrics
        """
        start_time = time.time()
        context: dict[str, Any] = {"errors": 0, "memory_bytes": 0}

        try:
            yield context
        finally:
            latency_ms = (time.time() - start_time) * 1000
            metrics = AgentMetrics(
                agent_id=agent_id,
                decision_latency_ms=latency_ms,
                api_errors=context.get("errors", 0),
                memory_size_bytes=context.get("memory_bytes", 0),
            )
            self.record_metrics(metrics)
