"""Dynamic Posting Cadence — adjusts post volume based on engagement trends.

Zero LLM calls — pure computation.
"""

from __future__ import annotations

import logging

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.cadence")


class CadenceOptimizer:
    """Recommend optimal number of posts per cycle based on engagement history."""

    def __init__(self, db: Database):
        self.db = db

    def calculate_optimal_posts(self, client_id: str, current_max: int = 4) -> int:
        """Calculate recommended post count for next cycle. Returns int (1-6).

        Rules:
        - No history → keep current
        - Last cycle 0 engagement → cool down to 1 post
        - Falling 2+ cycles → reduce by 1 (floor 1)
        - Rising + last cycle >2x historical → increase by 1 (cap 6)
        - Otherwise → keep current
        """
        history = self._get_cycle_engagement_history(client_id, num_cycles=3)

        if not history:
            return current_max

        last = history[0]  # Most recent cycle

        # Zero engagement → cool down
        if last["avg_engagement"] == 0:
            logger.info(f"Zero engagement in last cycle for {client_id}, cooling down to 1 post")
            return 1

        trend = self._detect_trend(history)

        # Falling for 2+ cycles → reduce
        if trend == "falling" and len(history) >= 2:
            recommended = max(1, current_max - 1)
            logger.info(f"Falling engagement for {client_id}, reducing to {recommended} posts")
            return recommended

        # Rising + strong performance → increase
        if trend == "rising" and len(history) >= 2:
            # Check if last cycle is >2x the overall historical average
            all_avgs = [h["avg_engagement"] for h in history if h["avg_engagement"] > 0]
            if all_avgs:
                historical_avg = sum(all_avgs) / len(all_avgs)
                if historical_avg > 0 and last["avg_engagement"] > historical_avg * 2:
                    recommended = min(6, current_max + 1)
                    logger.info(f"Rising engagement for {client_id}, increasing to {recommended} posts")
                    return recommended

        return current_max

    def get_cadence_context(self, client_id: str, recommended_posts: int) -> str:
        """Get formatted string for CEO memory about cadence recommendation."""
        history = self._get_cycle_engagement_history(client_id, num_cycles=3)
        if not history:
            return ""

        trend = self._detect_trend(history)
        last_avg = history[0]["avg_engagement"] if history else 0

        lines = [
            "## Dynamic Cadence Recommendation",
            f"Recommended posts this cycle: {recommended_posts}",
            f"Recent engagement trend: {trend}",
            f"Last cycle avg engagement: {last_avg:.1f}",
        ]
        return "\n".join(lines)

    def _get_cycle_engagement_history(self, client_id: str, num_cycles: int = 3) -> list[dict]:
        """Get engagement stats per pipeline cycle, most recent first.

        Returns list of {run_id, avg_engagement, post_count}.
        """
        rows = self.db.fetchall(
            """SELECT pr.id as run_id, pr.started_at,
                      COUNT(p.id) as post_count,
                      COALESCE(AVG(
                          COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) +
                          COALESCE(m.reply_count, 0) + COALESCE(m.quote_count, 0)
                      ), 0) as avg_engagement
               FROM pipeline_runs pr
               LEFT JOIN posts p ON p.run_id = pr.id AND p.status = 'published'
               LEFT JOIN metrics m ON p.id = m.post_id
               WHERE pr.client_id = ? AND pr.status = 'completed'
               GROUP BY pr.id, pr.started_at
               ORDER BY pr.started_at DESC
               LIMIT ?""",
            (client_id, num_cycles),
        )
        return [
            {
                "run_id": r["run_id"],
                "avg_engagement": float(r["avg_engagement"]),
                "post_count": r["post_count"],
            }
            for r in rows
        ]

    def _detect_trend(self, history: list[dict]) -> str:
        """Detect engagement trend from cycle history.

        Returns "rising", "falling", or "stable".
        """
        if len(history) < 2:
            return "stable"

        # Compare consecutive cycles (history is most-recent-first)
        changes = []
        for i in range(len(history) - 1):
            current = history[i]["avg_engagement"]
            previous = history[i + 1]["avg_engagement"]
            if previous > 0:
                change = (current - previous) / previous
            elif current > 0:
                change = 1.0  # Went from 0 to something = rising
            else:
                change = 0.0
            changes.append(change)

        # If all changes are positive → rising
        if all(c > 0.1 for c in changes):
            return "rising"
        # If all changes are negative → falling
        if all(c < -0.1 for c in changes):
            return "falling"
        return "stable"
