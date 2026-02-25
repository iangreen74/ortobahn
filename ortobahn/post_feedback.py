"""Real-Time Post Feedback Loop — check early engagement after publishing.

Cuts the learning cycle from 24 hours to ~10 minutes. Zero LLM calls.
"""

from __future__ import annotations

import logging

from ortobahn.db import Database
from ortobahn.memory import MemoryStore
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

logger = logging.getLogger("ortobahn.post_feedback")


class PostFeedbackLoop:
    """Check recently published posts for early engagement signals."""

    def __init__(
        self,
        db: Database,
        memory_store: MemoryStore,
        bluesky_client=None,
        twitter_client=None,
        linkedin_client=None,
        reddit_client=None,
    ):
        self.db = db
        self.memory = memory_store
        self.bluesky = bluesky_client
        self.twitter = twitter_client
        self.linkedin = linkedin_client
        self.reddit = reddit_client

    def check_recent_posts(self, run_id: str, client_id: str = "default") -> dict:
        """Check posts published in this run for early engagement.

        Returns {"posts_checked": N, "resonating": N, "silent": N, "viral": N}
        """
        # Get posts published in this run
        posts = self.db.fetchall(
            """SELECT p.id, p.text, p.platform, p.platform_uri, p.published_at
               FROM posts p
               WHERE p.run_id = ? AND p.status = 'published' AND p.client_id = ?""",
            (run_id, client_id),
        )

        if not posts:
            return {"posts_checked": 0, "resonating": 0, "silent": 0, "viral": 0}

        historical_avg = self._get_historical_early_avg(client_id)
        result = {"posts_checked": 0, "resonating": 0, "silent": 0, "viral": 0}

        for post in posts:
            metrics = self._fetch_early_metrics(post)
            if metrics is None:
                continue

            result["posts_checked"] += 1
            total_engagement = metrics.get("likes", 0) + metrics.get("reposts", 0) + metrics.get("replies", 0)
            text_preview = (post.get("text") or "")[:80]

            if total_engagement == 0:
                # Silent post
                result["silent"] += 1
                self.memory.remember(
                    AgentMemory(
                        agent_name="creator",
                        client_id=client_id,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.CONTENT_PATTERN,
                        content={
                            "summary": f"Early signal: 0 engagement within first check: {text_preview}",
                            "signal": "silent",
                        },
                        confidence=0.4,
                        source_run_id=run_id,
                        source_post_ids=[post["id"]],
                    )
                )

            elif historical_avg > 0 and total_engagement > historical_avg * 5:
                # Viral post
                result["viral"] += 1
                self.memory.remember(
                    AgentMemory(
                        agent_name="creator",
                        client_id=client_id,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.CONTENT_PATTERN,
                        content={
                            "summary": f"VIRAL early signal ({total_engagement} engagement, {total_engagement / historical_avg:.1f}x avg): {text_preview}",
                            "signal": "viral",
                            "engagement": total_engagement,
                            "historical_avg": historical_avg,
                        },
                        confidence=0.7,
                        source_run_id=run_id,
                        source_post_ids=[post["id"]],
                    )
                )

            elif total_engagement > 0:
                # Resonating post
                result["resonating"] += 1
                self.memory.remember(
                    AgentMemory(
                        agent_name="creator",
                        client_id=client_id,
                        memory_type=MemoryType.OBSERVATION,
                        category=MemoryCategory.CONTENT_PATTERN,
                        content={
                            "summary": f"Early positive signal ({total_engagement} engagement): {text_preview}",
                            "signal": "resonating",
                            "engagement": total_engagement,
                        },
                        confidence=0.5,
                        source_run_id=run_id,
                        source_post_ids=[post["id"]],
                    )
                )

        logger.info(
            f"Post feedback for run {run_id[:8]}: checked={result['posts_checked']}, "
            f"resonating={result['resonating']}, silent={result['silent']}, viral={result['viral']}"
        )
        return result

    def _fetch_early_metrics(self, post: dict) -> dict | None:
        """Fetch current metrics for a post from its platform.

        Returns {"likes": N, "reposts": N, "replies": N} or None if unavailable.
        """
        platform = post.get("platform", "")
        uri = post.get("platform_uri", "")

        if platform == "bluesky" and self.bluesky and uri:
            try:
                metrics = self.bluesky.get_post_metrics(uri)
                return {
                    "likes": metrics.like_count,
                    "reposts": metrics.repost_count,
                    "replies": metrics.reply_count,
                }
            except Exception as e:
                logger.warning(f"Failed to fetch Bluesky metrics for {uri}: {e}")
                return None

        if platform == "twitter" and self.twitter and uri:
            try:
                metrics = self.twitter.get_post_metrics(uri)
                return {
                    "likes": metrics.get("like_count", 0),
                    "reposts": metrics.get("retweet_count", 0),
                    "replies": metrics.get("reply_count", 0),
                }
            except Exception:
                return None

        if platform == "linkedin" and self.linkedin and uri:
            try:
                metrics = self.linkedin.get_post_metrics(uri)
                return {
                    "likes": metrics.get("like_count", 0),
                    "reposts": metrics.get("share_count", 0),
                    "replies": metrics.get("comment_count", 0),
                }
            except Exception:
                return None

        if platform == "reddit" and self.reddit:
            pid = post.get("platform_id", "")
            if pid:
                try:
                    metrics = self.reddit.get_post_metrics(pid)
                    return {
                        "likes": metrics.score,
                        "reposts": 0,
                        "replies": metrics.num_comments,
                    }
                except Exception:
                    return None

        return None

    def _get_historical_early_avg(self, client_id: str) -> float:
        """Average total engagement from the last 20 published posts.

        Used as baseline for classifying "viral" posts.
        """
        row = self.db.fetchone(
            """SELECT AVG(
                   COALESCE(m.like_count, 0) + COALESCE(m.repost_count, 0) +
                   COALESCE(m.reply_count, 0) + COALESCE(m.quote_count, 0)
               ) as avg_eng
               FROM (
                   SELECT p.id
                   FROM posts p
                   WHERE p.status = 'published' AND p.client_id = ?
                   ORDER BY p.published_at DESC
                   LIMIT 20
               ) recent
               JOIN metrics m ON recent.id = m.post_id""",
            (client_id,),
        )
        return float(row["avg_eng"]) if row and row["avg_eng"] is not None else 0.0
