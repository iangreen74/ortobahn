"""Tests for LearningEngine._generate_anomaly_experiments()."""

from __future__ import annotations

import uuid

import pytest

from ortobahn.db import Database
from ortobahn.learning import LearningEngine
from ortobahn.memory import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUN_ID = "test-run-001"
CLIENT_ID = "default"


def _insert_published_post(
    db: Database,
    *,
    post_id: str | None = None,
    text: str = "Test post",
    confidence: float = 0.7,
    client_id: str = CLIENT_ID,
    run_id: str = RUN_ID,
    platform: str = "bluesky",
) -> str:
    """Insert a published post directly into the database and return its id."""
    pid = post_id or str(uuid.uuid4())
    db.execute(
        """INSERT INTO posts
           (id, text, confidence, status, client_id, run_id,
            platform, published_at, content_type)
           VALUES (?, ?, ?, 'published', ?, ?, ?, CURRENT_TIMESTAMP, 'social_post')""",
        (pid, text, confidence, client_id, run_id, platform),
        commit=True,
    )
    return pid


def _insert_metrics(
    db: Database,
    post_id: str,
    *,
    likes: int = 0,
    reposts: int = 0,
    replies: int = 0,
    quotes: int = 0,
) -> None:
    db.execute(
        """INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), post_id, likes, reposts, replies, quotes),
        commit=True,
    )


def _make_anomaly(
    anomaly_type: str = "high_performer",
    text_preview: str = "Short viral hit",
    post_id: str = "post-001",
    engagement: int = 100,
) -> dict:
    """Create a synthetic anomaly dict matching _detect_anomalies() output."""
    return {
        "type": anomaly_type,
        "post_id": post_id,
        "engagement": engagement,
        "average": 10.0,
        "ratio": engagement / 10.0,
        "text_preview": text_preview,
    }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestAnomalyExperiments:
    @pytest.fixture(autouse=True)
    def setup(self, test_db):
        self.db = test_db
        self.memory = MemoryStore(test_db)
        self.engine = LearningEngine(test_db, self.memory)

    def test_no_experiments_without_anomalies(self):
        """No anomalies -> no experiments created."""
        result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, [])
        assert result == []

    def test_creates_experiment_from_short_viral_post(self):
        """A high_performer anomaly with short text (< 100 chars) creates a length experiment."""
        anomalies = [_make_anomaly(text_preview="AI is eating the world")]

        result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, anomalies)

        assert len(result) == 1
        exp = result[0]
        assert exp["variable"] == "anomaly_length"
        assert exp["status"] == "active"
        assert "short" in exp["hypothesis"].lower() or "Short" in exp["hypothesis"]

        # Verify it was actually saved to the database
        row = self.db.fetchone("SELECT * FROM ab_experiments WHERE id = ?", (exp["id"],))
        assert row is not None
        assert row["status"] == "active"

    def test_creates_experiment_from_question_post(self):
        """A high_performer anomaly containing '?' creates a question experiment."""
        long_question = "What if autonomous agents could run entire marketing departments without human oversight? That seems like a really big shift in how businesses operate"
        assert len(long_question) >= 100  # ensure it's not caught by length check
        anomalies = [_make_anomaly(text_preview=long_question)]

        result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, anomalies)

        assert len(result) == 1
        assert result[0]["variable"] == "anomaly_question"

    def test_does_not_stack_active_experiments(self):
        """If an anomaly_ experiment is already active, no new one is created."""
        # Insert an active anomaly experiment
        self.db.execute(
            """INSERT INTO ab_experiments
               (id, client_id, hypothesis, variable, variant_a_description,
                variant_b_description, status, min_pairs_required)
               VALUES (?, ?, ?, ?, ?, ?, 'active', 5)""",
            (
                "existing-exp",
                CLIENT_ID,
                "Existing anomaly experiment",
                "anomaly_length",
                "Short",
                "Long",
            ),
            commit=True,
        )

        anomalies = [_make_anomaly(text_preview="Short viral")]
        result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, anomalies)

        assert result == []

    def test_handles_style_evolution_error_gracefully(self):
        """If StyleEvolution.create_experiment raises, return empty list (non-fatal)."""
        from unittest.mock import patch

        anomalies = [_make_anomaly(text_preview="Short viral")]

        with patch(
            "ortobahn.style_evolution.StyleEvolution.create_experiment",
            side_effect=RuntimeError("DB exploded"),
        ):
            result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, anomalies)

        assert result == []

    def test_only_processes_high_performers(self):
        """low_performer anomalies should be ignored."""
        anomalies = [_make_anomaly(anomaly_type="low_performer", text_preview="Flopped")]
        result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, anomalies)
        assert result == []

    def test_creates_data_driven_experiment(self):
        """A high_performer with numbers/percentages creates a data experiment."""
        text = "Our conversion rate jumped 45% after switching to autonomous agents — here is the full breakdown of results"
        assert len(text) >= 100
        assert "?" not in text
        anomalies = [_make_anomaly(text_preview=text)]

        result = self.engine._generate_anomaly_experiments(CLIENT_ID, RUN_ID, anomalies)

        assert len(result) == 1
        assert result[0]["variable"] == "anomaly_data"
