"""Tests for Engagement Agent."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ortobahn.agents.engagement import EngagementAgent, EngagementReply, EngagementResult


def _make_notification(
    uri="at://did:plc:abc/app.bsky.feed.post/1", text="Hello @bot!", reason="mention", handle="alice.bsky.social"
):
    """Helper to build a notification dict matching the agent's expected format."""
    return {
        "uri": uri,
        "cid": "bafycid1",
        "author_handle": handle,
        "author_display_name": handle.split(".")[0].title(),
        "text": text,
        "reason": reason,
        "indexed_at": "2026-02-23T12:00:00Z",
        "parent_uri": "",
    }


VALID_REPLY_DRAFTS_JSON = json.dumps(
    {
        "replies": [
            {
                "notification_index": 1,
                "reply_text": "Thanks for the mention! We appreciate the support.",
                "confidence": 0.90,
                "reasoning": "Genuine positive engagement from a relevant user.",
            },
        ]
    }
)

TWO_REPLY_DRAFTS_JSON = json.dumps(
    {
        "replies": [
            {
                "notification_index": 1,
                "reply_text": "Thanks for reaching out!",
                "confidence": 0.92,
                "reasoning": "Positive engagement.",
            },
            {
                "notification_index": 2,
                "reply_text": "Great question! We're working on that.",
                "confidence": 0.85,
                "reasoning": "Relevant question about our product.",
            },
        ]
    }
)


