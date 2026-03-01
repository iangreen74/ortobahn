"""Tests for GoalTracker — evaluates, creates, and resolves measurable goals."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone  # noqa: UP017

from ortobahn.goal_tracker import GoalTracker


def _insert_post(db, client_id, likes=5, reposts=1, replies=1, days_ago=1):
    post_id = str(uuid.uuid4())[:8]
    published_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()  # noqa: UP017
    db.execute(
        "INSERT INTO posts (id, text, status, client_id, run_id, published_at, platform, confidence) "
        "VALUES (?, 'test', 'published', ?, 'r1', ?, 'bluesky', 0.8)",
        (post_id, client_id, published_at),
        commit=True,
    )
    db.execute(
        "INSERT INTO metrics (id, post_id, like_count, repost_count, reply_count, measured_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4())[:8],
            post_id,
            likes,
            reposts,
            replies,
            datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        ),
        commit=True,
    )


def _insert_goal(
    db,
    client_id,
    metric_name="avg_engagement",
    target_value=10.0,
    deadline_days=7,
    status="active",
):
    goal_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc)  # noqa: UP017
    deadline = (now + timedelta(days=deadline_days)).isoformat()
    # Use a created_at in the past so time_elapsed_pct > 0
    created = (now - timedelta(days=max(1, deadline_days // 2))).isoformat()
    db.execute(
        "INSERT INTO agent_goals (id, agent_name, client_id, metric_name, target_value, "
        "current_value, measurement_window_days, deadline, status, created_at, goal_type) "
        "VALUES (?, 'ceo', ?, ?, ?, 0.0, 7, ?, ?, ?, 'engagement_growth')",
        (goal_id, client_id, metric_name, target_value, deadline, status, created),
        commit=True,
    )
    return goal_id


class TestGoalTracker:
    def test_get_active_goals_empty(self, test_db):
        tracker = GoalTracker(test_db)
        result = tracker.get_active_goals("nonexistent")
        assert result == []

    def test_create_goals_from_ceo(self, test_db):
        tracker = GoalTracker(test_db)
        goals = [
            {"metric_name": "avg_engagement", "target_value": 10.0, "deadline_days": 7},
            {"metric_name": "posts_per_day", "target_value": 2.0, "deadline_days": 14},
        ]
        ids = tracker.create_goals_from_ceo("client1", goals, "run1", "strat1")
        assert len(ids) == 2

        # Verify in DB
        active = tracker.get_active_goals("client1")
        assert len(active) == 2
        metrics = {g["metric_name"] for g in active}
        assert metrics == {"avg_engagement", "posts_per_day"}

    def test_create_goals_deduplicates(self, test_db):
        tracker = GoalTracker(test_db)
        _insert_goal(test_db, "client1", metric_name="avg_engagement")

        goals = [
            {"metric_name": "avg_engagement", "target_value": 20.0, "deadline_days": 7},
            {"metric_name": "total_engagement", "target_value": 50.0, "deadline_days": 7},
        ]
        ids = tracker.create_goals_from_ceo("client1", goals, "run2")
        # avg_engagement already exists, only total_engagement created
        assert len(ids) == 1

        active = tracker.get_active_goals("client1")
        assert len(active) == 2

    def test_evaluate_progress_no_goals(self, test_db):
        tracker = GoalTracker(test_db)
        result = tracker.evaluate_progress("nonexistent")
        assert result == []

    def test_evaluate_progress_with_posts(self, test_db):
        tracker = GoalTracker(test_db)
        client_id = "eval_client"

        # Insert posts with engagement (likes=5, reposts=1, replies=1 => 7 per post)
        _insert_post(test_db, client_id, likes=5, reposts=1, replies=1, days_ago=1)
        _insert_post(test_db, client_id, likes=3, reposts=2, replies=2, days_ago=2)

        # Goal: avg_engagement target 10
        _insert_goal(test_db, client_id, metric_name="avg_engagement", target_value=10.0)

        reports = tracker.evaluate_progress(client_id)
        assert len(reports) == 1
        report = reports[0]
        assert report["metric_name"] == "avg_engagement"
        # avg of 7 and 7 = 7.0
        assert report["current_value"] == 7.0
        assert report["target_value"] == 10.0
        assert report["progress_pct"] == 70.0

    def test_evaluate_updates_current_value(self, test_db):
        tracker = GoalTracker(test_db)
        client_id = "update_client"

        _insert_post(test_db, client_id, likes=10, reposts=2, replies=3, days_ago=1)
        goal_id = _insert_goal(test_db, client_id, metric_name="avg_engagement", target_value=20.0)

        tracker.evaluate_progress(client_id)

        # Check DB updated
        row = test_db.fetchone(
            "SELECT current_value, trend, last_measured_at FROM agent_goals WHERE id=?",
            (goal_id,),
        )
        assert row is not None
        assert row["current_value"] == 15.0  # 10 + 2 + 3
        assert row["trend"] == "rising"  # 15 > 0 * 1.05
        assert row["last_measured_at"] is not None

    def test_resolve_expired_achieved(self, test_db):
        tracker = GoalTracker(test_db)
        client_id = "resolve_a"

        # Create an expired goal with current >= target
        goal_id = str(uuid.uuid4())[:8]
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()  # noqa: UP017
        test_db.execute(
            "INSERT INTO agent_goals (id, agent_name, client_id, metric_name, target_value, "
            "current_value, measurement_window_days, deadline, status, created_at, goal_type) "
            "VALUES (?, 'ceo', ?, 'avg_engagement', 10.0, 12.0, 7, ?, 'active', ?, 'engagement_growth')",
            (goal_id, client_id, past, past),
            commit=True,
        )

        result = tracker.resolve_expired_goals(client_id)
        assert len(result["achieved"]) == 1
        assert len(result["missed"]) == 0
        assert result["achieved"][0]["metric"] == "avg_engagement"
        assert result["achieved"][0]["final_value"] == 12.0

        # Verify status updated in DB
        row = test_db.fetchone("SELECT status, achieved_at FROM agent_goals WHERE id=?", (goal_id,))
        assert row["status"] == "achieved"
        assert row["achieved_at"] is not None

    def test_resolve_expired_missed(self, test_db):
        tracker = GoalTracker(test_db)
        client_id = "resolve_m"

        goal_id = str(uuid.uuid4())[:8]
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()  # noqa: UP017
        test_db.execute(
            "INSERT INTO agent_goals (id, agent_name, client_id, metric_name, target_value, "
            "current_value, measurement_window_days, deadline, status, created_at, goal_type) "
            "VALUES (?, 'ceo', ?, 'avg_engagement', 10.0, 3.0, 7, ?, 'active', ?, 'engagement_growth')",
            (goal_id, client_id, past, past),
            commit=True,
        )

        result = tracker.resolve_expired_goals(client_id)
        assert len(result["achieved"]) == 0
        assert len(result["missed"]) == 1
        assert result["missed"][0]["metric"] == "avg_engagement"
        assert result["missed"][0]["shortfall_pct"] == 70.0

        row = test_db.fetchone("SELECT status FROM agent_goals WHERE id=?", (goal_id,))
        assert row["status"] == "missed"

    def test_resolve_no_expired(self, test_db):
        tracker = GoalTracker(test_db)
        client_id = "resolve_none"

        # Goal with future deadline
        _insert_goal(test_db, client_id, deadline_days=30)

        result = tracker.resolve_expired_goals(client_id)
        assert result == {"achieved": [], "missed": []}

    def test_format_empty(self, test_db):
        tracker = GoalTracker(test_db)
        result = tracker.format_progress_for_ceo([], {"achieved": [], "missed": []})
        assert result == ""

    def test_format_with_behind_schedule(self, test_db):
        tracker = GoalTracker(test_db)
        progress = [
            {
                "goal_id": "g1",
                "goal_type": "engagement_growth",
                "metric_name": "avg_engagement",
                "target_value": 100.0,
                "current_value": 10.0,
                "progress_pct": 10.0,
                "time_elapsed_pct": 80.0,
                "trend": "stable",
                "on_track": False,
                "behind_schedule": True,
                "deadline": "2026-03-10",
            }
        ]
        resolved = {"achieved": [], "missed": []}
        result = tracker.format_progress_for_ceo(progress, resolved)
        assert "STRATEGY ADJUSTMENT NEEDED" in result
        assert "BEHIND SCHEDULE" in result
        assert "avg_engagement" in result
        assert "1 goal(s) are behind schedule" in result

    def test_compute_metric_unknown(self, test_db):
        tracker = GoalTracker(test_db)
        result = tracker._compute_metric("nonexistent_metric", "client1", 7)
        assert result is None
