"""Tests for the cross-agent SharedInsightBus."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ortobahn.shared_insights import (
    CI_FIX_PATTERN,
    CLIENT_HEALTH,
    CONTENT_TREND,
    COST_ANOMALY,
    DEPLOY_HEALTH,
    PLATFORM_ISSUE,
    SharedInsightBus,
)


class TestSharedInsightBus:
    def test_publish_creates_insight(self, test_db):
        bus = SharedInsightBus(test_db)
        insight_id = bus.publish(
            source_agent="sre",
            insight_type=PLATFORM_ISSUE,
            content="Database connection pool exhausted",
            confidence=0.8,
            metadata={"component": "postgres"},
        )

        assert insight_id is not None
        results = bus.query(insight_type=PLATFORM_ISSUE)
        assert len(results) == 1
        assert results[0]["source_agent"] == "sre"
        assert results[0]["content"] == "Database connection pool exhausted"
        assert results[0]["confidence"] == 0.8
        assert results[0]["reinforcement_count"] == 0

    def test_publish_deduplicates(self, test_db):
        """Same source + type + identical content should reinforce, not duplicate."""
        bus = SharedInsightBus(test_db)
        id1 = bus.publish(
            source_agent="sre",
            insight_type=PLATFORM_ISSUE,
            content="Database connection pool exhausted on primary server. Multiple timeouts observed.",
            confidence=0.7,
        )
        id2 = bus.publish(
            source_agent="sre",
            insight_type=PLATFORM_ISSUE,
            content="Database connection pool exhausted on primary server. Multiple timeouts observed.",
            confidence=0.7,
        )

        # Should return the same ID (reinforced)
        assert id1 == id2

        results = bus.query(insight_type=PLATFORM_ISSUE)
        assert len(results) == 1
        assert results[0]["reinforcement_count"] == 1
        assert results[0]["confidence"] == 0.75  # 0.7 + 0.05

    def test_publish_different_content_creates_new(self, test_db):
        """Different content from the same agent + type should create a new insight."""
        bus = SharedInsightBus(test_db)
        id1 = bus.publish(
            source_agent="sre",
            insight_type=PLATFORM_ISSUE,
            content="Database connection pool exhausted",
            confidence=0.7,
        )
        id2 = bus.publish(
            source_agent="sre",
            insight_type=PLATFORM_ISSUE,
            content="Redis cache miss rate spiked to 40%",
            confidence=0.6,
        )

        assert id1 != id2

        results = bus.query(insight_type=PLATFORM_ISSUE)
        assert len(results) == 2

    def test_query_by_type(self, test_db):
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Issue A")
        bus.publish(source_agent="cfo", insight_type=COST_ANOMALY, content="Budget exceeded")
        bus.publish(source_agent="learning", insight_type=CONTENT_TREND, content="Viral post")

        platform_issues = bus.query(insight_type=PLATFORM_ISSUE)
        assert len(platform_issues) == 1
        assert platform_issues[0]["insight_type"] == PLATFORM_ISSUE

        cost_issues = bus.query(insight_type=COST_ANOMALY)
        assert len(cost_issues) == 1
        assert cost_issues[0]["insight_type"] == COST_ANOMALY

    def test_query_by_confidence_threshold(self, test_db):
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Low conf", confidence=0.2)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="High conf", confidence=0.9)

        # Default threshold is 0.3
        results = bus.query(min_confidence=0.3)
        assert len(results) == 1
        assert results[0]["content"] == "High conf"

        # Lower threshold gets both
        results = bus.query(min_confidence=0.1)
        assert len(results) == 2

    def test_query_sorted_by_relevance(self, test_db):
        """Higher confidence insights should rank higher (same recency)."""
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Medium priority", confidence=0.5)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="High priority", confidence=0.95)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Low priority", confidence=0.35)

        results = bus.query(min_confidence=0.3)
        assert len(results) == 3
        # Highest confidence first (all have similar recency so confidence dominates)
        assert results[0]["content"] == "High priority"

    def test_query_since_hours_filter(self, test_db):
        """Insights older than since_hours should be excluded."""
        bus = SharedInsightBus(test_db)

        # Insert an insight with a timestamp 10 days ago
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        test_db.execute(
            """INSERT INTO shared_insights
               (id, source_agent, insight_type, content, confidence, metadata,
                reinforcement_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '{}', 0, ?, ?)""",
            ("old-id", "sre", PLATFORM_ISSUE, "Old issue", 0.9, old_time, old_time),
            commit=True,
        )

        # Insert a recent one
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Recent issue", confidence=0.8)

        # Default since_hours=168 (7 days) should exclude the 10-day-old one
        results = bus.query(insight_type=PLATFORM_ISSUE, since_hours=168)
        assert len(results) == 1
        assert results[0]["content"] == "Recent issue"

        # Wider window should include both
        results = bus.query(insight_type=PLATFORM_ISSUE, since_hours=300)
        assert len(results) == 2

    def test_get_insights_for_agent_mapping(self, test_db):
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Platform down")
        bus.publish(source_agent="cifix", insight_type=CI_FIX_PATTERN, content="Flaky test fix")
        bus.publish(source_agent="cfo", insight_type=COST_ANOMALY, content="Over budget")
        bus.publish(source_agent="learning", insight_type=CONTENT_TREND, content="Viral post")
        bus.publish(source_agent="ops", insight_type=CLIENT_HEALTH, content="Client churning")
        bus.publish(source_agent="pipeline", insight_type=DEPLOY_HEALTH, content="Deploy OK")

        # CEO sees all types
        ceo_insights = bus.get_insights_for_agent("ceo")
        assert len(ceo_insights) == 5  # limited to 5 by default

        ceo_all = bus.get_insights_for_agent("ceo", limit=10)
        assert len(ceo_all) == 6

        # CIFix sees CI_FIX_PATTERN + DEPLOY_HEALTH
        cifix_insights = bus.get_insights_for_agent("cifix", limit=10)
        types_found = {i["insight_type"] for i in cifix_insights}
        assert types_found <= {CI_FIX_PATTERN, DEPLOY_HEALTH}
        assert len(cifix_insights) == 2

        # SRE sees DEPLOY_HEALTH, PLATFORM_ISSUE, COST_ANOMALY
        sre_insights = bus.get_insights_for_agent("sre", limit=10)
        types_found = {i["insight_type"] for i in sre_insights}
        assert types_found <= {DEPLOY_HEALTH, PLATFORM_ISSUE, COST_ANOMALY}
        assert len(sre_insights) == 3

        # Creator sees only CONTENT_TREND
        creator_insights = bus.get_insights_for_agent("creator", limit=10)
        assert len(creator_insights) == 1
        assert creator_insights[0]["insight_type"] == CONTENT_TREND

        # Unknown agent gets nothing
        unknown = bus.get_insights_for_agent("nonexistent")
        assert unknown == []

    def test_summarize_format(self, test_db):
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="DB pool exhausted")
        bus.publish(source_agent="cfo", insight_type=COST_ANOMALY, content="Over budget by 20%")

        summary = bus.summarize()
        assert "Cross-Agent Insights" in summary
        assert "PLATFORM_ISSUE" in summary
        assert "COST_ANOMALY" in summary
        assert "DB pool exhausted" in summary
        assert "Over budget by 20%" in summary
        assert "sre" in summary
        assert "cfo" in summary

    def test_summarize_empty(self, test_db):
        bus = SharedInsightBus(test_db)
        summary = bus.summarize()
        assert summary == ""

    def test_summarize_filtered_by_type(self, test_db):
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Platform down")
        bus.publish(source_agent="cfo", insight_type=COST_ANOMALY, content="Over budget")

        summary = bus.summarize(insight_type=PLATFORM_ISSUE)
        assert "Platform down" in summary
        assert "Over budget" not in summary

    def test_publish_metadata_roundtrip(self, test_db):
        """Metadata should be stored as JSON and retrievable."""
        bus = SharedInsightBus(test_db)
        bus.publish(
            source_agent="sre",
            insight_type=PLATFORM_ISSUE,
            content="Issue with metadata",
            metadata={"component": "redis", "severity": "high"},
        )

        results = bus.query(insight_type=PLATFORM_ISSUE)
        assert len(results) == 1
        import json

        meta = json.loads(results[0]["metadata"])
        assert meta["component"] == "redis"
        assert meta["severity"] == "high"

    def test_confidence_capped_at_one(self, test_db):
        """Repeated reinforcement should not push confidence above 1.0."""
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Recurring issue", confidence=0.95)
        # Reinforce multiple times
        for _ in range(10):
            bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Recurring issue", confidence=0.95)

        results = bus.query(insight_type=PLATFORM_ISSUE)
        assert len(results) == 1
        assert results[0]["confidence"] <= 1.0

    def test_query_respects_limit(self, test_db):
        bus = SharedInsightBus(test_db)
        for i in range(20):
            bus.publish(
                source_agent="sre",
                insight_type=PLATFORM_ISSUE,
                content=f"Issue number {i:03d} with unique content",
                confidence=0.5 + i * 0.02,
            )

        results = bus.query(insight_type=PLATFORM_ISSUE, limit=5)
        assert len(results) == 5

    def test_summarize_shows_reinforcement(self, test_db):
        """Reinforced insights should show reinforcement count in summary."""
        bus = SharedInsightBus(test_db)
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Repeated issue")
        bus.publish(source_agent="sre", insight_type=PLATFORM_ISSUE, content="Repeated issue")

        summary = bus.summarize()
        assert "reinforced 1x" in summary
