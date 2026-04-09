"""Tests for content optimization agent."""

import json
import tempfile
from datetime import datetime

import numpy as np
import pytest

from ortobahn.agents.content_optimizer import (
    ABTestFramework,
    ABTestVariant,
    ContentMetrics,
    ContentStrategyAgent,
)


class TestContentMetrics:
    """Test ContentMetrics dataclass."""

    def test_engagement_rate(self):
        """Test engagement rate calculation."""
        metrics = ContentMetrics(content_id="test", impressions=100, engagements=10)
        assert metrics.engagement_rate == 0.1

    def test_conversion_rate(self):
        """Test conversion rate calculation."""
        metrics = ContentMetrics(content_id="test", clicks=50, conversions=5)
        assert metrics.conversion_rate == 0.1

    def test_ctr(self):
        """Test click-through rate calculation."""
        metrics = ContentMetrics(content_id="test", impressions=100, clicks=5)
        assert metrics.ctr == 0.05

    def test_zero_division(self):
        """Test metrics with zero impressions."""
        metrics = ContentMetrics(content_id="test", impressions=0)
        assert metrics.engagement_rate == 0.0
        assert metrics.ctr == 0.0


class TestABTestFramework:
    """Test A/B testing framework."""

    def test_create_test(self):
        """Test creating an A/B test."""
        framework = ABTestFramework()
        variants = [
            ABTestVariant("A", {"tone": "professional"}, 0.5),
            ABTestVariant("B", {"tone": "casual"}, 0.5),
        ]
        framework.create_test("test1", variants)
        assert "test1" in framework.active_tests
        assert len(framework.active_tests["test1"]) == 2

    def test_invalid_allocation(self):
        """Test invalid traffic allocation."""
        framework = ABTestFramework()
        variants = [
            ABTestVariant("A", {"tone": "professional"}, 0.6),
            ABTestVariant("B", {"tone": "casual"}, 0.5),
        ]
        with pytest.raises(ValueError, match="Traffic allocations must sum to 1.0"):
            framework.create_test("test1", variants)

    def test_select_variant(self):
        """Test variant selection."""
        np.random.seed(42)
        framework = ABTestFramework()
        variants = [
            ABTestVariant("A", {"tone": "professional"}, 0.5),
            ABTestVariant("B", {"tone": "casual"}, 0.5),
        ]
        framework.create_test("test1", variants)
        variant = framework.select_variant("test1")
        assert variant is not None
        assert variant.variant_id in ["A", "B"]

    def test_update_metrics(self):
        """Test updating variant metrics."""
        framework = ABTestFramework()
        variants = [
            ABTestVariant("A", {"tone": "professional"}, 0.5),
            ABTestVariant("B", {"tone": "casual"}, 0.5),
        ]
        framework.create_test("test1", variants)
        metrics = ContentMetrics("content1", "A", impressions=100, engagements=10)
        framework.update_metrics("test1", "A", metrics)
        assert framework.active_tests["test1"][0].metrics.impressions == 100

    def test_determine_winner(self):
        """Test winner determination."""
        framework = ABTestFramework(min_sample_size=50)
        variants = [
            ABTestVariant("A", {"tone": "professional"}, 0.5),
            ABTestVariant("B", {"tone": "casual"}, 0.5),
        ]
        framework.create_test("test1", variants)
        framework.update_metrics("test1", "A", ContentMetrics("c1", "A", 100, 5))
        framework.update_metrics("test1", "B", ContentMetrics("c1", "B", 100, 15))
        winner = framework.determine_winner("test1")
        assert winner == "B"


class TestContentStrategyAgent:
    """Test reinforcement learning agent."""

    def test_initialization(self):
        """Test agent initialization."""
        agent = ContentStrategyAgent()
        assert agent.learning_rate == 0.1
        assert agent.discount_factor == 0.9
        assert len(agent.action_space) == 5

    def test_get_state(self):
        """Test state extraction from metrics."""
        agent = ContentStrategyAgent()
        metrics = ContentMetrics("test", impressions=100, engagements=10, clicks=50, conversions=2)
        state = agent.get_state(metrics)
        assert state in ["high_high", "high_low", "low_high", "low_low"]

    def test_select_action(self):
        """Test action selection."""
        agent = ContentStrategyAgent(epsilon=0.0)
        state = "high_high"
        action = agent.select_action(state)
        assert action in agent.action_space

    def test_calculate_reward(self):
        """Test reward calculation."""
        agent = ContentStrategyAgent()
        metrics = ContentMetrics("test", impressions=100, engagements=10, clicks=5, conversions=1)
        reward = agent.calculate_reward(metrics)
        assert reward > 0

    def test_update_q_table(self):
        """Test Q-table update."""
        agent = ContentStrategyAgent()
        agent.update("high_high", "no_change", 10.0, "high_high")
        assert "high_high" in agent.q_table
        assert agent.q_table["high_high"]["no_change"] > 0

    def test_save_load_model(self):
        """Test model persistence."""
        agent = ContentStrategyAgent()
        agent.update("high_high", "no_change", 10.0, "high_high")

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            filepath = f.name

        agent.save_model(filepath)
        new_agent = ContentStrategyAgent()
        new_agent.load_model(filepath)
        assert new_agent.q_table == agent.q_table
