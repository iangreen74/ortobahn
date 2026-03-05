"""Tests for proactive engagement and engagement outcomes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.engagement import EngagementAgent
from ortobahn.engagement_outcomes import EngagementOutcomeTracker


@pytest.fixture
def engagement_agent(test_db, test_api_key):
    """Create an EngagementAgent with mock platform clients."""
    bluesky = MagicMock()
    twitter = MagicMock()
    reddit = MagicMock()
    linkedin = MagicMock()
    agent = EngagementAgent(
        db=test_db,
        api_key=test_api_key,
        bluesky_client=bluesky,
        twitter_client=twitter,
        linkedin_client=linkedin,
        reddit_client=reddit,
        max_replies_per_cycle=3,
        reply_confidence_threshold=0.75,
    )
    return agent


@pytest.fixture
def proactive_client(test_db):
    """Create a client with proactive engagement enabled."""
    test_db.create_client(
        {
            "id": "proactive_test",
            "name": "Proactive Test",
            "industry": "Technology",
            "target_audience": "Developers",
            "brand_voice": "Technical and direct",
            "content_pillars": "AI, automation",
        },
        start_trial=False,
    )
    test_db.execute(
        "UPDATE clients SET proactive_engagement_enabled=1, listening_enabled=1 WHERE id='proactive_test'",
        commit=True,
    )
    return "proactive_test"


@pytest.fixture
def queued_conversations(test_db, proactive_client):
    """Insert queued conversations for proactive engagement."""
    convs = []
    for i in range(3):
        conv_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')""",
            (
                conv_id,
                proactive_client,
                "bluesky",
                "keyword",
                "AI automation",
                f"at://did:plc:abc/app.bsky.feed.post/{i}",
                f"at://did:plc:abc/app.bsky.feed.post/{i}",
                f"user{i}.bsky.social",
                f"Post about AI automation topic {i}",
                10 + i * 5,
                0.8 + i * 0.05,
            ),
            commit=True,
        )
        convs.append(conv_id)
    return convs


