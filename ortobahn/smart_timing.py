"""Smart Timing — auto-extract optimal posting hours from engagement data.

Zero LLM calls. Analyzes historical post performance by hour-of-day and
writes the best hours to each client's preferred_posting_hours field.
The scheduler (__main__.py) already respects preferred_posting_hours.
"""

from __future__ import annotations

import logging

from ortobahn.db import Database

logger = logging.getLogger(__name__)

# Minimum posts needed before we trust the data
MIN_POSTS_FOR_TIMING = 5
# How many top hours to keep
TOP_HOURS = 6
# Minimum posts per hour bucket before including it
MIN_PER_BUCKET = 2

# Metrics join — latest metric per post (same pattern as tenant_insights.py)
_METRICS_JOIN = (
    " LEFT JOIN metrics m ON p.id = m.post_id"
    " AND m.id = (SELECT m2.id FROM metrics m2"
    " WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
)


class SmartTimingOptimizer:
    """Analyze engagement by hour-of-day and update preferred_posting_hours."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._is_postgres = getattr(db, "backend", "sqlite") == "postgresql"

    def _hour_extract_sql(self) -> str:
        """Return SQL fragment to extract hour from published_at."""
        if self._is_postgres:
            return "EXTRACT(HOUR FROM p.published_at::timestamp)::integer"
        return "CAST(strftime('%H', p.published_at) AS INTEGER)"

    def calculate_optimal_hours(self, client_id: str) -> list[int]:
        """Return sorted list of best posting hours (0-23) by avg engagement.

        Returns empty list if insufficient data (< MIN_POSTS_FOR_TIMING posts).
        """
        hour_expr = self._hour_extract_sql()

        # Check we have enough published posts with timestamps
        count_row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM posts p"
            " WHERE p.status='published' AND p.client_id=?"
            " AND p.published_at IS NOT NULL",
            (client_id,),
        )
        total = (count_row["cnt"] if count_row else 0) if count_row else 0
        if total < MIN_POSTS_FOR_TIMING:
            return []

        # Aggregate engagement by hour
        rows = self.db.fetchall(
            f"SELECT {hour_expr} as hour,"
            " COUNT(*) as cnt,"
            " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)"
            "+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p"
            f"{_METRICS_JOIN}"
            " WHERE p.status='published' AND p.client_id=?"
            " AND p.published_at IS NOT NULL"
            f" GROUP BY {hour_expr}"
            f" HAVING COUNT(*) >= {MIN_PER_BUCKET}"
            " ORDER BY avg_eng DESC",
            (client_id,),
        )

        if not rows:
            return []

        # Take top N hours, sorted ascending
        hours = sorted(int(row["hour"]) for row in rows[:TOP_HOURS])
        return hours

    def update_client_posting_hours(self, client_id: str) -> bool:
        """Recalculate and write preferred_posting_hours for a client.

        Returns True if hours were updated, False if insufficient data.
        Preserves existing hours if the new calculation has no data.
        """
        hours = self.calculate_optimal_hours(client_id)
        if not hours:
            logger.debug(
                "Smart Timing: insufficient data for %s, keeping current hours",
                client_id,
            )
            return False

        hours_str = ",".join(str(h) for h in hours)

        # Only update if different from current
        client = self.db.fetchone(
            "SELECT preferred_posting_hours FROM clients WHERE id=?",
            (client_id,),
        )
        current = client["preferred_posting_hours"] if client else ""
        if current == hours_str:
            logger.debug("Smart Timing: hours unchanged for %s (%s)", client_id, hours_str)
            return False

        self.db.execute(
            "UPDATE clients SET preferred_posting_hours=? WHERE id=?",
            (hours_str, client_id),
            commit=True,
        )
        logger.info(
            "Smart Timing: updated %s posting hours %s -> %s",
            client_id,
            current or "(default)",
            hours_str,
        )
        return True
