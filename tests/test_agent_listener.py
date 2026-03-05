"""Tests for the Social Listener Agent."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.listener import ListenerAgent, ListenerResult


@pytest.fixture
def listener_agent(test_db, test_api_key):
    """Create a ListenerAgent with mock platform clients."""
    bluesky = MagicMock()
    twitter = MagicMock()
    reddit = MagicMock()
    agent = ListenerAgent(
        db=test_db,
        api_key=test_api_key,
        bluesky_client=bluesky,
        twitter_client=twitter,
        reddit_client=reddit,
        relevance_threshold=0.6,
        max_conversations=50,
    )
    return agent


@pytest.fixture
def sample_client(test_db):
    """Create a sample client with listening enabled."""
    test_db.create_client(
        {
            "id": "testclient",
            "name": "Test Client",
            "industry": "Technology",
            "target_audience": "Developers",
            "brand_voice": "Technical and friendly",
            "content_pillars": "AI, DevOps, Cloud",
        },
        start_trial=False,
    )
    test_db.execute(
        "UPDATE clients SET listening_enabled=1 WHERE id='testclient'",
        commit=True,
    )
    return "testclient"


@pytest.fixture
def sample_rules(test_db, sample_client):
    """Insert sample listening rules."""
    rules = [
        {
            "id": str(uuid.uuid4()),
            "client_id": sample_client,
            "platform": "bluesky",
            "rule_type": "keyword",
            "value": "AI automation",
            "priority": 1,
            "active": 1,
            "max_results_per_scan": 10,
            "cooldown_minutes": 60,
        },
        {
            "id": str(uuid.uuid4()),
            "client_id": sample_client,
            "platform": "reddit",
            "rule_type": "subreddit",
            "value": "devops",
            "priority": 2,
            "active": 1,
            "max_results_per_scan": 15,
            "cooldown_minutes": 120,
        },
    ]
    for r in rules:
        test_db.execute(
            """INSERT INTO listening_rules
            (id, client_id, platform, rule_type, value, priority, active, max_results_per_scan, cooldown_minutes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["id"],
                r["client_id"],
                r["platform"],
                r["rule_type"],
                r["value"],
                r["priority"],
                r["active"],
                r["max_results_per_scan"],
                r["cooldown_minutes"],
            ),
            commit=True,
        )
    return rules


class TestListenerResult:
    def test_defaults(self):
        r = ListenerResult()
        assert r.rules_scanned == 0
        assert r.conversations_discovered == 0
        assert r.conversations_evaluated == 0
        assert r.conversations_queued == 0
        assert r.conversations_expired == 0
        assert r.errors == []