class TestProactiveEngagement:
    def test_proactive_disabled_skips(self, engagement_agent, test_db):
        """Proactive engagement is skipped when disabled."""
        test_db.create_client(
            {"id": "no_proactive", "name": "No Proactive"},
            start_trial=False,
        )
        # Mock bluesky notifications to return empty
        engagement_agent.bluesky.login.return_value = None
        mock_response = MagicMock()
        mock_response.notifications = []
        engagement_agent.bluesky._call_with_retry.return_value = mock_response

        result = engagement_agent.run("run-1", client_id="no_proactive")
        assert result.proactive_evaluated == 0
        assert result.proactive_posted == 0

    def test_proactive_drafts_and_posts(self, engagement_agent, test_db, proactive_client, queued_conversations):
        """Proactive engagement drafts and posts replies."""
        # Mock reactive to return nothing
        engagement_agent.bluesky.login.return_value = None
        mock_response = MagicMock()
        mock_response.notifications = []
        engagement_agent.bluesky._call_with_retry.return_value = mock_response

        # Mock proactive LLM call
        with patch.object(engagement_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps(
                    {
                        "reply_text": "Great point about AI automation!",
                        "confidence": 0.9,
                        "reasoning": "Highly relevant to our expertise",
                    }
                )
            )
            # Mock the multi-platform post
            with patch.object(engagement_agent, "_post_reply_multiplatform", return_value="at://reply/uri"):
                result = engagement_agent.run("run-1", client_id=proactive_client)

        assert result.proactive_evaluated >= 1
        assert result.proactive_posted >= 1

        # Check conversation status updated to 'replied'
        conv = test_db.fetchone(
            "SELECT status FROM discovered_conversations WHERE id=?",
            (queued_conversations[0],),
        )
        assert conv["status"] == "replied"

    def test_proactive_low_confidence_skipped(self, engagement_agent, test_db, proactive_client, queued_conversations):
        """Low-confidence proactive replies are skipped."""
        engagement_agent.bluesky.login.return_value = None
        mock_response = MagicMock()
        mock_response.notifications = []
        engagement_agent.bluesky._call_with_retry.return_value = mock_response

        with patch.object(engagement_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps(
                    {
                        "reply_text": "Hmm interesting",
                        "confidence": 0.3,
                        "reasoning": "Not very relevant",
                    }
                )
            )
            result = engagement_agent.run("run-1", client_id=proactive_client)

        assert result.proactive_posted == 0
        # Conversations should be marked as 'evaluated' (not queued anymore)
        conv = test_db.fetchone(
            "SELECT status FROM discovered_conversations WHERE id=?",
            (queued_conversations[0],),
        )
        assert conv["status"] == "evaluated"

    def test_rate_limit_check(self, engagement_agent, test_db, proactive_client):
        """Rate limiting works correctly."""
        # First call should be OK (creates rows)
        assert engagement_agent._check_rate_limit(proactive_client, "bluesky") is True

        # Record some replies
        for _ in range(3):
            engagement_agent._record_rate_limit(proactive_client, "bluesky")

        # Should now be rate limited (hourly limit is 3)
        assert engagement_agent._check_rate_limit(proactive_client, "bluesky") is False

    def test_author_dedup(self, engagement_agent, test_db, proactive_client):
        """Don't reply to same author twice in 24h."""
        # Record a recent proactive reply
        test_db.execute(
            """INSERT INTO engagement_replies
            (id, run_id, client_id, notification_uri, notification_text,
             reply_text, reply_uri, confidence, platform, status, engagement_type, created_at)
            VALUES (?, 'run-0', ?, 'at://user1.bsky.social/post', 'text', 'reply', 'uri', 0.9, 'bluesky', 'posted', 'proactive', ?)""",
            (str(uuid.uuid4())[:8], proactive_client, datetime.now(timezone.utc).isoformat()),
            commit=True,
        )

        assert engagement_agent._replied_to_author_recently(proactive_client, "user1.bsky.social") is True
        assert engagement_agent._replied_to_author_recently(proactive_client, "newuser.bsky.social") is False

    def test_multiplatform_dispatch_twitter(self, engagement_agent):
        """Multi-platform dispatch works for Twitter."""
        engagement_agent.twitter.reply_to_tweet.return_value = ("https://x.com/i/status/999", "999")
        result = engagement_agent._post_reply_multiplatform("twitter", "Great point!", "12345", "")
        assert result == "https://x.com/i/status/999"
        engagement_agent.twitter.reply_to_tweet.assert_called_with("12345", "Great point!")

    def test_multiplatform_dispatch_reddit(self, engagement_agent):
        """Multi-platform dispatch works for Reddit."""
        engagement_agent.reddit.reply_to_post.return_value = ("https://reddit.com/r/test/comment/abc", "abc")
        result = engagement_agent._post_reply_multiplatform("reddit", "Insightful!", "t3_xyz", "")
        assert result == "https://reddit.com/r/test/comment/abc"

    def test_multiplatform_dispatch_linkedin(self, engagement_agent):
        """Multi-platform dispatch works for LinkedIn."""
        engagement_agent.linkedin.comment_on_post.return_value = "urn:li:comment:123"
        result = engagement_agent._post_reply_multiplatform("linkedin", "Well said!", "urn:li:share:456", "")
        assert result == "urn:li:comment:123"

    def test_multiplatform_no_client(self, test_db, test_api_key):
        """Multi-platform returns None when no client available."""
        agent = EngagementAgent(
            db=test_db,
            api_key=test_api_key,
            bluesky_client=None,
            twitter_client=None,
        )
        result = agent._post_reply_multiplatform("twitter", "Hello", "123", "")
        assert result is None

    def test_dry_run_proactive(self, engagement_agent, test_db, proactive_client, queued_conversations):
        """Dry run counts proactive replies but doesn't post."""
        engagement_agent.bluesky.login.return_value = None
        mock_response = MagicMock()
        mock_response.notifications = []
        engagement_agent.bluesky._call_with_retry.return_value = mock_response

        with patch.object(engagement_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps(
                    {
                        "reply_text": "Interesting take!",
                        "confidence": 0.85,
                        "reasoning": "Relevant",
                    }
                )
            )
            result = engagement_agent.run("run-1", client_id=proactive_client, dry_run=True)

        assert result.proactive_posted >= 1
        # Conversation should NOT be marked as replied (dry run)
        conv = test_db.fetchone(
            "SELECT status FROM discovered_conversations WHERE id=?",
            (queued_conversations[0],),
        )
        assert conv["status"] == "queued"