class TestEngagementAgent:
    """Tests for the EngagementAgent."""

    # --- Basic initialization and no-op cases ---

    def test_returns_empty_result_when_no_bluesky_client(self, test_db):
        """Agent returns empty result immediately when bluesky_client is None."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=None)
        result = agent.run(run_id="run-1", client_id="default")

        assert isinstance(result, EngagementResult)
        assert result.notifications_checked == 0
        assert result.replies_drafted == 0
        assert result.replies_posted == 0

    def test_returns_empty_result_when_no_notifications(self, test_db, mock_bluesky_client):
        """Agent handles empty notification list gracefully."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)

        with patch.object(agent, "_fetch_notifications", return_value=[]):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.notifications_checked == 0
        assert result.replies_drafted == 0

    # --- Notification fetching ---

    def test_fetch_notifications_filters_mentions_and_replies(self, test_db, mock_bluesky_client):
        """_fetch_notifications only includes 'mention' and 'reply' reasons."""
        mock_notif_mention = MagicMock()
        mock_notif_mention.reason = "mention"
        mock_notif_mention.uri = "at://did:plc:abc/app.bsky.feed.post/1"
        mock_notif_mention.cid = "cid1"
        mock_notif_mention.author.handle = "alice.bsky.social"
        mock_notif_mention.author.display_name = "Alice"
        mock_notif_mention.record.text = "Hey @bot, thoughts?"
        mock_notif_mention.indexed_at = "2026-02-23T12:00:00Z"

        mock_notif_reply = MagicMock()
        mock_notif_reply.reason = "reply"
        mock_notif_reply.uri = "at://did:plc:abc/app.bsky.feed.post/2"
        mock_notif_reply.cid = "cid2"
        mock_notif_reply.author.handle = "bob.bsky.social"
        mock_notif_reply.author.display_name = "Bob"
        mock_notif_reply.record.text = "Interesting take!"
        mock_notif_reply.record.reply.parent.uri = "at://did:plc:xyz/app.bsky.feed.post/parent1"
        mock_notif_reply.indexed_at = "2026-02-23T12:01:00Z"

        mock_notif_like = MagicMock()
        mock_notif_like.reason = "like"

        response = MagicMock()
        response.notifications = [mock_notif_mention, mock_notif_reply, mock_notif_like]

        mock_bluesky_client._call_with_retry.return_value = response

        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        notifications = agent._fetch_notifications()

        assert len(notifications) == 2
        assert notifications[0]["reason"] == "mention"
        assert notifications[0]["text"] == "Hey @bot, thoughts?"
        assert notifications[1]["reason"] == "reply"
        assert notifications[1]["parent_uri"] == "at://did:plc:xyz/app.bsky.feed.post/parent1"

    def test_fetch_notifications_returns_empty_on_error(self, test_db, mock_bluesky_client):
        """_fetch_notifications returns [] when Bluesky API fails."""
        mock_bluesky_client.login.side_effect = Exception("Network error")

        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        notifications = agent._fetch_notifications()

        assert notifications == []

    def test_fetch_notifications_handles_missing_record_text(self, test_db, mock_bluesky_client):
        """Handles notification where record has no text attribute."""
        mock_notif = MagicMock()
        mock_notif.reason = "mention"
        mock_notif.uri = "at://did:plc:abc/app.bsky.feed.post/1"
        mock_notif.cid = "cid1"
        mock_notif.author.handle = "alice.bsky.social"
        mock_notif.author.display_name = "Alice"
        mock_notif.indexed_at = "2026-02-23T12:00:00Z"
        # record exists but has no 'text' attribute
        mock_notif.record = MagicMock(spec=[])  # empty spec = no attributes

        response = MagicMock()
        response.notifications = [mock_notif]
        mock_bluesky_client._call_with_retry.return_value = response

        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        notifications = agent._fetch_notifications()

        assert len(notifications) == 1
        assert notifications[0]["text"] == ""

    # --- Duplicate filtering ---

    def test_filter_already_replied_excludes_known_uris(self, test_db, mock_bluesky_client):
        """Notifications already replied to are filtered out."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)

        # Insert a previous reply into the database
        test_db.execute(
            """CREATE TABLE IF NOT EXISTS engagement_replies
               (id TEXT, run_id TEXT, client_id TEXT, notification_uri TEXT,
                notification_text TEXT, reply_text TEXT, reply_uri TEXT,
                confidence REAL, created_at TEXT)""",
            commit=True,
        )
        test_db.execute(
            """INSERT INTO engagement_replies
               (id, run_id, client_id, notification_uri, notification_text, reply_text, reply_uri, confidence, created_at)
               VALUES ('r1', 'run-old', 'default', 'at://did:plc:abc/app.bsky.feed.post/1',
                        'old text', 'old reply', 'at://reply/1', 0.9, '2026-02-23')""",
            commit=True,
        )

        notifications = [
            _make_notification(uri="at://did:plc:abc/app.bsky.feed.post/1"),
            _make_notification(uri="at://did:plc:abc/app.bsky.feed.post/2"),
        ]

        filtered = agent._filter_already_replied(notifications, "default")

        assert len(filtered) == 1
        assert filtered[0]["uri"] == "at://did:plc:abc/app.bsky.feed.post/2"

    def test_filter_already_replied_returns_empty_for_empty_input(self, test_db, mock_bluesky_client):
        """Returns empty list when given empty notifications."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        assert agent._filter_already_replied([], "default") == []

    def test_all_notifications_already_replied_returns_empty_result(
        self, test_db, mock_bluesky_client, mock_llm_response
    ):
        """When all notifications are already replied to, returns early."""
        notifications = [_make_notification(uri="at://already/1")]

        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)

        with (
            patch.object(agent, "_fetch_notifications", return_value=notifications),
            patch.object(agent, "_filter_already_replied", return_value=[]),
        ):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.notifications_checked == 1
        assert result.replies_drafted == 0

    # --- Reply drafting via LLM ---

    def test_draft_replies_parses_valid_json(self, test_db, mock_bluesky_client, mock_llm_response):
        """_draft_replies correctly parses LLM output into EngagementReply objects."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        notifications = [_make_notification()]

        with patch.object(agent, "call_llm", return_value=fake):
            replies = agent._draft_replies(notifications, "professional", "")

        assert len(replies) == 1
        assert isinstance(replies[0], EngagementReply)
        assert replies[0].confidence == 0.90
        assert "Thanks for the mention" in replies[0].reply_text
        assert replies[0].notification_uri == notifications[0]["uri"]

    def test_draft_replies_returns_empty_on_invalid_json(self, test_db, mock_bluesky_client, mock_llm_response):
        """_draft_replies returns empty list when LLM returns invalid JSON."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text="This is not JSON at all")

        with patch.object(agent, "call_llm", return_value=fake):
            replies = agent._draft_replies([_make_notification()], "professional", "")

        assert replies == []

    def test_draft_replies_skips_out_of_range_indices(self, test_db, mock_bluesky_client, mock_llm_response):
        """Replies referencing invalid notification indices are discarded."""
        bad_index_json = json.dumps(
            {
                "replies": [
                    {
                        "notification_index": 99,
                        "reply_text": "Ghost reply",
                        "confidence": 0.8,
                        "reasoning": "Invalid index",
                    }
                ]
            }
        )
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=bad_index_json)

        with patch.object(agent, "call_llm", return_value=fake):
            replies = agent._draft_replies([_make_notification()], "professional", "")

        assert replies == []

    def test_draft_replies_includes_memory_context(self, test_db, mock_bluesky_client, mock_llm_response):
        """Memory context is injected into the LLM prompt when provided."""
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with patch.object(agent, "call_llm", return_value=fake) as mock_call:
            agent._draft_replies([_make_notification()], "casual", "Past lesson: be concise")

        call_args = mock_call.call_args
        user_msg = call_args[0][0] if call_args[0] else call_args.kwargs.get("user_message", "")
        assert "Past lesson: be concise" in user_msg
        assert "casual" in user_msg

    # --- Confidence thresholding ---

    def test_low_confidence_replies_are_skipped(self, test_db, mock_bluesky_client, mock_llm_response):
        """Replies below the confidence threshold are not posted."""
        low_conf_json = json.dumps(
            {
                "replies": [
                    {
                        "notification_index": 1,
                        "reply_text": "Low confidence reply",
                        "confidence": 0.30,
                        "reasoning": "Not very sure.",
                    }
                ]
            }
        )
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.75,
        )
        fake = mock_llm_response(text=low_conf_json)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
        ):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.replies_drafted == 1
        assert result.replies_posted == 0
        assert len(result.replies) == 0

    def test_high_confidence_replies_are_posted(self, test_db, mock_bluesky_client, mock_llm_response):
        """Replies above the confidence threshold are posted."""
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.75,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply", return_value="at://did:plc:test/app.bsky.feed.post/reply1"),
            patch.object(agent, "_record_reply"),
        ):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.replies_drafted == 1
        assert result.replies_posted == 1
        assert len(result.replies) == 1

    def test_custom_confidence_threshold(self, test_db, mock_bluesky_client, mock_llm_response):
        """Custom threshold changes the cutoff for auto-replies."""
        # Confidence is 0.90 in the reply, threshold set to 0.95 -> should skip
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.95,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
        ):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.replies_drafted == 1
        assert result.replies_posted == 0

    # --- Dry run mode ---

    def test_dry_run_does_not_post(self, test_db, mock_bluesky_client, mock_llm_response):
        """In dry_run mode, replies are drafted but never posted."""
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.5,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply") as mock_post,
        ):
            result = agent.run(run_id="run-1", client_id="default", dry_run=True)

        mock_post.assert_not_called()
        assert result.replies_drafted == 1
        assert result.replies_posted == 0
        # Dry run still appends the reply to the result list
        assert len(result.replies) == 1

    # --- Rate limiting (max_replies_per_cycle) ---

    def test_max_replies_per_cycle_limits_posting(self, test_db, mock_bluesky_client, mock_llm_response):
        """Only max_replies_per_cycle replies are attempted even if more are drafted."""
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            max_replies_per_cycle=1,
            reply_confidence_threshold=0.5,
        )
        fake = mock_llm_response(text=TWO_REPLY_DRAFTS_JSON)

        notifications = [
            _make_notification(uri="at://did:plc:abc/app.bsky.feed.post/1", text="First"),
            _make_notification(uri="at://did:plc:abc/app.bsky.feed.post/2", text="Second"),
        ]

        with (
            patch.object(agent, "_fetch_notifications", return_value=notifications),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply", return_value="at://reply/x") as mock_post,
            patch.object(agent, "_record_reply"),
        ):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.replies_drafted == 2
        # Only 1 reply attempted due to max_replies_per_cycle=1
        assert mock_post.call_count == 1

    # --- Error handling ---

    def test_post_reply_failure_recorded_as_error(self, test_db, mock_bluesky_client, mock_llm_response):
        """When _post_reply raises an exception, it is captured in result.errors."""
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.5,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply", side_effect=Exception("Bluesky API down")),
        ):
            result = agent.run(run_id="run-1", client_id="default")

        assert result.replies_posted == 0
        assert len(result.errors) == 1
        assert "Bluesky API down" in result.errors[0]

    def test_post_reply_returns_none_not_recorded(self, test_db, mock_bluesky_client, mock_llm_response):
        """When _post_reply returns None (failure without exception), reply is not recorded."""
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.5,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake),
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply", return_value=None),
            patch.object(agent, "_record_reply") as mock_record,
        ):
            result = agent.run(run_id="run-1", client_id="default")

        mock_record.assert_not_called()
        assert result.replies_posted == 0

    # --- Brand voice / client context ---

    def test_brand_voice_from_client_data(self, test_db, mock_bluesky_client, mock_llm_response):
        """Brand voice is retrieved from client data and passed to _draft_replies."""
        test_db.create_client({"id": "acme", "name": "Acme Corp", "brand_voice": "witty"})

        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.5,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake) as mock_call,
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply", return_value="at://reply/x"),
            patch.object(agent, "_record_reply"),
        ):
            agent.run(run_id="run-1", client_id="acme")

        # Verify "witty" brand voice was passed in the LLM call
        call_args = mock_call.call_args
        user_msg = call_args[0][0] if call_args[0] else call_args.kwargs.get("user_message", "")
        assert "witty" in user_msg

    def test_default_brand_voice_when_client_missing(self, test_db, mock_bluesky_client, mock_llm_response):
        """Falls back to 'professional' when client has no brand_voice."""
        agent = EngagementAgent(
            db=test_db,
            api_key="sk-ant-test",
            bluesky_client=mock_bluesky_client,
            reply_confidence_threshold=0.5,
        )
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        with (
            patch.object(agent, "_fetch_notifications", return_value=[_make_notification()]),
            patch.object(agent, "_filter_already_replied", side_effect=lambda n, c: n),
            patch.object(agent, "call_llm", return_value=fake) as mock_call,
            patch.object(agent, "get_memory_context", return_value=""),
            patch.object(agent, "_post_reply", return_value="at://reply/x"),
            patch.object(agent, "_record_reply"),
        ):
            agent.run(run_id="run-1", client_id="nonexistent")

        call_args = mock_call.call_args
        user_msg = call_args[0][0] if call_args[0] else call_args.kwargs.get("user_message", "")
        assert "professional" in user_msg

    # --- Notification text truncation ---

    def test_notification_text_truncated_in_reply_object(self, test_db, mock_bluesky_client, mock_llm_response):
        """Long notification text is truncated to 200 chars in EngagementReply."""
        long_text = "A" * 500
        agent = EngagementAgent(db=test_db, api_key="sk-ant-test", bluesky_client=mock_bluesky_client)
        fake = mock_llm_response(text=VALID_REPLY_DRAFTS_JSON)

        notifications = [_make_notification(text=long_text)]

        with patch.object(agent, "call_llm", return_value=fake):
            replies = agent._draft_replies(notifications, "professional", "")

        assert len(replies) == 1
        assert len(replies[0].notification_text) == 200
