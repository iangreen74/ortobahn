"""Tests for the Predictive Timing module (TopicVelocityTracker)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ortobahn.predictive_timing import TopicVelocityTracker


@pytest.fixture
def tracker(test_db):
    return TopicVelocityTracker(db=test_db)


def _insert_topic(
    db,
    *,
    topic_id,
    title,
    source="test",
    mention_count=1,
    first_seen_at=None,
    last_seen_at=None,
    velocity_score=1.0,
    peak_detected=0,
):
    """Helper to insert a topic_velocity row with explicit timestamps."""
    now = datetime.now(timezone.utc).isoformat()
    first_seen = first_seen_at or now
    last_seen = last_seen_at or now
    db.execute(
        "INSERT INTO topic_velocity "
        "(id, topic_title, source, mention_count, first_seen_at, last_seen_at, "
        "velocity_score, peak_detected) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (topic_id, title, source, mention_count, first_seen, last_seen, velocity_score, peak_detected),
        commit=True,
    )


# ── record_topics ───────────────────────────────────────────────────────────


class TestRecordTopics:
    """Tests for TopicVelocityTracker.record_topics()."""

    def test_inserts_new_topics(self, tracker, test_db):
        topics = [
            {"title": "Rust in Production", "source": "hackernews"},
            {"title": "Python 3.14 Released", "source": "reddit"},
        ]
        count = tracker.record_topics(topics)

        assert count == 2

        rows = test_db.fetchall(
            "SELECT topic_title, source, mention_count, velocity_score, peak_detected "
            "FROM topic_velocity ORDER BY topic_title"
        )
        assert len(rows) == 2

        row0 = dict(rows[0])
        assert row0["topic_title"] == "python 3.14 released"
        assert row0["source"] == "reddit"
        assert row0["mention_count"] == 1
        assert row0["velocity_score"] == 1.0
        assert row0["peak_detected"] == 0

        row1 = dict(rows[1])
        assert row1["topic_title"] == "rust in production"
        assert row1["source"] == "hackernews"

    def test_increments_mention_count_on_reseen(self, tracker, test_db):
        topics = [{"title": "AI Safety", "source": "rss"}]

        tracker.record_topics(topics)
        tracker.record_topics(topics)
        tracker.record_topics(topics)

        row = test_db.fetchone(
            "SELECT mention_count, velocity_score FROM topic_velocity WHERE topic_title = 'ai safety'"
        )
        assert row["mention_count"] == 3
        # velocity_score mirrors the raw count
        assert row["velocity_score"] == 3.0

    def test_ignores_empty_and_whitespace_titles(self, tracker, test_db):
        topics = [
            {"title": "", "source": "rss"},
            {"title": "   ", "source": "rss"},
            {"title": None, "source": "rss"},
            {"source": "rss"},  # missing title key entirely
        ]
        count = tracker.record_topics(topics)

        assert count == 0

        row = test_db.fetchone("SELECT COUNT(*) as cnt FROM topic_velocity")
        assert row["cnt"] == 0


# ── get_emerging_topics ─────────────────────────────────────────────────────


class TestGetEmerging:
    """Tests for TopicVelocityTracker.get_emerging_topics()."""

    def test_returns_topics_above_min_mentions(self, tracker, test_db):
        _insert_topic(
            test_db, topic_id="e1", title="emerging topic", mention_count=3, velocity_score=3.0, peak_detected=0
        )
        _insert_topic(
            test_db, topic_id="e2", title="just appeared", mention_count=1, velocity_score=1.0, peak_detected=0
        )

        results = tracker.get_emerging_topics(min_mentions=2)

        assert len(results) == 1
        assert results[0]["topic_title"] == "emerging topic"

    def test_excludes_peaked_topics(self, tracker, test_db):
        _insert_topic(
            test_db, topic_id="p1", title="peaked topic", mention_count=5, velocity_score=5.0, peak_detected=1
        )
        _insert_topic(
            test_db, topic_id="p2", title="still rising", mention_count=5, velocity_score=5.0, peak_detected=0
        )

        results = tracker.get_emerging_topics(min_mentions=2)

        titles = [r["topic_title"] for r in results]
        assert "still rising" in titles
        assert "peaked topic" not in titles

    def test_respects_min_mentions_parameter(self, tracker, test_db):
        _insert_topic(test_db, topic_id="m1", title="topic a", mention_count=4, velocity_score=4.0)
        _insert_topic(test_db, topic_id="m2", title="topic b", mention_count=6, velocity_score=6.0)
        _insert_topic(test_db, topic_id="m3", title="topic c", mention_count=2, velocity_score=2.0)

        results_5 = tracker.get_emerging_topics(min_mentions=5)
        assert len(results_5) == 1
        assert results_5[0]["topic_title"] == "topic b"

        results_3 = tracker.get_emerging_topics(min_mentions=3)
        assert len(results_3) == 2

        results_1 = tracker.get_emerging_topics(min_mentions=1)
        assert len(results_1) == 3

    def test_sorted_by_velocity_descending(self, tracker, test_db):
        _insert_topic(test_db, topic_id="s1", title="slow", mention_count=2, velocity_score=2.0)
        _insert_topic(test_db, topic_id="s2", title="fast", mention_count=10, velocity_score=10.0)
        _insert_topic(test_db, topic_id="s3", title="medium", mention_count=5, velocity_score=5.0)

        results = tracker.get_emerging_topics(min_mentions=1)

        assert results[0]["topic_title"] == "fast"
        assert results[1]["topic_title"] == "medium"
        assert results[2]["topic_title"] == "slow"


# ── detect_peaks ────────────────────────────────────────────────────────────


class TestDetectPeaks:
    """Tests for TopicVelocityTracker.detect_peaks()."""

    def test_marks_old_topics_as_peaked(self, tracker, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        _insert_topic(
            test_db,
            topic_id="t1",
            title="old topic",
            source="test",
            mention_count=5,
            first_seen_at=old_time,
            last_seen_at=old_time,
            velocity_score=5.0,
            peak_detected=0,
        )

        peaked_count = tracker.detect_peaks()

        assert peaked_count == 1
        row = test_db.fetchone("SELECT peak_detected FROM topic_velocity WHERE id = 't1'")
        assert row["peak_detected"] == 1

    def test_does_not_peak_recently_seen_topics(self, tracker, test_db):
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_topic(
            test_db,
            topic_id="t2",
            title="recent topic",
            mention_count=5,
            first_seen_at=recent_time,
            last_seen_at=recent_time,
            velocity_score=5.0,
            peak_detected=0,
        )

        peaked_count = tracker.detect_peaks()

        assert peaked_count == 0
        row = test_db.fetchone("SELECT peak_detected FROM topic_velocity WHERE id = 't2'")
        assert row["peak_detected"] == 0

    def test_requires_minimum_3_mentions_before_peaking(self, tracker, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        # Only 2 mentions — should NOT be marked as peaked
        _insert_topic(
            test_db,
            topic_id="t3",
            title="low mention topic",
            mention_count=2,
            first_seen_at=old_time,
            last_seen_at=old_time,
            velocity_score=2.0,
            peak_detected=0,
        )

        peaked_count = tracker.detect_peaks()

        assert peaked_count == 0
        row = test_db.fetchone("SELECT peak_detected FROM topic_velocity WHERE id = 't3'")
        assert row["peak_detected"] == 0

    def test_does_not_re_peak_already_peaked(self, tracker, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        _insert_topic(
            test_db,
            topic_id="t4",
            title="already peaked",
            mention_count=10,
            first_seen_at=old_time,
            last_seen_at=old_time,
            velocity_score=10.0,
            peak_detected=1,
        )

        peaked_count = tracker.detect_peaks()

        assert peaked_count == 0


# ── cleanup_old_topics ──────────────────────────────────────────────────────


class TestCleanup:
    """Tests for TopicVelocityTracker.cleanup_old_topics()."""

    def test_removes_topics_older_than_max_age(self, tracker, test_db):
        old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        _insert_topic(test_db, topic_id="c1", title="ancient topic", first_seen_at=old_time, last_seen_at=old_time)

        removed = tracker.cleanup_old_topics(max_age_days=30)

        assert removed == 1
        row = test_db.fetchone("SELECT COUNT(*) as cnt FROM topic_velocity WHERE id = 'c1'")
        assert row["cnt"] == 0

    def test_keeps_recent_topics(self, tracker, test_db):
        recent_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        _insert_topic(test_db, topic_id="c2", title="fresh topic", first_seen_at=recent_time, last_seen_at=recent_time)

        removed = tracker.cleanup_old_topics(max_age_days=30)

        assert removed == 0
        row = test_db.fetchone("SELECT COUNT(*) as cnt FROM topic_velocity WHERE id = 'c2'")
        assert row["cnt"] == 1

    def test_respects_max_age_days_parameter(self, tracker, test_db):
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        _insert_topic(
            test_db, topic_id="c3", title="ten day topic", first_seen_at=ten_days_ago, last_seen_at=ten_days_ago
        )

        # max_age_days=15 should keep it
        removed_15 = tracker.cleanup_old_topics(max_age_days=15)
        assert removed_15 == 0

        # max_age_days=7 should remove it
        removed_7 = tracker.cleanup_old_topics(max_age_days=7)
        assert removed_7 == 1


# ── get_velocity_summary ────────────────────────────────────────────────────


class TestSummary:
    """Tests for TopicVelocityTracker.get_velocity_summary()."""

    def test_returns_correct_counts(self, tracker, test_db):
        # 1 emerging (mention_count>=2, peak_detected=0)
        _insert_topic(test_db, topic_id="s1", title="emerging a", mention_count=3, velocity_score=3.0, peak_detected=0)
        # 1 peaked
        _insert_topic(test_db, topic_id="s2", title="peaked a", mention_count=5, velocity_score=5.0, peak_detected=1)
        # 1 topic with only 1 mention (not emerging, not peaked)
        _insert_topic(test_db, topic_id="s3", title="new topic", mention_count=1, velocity_score=1.0, peak_detected=0)

        summary = tracker.get_velocity_summary()

        assert summary["total_tracked"] == 3
        assert summary["emerging"] == 1
        assert summary["peaked"] == 1

    def test_empty_table_returns_zeros(self, tracker):
        summary = tracker.get_velocity_summary()

        assert summary["total_tracked"] == 0
        assert summary["emerging"] == 0
        assert summary["peaked"] == 0
