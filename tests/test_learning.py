"""Tests for the LearningEngine — pure computation, zero LLM calls."""

from __future__ import annotations

import json
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
    strategy_id: str | None = None,
    platform: str = "bluesky",
    source_idea: str = "",
    ab_group: str | None = None,
    ab_pair_id: str | None = None,
) -> str:
    """Insert a published post directly into the database and return its id."""
    pid = post_id or str(uuid.uuid4())
    db.execute(
        """INSERT INTO posts
           (id, text, confidence, status, client_id, run_id, strategy_id,
            platform, source_idea, published_at, content_type, ab_group, ab_pair_id)
           VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'social_post', ?, ?)""",
        (pid, text, confidence, client_id, run_id, strategy_id, platform, source_idea, ab_group, ab_pair_id),
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
    """Insert a metrics row for a given post."""
    db.execute(
        """INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, quote_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), post_id, likes, reposts, replies, quotes),
        commit=True,
    )


def _insert_strategy(
    db: Database,
    *,
    strategy_id: str | None = None,
    themes: list[str] | None = None,
    client_id: str = CLIENT_ID,
    run_id: str = RUN_ID,
) -> str:
    """Insert a strategy row with JSON themes and return its id."""
    from datetime import datetime, timedelta, timezone

    sid = strategy_id or str(uuid.uuid4())
    valid_until = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    db.execute(
        """INSERT INTO strategies
           (id, themes, tone, goals, content_guidelines, posting_frequency,
            valid_until, run_id, client_id)
           VALUES (?, ?, 'professional', '[]', 'none', 'daily',
                   ?, ?, ?)""",
        (sid, json.dumps(themes or ["AI", "automation"]), valid_until, run_id, client_id),
        commit=True,
    )
    return sid


def _insert_ab_experiment(
    db: Database,
    *,
    exp_id: str | None = None,
    client_id: str = CLIENT_ID,
    hypothesis: str = "Short posts outperform long posts",
    variable: str = "length",
    variant_a: str = "Short (under 100 chars)",
    variant_b: str = "Long (over 200 chars)",
    status: str = "active",
    min_pairs: int = 3,
) -> str:
    """Insert an A/B experiment and return its id."""
    eid = exp_id or str(uuid.uuid4())
    db.execute(
        """INSERT INTO ab_experiments
           (id, client_id, hypothesis, variable, variant_a_description,
            variant_b_description, status, min_pairs_required)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (eid, client_id, hypothesis, variable, variant_a, variant_b, status, min_pairs),
        commit=True,
    )
    return eid


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestLearningEngine:
    """Tests for LearningEngine methods."""

    @pytest.fixture(autouse=True)
    def setup(self, test_db):
        """Provide a fresh LearningEngine for every test."""
        self.db = test_db
        self.memory = MemoryStore(test_db)
        self.engine = LearningEngine(test_db, self.memory)

    # ------------------------------------------------------------------
    # 1. No data -> empty / zero results
    # ------------------------------------------------------------------

    def test_process_outcomes_with_no_data(self):
        """With an empty database, process_outcomes returns empty/zero results."""
        result = self.engine.process_outcomes(RUN_ID, CLIENT_ID)

        assert result["calibrations"]["new_records"] == 0
        assert result["calibrations"]["avg_error"] == 0.0
        assert result["anomalies"] == []
        assert result["theme_tracking"] == {}
        assert result["experiments"] == []

    # ------------------------------------------------------------------
    # 2. Calibration records created
    # ------------------------------------------------------------------

    def test_calibration_records_created(self):
        """Published posts with metrics get confidence_calibration rows inserted."""
        # Insert three published posts with varying confidence and engagement
        pid1 = _insert_published_post(self.db, text="Low confidence, high eng", confidence=0.3)
        _insert_metrics(self.db, pid1, likes=10, reposts=5, replies=3, quotes=2)  # 20 total

        pid2 = _insert_published_post(self.db, text="High confidence, low eng", confidence=0.9)
        _insert_metrics(self.db, pid2, likes=1, reposts=0, replies=0, quotes=0)  # 1 total

        pid3 = _insert_published_post(self.db, text="Medium confidence, med eng", confidence=0.5)
        _insert_metrics(self.db, pid3, likes=5, reposts=2, replies=1, quotes=0)  # 8 total

        result = self.engine._update_calibration_records(CLIENT_ID, RUN_ID)

        assert result["new_records"] == 3
        assert result["avg_error"] > 0  # confidence vs percentile will differ

        # Verify rows actually landed in the table
        rows = self.db.fetchall("SELECT * FROM confidence_calibration WHERE client_id = ?", (CLIENT_ID,))
        assert len(rows) == 3

        # Each row should reference one of our posts
        calibrated_post_ids = {r["post_id"] for r in rows}
        assert calibrated_post_ids == {pid1, pid2, pid3}

        # Running again should produce 0 new records (idempotent)
        result2 = self.engine._update_calibration_records(CLIENT_ID, "run-002")
        assert result2["new_records"] == 0

    # ------------------------------------------------------------------
    # 3. Anomaly detection — high performer
    # ------------------------------------------------------------------

    def test_anomaly_detection_high_performer(self):
        """A post with 3x+ average engagement is flagged as high_performer."""
        # Create several normal posts (engagement ~5 each)
        for _ in range(5):
            pid = _insert_published_post(self.db)
            _insert_metrics(self.db, pid, likes=3, reposts=1, replies=1)  # 5

        # Create one viral post (engagement 30 => 6x the avg of 5)
        viral_id = _insert_published_post(self.db, text="Viral post!")
        _insert_metrics(self.db, viral_id, likes=15, reposts=10, replies=5)  # 30

        anomalies = self.engine._detect_anomalies(CLIENT_ID, RUN_ID)

        high_performers = [a for a in anomalies if a["type"] == "high_performer"]
        assert len(high_performers) >= 1

        viral_anomaly = next(a for a in high_performers if a["post_id"] == viral_id)
        assert viral_anomaly["engagement"] == 30
        assert viral_anomaly["ratio"] >= 3.0

    # ------------------------------------------------------------------
    # 4. Anomaly detection — low performer
    # ------------------------------------------------------------------

    def test_anomaly_detection_low_performer(self):
        """A post with 0 engagement (when avg > 2) is flagged as low_performer."""
        # Create posts with decent engagement so average > 2
        for _ in range(4):
            pid = _insert_published_post(self.db)
            _insert_metrics(self.db, pid, likes=5, reposts=3, replies=2)  # 10

        # Create a zero-engagement post
        zero_id = _insert_published_post(self.db, text="Silent failure post")
        _insert_metrics(self.db, zero_id, likes=0, reposts=0, replies=0)

        anomalies = self.engine._detect_anomalies(CLIENT_ID, RUN_ID)

        low_performers = [a for a in anomalies if a["type"] == "low_performer"]
        assert len(low_performers) >= 1

        zero_anomaly = next(a for a in low_performers if a["post_id"] == zero_id)
        assert zero_anomaly["engagement"] == 0
        assert zero_anomaly["average"] > 2

    # ------------------------------------------------------------------
    # 5. Theme performance tracking
    # ------------------------------------------------------------------

    def test_theme_tracking(self):
        """Calculates average engagement per strategy theme."""
        # Create a strategy with two themes
        sid = _insert_strategy(self.db, themes=["AI", "automation"])

        # Link several published posts to this strategy
        for i in range(3):
            pid = _insert_published_post(self.db, strategy_id=sid)
            _insert_metrics(self.db, pid, likes=10 + i, reposts=2, replies=1)

        # Create another strategy with a different theme
        sid2 = _insert_strategy(self.db, themes=["marketing"])
        pid2 = _insert_published_post(self.db, strategy_id=sid2)
        _insert_metrics(self.db, pid2, likes=1, reposts=0, replies=0)

        theme_avgs = self.engine._track_theme_performance(CLIENT_ID, RUN_ID)

        # We should get entries for "AI", "automation", and "marketing"
        assert "AI" in theme_avgs
        assert "automation" in theme_avgs
        assert "marketing" in theme_avgs

        # AI and automation should have the same average (same posts)
        assert theme_avgs["AI"] == theme_avgs["automation"]
        # Average for AI/automation: each post has engagement (10+2+1)=13, (11+2+1)=14, (12+2+1)=15 -> avg 14
        assert theme_avgs["AI"] == 14.0

        # Marketing should be lower (1 engagement)
        assert theme_avgs["marketing"] == 1.0

    # ------------------------------------------------------------------
    # 6. process_outcomes returns all expected keys
    # ------------------------------------------------------------------

    def test_process_outcomes_returns_all_keys(self):
        """process_outcomes dict contains calibrations, anomalies, theme_tracking, experiments."""
        # Seed a small amount of data so paths are exercised
        pid = _insert_published_post(self.db, confidence=0.6)
        _insert_metrics(self.db, pid, likes=3, reposts=1, replies=1)

        result = self.engine.process_outcomes(RUN_ID, CLIENT_ID)

        assert set(result.keys()) == {"calibrations", "anomalies", "theme_tracking", "experiments"}
        # calibrations should be a dict
        assert isinstance(result["calibrations"], dict)
        assert "new_records" in result["calibrations"]
        assert "avg_error" in result["calibrations"]
        # anomalies should be a list
        assert isinstance(result["anomalies"], list)
        # theme_tracking should be a dict
        assert isinstance(result["theme_tracking"], dict)
        # experiments should be a list
        assert isinstance(result["experiments"], list)
