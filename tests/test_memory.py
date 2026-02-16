"""Comprehensive tests for the MemoryStore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ortobahn.memory import MemoryStore
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType


class TestMemoryStore:
    """Tests for MemoryStore CRUD, filtering, reinforcement, pruning, and limits."""

    # --- helpers ---

    def _make_memory(
        self,
        agent_name: str = "analytics",
        client_id: str = "default",
        memory_type: MemoryType = MemoryType.OBSERVATION,
        category: MemoryCategory = MemoryCategory.CONTENT_PATTERN,
        summary: str = "posts with questions get 2x engagement",
        confidence: float = 0.6,
        times_reinforced: int = 1,
        times_contradicted: int = 0,
    ) -> AgentMemory:
        return AgentMemory(
            agent_name=agent_name,
            client_id=client_id,
            memory_type=memory_type,
            category=category,
            content={"summary": summary},
            confidence=confidence,
            source_run_id="run-001",
            source_post_ids=["p1", "p2"],
            times_reinforced=times_reinforced,
            times_contradicted=times_contradicted,
        )

    # --- 1. remember and recall ---

    def test_remember_and_recall(self, test_db):
        store = MemoryStore(test_db)
        mem = self._make_memory()

        mem_id = store.remember(mem)
        assert mem_id  # non-empty id returned

        recalled = store.recall("analytics")
        assert len(recalled) == 1

        r = recalled[0]
        assert r.id == mem_id
        assert r.agent_name == "analytics"
        assert r.client_id == "default"
        assert r.memory_type == MemoryType.OBSERVATION
        assert r.category == MemoryCategory.CONTENT_PATTERN
        assert r.content["summary"] == "posts with questions get 2x engagement"
        assert r.confidence == 0.6
        assert r.source_run_id == "run-001"
        assert r.source_post_ids == ["p1", "p2"]
        assert r.times_reinforced == 1
        assert r.times_contradicted == 0

    # --- 2. reinforcement of similar memories ---

    def test_remember_reinforces_similar(self, test_db):
        store = MemoryStore(test_db)
        original = self._make_memory(confidence=0.5)
        mem_id = store.remember(original)

        # Store a memory with the same summary -- should reinforce, not duplicate
        duplicate = self._make_memory(confidence=0.5)
        returned_id = store.remember(duplicate)

        assert returned_id == mem_id  # same id returned

        recalled = store.recall("analytics")
        assert len(recalled) == 1  # still only one memory

        r = recalled[0]
        assert r.times_reinforced == 2  # incremented from 1 to 2
        assert r.confidence == pytest.approx(0.55, abs=0.01)  # boosted by 0.05

    # --- 3. category filter ---

    def test_recall_filters_by_category(self, test_db):
        store = MemoryStore(test_db)

        store.remember(self._make_memory(category=MemoryCategory.CONTENT_PATTERN, summary="pattern A"))
        store.remember(self._make_memory(category=MemoryCategory.TIMING, summary="timing B"))
        store.remember(self._make_memory(category=MemoryCategory.AUDIENCE_BEHAVIOR, summary="audience C"))

        only_timing = store.recall("analytics", category=MemoryCategory.TIMING)
        assert len(only_timing) == 1
        assert only_timing[0].category == MemoryCategory.TIMING
        assert only_timing[0].content["summary"] == "timing B"

        only_content = store.recall("analytics", category=MemoryCategory.CONTENT_PATTERN)
        assert len(only_content) == 1
        assert only_content[0].category == MemoryCategory.CONTENT_PATTERN

    # --- 4. min_confidence filter ---

    def test_recall_filters_by_min_confidence(self, test_db):
        store = MemoryStore(test_db)

        store.remember(self._make_memory(confidence=0.2, summary="low conf"))
        store.remember(self._make_memory(confidence=0.8, summary="high conf"))

        # Default min_confidence is 0.3 -- the 0.2 memory should be excluded
        results = store.recall("analytics")
        assert len(results) == 1
        assert results[0].content["summary"] == "high conf"

        # Explicitly lower threshold to include both
        results_all = store.recall("analytics", min_confidence=0.1)
        assert len(results_all) == 2

    # --- 5. contradict ---

    def test_contradict_increments_counter(self, test_db):
        store = MemoryStore(test_db)
        mem_id = store.remember(self._make_memory())

        store.contradict(mem_id, evidence="new data shows otherwise")
        store.contradict(mem_id)

        recalled = store.recall("analytics")
        assert len(recalled) == 1
        assert recalled[0].times_contradicted == 2

    # --- 6. prune removes low confidence ---

    def test_prune_removes_low_confidence(self, test_db):
        store = MemoryStore(test_db)

        # Create a low-confidence memory and backdate it so it's old enough to prune
        mem_id = store.remember(self._make_memory(confidence=0.15, summary="stale insight"))

        # Manually backdate the updated_at to make it older than max_age_days
        old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        test_db.conn.execute(
            "UPDATE agent_memories SET updated_at = ?, created_at = ? WHERE id = ?",
            (old_date, old_date, mem_id),
        )
        test_db.conn.commit()

        # Also add a healthy memory that should survive
        store.remember(self._make_memory(confidence=0.8, summary="healthy insight"))

        pruned = store.prune(max_age_days=90, min_confidence=0.2)
        assert pruned >= 1

        # The low-confidence old memory should be deactivated
        remaining = store.recall("analytics", min_confidence=0.0)
        summaries = [m.content["summary"] for m in remaining]
        assert "stale insight" not in summaries
        assert "healthy insight" in summaries

    # --- 7. get_memory_context format ---

    def test_get_memory_context_format(self, test_db):
        store = MemoryStore(test_db)

        store.remember(
            self._make_memory(
                memory_type=MemoryType.OBSERVATION,
                confidence=0.8,
                summary="questions drive engagement",
            )
        )
        store.remember(
            self._make_memory(
                memory_type=MemoryType.PREFERENCE,
                category=MemoryCategory.AUDIENCE_BEHAVIOR,
                confidence=0.7,
                summary="avoid corporate jargon",
            )
        )

        ctx = store.get_memory_context("analytics")
        assert ctx  # non-empty
        assert ctx.startswith("## Learned Patterns (from past performance)")
        assert "questions drive engagement" in ctx
        assert "avoid corporate jargon" in ctx
        # Check formatting tokens
        assert "confidence:" in ctx
        assert "STRONG" in ctx or "AVOID" in ctx or "NOTE" in ctx or "PREFER" in ctx

    # --- 8. get_memory_context empty ---

    def test_get_memory_context_empty_when_no_memories(self, test_db):
        store = MemoryStore(test_db)
        ctx = store.get_memory_context("nonexistent_agent")
        assert ctx == ""

    # --- 9. count ---

    def test_count(self, test_db):
        store = MemoryStore(test_db)
        assert store.count("analytics") == 0

        store.remember(self._make_memory(summary="first"))
        assert store.count("analytics") == 1

        store.remember(self._make_memory(summary="second"))
        assert store.count("analytics") == 2

        # Different agent should not affect count
        store.remember(self._make_memory(agent_name="creator", summary="creator mem"))
        assert store.count("analytics") == 2
        assert store.count("creator") == 1

    # --- 10. enforce_limits ---

    def test_enforce_limits(self, test_db):
        store = MemoryStore(test_db)

        # Insert 5 memories with varying confidence
        for i in range(5):
            store.remember(
                self._make_memory(
                    summary=f"memory {i}",
                    confidence=0.3 + i * 0.1,  # 0.3, 0.4, 0.5, 0.6, 0.7
                    category=[
                        MemoryCategory.CONTENT_PATTERN,
                        MemoryCategory.TIMING,
                        MemoryCategory.AUDIENCE_BEHAVIOR,
                        MemoryCategory.THEME_PERFORMANCE,
                        MemoryCategory.CALIBRATION,
                    ][i],
                )
            )

        assert store.count("analytics") == 5

        # Enforce a limit of 3 -- should deactivate 2 lowest confidence
        excess = store.enforce_limits("analytics", max_memories=3)
        assert excess == 2
        assert store.count("analytics") == 3

        # The surviving memories should be the higher-confidence ones
        remaining = store.recall("analytics", min_confidence=0.0)
        confs = sorted([m.confidence for m in remaining])
        assert confs == pytest.approx([0.5, 0.6, 0.7], abs=0.01)

    # --- 11. recall sorted by relevance ---

    def test_recall_sorted_by_relevance(self, test_db):
        store = MemoryStore(test_db)

        # Memory A: high confidence, reinforced many times
        store.remember(
            self._make_memory(
                summary="high relevance",
                confidence=0.9,
                times_reinforced=5,
                category=MemoryCategory.CONTENT_PATTERN,
            )
        )

        # Memory B: medium confidence, reinforced once
        store.remember(
            self._make_memory(
                summary="medium relevance",
                confidence=0.5,
                times_reinforced=1,
                category=MemoryCategory.TIMING,
            )
        )

        # Memory C: low confidence, contradicted
        mem_c_id = store.remember(
            self._make_memory(
                summary="low relevance",
                confidence=0.35,
                times_reinforced=1,
                category=MemoryCategory.AUDIENCE_BEHAVIOR,
            )
        )
        store.contradict(mem_c_id)

        results = store.recall("analytics", min_confidence=0.3)
        assert len(results) >= 2

        # The high-confidence, highly-reinforced memory should be first
        assert results[0].content["summary"] == "high relevance"

        # The contradicted low-confidence memory should be last (if present)
        summaries = [m.content["summary"] for m in results]
        assert summaries.index("high relevance") < summaries.index("medium relevance")

    # --- additional edge-case tests ---

    def test_enforce_limits_no_op_when_under_limit(self, test_db):
        store = MemoryStore(test_db)
        store.remember(self._make_memory(summary="only one"))
        excess = store.enforce_limits("analytics", max_memories=100)
        assert excess == 0
        assert store.count("analytics") == 1

    def test_recall_respects_client_id_isolation(self, test_db):
        store = MemoryStore(test_db)
        store.remember(self._make_memory(client_id="acme", summary="acme memory"))
        store.remember(self._make_memory(client_id="globex", summary="globex memory"))

        acme = store.recall("analytics", client_id="acme")
        assert len(acme) == 1
        assert acme[0].content["summary"] == "acme memory"

        globex = store.recall("analytics", client_id="globex")
        assert len(globex) == 1
        assert globex[0].content["summary"] == "globex memory"

        # Default client_id yields nothing here
        default = store.recall("analytics", client_id="default")
        assert len(default) == 0

    def test_prune_deactivates_heavily_contradicted(self, test_db):
        """Memories where times_contradicted > times_reinforced * 2 and > 2 get pruned."""
        store = MemoryStore(test_db)
        mem_id = store.remember(self._make_memory(confidence=0.5, summary="contested"))

        # Contradict it 3 times (times_reinforced is 1, so 3 > 1*2 and 3 > 2)
        for _ in range(3):
            store.contradict(mem_id)

        pruned = store.prune()
        assert pruned >= 1

        remaining = store.recall("analytics", min_confidence=0.0)
        summaries = [m.content["summary"] for m in remaining]
        assert "contested" not in summaries

    def test_remember_returns_unique_ids(self, test_db):
        store = MemoryStore(test_db)
        ids = set()
        for i in range(10):
            mid = store.remember(
                self._make_memory(
                    summary=f"unique memory {i}",
                    category=[
                        MemoryCategory.CONTENT_PATTERN,
                        MemoryCategory.TIMING,
                        MemoryCategory.AUDIENCE_BEHAVIOR,
                        MemoryCategory.THEME_PERFORMANCE,
                        MemoryCategory.CALIBRATION,
                        MemoryCategory.PLATFORM_SPECIFIC,
                        MemoryCategory.CONTENT_PATTERN,
                        MemoryCategory.TIMING,
                        MemoryCategory.AUDIENCE_BEHAVIOR,
                        MemoryCategory.THEME_PERFORMANCE,
                    ][i],
                )
            )
            ids.add(mid)
        assert len(ids) == 10

    def test_recall_limit(self, test_db):
        store = MemoryStore(test_db)
        categories = list(MemoryCategory)
        for i in range(8):
            store.remember(
                self._make_memory(
                    summary=f"memory {i}",
                    confidence=0.5 + i * 0.05,
                    category=categories[i % len(categories)],
                )
            )

        assert store.count("analytics") == 8
        limited = store.recall("analytics", limit=3)
        assert len(limited) == 3

    def test_get_memory_context_with_reinforced_memories(self, test_db):
        """Context string should show 'seen Nx' for reinforced memories."""
        store = MemoryStore(test_db)

        mem = self._make_memory(summary="recurring pattern", confidence=0.7)
        store.remember(mem)
        # Reinforce it twice more
        store.remember(self._make_memory(summary="recurring pattern", confidence=0.7))
        store.remember(self._make_memory(summary="recurring pattern", confidence=0.7))

        ctx = store.get_memory_context("analytics")
        assert "seen 3x" in ctx

    def test_count_excludes_inactive(self, test_db):
        """count() should only return active memories."""
        store = MemoryStore(test_db)
        mem_id = store.remember(self._make_memory(confidence=0.15, summary="will be pruned"))

        # Backdate and prune it
        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        test_db.conn.execute(
            "UPDATE agent_memories SET updated_at = ? WHERE id = ?",
            (old_date, mem_id),
        )
        test_db.conn.commit()
        store.prune(max_age_days=90, min_confidence=0.2)

        assert store.count("analytics") == 0
