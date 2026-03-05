"""Engagement Outcome Tracker — measures reply effectiveness."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from ortobahn.db import Database

logger = logging.getLogger("ortobahn.engagement_outcomes")


class EngagementOutcomeTracker:
    """Track and score the effectiveness of engagement replies."""

    def __init__(
        self,
        db: Database,
        bluesky_client=None,
        twitter_client=None,
        reddit_client=None,
        linkedin_client=None,
    ):
        self.db = db
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.reddit = reddit_client
        self.linkedin = linkedin_client

    def check_recent_replies(self, client_id: str, lookback_hours: int = 48) -> int:
        """Check outcomes for recent replies. Returns count of outcomes recorded."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        replies = self.db.fetchall(
            "SELECT er.* FROM engagement_replies er "
            "LEFT JOIN engagement_outcomes eo ON eo.reply_id = er.id "
            "WHERE er.client_id=? AND er.status='posted' AND er.created_at > ? "
            "AND eo.id IS NULL "
            "ORDER BY er.created_at DESC LIMIT 20",
            (client_id, cutoff),
        )
        if not replies:
            return 0

        outcomes_recorded = 0
        for reply in replies:
            try:
                outcome = self._check_single_reply(reply)
                if outcome:
                    self.db.execute(
                        """INSERT INTO engagement_outcomes
                        (id, reply_id, client_id, platform, reply_uri, target_author,
                         like_count, reply_count, target_responded, target_followed,
                         outcome_score, checked_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            reply["id"],
                            client_id,
                            reply.get("platform", "bluesky"),
                            reply.get("reply_uri", ""),
                            reply.get("notification_uri", "")[:100],
                            outcome.get("like_count", 0),
                            outcome.get("reply_count", 0),
                            1 if outcome.get("target_responded") else 0,
                            1 if outcome.get("target_followed") else 0,
                            outcome.get("outcome_score", 0.0),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                        commit=True,
                    )
                    outcomes_recorded += 1
            except Exception as e:
                logger.warning("[outcomes] Failed to check reply %s: %s", reply["id"], e)

        return outcomes_recorded

    def _check_single_reply(self, reply: dict) -> dict | None:
        """Check metrics for a single reply. Returns outcome dict or None."""
        platform = reply.get("platform", "bluesky")
        reply_uri = reply.get("reply_uri", "")
        if not reply_uri:
            return None

        like_count = 0
        reply_count = 0

        if platform == "bluesky" and self.bluesky:
            try:
                # Get thread to check for replies to our reply
                thread = self.bluesky.get_post_thread(reply_uri, depth=1)
                if thread:
                    for post in thread:
                        if post.get("uri") == reply_uri:
                            like_count = post.get("like_count", 0)
                            reply_count = post.get("reply_count", 0)
                            break
            except Exception:
                pass
        elif platform == "twitter" and self.twitter:
            try:
                # Extract tweet ID from URI
                tweet_id = reply_uri.split("/")[-1] if "/" in reply_uri else reply_uri
                metrics = self.twitter.get_post_metrics(tweet_id)
                like_count = metrics.get("like_count", 0)
                reply_count = metrics.get("reply_count", 0)
            except Exception:
                pass

        # Score: 0-1 based on engagement received
        score = min(1.0, (like_count * 0.3 + reply_count * 0.5) / 5.0)

        return {
            "like_count": like_count,
            "reply_count": reply_count,
            "target_responded": reply_count > 0,
            "target_followed": False,  # Hard to detect via API
            "outcome_score": round(score, 2),
        }

    def get_effectiveness_report(self, client_id: str, days: int = 30) -> dict:
        """Aggregate engagement effectiveness over a period."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.db.fetchall(
            "SELECT * FROM engagement_outcomes WHERE client_id=? AND created_at > ?",
            (client_id, cutoff),
        )
        if not rows:
            return {
                "total_replies": 0,
                "avg_outcome_score": 0.0,
                "total_likes": 0,
                "total_reply_count": 0,
                "target_response_rate": 0.0,
                "best_platform": "",
            }

        total = len(rows)
        total_likes = sum(r.get("like_count", 0) for r in rows)
        total_replies = sum(r.get("reply_count", 0) for r in rows)
        avg_score = sum(r.get("outcome_score", 0.0) for r in rows) / total
        target_responses = sum(1 for r in rows if r.get("target_responded"))

        # Best platform by average score
        platform_scores: dict[str, list[float]] = {}
        for r in rows:
            p = r.get("platform", "unknown")
            platform_scores.setdefault(p, []).append(r.get("outcome_score", 0.0))

        best_platform = ""
        best_avg = 0.0
        for p, scores in platform_scores.items():
            avg = sum(scores) / len(scores)
            if avg > best_avg:
                best_avg = avg
                best_platform = p

        return {
            "total_replies": total,
            "avg_outcome_score": round(avg_score, 3),
            "total_likes": total_likes,
            "total_reply_count": total_replies,
            "target_response_rate": round(target_responses / total, 3) if total else 0.0,
            "best_platform": best_platform,
        }
