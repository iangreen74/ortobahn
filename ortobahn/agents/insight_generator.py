"""Insight Generator Agent — explains why high-performing posts worked.

Generates 'Why This Worked' insights for posts that significantly
outperform a client's average engagement. Publishes insights to
the SharedInsightBus so other agents (strategist, creator) can learn.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from ortobahn.agents.base import BaseAgent

logger = logging.getLogger(__name__)

# Minimum engagement multiplier vs average to trigger insight generation
HIGH_PERFORMANCE_MULTIPLIER = 2.0
# Max insights to generate per pipeline cycle (cost control)
MAX_INSIGHTS_PER_CYCLE = 2


@dataclass
class InsightReport:
    insights_generated: int = 0
    posts_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


class InsightGeneratorAgent(BaseAgent):
    """Analyze high-performing posts and explain what made them work."""

    name = "insight_generator"
    prompt_file = "insight_generator.txt"
    thinking_budget = 4_000

    def run(self, run_id: str, client_id: str = "default", **kwargs: Any) -> InsightReport:
        """Generate insights for high-performing posts.

        1. Find posts with engagement >= 2x client average
        2. Skip posts that already have insights
        3. Call LLM to explain why each post worked
        4. Store insight and publish to SharedInsightBus
        """
        report = InsightReport()

        # Get client info
        client = self.db.get_client(client_id)
        if not client:
            return report

        # Get high-performing posts
        high_performers = self._find_high_performers(client_id)
        report.posts_analyzed = len(high_performers)

        if not high_performers:
            self.log_decision(
                run_id=run_id,
                input_summary=f"Checked posts for {client_id}",
                output_summary="No high-performing posts found needing insights",
            )
            return report

        # Calculate client average for context
        avg_engagement = self._get_client_avg_engagement(client_id)

        generated = 0
        for post in high_performers:
            if generated >= MAX_INSIGHTS_PER_CYCLE:
                break

            try:
                insight = self._generate_insight(post, client, avg_engagement)
                if insight:
                    self._store_insight(post, client_id, insight)
                    self._publish_to_bus(client_id, post, insight)
                    generated += 1
            except Exception as e:
                logger.warning("Insight generation failed for post %s: %s", post["id"], e)
                report.errors.append(str(e))

        report.insights_generated = generated

        self.log_decision(
            run_id=run_id,
            input_summary=f"{report.posts_analyzed} high-performing posts for {client_id}",
            output_summary=f"Generated {generated} 'Why This Worked' insights",
        )

        return report

    def _find_high_performers(self, client_id: str) -> list[dict]:
        """Find published posts with engagement >= 2x client average that lack insights."""
        avg = self._get_client_avg_engagement(client_id)
        if avg <= 0:
            return []

        threshold = avg * HIGH_PERFORMANCE_MULTIPLIER

        rows = self.db.fetchall(
            "SELECT p.id, p.text, p.platform, p.published_at, p.confidence,"
            " COALESCE(m.like_count,0) as likes,"
            " COALESCE(m.repost_count,0) as reposts,"
            " COALESCE(m.reply_count,0) as replies"
            " FROM posts p"
            " LEFT JOIN metrics m ON p.id = m.post_id"
            " AND m.id = (SELECT m2.id FROM metrics m2"
            " WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
            " WHERE p.status='published' AND p.client_id=?"
            " AND p.published_at IS NOT NULL"
            " AND (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) >= ?"
            " AND p.id NOT IN (SELECT post_id FROM post_insights WHERE client_id=?)"
            " ORDER BY (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) DESC"
            " LIMIT ?",
            (client_id, threshold, client_id, MAX_INSIGHTS_PER_CYCLE * 2),
        )
        return [dict(r) for r in rows]

    def _get_client_avg_engagement(self, client_id: str) -> float:
        """Calculate average total engagement per post for a client."""
        row = self.db.fetchone(
            "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p"
            " LEFT JOIN metrics m ON p.id = m.post_id"
            " AND m.id = (SELECT m2.id FROM metrics m2"
            " WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
            " WHERE p.status='published' AND p.client_id=?"
            " AND p.published_at IS NOT NULL",
            (client_id,),
        )
        return float(row["avg_eng"] or 0) if row else 0.0

    def _generate_insight(self, post: dict, client: dict, avg_engagement: float) -> dict | None:
        """Call LLM to generate an insight for a single post."""
        total_eng = post.get("likes", 0) + post.get("reposts", 0) + post.get("replies", 0)
        multiplier = total_eng / avg_engagement if avg_engagement > 0 else 0

        user_message = json.dumps(
            {
                "post_text": (post.get("text") or "")[:500],
                "platform": post.get("platform", ""),
                "engagement": {
                    "likes": post.get("likes", 0),
                    "reposts": post.get("reposts", 0),
                    "replies": post.get("replies", 0),
                    "total": total_eng,
                },
                "client_average_engagement": round(avg_engagement, 1),
                "performance_multiplier": round(multiplier, 1),
                "brand_voice": client.get("brand_voice", ""),
                "target_audience": client.get("target_audience", ""),
            },
            indent=2,
        )

        response = self.call_llm(user_message)
        if not response:
            return None

        try:
            # Extract JSON from response
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(text)
            return {
                "insight_text": data.get("insight_text", ""),
                "factors": data.get("factors", []),
                "confidence": min(1.0, max(0.0, float(data.get("confidence", 0.5)))),
            }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to parse insight response: %s", e)
            return None

    def _store_insight(self, post: dict, client_id: str, insight: dict) -> str:
        """Store insight in the post_insights table."""
        insight_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO post_insights (id, post_id, client_id, insight_text, factors, confidence)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                insight_id,
                post["id"],
                client_id,
                insight["insight_text"],
                json.dumps(insight["factors"]),
                insight["confidence"],
            ),
            commit=True,
        )
        return insight_id

    def _publish_to_bus(self, client_id: str, post: dict, insight: dict) -> None:
        """Publish the insight to SharedInsightBus for other agents."""
        from ortobahn.shared_insights import SharedInsightBus

        bus = SharedInsightBus(self.db)
        factors_str = ", ".join(insight["factors"][:3])
        content = f"High-performing post on {post.get('platform', 'unknown')}: {insight['insight_text']} (Factors: {factors_str})"
        bus.publish(
            source_agent=self.name,
            insight_type="CONTENT_TREND",
            content=content,
            confidence=insight["confidence"],
            metadata={
                "post_id": post["id"],
                "client_id": client_id,
                "factors": insight["factors"],
            },
        )
