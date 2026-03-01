"""Goal Tracker — evaluates, creates, and resolves measurable goals (0 LLM calls)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone  # noqa: UP017
from typing import TYPE_CHECKING

from ortobahn.db import to_datetime

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.goal_tracker")


class GoalTracker:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_active_goals(self, client_id: str) -> list[dict]:
        return self.db.fetchall(
            "SELECT * FROM agent_goals WHERE client_id=? AND status='active'",
            (client_id,),
        )

    def evaluate_progress(self, client_id: str) -> list[dict]:
        """Evaluate current progress for all active goals. Returns list of progress reports."""
        goals = self.get_active_goals(client_id)
        if not goals:
            return []

        now = datetime.now(timezone.utc)  # noqa: UP017
        reports = []

        for goal in goals:
            current = self._compute_metric(
                goal["metric_name"],
                client_id,
                goal.get("measurement_window_days", 7),
            )
            if current is None:
                continue

            target = goal["target_value"]
            progress_pct = (current / target * 100) if target > 0 else 0.0

            # Time elapsed
            time_elapsed_pct = 0.0
            if goal.get("deadline"):
                try:
                    deadline = to_datetime(goal["deadline"])
                    if deadline.tzinfo is None:
                        deadline = deadline.replace(tzinfo=timezone.utc)  # noqa: UP017
                    created = to_datetime(goal["created_at"])
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)  # noqa: UP017
                    total = (deadline - created).total_seconds()
                    elapsed = (now - created).total_seconds()
                    time_elapsed_pct = (elapsed / total * 100) if total > 0 else 100.0
                except (ValueError, TypeError):
                    pass

            behind_schedule = progress_pct < 50 and time_elapsed_pct > 50

            # Update current_value
            old_value = goal.get("current_value", 0) or 0
            if current > old_value * 1.05:
                trend = "rising"
            elif current < old_value * 0.95:
                trend = "falling"
            else:
                trend = "stable"

            self.db.execute(
                "UPDATE agent_goals SET current_value=?, trend=?, last_measured_at=? WHERE id=?",
                (current, trend, now.isoformat(), goal["id"]),
                commit=True,
            )

            reports.append(
                {
                    "goal_id": goal["id"],
                    "goal_type": goal.get("goal_type", ""),
                    "metric_name": goal["metric_name"],
                    "target_value": target,
                    "current_value": round(current, 2),
                    "progress_pct": round(progress_pct, 1),
                    "time_elapsed_pct": round(time_elapsed_pct, 1),
                    "trend": trend,
                    "on_track": progress_pct >= time_elapsed_pct * 0.8,
                    "behind_schedule": behind_schedule,
                    "deadline": goal.get("deadline"),
                }
            )

        return reports

    def create_goals_from_ceo(
        self,
        client_id: str,
        measurable_goals: list[dict],
        run_id: str,
        strategy_id: str = "",
    ) -> list[str]:
        """Create agent_goals rows from CEO output. Skips if active goal for same metric exists."""
        active_metrics = {g["metric_name"] for g in self.get_active_goals(client_id)}
        created_ids = []
        now = datetime.now(timezone.utc)  # noqa: UP017

        for goal_data in measurable_goals:
            metric = goal_data.get("metric_name", "")
            if metric in active_metrics:
                continue

            goal_id = str(uuid.uuid4())[:8]
            deadline_days = goal_data.get("deadline_days", 7)
            deadline = (now + timedelta(days=deadline_days)).isoformat()

            self.db.execute(
                """INSERT INTO agent_goals
                   (id, agent_name, client_id, goal_type, metric_name, target_value,
                    current_value, trend, measurement_window_days, deadline,
                    status, created_by_run_id, strategy_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0.0, 'stable', ?, ?, 'active', ?, ?, ?)""",
                (
                    goal_id,
                    "ceo",
                    client_id,
                    goal_data.get("goal_type", "engagement_growth"),
                    metric,
                    goal_data.get("target_value", 0),
                    deadline_days,
                    deadline,
                    run_id,
                    strategy_id,
                    now.isoformat(),
                ),
                commit=True,
            )
            created_ids.append(goal_id)

        return created_ids

    def resolve_expired_goals(self, client_id: str) -> dict:
        """Mark expired goals as achieved or missed. Returns {"achieved": [...], "missed": [...]}."""
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        expired = self.db.fetchall(
            "SELECT * FROM agent_goals WHERE client_id=? AND status='active' AND deadline IS NOT NULL AND deadline<=?",
            (client_id, now),
        )

        achieved = []
        missed = []

        for goal in expired:
            current = goal.get("current_value", 0) or 0
            target = goal.get("target_value", 0) or 0

            if target > 0 and current >= target:
                status = "achieved"
                achieved.append(
                    {
                        "metric": goal["metric_name"],
                        "target": target,
                        "final_value": current,
                    }
                )
            else:
                status = "missed"
                shortfall = round((1 - current / target) * 100, 1) if target > 0 else 100
                missed.append(
                    {
                        "metric": goal["metric_name"],
                        "target": target,
                        "final_value": current,
                        "shortfall_pct": shortfall,
                    }
                )

            self.db.execute(
                "UPDATE agent_goals SET status=?, achieved_at=? WHERE id=?",
                (status, now, goal["id"]),
                commit=True,
            )

        return {"achieved": achieved, "missed": missed}

    def format_progress_for_ceo(self, progress: list[dict], resolved: dict) -> str:
        """Format goal progress for CEO prompt injection."""
        if not progress and not resolved.get("achieved") and not resolved.get("missed"):
            return ""

        parts = ["\n## Goal Progress"]

        if progress:
            for p in progress:
                status_icon = "ON TRACK" if p["on_track"] else "BEHIND SCHEDULE"
                parts.append(
                    f"- [{status_icon}] {p['metric_name']}: "
                    f"{p['current_value']}/{p['target_value']} "
                    f"({p['progress_pct']}% complete, {p['time_elapsed_pct']}% time elapsed, "
                    f"trend: {p['trend']})"
                )

            behind = [p for p in progress if p["behind_schedule"]]
            if behind:
                parts.append(
                    f"\n### STRATEGY ADJUSTMENT NEEDED"
                    f"\n{len(behind)} goal(s) are behind schedule."
                    "\nConsider adjusting strategy to catch up."
                )

        if resolved.get("achieved"):
            parts.append("\n### Goals Achieved")
            for a in resolved["achieved"]:
                parts.append(f"- {a['metric']}: target {a['target']}, achieved {a['final_value']}")

        if resolved.get("missed"):
            parts.append("\n### Goals Missed")
            for m in resolved["missed"]:
                parts.append(
                    f"- {m['metric']}: target {m['target']}, actual {m['final_value']}"
                    f" (shortfall: {m['shortfall_pct']}%)"
                )

        if resolved.get("achieved") or resolved.get("missed"):
            parts.append("\nPlease set new measurable_goals to replace completed ones.")

        return "\n".join(parts)

    def _compute_metric(self, metric_name: str, client_id: str, window_days: int) -> float | None:
        """Compute a metric from the database. Zero LLM calls."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()  # noqa: UP017

        if metric_name == "avg_engagement":
            row = self.db.fetchone(
                """SELECT AVG(
                       COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) + COALESCE(m.reply_count, 0)
                   ) as val
                   FROM posts p
                   LEFT JOIN metrics m ON p.id = m.post_id
                       AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id
                                   ORDER BY m2.measured_at DESC LIMIT 1)
                   WHERE p.status='published' AND p.client_id=? AND p.published_at>=?""",
                (client_id, cutoff),
            )
            return float(row["val"]) if row and row["val"] is not None else None

        if metric_name == "total_engagement":
            row = self.db.fetchone(
                """SELECT SUM(
                       COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) + COALESCE(m.reply_count, 0)
                   ) as val
                   FROM posts p
                   LEFT JOIN metrics m ON p.id = m.post_id
                       AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id
                                   ORDER BY m2.measured_at DESC LIMIT 1)
                   WHERE p.status='published' AND p.client_id=? AND p.published_at>=?""",
                (client_id, cutoff),
            )
            return float(row["val"]) if row and row["val"] is not None else None

        if metric_name == "posts_per_day":
            row = self.db.fetchone(
                "SELECT COUNT(*) as c FROM posts WHERE status='published' AND client_id=? AND published_at>=?",
                (client_id, cutoff),
            )
            count = row["c"] if row else 0
            return count / max(window_days, 1)

        if metric_name == "total_posts_per_week":
            row = self.db.fetchone(
                "SELECT COUNT(*) as c FROM posts WHERE status='published' AND client_id=? AND published_at>=?",
                (client_id, cutoff),
            )
            count = row["c"] if row else 0
            return count / max(window_days / 7, 1)

        if metric_name == "avg_confidence":
            row = self.db.fetchone(
                "SELECT AVG(confidence) as val FROM posts WHERE status='published' AND client_id=? AND published_at>=?",
                (client_id, cutoff),
            )
            return float(row["val"]) if row and row["val"] is not None else None

        return None
