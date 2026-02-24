"""Cross-agent shared insight bus for breaking memory silos.

Agents publish insights (patterns, anomalies, observations) and other agents
consume them — enabling cross-boundary learning without direct coupling.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ortobahn.db import Database

logger = logging.getLogger("ortobahn.shared_insights")

# ---------------------------------------------------------------------------
# Insight type constants
# ---------------------------------------------------------------------------
CI_FIX_PATTERN = "CI_FIX_PATTERN"
DEPLOY_HEALTH = "DEPLOY_HEALTH"
CONTENT_TREND = "CONTENT_TREND"
PLATFORM_ISSUE = "PLATFORM_ISSUE"
COST_ANOMALY = "COST_ANOMALY"
CLIENT_HEALTH = "CLIENT_HEALTH"

ALL_INSIGHT_TYPES = [
    CI_FIX_PATTERN,
    DEPLOY_HEALTH,
    CONTENT_TREND,
    PLATFORM_ISSUE,
    COST_ANOMALY,
    CLIENT_HEALTH,
]

# Which insight types each agent should consume
AGENT_RELEVANCE: dict[str, list[str]] = {
    "ceo": ALL_INSIGHT_TYPES,
    "cifix": [CI_FIX_PATTERN, DEPLOY_HEALTH],
    "sre": [DEPLOY_HEALTH, PLATFORM_ISSUE, COST_ANOMALY],
    "ops": [CLIENT_HEALTH, PLATFORM_ISSUE],
    "creator": [CONTENT_TREND],
    "strategist": [CONTENT_TREND, CLIENT_HEALTH],
}

# Similarity threshold: if existing content starts with the same N chars, treat as duplicate
_DEDUP_PREFIX_LENGTH = 120


class SharedInsightBus:
    """Publish / query cross-agent insights backed by the shared_insights table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------
    def publish(
        self,
        source_agent: str,
        insight_type: str,
        content: str,
        confidence: float = 0.7,
        metadata: dict | None = None,
    ) -> str:
        """Publish an insight. Returns the insight ID.

        Deduplication: if an insight with the same source_agent + insight_type
        and similar content already exists, we reinforce it (bump confidence
        and reinforcement_count) instead of creating a duplicate.
        """
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})

        # --- Deduplication check ---
        prefix = content[:_DEDUP_PREFIX_LENGTH]
        existing = self.db.fetchall(
            """SELECT id, confidence, reinforcement_count
               FROM shared_insights
               WHERE source_agent = ? AND insight_type = ? AND content LIKE ?
               ORDER BY updated_at DESC LIMIT 1""",
            (source_agent, insight_type, f"{prefix}%"),
        )

        if existing:
            row = existing[0]
            new_confidence = min(1.0, row["confidence"] + 0.05)
            new_count = row["reinforcement_count"] + 1
            self.db.execute(
                """UPDATE shared_insights
                   SET confidence = ?, reinforcement_count = ?, updated_at = ?, metadata = ?
                   WHERE id = ?""",
                (new_confidence, new_count, now, metadata_json, row["id"]),
                commit=True,
            )
            logger.debug(
                "Reinforced insight %s (confidence=%.2f, count=%d)",
                row["id"][:8],
                new_confidence,
                new_count,
            )
            return row["id"]

        # --- New insight ---
        insight_id = str(uuid.uuid4())
        self.db.execute(
            """INSERT INTO shared_insights
               (id, source_agent, insight_type, content, confidence, metadata,
                reinforcement_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (insight_id, source_agent, insight_type, content, confidence, metadata_json, now, now),
            commit=True,
        )
        logger.debug("Published insight %s [%s] from %s", insight_id[:8], insight_type, source_agent)
        return insight_id

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def query(
        self,
        insight_type: str | None = None,
        min_confidence: float = 0.3,
        limit: int = 10,
        since_hours: int = 168,
    ) -> list[dict]:
        """Query recent insights sorted by confidence * recency.

        ``since_hours`` defaults to 168 (7 days).
        """
        now = datetime.now(timezone.utc)
        cutoff = datetime(
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            tzinfo=timezone.utc,
        )
        # Shift back by since_hours
        from datetime import timedelta

        cutoff_str = (cutoff - timedelta(hours=since_hours)).isoformat()

        if insight_type:
            rows = self.db.fetchall(
                """SELECT *, (confidence * (1.0 / (1 + (julianday('now') - julianday(updated_at))))) AS relevance
                   FROM shared_insights
                   WHERE insight_type = ? AND confidence >= ? AND updated_at >= ?
                   ORDER BY relevance DESC
                   LIMIT ?""",
                (insight_type, min_confidence, cutoff_str, limit),
            )
        else:
            rows = self.db.fetchall(
                """SELECT *, (confidence * (1.0 / (1 + (julianday('now') - julianday(updated_at))))) AS relevance
                   FROM shared_insights
                   WHERE confidence >= ? AND updated_at >= ?
                   ORDER BY relevance DESC
                   LIMIT ?""",
                (min_confidence, cutoff_str, limit),
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Agent-scoped query
    # ------------------------------------------------------------------
    def get_insights_for_agent(self, consumer_agent: str, limit: int = 5) -> list[dict]:
        """Return insights relevant to *consumer_agent* based on the relevance mapping."""
        types = AGENT_RELEVANCE.get(consumer_agent)
        if not types:
            return []

        placeholders = ",".join("?" for _ in types)
        rows = self.db.fetchall(
            f"""SELECT *, (confidence * (1.0 / (1 + (julianday('now') - julianday(updated_at))))) AS relevance
                FROM shared_insights
                WHERE insight_type IN ({placeholders}) AND confidence >= 0.3
                ORDER BY relevance DESC
                LIMIT ?""",
            (*types, limit),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Summarize
    # ------------------------------------------------------------------
    def summarize(self, insight_type: str | None = None) -> str:
        """Return a human-readable summary of recent insights for prompt injection."""
        insights = self.query(insight_type=insight_type, limit=15, since_hours=168)
        if not insights:
            return ""

        lines = ["## Cross-Agent Insights"]
        for ins in insights:
            source = ins["source_agent"]
            itype = ins["insight_type"]
            content = ins["content"]
            conf = ins["confidence"]
            reinforced = ins.get("reinforcement_count", 0)
            suffix = f" (reinforced {reinforced}x)" if reinforced else ""
            lines.append(f"- [{itype}] ({source}, confidence={conf:.2f}{suffix}): {content}")
        return "\n".join(lines)
