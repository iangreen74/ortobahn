"""Agent memory store — persistent structured observations and learnings."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from ortobahn.db import Database, to_datetime
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType

logger = logging.getLogger("ortobahn.memory")


class MemoryStore:
    """Read/write access to the agent_memories table with intelligent retrieval."""

    def __init__(self, db: Database):
        self.db = db

    def remember(self, memory: AgentMemory) -> str:
        """Store a new memory. If a similar memory exists, reinforce it instead."""
        existing = self._find_similar(memory)
        if existing:
            self._reinforce(existing["id"], memory)
            return existing["id"]
        return self._create(memory)

    def recall(
        self,
        agent_name: str,
        client_id: str = "default",
        category: MemoryCategory | None = None,
        limit: int = 10,
        min_confidence: float = 0.3,
    ) -> list[AgentMemory]:
        """Retrieve relevant memories sorted by relevance score."""
        query = """
            SELECT * FROM agent_memories
            WHERE agent_name = ? AND client_id = ? AND active = 1
                AND confidence >= ?
        """
        params: list = [agent_name, client_id, min_confidence]

        if category is not None:
            query += " AND category = ?"
            params.append(category.value)

        query += " ORDER BY confidence DESC, times_reinforced DESC LIMIT ?"
        params.append(limit * 2)  # Fetch extra for scoring

        rows = self.db.fetchall(query, params)
        memories = [self._row_to_memory(row) for row in rows]

        # Score and sort by relevance
        scored = [(m, self._relevance_score(m, row)) for m, row in zip(memories, rows, strict=True)]
        scored.sort(key=lambda x: x[1], reverse=True)

        return [m for m, _ in scored[:limit]]

    def contradict(self, memory_id: str, evidence: str = "") -> None:
        """Record counter-evidence against a memory."""
        self.db.execute(
            "UPDATE agent_memories SET times_contradicted = times_contradicted + 1, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), memory_id),
            commit=True,
        )

    def prune(self, max_age_days: int = 90, min_confidence: float = 0.2) -> int:
        """Remove stale or low-confidence memories. Returns count removed."""
        from datetime import timedelta

        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        # Count how many will be affected, then deactivate
        condition = """active = 1 AND (
                (confidence < ? AND updated_at < ?)
                OR (times_contradicted > times_reinforced * 2 AND times_contradicted > 2)
                OR (expires_at IS NOT NULL AND expires_at < ?)
            )"""
        params = (min_confidence, cutoff, now)
        row = self.db.fetchone(f"SELECT COUNT(*) as cnt FROM agent_memories WHERE {condition}", params)
        count = row["cnt"] if row else 0
        if count > 0:
            self.db.execute(
                f"UPDATE agent_memories SET active = 0 WHERE {condition}",
                params,
                commit=True,
            )
            logger.info(f"Pruned {count} stale/low-confidence memories")
        return count

    def get_memory_context(
        self,
        agent_name: str,
        client_id: str = "default",
        max_tokens: int = 800,
    ) -> str:
        """Build a formatted text block of relevant memories for prompt injection.

        Stays within approximate token budget (1 token ~ 4 chars).
        """
        memories = self.recall(agent_name, client_id, limit=15)
        if not memories:
            return ""

        lines = ["## Learned Patterns (from past performance)"]
        char_budget = max_tokens * 4
        used = len(lines[0])

        # Group memories by type for clearer presentation
        for mem in memories:
            strength = "STRONG" if mem.confidence >= 0.7 else "NOTE"
            if mem.memory_type == MemoryType.PREFERENCE:
                strength = "AVOID" if "avoid" in json.dumps(mem.content).lower() else "PREFER"
            elif mem.memory_type == MemoryType.GOAL_STATE:
                strength = "GOAL"
            elif mem.category == MemoryCategory.CALIBRATION:
                strength = "CALIBRATION"

            summary = mem.content.get("summary", mem.content.get("finding", json.dumps(mem.content)))
            if isinstance(summary, dict):
                summary = json.dumps(summary)

            reinforced = f", seen {mem.times_reinforced}x" if mem.times_reinforced > 1 else ""
            line = f"- {strength}: {summary} [confidence: {mem.confidence:.2f}{reinforced}]"

            if used + len(line) + 1 > char_budget:
                break
            lines.append(line)
            used += len(line) + 1

        # Add goal context
        goals = self._get_goals(agent_name, client_id)
        for goal in goals:
            line = f"- GOAL: {goal['metric_name']} {goal['current_value']:.1f}/{goal['target_value']:.1f} target (trend: {goal['trend']})"
            if used + len(line) + 1 > char_budget:
                break
            lines.append(line)
            used += len(line) + 1

        return "\n".join(lines) if len(lines) > 1 else ""

    def count(self, agent_name: str, client_id: str = "default") -> int:
        """Count active memories for an agent."""
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM agent_memories WHERE agent_name = ? AND client_id = ? AND active = 1",
            (agent_name, client_id),
        )
        return row["cnt"] if row else 0

    def enforce_limits(self, agent_name: str, client_id: str = "default", max_memories: int = 100) -> int:
        """Ensure memory count stays within limits by deactivating lowest-relevance entries."""
        count = self.count(agent_name, client_id)
        if count <= max_memories:
            return 0
        excess = count - max_memories
        self.db.execute(
            """UPDATE agent_memories SET active = 0 WHERE id IN (
                SELECT id FROM agent_memories
                WHERE agent_name = ? AND client_id = ? AND active = 1
                ORDER BY confidence ASC, times_reinforced ASC
                LIMIT ?
            )""",
            (agent_name, client_id, excess),
            commit=True,
        )
        return excess

    # --- Internal helpers ---

    def _create(self, memory: AgentMemory) -> str:
        mem_id = memory.id or str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """INSERT INTO agent_memories
            (id, agent_name, client_id, memory_type, category, content, confidence,
             source_run_id, source_post_ids, times_reinforced, times_contradicted,
             created_at, updated_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                mem_id,
                memory.agent_name,
                memory.client_id,
                memory.memory_type.value,
                memory.category.value,
                json.dumps(memory.content),
                memory.confidence,
                memory.source_run_id,
                json.dumps(memory.source_post_ids),
                memory.times_reinforced,
                memory.times_contradicted,
                now,
                now,
            ),
            commit=True,
        )
        logger.debug(f"Created memory {mem_id} for {memory.agent_name}: {memory.category.value}")
        return mem_id

    def _find_similar(self, memory: AgentMemory) -> dict | None:
        """Find an existing active memory with same agent, client, type, and category."""
        rows = self.db.fetchall(
            """SELECT * FROM agent_memories
            WHERE agent_name = ? AND client_id = ? AND memory_type = ? AND category = ?
                AND active = 1
            ORDER BY times_reinforced DESC LIMIT 5""",
            (memory.agent_name, memory.client_id, memory.memory_type.value, memory.category.value),
        )

        # Check content similarity (same key findings)
        for row in rows:
            existing_content = json.loads(row["content"])
            # Match on summary/finding text if present
            new_summary = memory.content.get("summary", memory.content.get("finding", ""))
            old_summary = existing_content.get("summary", existing_content.get("finding", ""))
            if new_summary and old_summary and new_summary == old_summary:
                return dict(row)
        return None

    def _reinforce(self, memory_id: str, new_memory: AgentMemory) -> None:
        """Reinforce an existing memory with new supporting evidence."""
        now = datetime.now(timezone.utc).isoformat()
        # Boost confidence slightly (up to 0.95)
        self.db.execute(
            """UPDATE agent_memories SET
                times_reinforced = times_reinforced + 1,
                confidence = MIN(0.95, confidence + 0.05),
                updated_at = ?
            WHERE id = ?""",
            (now, memory_id),
            commit=True,
        )
        logger.debug(f"Reinforced memory {memory_id}")

    def _row_to_memory(self, row) -> AgentMemory:
        return AgentMemory(
            id=row["id"],
            agent_name=row["agent_name"],
            client_id=row["client_id"],
            memory_type=MemoryType(row["memory_type"]),
            category=MemoryCategory(row["category"]),
            content=json.loads(row["content"]),
            confidence=row["confidence"],
            source_run_id=row["source_run_id"] or "",
            source_post_ids=json.loads(row["source_post_ids"]) if row["source_post_ids"] else [],
            times_reinforced=row["times_reinforced"],
            times_contradicted=row["times_contradicted"],
        )

    def _relevance_score(self, memory: AgentMemory, row) -> float:
        """Calculate relevance: confidence * trust_ratio * recency_decay."""
        trust = memory.times_reinforced / max(1, memory.times_reinforced + memory.times_contradicted)

        updated = row["updated_at"] or row["created_at"]
        try:
            updated_dt = to_datetime(updated)
            days_old = (datetime.now(timezone.utc) - updated_dt.replace(tzinfo=timezone.utc)).days
        except (ValueError, TypeError):
            days_old = 0
        recency = max(0.3, 1.0 - (days_old / 90))

        return memory.confidence * trust * recency

    def _get_goals(self, agent_name: str, client_id: str) -> list[dict]:
        """Retrieve current goals for an agent."""
        return self.db.fetchall(
            "SELECT * FROM agent_goals WHERE agent_name = ? AND client_id = ?",
            (agent_name, client_id),
        )
