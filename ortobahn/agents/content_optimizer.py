"""Reinforcement learning agent for content strategy optimization."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ContentMetrics:
    """Track content performance metrics."""

    content_id: str
    variant_id: Optional[str] = None
    impressions: int = 0
    engagements: int = 0
    clicks: int = 0
    conversions: int = 0
    reach: int = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def engagement_rate(self) -> float:
        """Calculate engagement rate."""
        return self.engagements / self.impressions if self.impressions > 0 else 0.0

    @property
    def conversion_rate(self) -> float:
        """Calculate conversion rate."""
        return self.conversions / self.clicks if self.clicks > 0 else 0.0

    @property
    def ctr(self) -> float:
        """Calculate click-through rate."""
        return self.clicks / self.impressions if self.impressions > 0 else 0.0


@dataclass
class ABTestVariant:
    """A/B test variant configuration."""

    variant_id: str
    content_config: Dict[str, Any]
    traffic_allocation: float = 0.5
    metrics: ContentMetrics = field(default_factory=lambda: ContentMetrics(""))


class ABTestFramework:
    """A/B testing framework for content variants."""

    def __init__(self, min_sample_size: int = 100, confidence_level: float = 0.95):
        self.min_sample_size = min_sample_size
        self.confidence_level = confidence_level
        self.active_tests: Dict[str, List[ABTestVariant]] = {}

    def create_test(self, test_id: str, variants: List[ABTestVariant]) -> None:
        """Create a new A/B test."""
        total_allocation = sum(v.traffic_allocation for v in variants)
        if not np.isclose(total_allocation, 1.0):
            raise ValueError(f"Traffic allocations must sum to 1.0, got {total_allocation}")
        self.active_tests[test_id] = variants
        logger.info(f"Created A/B test {test_id} with {len(variants)} variants")

    def select_variant(self, test_id: str) -> Optional[ABTestVariant]:
        """Select variant based on traffic allocation."""
        if test_id not in self.active_tests:
            return None
        variants = self.active_tests[test_id]
        allocations = [v.traffic_allocation for v in variants]
        return np.random.choice(variants, p=allocations)

    def update_metrics(self, test_id: str, variant_id: str, metrics: ContentMetrics) -> None:
        """Update metrics for a variant."""
        if test_id not in self.active_tests:
            return
        for variant in self.active_tests[test_id]:
            if variant.variant_id == variant_id:
                variant.metrics = metrics
                break

    def determine_winner(self, test_id: str, metric: str = "engagement_rate") -> Optional[str]:
        """Determine winning variant using statistical significance."""
        if test_id not in self.active_tests:
            return None

        variants = self.active_tests[test_id]
        if not all(v.metrics.impressions >= self.min_sample_size for v in variants):
            logger.info(f"Test {test_id} needs more samples")
            return None

        metric_values = [getattr(v.metrics, metric) for v in variants]
        best_idx = int(np.argmax(metric_values))
        winner = variants[best_idx]

        logger.info(f"Test {test_id} winner: {winner.variant_id} with {metric}={metric_values[best_idx]:.4f}")
        return winner.variant_id


class ContentStrategyAgent:
    """Reinforcement learning agent for content strategy optimization."""

    def __init__(self, learning_rate: float = 0.1, discount_factor: float = 0.9, epsilon: float = 0.1):
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.epsilon = epsilon
        self.q_table: Dict[str, Dict[str, float]] = {}
        self.action_space = ["increase_frequency", "decrease_frequency", "change_tone", "change_format", "no_change"]

    def get_state(self, metrics: ContentMetrics) -> str:
        """Convert metrics to discrete state."""
        eng_level = "high" if metrics.engagement_rate > 0.05 else "low"
        conv_level = "high" if metrics.conversion_rate > 0.02 else "low"
        return f"{eng_level}_{conv_level}"

    def select_action(self, state: str) -> str:
        """Select action using epsilon-greedy policy."""
        if state not in self.q_table:
            self.q_table[state] = {action: 0.0 for action in self.action_space}

        if np.random.random() < self.epsilon:
            return np.random.choice(self.action_space)

        return max(self.q_table[state].items(), key=lambda x: x[1])[0]

    def calculate_reward(self, metrics: ContentMetrics) -> float:
        """Calculate reward from metrics."""
        return (
            metrics.engagement_rate * 10.0 + metrics.conversion_rate * 50.0 + metrics.ctr * 5.0
        )

    def update(self, state: str, action: str, reward: float, next_state: str) -> None:
        """Update Q-table using Q-learning."""
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in self.action_space}
        if next_state not in self.q_table:
            self.q_table[next_state] = {a: 0.0 for a in self.action_space}

        current_q = self.q_table[state][action]
        max_next_q = max(self.q_table[next_state].values())
        new_q = current_q + self.learning_rate * (reward + self.discount_factor * max_next_q - current_q)
        self.q_table[state][action] = new_q

    def save_model(self, filepath: str) -> None:
        """Save Q-table to file."""
        with open(filepath, "w") as f:
            json.dump(self.q_table, f, indent=2)

    def load_model(self, filepath: str) -> None:
        """Load Q-table from file."""
        with open(filepath) as f:
            self.q_table = json.load(f)
