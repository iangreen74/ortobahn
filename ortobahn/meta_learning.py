"""Cross-Client Meta-Learning — promotes patterns observed across multiple clients.

Uses client_id='__meta__' in existing agent_memories table to store industry-wide
patterns. When the same insight is reinforced across 3+ clients, it becomes a
shared "meta-learning" available to all clients at reduced confidence.

Zero LLM calls — pure computation.
"""

from __future__ import annotations

import json
import logging

from ortobahn.db import Database
from ortobahn.memory import MemoryStore
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

logger = logging.getLogger("ortobahn.meta_learning")

META_CLIENT_ID = "__meta__"


class MetaLearning:
    """Scan for cross-client patterns and promote to shared meta-memories."""

    def __init__(self, db: Database, memory_store: MemoryStore):
        self.db = db
        self.memory = memory_store

    def scan_for_promotable(self, min_reinforcements: int = 3, min_clients: int = 2) -> int:
        """Find memories reinforced across multiple clients and promote to __meta__.

        Returns count of newly promoted meta-memories.
        """
        # Find active memories grouped by content summary, agent_name, category
        # that appear across multiple clients with enough reinforcement
        rows = self.db.fetchall(
            """SELECT agent_name, category, memory_type, content,
                      SUM(times_reinforced) as total_reinforced,
                      COUNT(DISTINCT client_id) as client_count,
                      AVG(confidence) as avg_confidence
               FROM agent_memories
               WHERE active = 1 AND client_id != ?
               GROUP BY agent_name, category, content
               HAVING SUM(times_reinforced) >= ? AND COUNT(DISTINCT client_id) >= ?""",
            (META_CLIENT_ID, min_reinforcements, min_clients),
        )

        promoted = 0
        for row in rows:
            try:
                content = json.loads(row["content"])
            except (json.JSONDecodeError, TypeError):
                continue

            summary = content.get("summary", content.get("finding", ""))
            if not summary:
                continue

            self._promote_to_meta(
                summary=summary,
                agent_name=row["agent_name"],
                category=row["category"],
                memory_type=row["memory_type"],
                avg_confidence=float(row["avg_confidence"]),
                client_count=row["client_count"],
            )
            promoted += 1

        if promoted:
            logger.info(f"Promoted {promoted} memories to meta-learning pool")
        return promoted

    def _promote_to_meta(
        self,
        summary: str,
        agent_name: str,
        category: str,
        memory_type: str,
        avg_confidence: float,
        client_count: int,
    ) -> str:
        """Create or reinforce a __meta__ memory.

        Meta-memories are stored at 50% of the average client confidence.
        """
        meta_confidence = min(0.7, avg_confidence * 0.5)

        return self.memory.remember(
            AgentMemory(
                agent_name=agent_name,
                client_id=META_CLIENT_ID,
                memory_type=MemoryType(memory_type),
                category=MemoryCategory(category),
                content={
                    "summary": summary,
                    "meta": True,
                    "source_client_count": client_count,
                },
                confidence=meta_confidence,
            )
        )

    def get_meta_context(self, agent_name: str, client_id: str = "default", max_tokens: int = 300) -> str:
        """Get formatted meta-memory context for prompt injection.

        Returns formatted block of shared industry patterns.
        """
        memories = self.memory.recall(
            agent_name=agent_name,
            client_id=META_CLIENT_ID,
            limit=10,
            min_confidence=0.2,
        )

        if not memories:
            return ""

        lines = ["## Industry Patterns (shared across clients)"]
        char_budget = max_tokens * 4
        used = len(lines[0])

        for mem in memories:
            summary = mem.content.get("summary", json.dumps(mem.content))
            client_count = mem.content.get("source_client_count", "?")
            line = f"- SHARED: {summary} [seen across {client_count} clients, confidence: {mem.confidence:.2f}]"

            if used + len(line) + 1 > char_budget:
                break
            lines.append(line)
            used += len(line) + 1

        return "\n".join(lines) if len(lines) > 1 else ""
