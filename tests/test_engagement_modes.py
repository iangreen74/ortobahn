"""Tests for Engagement Autopilot expansion — draft mode, multi-platform, approve/reject."""

from __future__ import annotations

import pytest

from ortobahn.agents.engagement import EngagementAgent, EngagementReply, EngagementResult


@pytest.fixture()
def _seed_client(test_db):
    test_db.create_client(
        {
            "id": "eng-test",
            "name": "Engagement Test Co",
            "industry": "tech",
            "target_audience": "developers",
            "brand_voice": "casual",
        }
    )


class TestEngagementMode:
    def test_off_mode_skips_entirely(self, test_db, _seed_client):
        """When engagement_mode is 'off', agent returns empty result."""
        test_db.execute(
            "UPDATE clients SET engagement_mode='off' WHERE id='eng-test'",
            commit=True,
        )
        agent = EngagementAgent(test_db, "fake-key", "fake-model")
        result = agent.run("test-run", client_id="eng-test")
        assert isinstance(result, EngagementResult)
        assert result.notifications_checked == 0
        assert result.replies_posted == 0

    def test_auto_mode_is_default(self, test_db, _seed_client):
        """Default engagement_mode should be 'auto'."""
        client = test_db.get_client("eng-test")
        assert client.get("engagement_mode") in ("auto", "")

    def test_draft_mode_records_draft_status(self, test_db, _seed_client):
        """In draft mode, replies should be recorded with status='draft'."""
        test_db.execute(
            "UPDATE clients SET engagement_mode='draft' WHERE id='eng-test'",
            commit=True,
        )
        agent = EngagementAgent(test_db, "fake-key", "fake-model")
        reply = EngagementReply(
            notification_uri="at://did:plc:test/app.bsky.feed.post/123",
            notification_text="Hey @test, great post!",
            reply_text="Thanks for the kind words!",
            confidence=0.85,
            reasoning="Positive mention deserves acknowledgment",
        )
        agent._record_reply("run-1", "eng-test", reply, posted_uri="", status="draft")

        rows = test_db.fetchall("SELECT status, platform FROM engagement_replies WHERE client_id='eng-test'")
        assert len(rows) == 1
        assert rows[0]["status"] == "draft"
        assert rows[0]["platform"] == "bluesky"


class TestRecordReply:
    def test_records_with_platform(self, test_db, _seed_client):
        """Record reply should include platform field."""
        agent = EngagementAgent(test_db, "fake-key", "fake-model")
        reply = EngagementReply(
            notification_uri="at://test",
            notification_text="test",
            reply_text="reply",
            confidence=0.9,
            reasoning="test",
        )
        agent._record_reply("run-1", "eng-test", reply, "at://reply", status="posted", platform="bluesky")

        rows = test_db.fetchall("SELECT platform, status FROM engagement_replies WHERE client_id='eng-test'")
        assert len(rows) == 1
        assert rows[0]["platform"] == "bluesky"
        assert rows[0]["status"] == "posted"

    def test_records_draft_status(self, test_db, _seed_client):
        """Record reply in draft status."""
        agent = EngagementAgent(test_db, "fake-key", "fake-model")
        reply = EngagementReply(
            notification_uri="at://test",
            notification_text="test",
            reply_text="draft reply",
            confidence=0.8,
            reasoning="test",
        )
        agent._record_reply("run-1", "eng-test", reply, "", status="draft")

        row = test_db.fetchone("SELECT status, reply_uri FROM engagement_replies WHERE client_id='eng-test'")
        assert row["status"] == "draft"
        assert row["reply_uri"] == ""


class TestApproveRejectReply:
    def test_approve_changes_status(self, test_db, _seed_client):
        """Approving a draft reply should change its status to 'posted'."""
        agent = EngagementAgent(test_db, "fake-key", "fake-model")
        reply = EngagementReply(
            notification_uri="at://test-approve",
            notification_text="test",
            reply_text="to approve",
            confidence=0.8,
            reasoning="test",
        )
        agent._record_reply("run-1", "eng-test", reply, "", status="draft")

        row = test_db.fetchone("SELECT id FROM engagement_replies WHERE client_id='eng-test' AND status='draft'")
        assert row is not None

        test_db.execute(
            "UPDATE engagement_replies SET status='posted' WHERE id=? AND client_id=?",
            (row["id"], "eng-test"),
            commit=True,
        )

        updated = test_db.fetchone("SELECT status FROM engagement_replies WHERE id=?", (row["id"],))
        assert updated["status"] == "posted"

    def test_reject_changes_status(self, test_db, _seed_client):
        """Rejecting a draft reply should change its status to 'rejected'."""
        agent = EngagementAgent(test_db, "fake-key", "fake-model")
        reply = EngagementReply(
            notification_uri="at://test-reject",
            notification_text="test",
            reply_text="to reject",
            confidence=0.6,
            reasoning="test",
        )
        agent._record_reply("run-1", "eng-test", reply, "", status="draft")

        row = test_db.fetchone("SELECT id FROM engagement_replies WHERE client_id='eng-test' AND status='draft'")

        test_db.execute(
            "UPDATE engagement_replies SET status='rejected' WHERE id=? AND client_id=?",
            (row["id"], "eng-test"),
            commit=True,
        )

        updated = test_db.fetchone("SELECT status FROM engagement_replies WHERE id=?", (row["id"],))
        assert updated["status"] == "rejected"


class TestMultiPlatformInit:
    def test_accepts_all_platform_clients(self):
        """EngagementAgent should accept all platform client kwargs."""
        agent = EngagementAgent(
            db=None,
            api_key="fake",
            model="fake",
            bluesky_client="mock-bs",
            twitter_client="mock-tw",
            linkedin_client="mock-li",
            reddit_client="mock-rd",
        )
        assert agent.bluesky == "mock-bs"
        assert agent.twitter == "mock-tw"
        assert agent.linkedin == "mock-li"
        assert agent.reddit == "mock-rd"
