"""Tests for Engagement Agent."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ortobahn.agents.engagement import EngagementAgent, EngagementReply, EngagementResult

VALID_REPLY_DRAFTS_JSON = json.dumps(
    {
        "replies": [
            {
                "notification_index": 1,
                "reply_text": "Great question! AI agents work best when you give them clear goals.",
                "confidence": 0.90,
                "reasoning": "Directly relevant to our expertise, high engagement potential",
            },
            {
                "notification_index": 2,
                "reply_text": "We've seen similar results. The key is iterative testing.",
                "confidence": 0.60,
                "reasoning": "Somewhat relevant but vague question",
            },
        ]
    }
)

SAMPLE_NOTIFICATIONS = [
    {
        "uri": "at://did:plc:abc/app.bsky.feed.post/111",
        "cid": "bafyabc111",
        "author_handle": "alice.bsky.social",
        "author_display_name": "Alice",
        "text": "Hey @ortobahn, how do AI agents actually work in production?",
        "reason": "mention",
        "indexed_at": "2026-02-22T10:00:00Z",
        "parent_uri": "",
    },
    {
        "uri": "at://did:plc:def/app.bsky.feed.post/222",
        "cid": "bafydef222",
        "author_handle": "bob.bsky.social",
        "author_display_name": "Bob",
        "text": "Interesting take on autonomous systems. What metrics do you track?",
        "reason": "reply",
        "indexed_at": "2026-02-22T11:00:00Z",
        "parent_uri": "at://did:plc:ours/app.bsky.feed.post/000",
    },
]


@pytest.fixture
def engagement_agent(test_db, mock_bluesky_client):
    return EngagementAgent(
        db=test_db,
        api_key="sk-ant-test",
        bluesky_client=mock_bluesky_client,
        max_replies_per_cycle=3,
        reply_confidence_threshold=0.75,
    )


class TestEngagementAgentCreation:
    def test_creates_with_required_params(self, test_db, mock_bluesky_client):
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
        )
        assert agent.db is test_db
        assert agent.api_key == "sk-ant-test"
        assert agent.bluesky is mock_bluesky_client
        assert agent.name == "engagement"

    def test_default_config_values(self, test_db, mock_bluesky_client):
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
        )
        assert agent.max_replies_per_cycle == 3
        assert agent.reply_confidence_threshold == 0.75

    def test_custom_config_values(self, test_db, mock_bluesky_client):
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            max_replies_per_cycle=5,
            reply_confidence_threshold=0.50,
        )
        assert agent.max_replies_per_cycle == 5
        assert agent.reply_confidence_threshold == 0.50

    def test_creates_without_bluesky_client(self, test_db):
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test")
        assert agent.bluesky is None


class TestRunNoBlueskyClient:
    def test_returns_empty_result_when_no_bluesky_client(self, test_db):
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=None)
        result = agent.run(run_id="run-1")

        assert isinstance(result, EngagementResult)
        assert result.notifications_checked == 0
        assert result.replies_drafted == 0
        assert result.replies_posted == 0
        assert result.replies == []
        assert result.errors == []


class TestRunNoNotifications:
    def test_returns_empty_result_when_no_notifications(self, engagement_agent):
        with patch.object(engagement_agent, "_fetch_notifications", return_value=[]):
            result = engagement_agent.run(run_id="run-1")

        assert isinstance(result, EngagementResult)
        assert result.notifications_checked == 0
        assert result.replies_drafted == 0
        assert result.replies_posted == 0


class TestFilterAlreadyReplied:
    def test_filters_out_already_replied_uris(self, engagement_agent, test_db):
        # Insert a reply record for the first notification
        test_db.execute(
            """INSERT INTO engagement_replies
                (id, run_id, client_id, notification_uri, notification_text,
                 reply_text, reply_uri, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                "reply-01",
                "run-prev",
                "default",
                "at://did:plc:abc/app.bsky.feed.post/111",
                "Some text",
                "We replied already",
                "at://did:plc:ours/app.bsky.feed.post/resp1",
                0.90,
            ),
            commit=True,
        )

        filtered = engagement_agent._filter_already_replied(SAMPLE_NOTIFICATIONS, "default")

        assert len(filtered) == 1
        assert filtered[0]["uri"] == "at://did:plc:def/app.bsky.feed.post/222"

    def test_returns_all_when_none_replied(self, engagement_agent):
        filtered = engagement_agent._filter_already_replied(SAMPLE_NOTIFICATIONS, "default")
        assert len(filtered) == 2

    def test_returns_empty_for_empty_input(self, engagement_agent):
        filtered = engagement_agent._filter_already_replied([], "default")
        assert filtered == []