class TestEngagementOutcomeTracker:
    def test_no_replies_returns_zero(self, test_db):
        """No recent replies means 0 outcomes tracked."""
        tracker = EngagementOutcomeTracker(test_db)
        assert tracker.check_recent_replies("nonexistent") == 0

    def test_check_reply_creates_outcome(self, test_db):
        """Checking a posted reply creates an outcome record."""
        test_db.create_client({"id": "outcome_test", "name": "Outcome Test"}, start_trial=False)
        test_db.execute(
            """INSERT INTO engagement_replies
            (id, run_id, client_id, notification_uri, notification_text,
             reply_text, reply_uri, confidence, platform, status, created_at)
            VALUES ('rep1', 'run-1', 'outcome_test', 'at://notif', 'text',
                    'reply', 'at://reply/uri', 0.9, 'bluesky', 'posted', ?)""",
            (datetime.now(timezone.utc).isoformat(),),
            commit=True,
        )

        mock_bluesky = MagicMock()
        mock_bluesky.get_post_thread.return_value = [
            {"uri": "at://reply/uri", "like_count": 3, "reply_count": 1},
        ]
        tracker = EngagementOutcomeTracker(test_db, bluesky_client=mock_bluesky)
        count = tracker.check_recent_replies("outcome_test")
        assert count == 1

        # Check outcome was recorded
        outcome = test_db.fetchone("SELECT * FROM engagement_outcomes WHERE reply_id='rep1'")
        assert outcome is not None
        assert outcome["like_count"] == 3
        assert outcome["reply_count"] == 1
        assert outcome["outcome_score"] > 0

    def test_no_duplicate_outcomes(self, test_db):
        """Don't create duplicate outcomes for already-checked replies."""
        test_db.create_client({"id": "dedup_test", "name": "Dedup Test"}, start_trial=False)
        test_db.execute(
            """INSERT INTO engagement_replies
            (id, run_id, client_id, notification_uri, notification_text,
             reply_text, reply_uri, confidence, platform, status, created_at)
            VALUES ('rep2', 'run-1', 'dedup_test', 'at://notif', 'text',
                    'reply', 'at://reply/uri2', 0.9, 'bluesky', 'posted', ?)""",
            (datetime.now(timezone.utc).isoformat(),),
            commit=True,
        )
        # Already has an outcome
        test_db.execute(
            """INSERT INTO engagement_outcomes
            (id, reply_id, client_id, platform, outcome_score)
            VALUES (?, 'rep2', 'dedup_test', 'bluesky', 0.5)""",
            (str(uuid.uuid4()),),
            commit=True,
        )

        tracker = EngagementOutcomeTracker(test_db)
        count = tracker.check_recent_replies("dedup_test")
        assert count == 0

    def test_effectiveness_report(self, test_db):
        """Effectiveness report aggregates correctly."""
        test_db.create_client({"id": "report_test", "name": "Report Test"}, start_trial=False)
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            test_db.execute(
                """INSERT INTO engagement_outcomes
                (id, reply_id, client_id, platform, like_count, reply_count,
                 target_responded, outcome_score, created_at)
                VALUES (?, ?, 'report_test', 'bluesky', ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), f"rep{i}", i * 2, i, 1 if i > 0 else 0, 0.3 * (i + 1), now),
                commit=True,
            )

        tracker = EngagementOutcomeTracker(test_db)
        report = tracker.get_effectiveness_report("report_test")
        assert report["total_replies"] == 3
        assert report["total_likes"] == 6  # 0 + 2 + 4
        assert report["avg_outcome_score"] > 0
        assert report["best_platform"] == "bluesky"
        assert 0 < report["target_response_rate"] <= 1.0

    def test_empty_effectiveness_report(self, test_db):
        """Empty report returns zeros."""
        tracker = EngagementOutcomeTracker(test_db)
        report = tracker.get_effectiveness_report("nonexistent")
        assert report["total_replies"] == 0
        assert report["avg_outcome_score"] == 0.0
