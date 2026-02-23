"""Tests for Cross-Client Meta-Learning module."""

from __future__ import annotations

import pytest

from ortobahn.memory import MemoryStore
from ortobahn.meta_learning import META_CLIENT_ID, MetaLearning
from ortobahn.models import AgentMemory, MemoryCategory, MemoryType


@pytest.fixture
def memory_store(test_db):
    return MemoryStore(db=test_db)


@pytest.fixture
def meta(test_db, memory_store):
    return MetaLearning(db=test_db, memory_store=memory_store)


def _create_memory(memory_store, agent_name, client_id, summary, times_reinforced=1, confidence=0.6):
    """Helper to create a memory for a specific client."""
    mem_id = memory_store.remember(
        AgentMemory(
            agent_name=agent_name,
            client_id=client_id,
            memory_type=MemoryType.OBSERVATION,
            category=MemoryCategory.CONTENT_PATTERN,
            content={"summary": summary},
            confidence=confidence,
        )
    )
    # Manually set times_reinforced if > 1
    if times_reinforced > 1:
        memory_store.db.execute(
            "UPDATE agent_memories SET times_reinforced = ? WHERE id = ?",
            (times_reinforced, mem_id),
            commit=True,
        )
    return mem_id


class TestScanForPromotable:
    def test_single_client_not_promoted(self, meta, memory_store):
        """Memories from only one client should not be promoted."""
        _create_memory(memory_store, "creator", "client-a", "Short posts work", times_reinforced=5)

        promoted = meta.scan_for_promotable(min_reinforcements=3, min_clients=2)
        assert promoted == 0

    def test_promotes_when_threshold_met(self, meta, memory_store, test_db):
        """Memories across multiple clients with enough reinforcement should be promoted."""
        _create_memory(memory_store, "creator", "client-a", "Short posts work", times_reinforced=2)
        _create_memory(memory_store, "creator", "client-b", "Short posts work", times_reinforced=2)

        promoted = meta.scan_for_promotable(min_reinforcements=3, min_clients=2)
        assert promoted >= 1

        # Verify meta memory was created
        meta_mems = memory_store.recall("creator", META_CLIENT_ID)
        assert len(meta_mems) >= 1
        assert any("Short posts work" in m.content.get("summary", "") for m in meta_mems)

    def test_reduced_confidence(self, meta, memory_store):
        """Meta-memories should have reduced confidence (50% of avg)."""
        _create_memory(memory_store, "creator", "client-a", "Question hooks work", times_reinforced=3, confidence=0.8)
        _create_memory(memory_store, "creator", "client-b", "Question hooks work", times_reinforced=3, confidence=0.8)

        meta.scan_for_promotable(min_reinforcements=3, min_clients=2)

        meta_mems = memory_store.recall("creator", META_CLIENT_ID, min_confidence=0.0)
        matching = [m for m in meta_mems if "Question hooks" in m.content.get("summary", "")]
        assert len(matching) >= 1
        assert matching[0].confidence <= 0.7  # 50% of 0.8 = 0.4, capped at 0.7

    def test_does_not_promote_meta_memories(self, meta, memory_store):
        """Should not scan __meta__ client's own memories."""
        _create_memory(memory_store, "creator", META_CLIENT_ID, "Already meta", times_reinforced=5)
        _create_memory(memory_store, "creator", "client-a", "Already meta", times_reinforced=5)

        promoted = meta.scan_for_promotable(min_reinforcements=3, min_clients=2)
        # Only one non-meta client, so shouldn't promote
        assert promoted == 0


class TestGetMetaContext:
    def test_empty_when_no_meta_memories(self, meta):
        assert meta.get_meta_context("creator", "default") == ""

    def test_includes_shared_label(self, meta, memory_store):
        _create_memory(memory_store, "creator", META_CLIENT_ID, "Industry pattern: short posts win")

        ctx = meta.get_meta_context("creator", "default")
        assert "Industry Patterns" in ctx
        assert "SHARED" in ctx
        assert "short posts win" in ctx