class TestListenerAgent:
    def test_no_rules_returns_empty(self, listener_agent, test_db, sample_client):
        """Agent returns empty result when no listening rules exist."""
        # Don't insert any rules
        result = listener_agent.run("run-1", client_id="nonexistent")
        assert result.rules_scanned == 0
        assert result.conversations_discovered == 0

    def test_scan_bluesky(self, listener_agent, test_db, sample_client, sample_rules):
        """Agent scans Bluesky for posts matching keyword rules."""
        listener_agent.bluesky.search_posts.return_value = [
            {
                "uri": "at://did:plc:abc/app.bsky.feed.post/123",
                "author_handle": "alice.bsky.social",
                "author_display_name": "Alice",
                "text": "AI automation is changing everything",
                "like_count": 5,
                "repost_count": 2,
                "reply_count": 1,
                "indexed_at": "2024-01-01T00:00:00Z",
                "cid": "cid123",
            },
        ]
        listener_agent.reddit.search_subreddit.return_value = []

        with patch.object(listener_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps(
                    {"evaluations": [{"index": 0, "relevance_score": 0.85, "reasoning": "Highly relevant"}]}
                )
            )
            result = listener_agent.run("run-1", client_id=sample_client)

        assert result.rules_scanned >= 1
        assert result.conversations_discovered >= 1
        listener_agent.bluesky.search_posts.assert_called()

    def test_scan_reddit(self, listener_agent, test_db, sample_client, sample_rules):
        """Agent scans Reddit subreddits."""
        listener_agent.bluesky.search_posts.return_value = []
        listener_agent.reddit.search_subreddit.return_value = [
            {
                "post_id": "t3_abc123",
                "url": "https://reddit.com/r/devops/comments/abc123",
                "author": "bob_dev",
                "title": "Best CI/CD tools for AI workloads",
                "text": "Looking for recommendations on AI pipeline tools",
                "score": 42,
                "num_comments": 15,
                "subreddit": "devops",
                "created_utc": 1700000000,
            },
        ]

        with patch.object(listener_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps({"evaluations": [{"index": 0, "relevance_score": 0.7, "reasoning": "Related topic"}]})
            )
            result = listener_agent.run("run-1", client_id=sample_client)

        assert result.rules_scanned >= 1
        listener_agent.reddit.search_subreddit.assert_called()

    def test_deduplication(self, listener_agent, test_db, sample_client, sample_rules):
        """Duplicate posts are not stored twice."""
        posts = [
            {
                "uri": "at://did:plc:abc/app.bsky.feed.post/dup1",
                "author_handle": "alice.bsky.social",
                "text": "Duplicate post",
                "like_count": 1,
                "repost_count": 0,
                "reply_count": 0,
            },
        ]
        listener_agent.bluesky.search_posts.return_value = posts
        listener_agent.reddit.search_subreddit.return_value = []

        with patch.object(listener_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps({"evaluations": [{"index": 0, "relevance_score": 0.5, "reasoning": "ok"}]})
            )
            listener_agent.run("run-1", client_id=sample_client)

        # Reset last_scanned_at so rules are active again
        test_db.execute("UPDATE listening_rules SET last_scanned_at=NULL", commit=True)

        with patch.object(listener_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text=json.dumps({"evaluations": []}))
            result2 = listener_agent.run("run-2", client_id=sample_client)

        # Second run should discover 0 new conversations (dedup)
        assert result2.conversations_discovered == 0

    def test_cooldown_respected(self, listener_agent, test_db, sample_client, sample_rules):
        """Rules scanned recently (within cooldown) are skipped."""
        # Set last_scanned_at to now for all rules
        now = datetime.now(timezone.utc).isoformat()
        test_db.execute(
            "UPDATE listening_rules SET last_scanned_at=?",
            (now,),
            commit=True,
        )

        result = listener_agent.run("run-1", client_id=sample_client)
        assert result.rules_scanned == 0

    def test_relevance_threshold(self, listener_agent, test_db, sample_client, sample_rules):
        """Only conversations above threshold are queued."""
        listener_agent.bluesky.search_posts.return_value = [
            {
                "uri": f"at://did:plc:abc/app.bsky.feed.post/{i}",
                "author_handle": f"user{i}.bsky.social",
                "text": f"Post about topic {i}",
                "like_count": i,
                "repost_count": 0,
                "reply_count": 0,
            }
            for i in range(3)
        ]
        listener_agent.reddit.search_subreddit.return_value = []

        with patch.object(listener_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps(
                    {
                        "evaluations": [
                            {"index": 0, "relevance_score": 0.3, "reasoning": "Low"},
                            {"index": 1, "relevance_score": 0.65, "reasoning": "Good"},
                            {"index": 2, "relevance_score": 0.9, "reasoning": "Excellent"},
                        ]
                    }
                )
            )
            result = listener_agent.run("run-1", client_id=sample_client)

        assert result.conversations_queued == 2  # 0.65 and 0.9 above 0.6 threshold

    def test_expire_stale(self, listener_agent, test_db, sample_client, sample_rules):
        """Stale conversations are expired."""
        # Insert old conversation
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             status, discovered_at)
            VALUES (?, ?, 'bluesky', 'keyword', 'test', 'ext1', 'uri1', 'user1', 'old post', 'new', ?)""",
            (str(uuid.uuid4()), sample_client, old_time),
            commit=True,
        )

        listener_agent.bluesky.search_posts.return_value = []
        listener_agent.reddit.search_subreddit.return_value = []

        result = listener_agent.run("run-1", client_id=sample_client)
        assert result.conversations_expired >= 1

    def test_scan_error_continues(self, listener_agent, test_db, sample_client, sample_rules):
        """Platform scan errors are caught and reported, not fatal."""
        listener_agent.bluesky.search_posts.side_effect = Exception("API timeout")
        listener_agent.reddit.search_subreddit.return_value = []

        result = listener_agent.run("run-1", client_id=sample_client)
        assert len(result.errors) >= 1
        assert "API timeout" in result.errors[0]

    def test_parse_evaluations_valid(self, listener_agent):
        """_parse_evaluations handles valid JSON."""
        text = json.dumps(
            {
                "evaluations": [
                    {"index": 0, "relevance_score": 0.8, "reasoning": "Good"},
                ]
            }
        )
        result = listener_agent._parse_evaluations(text)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.8

    def test_parse_evaluations_wrapped_json(self, listener_agent):
        """_parse_evaluations handles JSON wrapped in code fences."""
        text = '```json\n{"evaluations": [{"index": 0, "relevance_score": 0.5}]}\n```'
        result = listener_agent._parse_evaluations(text)
        assert len(result) == 1

    def test_parse_evaluations_invalid(self, listener_agent):
        """_parse_evaluations returns empty list for invalid input."""
        assert listener_agent._parse_evaluations("not json") == []
        assert listener_agent._parse_evaluations("") == []

    def test_hashtag_prefix(self, listener_agent):
        """Hashtag rules add # prefix if missing."""
        listener_agent.bluesky.search_posts.return_value = []
        listener_agent._scan_bluesky("trending", "hashtag", 10)
        listener_agent.bluesky.search_posts.assert_called_with(query="#trending", limit=10)

    def test_twitter_scan(self, listener_agent):
        """Twitter search delegates to client correctly."""
        listener_agent.twitter.search_recent.return_value = [
            {
                "tweet_id": "12345",
                "url": "https://x.com/i/status/12345",
                "text": "Great AI tool",
                "author_handle": "alice",
                "author_display_name": "Alice",
                "like_count": 10,
                "retweet_count": 5,
                "reply_count": 3,
                "created_at": "2024-01-01T00:00:00Z",
                "conversation_id": "12345",
            }
        ]
        posts = listener_agent._scan_twitter("AI tools", "keyword", 25)
        assert len(posts) == 1
        assert posts[0]["platform"] == "twitter"
        assert posts[0]["external_id"] == "12345"

    def test_no_platform_client_returns_empty(self, test_db, test_api_key):
        """Agent without platform clients returns empty scan results."""
        agent = ListenerAgent(
            db=test_db,
            api_key=test_api_key,
            bluesky_client=None,
            twitter_client=None,
            reddit_client=None,
        )
        result = agent._scan_for_rule(
            {"platform": "bluesky", "rule_type": "keyword", "value": "test", "max_results_per_scan": 10},
            "default",
        )
        assert result == []
