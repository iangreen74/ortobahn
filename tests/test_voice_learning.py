"""Tests for the voice learning system."""

from __future__ import annotations

import json

from ortobahn.memory import MemoryStore
from ortobahn.models import MemoryCategory, MemoryType
from ortobahn.voice_learning import VoiceLearner


class TestRecordReview:
    def test_stores_review_in_content_reviews(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        rid = vl.record_review(
            client_id="default",
            content_type="post",
            content_id="p1",
            action="approved",
            content_snapshot={"text": "hello world", "platform": "bluesky"},
        )
        assert rid
        row = test_db.fetchone("SELECT * FROM content_reviews WHERE id=?", (rid,))
        assert row is not None
        assert row["client_id"] == "default"
        assert row["content_type"] == "post"
        assert row["action"] == "approved"

    def test_rejection_stores_reason(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        rid = vl.record_review(
            client_id="default",
            content_type="post",
            content_id="p2",
            action="rejected",
            rejection_reason="too corporate",
            content_snapshot={"text": "Leveraging synergies...", "platform": "bluesky"},
        )
        row = test_db.fetchone("SELECT * FROM content_reviews WHERE id=?", (rid,))
        assert row["rejection_reason"] == "too corporate"

    def test_stores_content_snapshot_as_json(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        snapshot = {"text": "hello", "confidence": 0.85, "platform": "twitter"}
        rid = vl.record_review(
            client_id="default",
            content_type="post",
            content_id="p3",
            action="approved",
            content_snapshot=snapshot,
        )
        row = test_db.fetchone("SELECT * FROM content_reviews WHERE id=?", (rid,))
        parsed = json.loads(row["content_snapshot"])
        assert parsed["text"] == "hello"
        assert parsed["confidence"] == 0.85

    def test_article_review_works(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        rid = vl.record_review(
            client_id="default",
            content_type="article",
            content_id="a1",
            action="edited",
            content_snapshot={"title": "My Article"},
        )
        row = test_db.fetchone("SELECT * FROM content_reviews WHERE id=?", (rid,))
        assert row["content_type"] == "article"
        assert row["action"] == "edited"


class TestVoiceConfidence:
    def _approve_n(self, vl: VoiceLearner, n: int) -> None:
        for i in range(n):
            vl.record_review(
                client_id="default",
                content_type="post",
                content_id=f"ap{i}",
                action="approved",
                content_snapshot={"text": f"Short approved {i}"},
            )

    def _reject_n(self, vl: VoiceLearner, n: int) -> None:
        for i in range(n):
            vl.record_review(
                client_id="default",
                content_type="post",
                content_id=f"rj{i}",
                action="rejected",
                content_snapshot={"text": f"Rejected post {i}"},
            )

    def test_confidence_increases_with_approvals(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        self._approve_n(vl, 10)
        client = test_db.get_client("default")
        assert client["voice_confidence"] > 0.5

    def test_confidence_low_with_rejections(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        self._reject_n(vl, 10)
        client = test_db.get_client("default")
        assert client["voice_confidence"] == 0.0

    def test_confidence_mixed_reviews(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        self._approve_n(vl, 7)
        self._reject_n(vl, 3)
        client = test_db.get_client("default")
        # 7 approved out of 10 = 0.7 approval rate, data_weight = 10/10 = 1.0
        assert 0.5 < client["voice_confidence"] < 0.9

    def test_confidence_below_threshold_when_few_reviews(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        # Just 2 approvals — data_weight = 2/10 = 0.2
        self._approve_n(vl, 2)
        client = test_db.get_client("default")
        assert client["voice_confidence"] <= 0.3


class TestHeuristicAnalysis:
    def test_detects_length_preference(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        # Approve short posts
        for i in range(4):
            vl.record_review(
                client_id="default",
                content_type="post",
                content_id=f"short{i}",
                action="approved",
                content_snapshot={"text": "Short post."},
            )
        # Reject long posts
        for i in range(4):
            vl.record_review(
                client_id="default",
                content_type="post",
                content_id=f"long{i}",
                action="rejected",
                content_snapshot={"text": "A " * 200},
            )
        # Should have created a preference memory about length
        memories = ms.recall("voice_learner", "default", category=MemoryCategory.VOICE_PREFERENCE)
        summaries = [m.content.get("summary", "") for m in memories]
        assert any("concise" in s.lower() or "shorter" in s.lower() for s in summaries)

    def test_detects_emoji_avoidance(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        # Approve posts without emojis
        for i in range(4):
            vl.record_review(
                client_id="default",
                content_type="post",
                content_id=f"noemoji{i}",
                action="approved",
                content_snapshot={"text": "Clean professional content."},
            )
        # Reject posts with emojis
        for i in range(4):
            vl.record_review(
                client_id="default",
                content_type="post",
                content_id=f"emoji{i}",
                action="rejected",
                content_snapshot={"text": "Great content! \U0001f680\U0001f525"},
            )
        memories = ms.recall("voice_learner", "default", category=MemoryCategory.VOICE_PREFERENCE)
        summaries = [m.content.get("summary", "") for m in memories]
        assert any("emoji" in s.lower() for s in summaries)


class TestGetVoiceContext:
    def test_returns_empty_when_no_memories(self, test_db):
        ms = MemoryStore(test_db)
        vl = VoiceLearner(test_db, ms)
        ctx = vl.get_voice_context("default")
        assert ctx == ""

    def test_returns_formatted_preferences(self, test_db):
        ms = MemoryStore(test_db)
        from ortobahn.models import AgentMemory

        ms.remember(
            AgentMemory(
                agent_name="voice_learner",
                client_id="default",
                memory_type=MemoryType.PREFERENCE,
                category=MemoryCategory.VOICE_PREFERENCE,
                content={"summary": "Conversational tone", "type": "prefer"},
                confidence=0.7,
            )
        )
        vl = VoiceLearner(test_db, ms)
        ctx = vl.get_voice_context("default")
        assert "Voice Preferences" in ctx
        assert "PREFER" in ctx
        assert "Conversational tone" in ctx

    def test_avoid_label_for_avoid_type(self, test_db):
        ms = MemoryStore(test_db)
        from ortobahn.models import AgentMemory

        ms.remember(
            AgentMemory(
                agent_name="voice_learner",
                client_id="default",
                memory_type=MemoryType.PREFERENCE,
                category=MemoryCategory.VOICE_PREFERENCE,
                content={"summary": "Corporate jargon", "type": "avoid"},
                confidence=0.7,
            )
        )
        vl = VoiceLearner(test_db, ms)
        ctx = vl.get_voice_context("default")
        assert "AVOID" in ctx
        assert "Corporate jargon" in ctx


class TestMigration033:
    def test_content_reviews_table_exists(self, test_db):
        test_db.fetchall("SELECT id, client_id, content_type, content_id, action FROM content_reviews LIMIT 1")

    def test_auto_publish_articles_column(self, test_db):
        row = test_db.fetchone("SELECT auto_publish_articles FROM clients WHERE id='default'")
        assert row is not None
        assert row["auto_publish_articles"] == 0

    def test_voice_confidence_column(self, test_db):
        row = test_db.fetchone("SELECT voice_confidence FROM clients WHERE id='default'")
        assert row is not None
        assert row["voice_confidence"] == 0.0


class TestDefaultAutoPublish:
    def test_new_client_defaults_to_review_mode(self, test_db):
        cid = test_db.create_client({"name": "TestCo"})
        client = test_db.get_client(cid)
        assert client["auto_publish"] == 0

    def test_new_client_with_explicit_auto_publish(self, test_db):
        cid = test_db.create_client({"name": "AutoCo", "auto_publish": 1})
        client = test_db.get_client(cid)
        assert client["auto_publish"] == 1