class TestDraftReplies:
    def test_parses_llm_response_into_engagement_replies(self, engagement_agent, mock_llm_response):
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            replies = engagement_agent._draft_replies(
                SAMPLE_NOTIFICATIONS,
                brand_voice="professional",
                memory_context="",
            )

        assert len(replies) == 2
        assert isinstance(replies[0], EngagementReply)
        assert replies[0].notification_uri == "at://did:plc:abc/app.bsky.feed.post/111"
        assert replies[0].confidence == 0.90
        assert "AI agents" in replies[0].reply_text
        assert replies[1].notification_uri == "at://did:plc:def/app.bsky.feed.post/222"
        assert replies[1].confidence == 0.60

    def test_returns_empty_on_invalid_llm_response(self, engagement_agent, mock_llm_response):
        fake = mock_llm_response(text="This is not JSON at all.")

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            replies = engagement_agent._draft_replies(
                SAMPLE_NOTIFICATIONS,
                brand_voice="professional",
                memory_context="",
            )

        assert replies == []

    def test_skips_out_of_range_notification_index(self, engagement_agent, mock_llm_response):
        bad_index_json = json.dumps(
            {
                "replies": [
                    {
                        "notification_index": 99,
                        "reply_text": "Orphan reply",
                        "confidence": 0.80,
                        "reasoning": "Index out of range",
                    },
                ]
            }
        )
        fake = mock_llm_response(text=bad_index_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            replies = engagement_agent._draft_replies(
                SAMPLE_NOTIFICATIONS,
                brand_voice="professional",
                memory_context="",
            )

        assert replies == []


class TestRecordReply:
    def test_inserts_into_engagement_replies_table(self, engagement_agent, test_db):
        reply = EngagementReply(
            notification_uri="at://did:plc:abc/app.bsky.feed.post/111",
            notification_text="Hello!",
            reply_text="Thanks for reaching out!",
            confidence=0.88,
            reasoning="Friendly greeting deserves a response",
        )

        engagement_agent._record_reply(
            run_id="run-42",
            client_id="default",
            reply=reply,
            posted_uri="at://did:plc:ours/app.bsky.feed.post/resp42",
        )

        rows = test_db.fetchall("SELECT * FROM engagement_replies WHERE run_id = ?", ("run-42",))
        assert len(rows) == 1
        row = rows[0]
        assert row["client_id"] == "default"
        assert row["notification_uri"] == "at://did:plc:abc/app.bsky.feed.post/111"
        assert row["reply_text"] == "Thanks for reaching out!"
        assert row["reply_uri"] == "at://did:plc:ours/app.bsky.feed.post/resp42"
        assert row["confidence"] == pytest.approx(0.88)


class TestDryRunMode:
    def test_dry_run_drafts_but_does_not_post(self, engagement_agent, mock_llm_response):
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(engagement_agent, "_fetch_notifications", return_value=SAMPLE_NOTIFICATIONS),
            patch.object(engagement_agent, "_filter_already_replied", return_value=SAMPLE_NOTIFICATIONS),
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(engagement_agent, "_post_reply") as mock_post,
        ):
            result = engagement_agent.run(run_id="run-dry", client_id="default", dry_run=True)

        # Should have drafted replies
        assert result.replies_drafted == 2
        # Should NOT have posted (only the high-confidence one appears in result.replies via dry_run path)
        mock_post.assert_not_called()
        assert result.replies_posted == 0
        # Only the reply at confidence 0.90 is above the 0.75 threshold, so it appears in dry_run results
        assert len(result.replies) == 1
        assert result.replies[0].confidence == 0.90


class TestConfidenceThreshold:
    def test_skips_replies_below_confidence_threshold(self, engagement_agent, mock_llm_response):
        # All replies below threshold
        low_confidence_json = json.dumps(
            {
                "replies": [
                    {
                        "notification_index": 1,
                        "reply_text": "Hmm, interesting point.",
                        "confidence": 0.40,
                        "reasoning": "Low relevance",
                    },
                    {
                        "notification_index": 2,
                        "reply_text": "Thanks for sharing.",
                        "confidence": 0.50,
                        "reasoning": "Generic response",
                    },
                ]
            }
        )
        fake = mock_llm_response(text=low_confidence_json)

        with (
            patch.object(engagement_agent, "_fetch_notifications", return_value=SAMPLE_NOTIFICATIONS),
            patch.object(engagement_agent, "_filter_already_replied", return_value=SAMPLE_NOTIFICATIONS),
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(engagement_agent, "_post_reply") as mock_post,
        ):
            result = engagement_agent.run(run_id="run-low", client_id="default")

        assert result.replies_drafted == 2
        assert result.replies_posted == 0
        mock_post.assert_not_called()
        assert result.replies == []

    def test_posts_only_high_confidence_replies(self, engagement_agent, mock_llm_response):
        # Mix of above and below threshold
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(engagement_agent, "_fetch_notifications", return_value=SAMPLE_NOTIFICATIONS),
            patch.object(engagement_agent, "_filter_already_replied", return_value=SAMPLE_NOTIFICATIONS),
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch.object(
                engagement_agent, "_post_reply", return_value="at://did:plc:ours/app.bsky.feed.post/new1"
            ) as mock_post,
            patch.object(engagement_agent, "_record_reply") as mock_record,
        ):
            result = engagement_agent.run(run_id="run-mix", client_id="default")

        # Only the 0.90 reply (above 0.75 threshold) should be posted
        assert mock_post.call_count == 1
        assert result.replies_posted == 1
        assert result.replies_drafted == 2
        assert len(result.replies) == 1
        assert result.replies[0].confidence == 0.90
        mock_record.assert_called_once()
