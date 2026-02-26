"""Tests for the Insight Generator agent."""

from __future__ import annotations

import json

import pytest

from ortobahn.agents.insight_generator import (
    InsightGeneratorAgent,
    InsightReport,
)


@pytest.fixture()
def _seed_client(test_db):
    test_db.create_client(
        {
            "id": "insight-test",
            "name": "Insight Test Co",
            "industry": "tech",
            "target_audience": "developers",
            "brand_voice": "professional",
        }
    )


@pytest.fixture()
def _seed_posts_with_metrics(test_db, _seed_client):
    """Seed posts with varying engagement levels."""
    # Low engagement posts (average ~5 total engagement)
    for i in range(5):
        pid = test_db.save_post(
            text=f"Regular post {i}",
            run_id=f"insight-run-{i}",
            status="published",
            confidence=0.7,
            client_id="insight-test",
            platform="bluesky",
        )
        test_db.execute(
            "UPDATE posts SET published_at=CURRENT_TIMESTAMP WHERE id=?",
            (pid,),
            commit=True,
        )
        test_db.execute(
            "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at)"
            " VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (f"m-low-{i}", pid, 3, 1, 1),
            commit=True,
        )

    # High engagement post (way above average)
    high_pid = test_db.save_post(
        text="This post went viral and everyone loved it!",
        run_id="insight-run-high",
        status="published",
        confidence=0.9,
        client_id="insight-test",
        platform="bluesky",
    )
    test_db.execute(
        "UPDATE posts SET published_at=CURRENT_TIMESTAMP WHERE id=?",
        (high_pid,),
        commit=True,
    )
    test_db.execute(
        "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at)"
        " VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        ("m-high", high_pid, 50, 20, 10),
        commit=True,
    )
    return high_pid


class TestFindHighPerformers:
    def test_no_high_performers_with_no_data(self, test_db, _seed_client):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        result = agent._find_high_performers("insight-test")
        assert result == []

    def test_finds_high_performers(self, test_db, _seed_posts_with_metrics):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        result = agent._find_high_performers("insight-test")
        assert len(result) >= 1
        # The high engagement post should be found
        ids = [r["id"] for r in result]
        assert _seed_posts_with_metrics in ids

    def test_skips_posts_with_existing_insights(self, test_db, _seed_posts_with_metrics):
        high_pid = _seed_posts_with_metrics
        # Add an existing insight for the high performer
        test_db.execute(
            "INSERT INTO post_insights (id, post_id, client_id, insight_text, factors, confidence)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("existing-insight", high_pid, "insight-test", "Already analyzed", "[]", 0.8),
            commit=True,
        )
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        result = agent._find_high_performers("insight-test")
        ids = [r["id"] for r in result]
        assert high_pid not in ids


class TestGetClientAvgEngagement:
    def test_zero_with_no_data(self, test_db, _seed_client):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        avg = agent._get_client_avg_engagement("insight-test")
        assert avg == 0.0

    def test_calculates_average(self, test_db, _seed_posts_with_metrics):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        avg = agent._get_client_avg_engagement("insight-test")
        # 5 posts with 5 each + 1 post with 80 = 105/6 = 17.5
        assert avg > 0


class TestStoreInsight:
    def test_stores_insight(self, test_db, _seed_posts_with_metrics):
        high_pid = _seed_posts_with_metrics
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        post = {"id": high_pid}
        insight = {
            "insight_text": "This post resonated because of its emotional hook.",
            "factors": ["Emotional appeal", "Timely topic"],
            "confidence": 0.85,
        }
        insight_id = agent._store_insight(post, "insight-test", insight)
        assert insight_id

        row = test_db.fetchone("SELECT * FROM post_insights WHERE id=?", (insight_id,))
        assert row is not None
        assert row["post_id"] == high_pid
        assert row["client_id"] == "insight-test"
        assert row["insight_text"] == "This post resonated because of its emotional hook."
        assert json.loads(row["factors"]) == ["Emotional appeal", "Timely topic"]
        assert row["confidence"] == 0.85


class TestInsightGeneratorRun:
    def test_returns_empty_for_no_data(self, test_db, _seed_client):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        report = agent.run("test-run", client_id="insight-test")
        assert isinstance(report, InsightReport)
        assert report.insights_generated == 0

    def test_returns_empty_for_nonexistent_client(self, test_db):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        report = agent.run("test-run", client_id="nonexistent")
        assert report.insights_generated == 0

    def test_generates_insights_with_mock_llm(self, test_db, _seed_posts_with_metrics, monkeypatch):
        mock_response = json.dumps(
            {
                "insight_text": "This post performed well due to emotional language.",
                "factors": ["Emotional hook", "Trending topic", "Clear CTA"],
                "confidence": 0.85,
            }
        )

        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        monkeypatch.setattr(agent, "call_llm", lambda *a, **kw: mock_response)

        report = agent.run("test-run", client_id="insight-test")
        assert report.insights_generated >= 1

        # Check insight was stored
        rows = test_db.fetchall("SELECT * FROM post_insights WHERE client_id='insight-test'")
        assert len(rows) >= 1
        assert rows[0]["insight_text"] == "This post performed well due to emotional language."

    def test_handles_llm_failure_gracefully(self, test_db, _seed_posts_with_metrics, monkeypatch):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        monkeypatch.setattr(agent, "call_llm", lambda *a, **kw: None)

        report = agent.run("test-run", client_id="insight-test")
        assert report.insights_generated == 0

    def test_handles_malformed_llm_response(self, test_db, _seed_posts_with_metrics, monkeypatch):
        agent = InsightGeneratorAgent(test_db, "fake-key", "fake-model")
        monkeypatch.setattr(agent, "call_llm", lambda *a, **kw: "not json at all")

        report = agent.run("test-run", client_id="insight-test")
        assert report.insights_generated == 0
