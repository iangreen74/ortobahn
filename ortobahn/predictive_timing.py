"""Predictive Timing — detect emerging topics before they peak.

Tracks topic mentions across pipeline cycles and calculates velocity
(rate-of-change in mention frequency). Topics with accelerating mentions
that haven't peaked are flagged as "emerging" — signals to publish early.

Zero LLM calls — pure computation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.predictive_timing")


class TopicVelocityTracker:
    """Track topic mention velocity across pipeline cycles."""

    def __init__(self, db: Database):
        self.db = db

    def record_topics(self, topics: list[dict], run_id: str = "") -> int:
        """Record topic mentions from a pipeline cycle.

        Each topic dict should have at minimum: {"title": str, "source": str}.
        Returns count of topics recorded.
        """
        now = datetime.now(timezone.utc).isoformat()
        recorded = 0

        for topic in topics:
            title = (topic.get("title") or "").strip().lower()
            source = topic.get("source", "unknown")
            if not title:
                continue

            # Check if we've seen this topic before
            existing = self.db.fetchone(
                "SELECT id, mention_count, velocity_score FROM topic_velocity WHERE topic_title = ?",
                (title,),
            )

            if existing:
                # Update: increment mention count, update last_seen
                new_count = (existing["mention_count"] or 0) + 1
                new_velocity = new_count  # Raw count serves as velocity proxy
                self.db.execute(
                    """UPDATE topic_velocity
                       SET mention_count = ?, last_seen_at = ?, velocity_score = ?
                       WHERE id = ?""",
                    (new_count, now, new_velocity, existing["id"]),
                    commit=True,
                )
            else:
                # First sighting of this topic
                topic_id = str(uuid.uuid4())[:8]
                self.db.execute(
                    """INSERT INTO topic_velocity
                       (id, topic_title, source, mention_count, first_seen_at,
                        last_seen_at, velocity_score, peak_detected)
                    VALUES (?, ?, ?, 1, ?, ?, 1.0, 0)""",
                    (topic_id, title, source, now, now),
                    commit=True,
                )
            recorded += 1

        logger.info(f"Recorded {recorded} topic mentions")
        return recorded

    def get_emerging_topics(self, min_mentions: int = 2, limit: int = 10) -> list[dict]:
        """Get topics with positive velocity that haven't peaked.

        "Emerging" = seen in multiple cycles + velocity still increasing + not peaked.
        Returns list of dicts sorted by velocity_score descending.
        """
        rows = self.db.fetchall(
            """SELECT id, topic_title, source, mention_count, velocity_score,
                      first_seen_at, last_seen_at
               FROM topic_velocity
               WHERE peak_detected = 0
                 AND mention_count >= ?
                 AND velocity_score > 0
               ORDER BY velocity_score DESC
               LIMIT ?""",
            (min_mentions, limit),
        )
        return [dict(r) for r in rows]

    def detect_peaks(self) -> int:
        """Mark topics that have peaked (not seen in recent cycles).

        A topic is considered peaked if it wasn't seen in the last 48 hours
        and has been seen at least 3 times total.

        Returns count of newly peaked topics.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()

        # Topics not seen recently with enough data to judge
        peaked = self.db.fetchall(
            """SELECT id FROM topic_velocity
               WHERE peak_detected = 0
                 AND last_seen_at < ?
                 AND mention_count >= 3""",
            (cutoff,),
        )

        if not peaked:
            return 0

        ids = [r["id"] for r in peaked]
        placeholders = ",".join("?" for _ in ids)
        self.db.execute(
            f"UPDATE topic_velocity SET peak_detected = 1 WHERE id IN ({placeholders})",
            ids,
            commit=True,
        )

        logger.info(f"Detected {len(ids)} peaked topics")
        return len(ids)

    def cleanup_old_topics(self, max_age_days: int = 30) -> int:
        """Remove topics older than max_age_days. Returns count removed."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM topic_velocity WHERE first_seen_at < ?",
            (cutoff,),
        )
        count = row["cnt"] if row else 0
        if count > 0:
            self.db.execute(
                "DELETE FROM topic_velocity WHERE first_seen_at < ?",
                (cutoff,),
                commit=True,
            )
            logger.info(f"Cleaned up {count} old topics")
        return count

    def get_velocity_summary(self) -> dict:
        """Get summary stats for logging/reporting."""
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM topic_velocity")
        emerging = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM topic_velocity WHERE peak_detected = 0 AND mention_count >= 2"
        )
        peaked = self.db.fetchone("SELECT COUNT(*) as cnt FROM topic_velocity WHERE peak_detected = 1")
        return {
            "total_tracked": total["cnt"] if total else 0,
            "emerging": emerging["cnt"] if emerging else 0,
            "peaked": peaked["cnt"] if peaked else 0,
        }
