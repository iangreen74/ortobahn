"""Tests for listening analytics aggregation."""

from __future__ import annotations

import uuid

import pytest

from ortobahn.listening_analytics import aggregate_daily, get_listening_summary


@pytest.fixture
def analytics_client(test_db):
    """Create a client for analytics tests."""
    test_db.create_client({"id": "analytics_test", "name": "Analytics Test"}, start_trial=False)
    return "analytics_test"


@pytest.fixture
def sample_conversations(test_db, analytics_client):
    """Insert sample conversations for aggregation."""
    date_str = "2026-03-01"
    for i in range(5):
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status, discovered_at)
            VALUES (?, ?, 'bluesky', 'keyword', 'AI automation', ?, ?, ?,
                    'Test post about AI', ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                analytics_client,
                f"ext_{i}",
                f"uri_{i}",
                f"user{i}.bsky.social",
                10 + i,
                0.7 + i * 0.05,
                "replied" if i < 2 else "queued",
                f"{date_str}T{10 + i}:00:00",
            ),
            commit=True,
        )
    return date_str


class TestAggregate:
    def test_aggregate_creates_row(self, test_db, analytics_client, sample_conversations):
        """Aggregation creates a row for each platform-day."""
        count = aggregate_daily(test_db, analytics_client, sample_conversations)
        assert count == 1  # One platform (bluesky)

        row = test_db.fetchone(
            "SELECT * FROM listening_analytics WHERE client_id=? AND date=?",
            (analytics_client, sample_conversations),
        )
        assert row is not None
        assert row["platform"] == "bluesky"
        assert row["conversations_discovered"] == 5
        assert row["conversations_replied"] == 2

    def test_aggregate_idempotent(self, test_db, analytics_client, sample_conversations):
        """Re-running aggregation doesn't create duplicates."""
        count1 = aggregate_daily(test_db, analytics_client, sample_conversations)
        count2 = aggregate_daily(test_db, analytics_client, sample_conversations)
        assert count1 == 1
        assert count2 == 0

    def test_aggregate_no_data(self, test_db, analytics_client):
        """No conversations means no aggregation rows."""
        count = aggregate_daily(test_db, analytics_client, "2026-01-01")
        assert count == 0

    def test_aggregate_multiple_platforms(self, test_db, analytics_client):
        """Multiple platforms get separate rows."""
        date_str = "2026-03-02"
        for platform in ("bluesky", "twitter"):
            test_db.execute(
                """INSERT INTO discovered_conversations
                (id, client_id, platform, source_type, source_query,
                 external_id, external_uri, author_handle, text_content,
                 engagement_score, relevance_score, status, discovered_at)
                VALUES (?, ?, ?, 'keyword', 'test', ?, ?, 'user.bsky',
                        'test', 10, 0.8, 'queued', ?)""",
                (
                    str(uuid.uuid4()),
                    analytics_client,
                    platform,
                    f"ext_{platform}",
                    f"uri_{platform}",
                    f"{date_str}T12:00:00",
                ),
                commit=True,
            )
        count = aggregate_daily(test_db, analytics_client, date_str)
        assert count == 2


class TestSummary:
    def test_summary_with_data(self, test_db, analytics_client):
        """Summary returns correct totals."""
        for i, date in enumerate(["2026-03-01", "2026-03-02", "2026-03-03"]):
            test_db.execute(
                """INSERT INTO listening_analytics
                (id, client_id, date, platform, conversations_discovered,
                 conversations_replied, avg_relevance_score, top_keywords)
                VALUES (?, ?, ?, 'bluesky', ?, ?, 0.75, '["AI"]')""",
                (str(uuid.uuid4()), analytics_client, date, 10 + i * 5, i * 2),
                commit=True,
            )

        summary = get_listening_summary(test_db, analytics_client, days=30)
        # i=0: 10, i=1: 15, i=2: 20 = 45
        assert summary["total_discovered"] == 45
        assert summary["total_replied"] == 6  # 0 + 2 + 4
        assert summary["avg_relevance"] == 0.75
        assert "bluesky" in summary["platforms"]
        assert len(summary["daily"]) == 3

    def test_summary_empty(self, test_db, analytics_client):
        """Empty summary returns zeros."""
        summary = get_listening_summary(test_db, analytics_client)
        assert summary["total_discovered"] == 0
        assert summary["reply_rate"] == 0.0
