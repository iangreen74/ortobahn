"""Tests for CommunityAgent — account tracking, threading, insights."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.community import _AUTO_DISCOVER_THRESHOLD, CommunityAgent


@pytest.fixture
def community_agent(test_db, test_api_key):
    """Create a CommunityAgent with mock platform clients."""
    bluesky = MagicMock()
    twitter = MagicMock()
    reddit = MagicMock()
    linkedin = MagicMock()
    agent = CommunityAgent(
        db=test_db,
        api_key=test_api_key,
        bluesky_client=bluesky,
        twitter_client=twitter,
        reddit_client=reddit,
        linkedin_client=linkedin,
    )
    return agent


@pytest.fixture
def community_client(test_db):
    """Create a client with listening enabled."""
    test_db.create_client(
        {
            "id": "community_test",
            "name": "Community Test",
            "industry": "Technology",
            "target_audience": "Developers",
            "brand_voice": "Technical",
            "content_pillars": "AI, automation",
        },
        start_trial=False,
    )
    test_db.execute(
        "UPDATE clients SET listening_enabled=1, proactive_engagement_enabled=1 WHERE id='community_test'",
        commit=True,
    )
    return "community_test"


@pytest.fixture
def discovered_conversations(test_db, community_client):
    """Insert discovered conversations for community analysis."""
    convs = []
    for i in range(5):
        conv_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
            (
                conv_id,
                community_client,
                "bluesky",
                "keyword",
                "AI automation",
                f"at://did:plc:abc/app.bsky.feed.post/{i}",
                f"at://did:plc:abc/app.bsky.feed.post/{i}",
                f"user{i % 2}.bsky.social",  # Only 2 unique authors
                f"Post about AI automation topic {i}",
                10 + i * 5,
                0.8,
                datetime.now(timezone.utc).isoformat(),
            ),
            commit=True,
        )
        convs.append(conv_id)
    return convs


class TestAutoDiscoverAccounts:
    def test_no_conversations_no_discovery(self, community_agent, community_client):
        """No conversations means no accounts discovered."""
        count = community_agent._auto_discover_accounts(community_client)
        assert count == 0

    def test_discovers_frequent_authors(self, community_agent, test_db, community_client):
        """Authors appearing >= threshold times are auto-discovered."""
        # Insert enough conversations from one author
        for i in range(_AUTO_DISCOVER_THRESHOLD + 1):
            test_db.execute(
                """INSERT INTO discovered_conversations
                (id, client_id, platform, source_type, source_query,
                 external_id, external_uri, author_handle, text_content,
                 engagement_score, relevance_score, status)
                VALUES (?, ?, 'bluesky', 'keyword', 'test', ?, ?, 'frequent.bsky.social',
                        'test post', 10, 0.8, 'queued')""",
                (str(uuid.uuid4()), community_client, f"ext_{i}", f"uri_{i}"),
                commit=True,
            )

        count = community_agent._auto_discover_accounts(community_client)
        assert count == 1

        # Verify the account was created
        acct = test_db.fetchone(
            "SELECT * FROM tracked_accounts WHERE client_id=? AND account_handle='frequent.bsky.social'",
            (community_client,),
        )
        assert acct is not None
        assert acct["account_type"] == "prospect"
        assert acct["auto_discovered"] == 1

    def test_no_duplicate_discovery(self, community_agent, test_db, community_client):
        """Already-tracked accounts aren't re-discovered."""
        # Pre-insert a tracked account
        test_db.execute(
            """INSERT INTO tracked_accounts
            (id, client_id, platform, account_handle, account_type, active)
            VALUES (?, ?, 'bluesky', 'existing.bsky.social', 'influencer', 1)""",
            (str(uuid.uuid4()), community_client),
            commit=True,
        )

        # Insert conversations from the same author
        for i in range(_AUTO_DISCOVER_THRESHOLD + 1):
            test_db.execute(
                """INSERT INTO discovered_conversations
                (id, client_id, platform, source_type, source_query,
                 external_id, external_uri, author_handle, text_content,
                 engagement_score, relevance_score, status)
                VALUES (?, ?, 'bluesky', 'keyword', 'test', ?, ?, 'existing.bsky.social',
                        'test post', 10, 0.8, 'queued')""",
                (str(uuid.uuid4()), community_client, f"ext_{i}", f"uri_{i}"),
                commit=True,
            )

        count = community_agent._auto_discover_accounts(community_client)
        assert count == 0


class TestUpdateAccountActivity:
    def test_updates_tracked_accounts(self, community_agent, test_db, community_client):
        """Activity snapshot is created for tracked accounts."""
        acct_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO tracked_accounts
            (id, client_id, platform, account_handle, account_type, active)
            VALUES (?, ?, 'bluesky', 'tracked.bsky.social', 'influencer', 1)""",
            (acct_id, community_client),
            commit=True,
        )

        count = community_agent._update_account_activity(community_client)
        assert count == 1

        # Check activity record was created
        activity = test_db.fetchone("SELECT * FROM account_activity WHERE tracked_account_id=?", (acct_id,))
        assert activity is not None
        assert activity["post_count_7d"] == 0  # No conversations from this author

    def test_no_accounts_returns_zero(self, community_agent, community_client):
        """No tracked accounts means zero updates."""
        count = community_agent._update_account_activity(community_client)
        assert count == 0

    def test_inactive_accounts_skipped(self, community_agent, test_db, community_client):
        """Inactive accounts are not updated."""
        test_db.execute(
            """INSERT INTO tracked_accounts
            (id, client_id, platform, account_handle, account_type, active)
            VALUES (?, ?, 'bluesky', 'inactive.bsky.social', 'influencer', 0)""",
            (str(uuid.uuid4()), community_client),
            commit=True,
        )

        count = community_agent._update_account_activity(community_client)
        assert count == 0


class TestThreadConversations:
    def test_threads_conversations_with_parent(self, community_agent, test_db, community_client):
        """Conversations with parent_external_id are threaded together."""
        # Parent conversation
        parent_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status)
            VALUES (?, ?, 'bluesky', 'keyword', 'test', 'parent_ext_1', 'uri_1',
                    'author1.bsky.social', 'Parent post', 20, 0.9, 'queued')""",
            (parent_id, community_client),
            commit=True,
        )

        # Child conversation referencing parent
        child_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status, parent_external_id)
            VALUES (?, ?, 'bluesky', 'keyword', 'test', 'child_ext_1', 'uri_2',
                    'author2.bsky.social', 'Reply to parent', 10, 0.8, 'queued', 'parent_ext_1')""",
            (child_id, community_client),
            commit=True,
        )

        created, updated = community_agent._thread_conversations(community_client)
        assert created == 1
        assert updated == 0

        # Both should have the same thread_id
        parent = test_db.fetchone("SELECT thread_id FROM discovered_conversations WHERE id=?", (parent_id,))
        child = test_db.fetchone("SELECT thread_id FROM discovered_conversations WHERE id=?", (child_id,))
        assert parent["thread_id"] != ""
        assert parent["thread_id"] == child["thread_id"]

    def test_no_parent_no_threading(self, community_agent, test_db, community_client):
        """Conversations without parent_external_id are not threaded."""
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status)
            VALUES (?, ?, 'bluesky', 'keyword', 'test', 'standalone_1', 'uri_1',
                    'author1.bsky.social', 'Standalone post', 20, 0.9, 'queued')""",
            (str(uuid.uuid4()), community_client),
            commit=True,
        )

        created, updated = community_agent._thread_conversations(community_client)
        assert created == 0
        assert updated == 0

    def test_existing_thread_gets_updated(self, community_agent, test_db, community_client):
        """Adding a reply to an existing thread updates the thread stats."""
        # Create thread manually
        thread_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO conversation_threads
            (id, client_id, platform, root_conversation_id, thread_depth, total_engagement, status)
            VALUES (?, ?, 'bluesky', 'root_ext_1', 1, 20, 'active')""",
            (thread_id, community_client),
            commit=True,
        )

        # Parent with thread assigned
        parent_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status, thread_id)
            VALUES (?, ?, 'bluesky', 'keyword', 'test', 'root_ext_1', 'uri_1',
                    'author1.bsky.social', 'Root post', 20, 0.9, 'queued', ?)""",
            (parent_id, community_client, thread_id),
            commit=True,
        )

        # New reply referencing root
        child_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO discovered_conversations
            (id, client_id, platform, source_type, source_query,
             external_id, external_uri, author_handle, text_content,
             engagement_score, relevance_score, status, parent_external_id)
            VALUES (?, ?, 'bluesky', 'keyword', 'test', 'child_ext_2', 'uri_2',
                    'author2.bsky.social', 'Another reply', 15, 0.8, 'queued', 'root_ext_1')""",
            (child_id, community_client),
            commit=True,
        )

        created, updated = community_agent._thread_conversations(community_client)
        assert created == 0
        assert updated == 1


class TestAnalyzeAndPublish:
    def test_publishes_engagement_pattern(self, community_agent, test_db, community_client):
        """Engagement patterns are published when enough outcome data exists."""
        # Insert engagement outcomes
        now = datetime.now(timezone.utc).isoformat()
        for i in range(5):
            test_db.execute(
                """INSERT INTO engagement_outcomes
                (id, reply_id, client_id, platform, outcome_score, created_at)
                VALUES (?, ?, ?, 'bluesky', ?, ?)""",
                (str(uuid.uuid4()), f"rep_{i}", community_client, 0.5 + i * 0.1, now),
                commit=True,
            )

        count = community_agent._analyze_and_publish("run-1", community_client)
        assert count >= 1

        # Verify insight was published
        insight = test_db.fetchone(
            "SELECT * FROM shared_insights WHERE source_agent='community' AND insight_type='ENGAGEMENT_PATTERN'",
        )
        assert insight is not None

    def test_publishes_competitor_move(self, community_agent, test_db, community_client):
        """Competitor activity is published as insight."""
        acct_id = str(uuid.uuid4())
        test_db.execute(
            """INSERT INTO tracked_accounts
            (id, client_id, platform, account_handle, account_type, active)
            VALUES (?, ?, 'twitter', 'competitor.x', 'competitor', 1)""",
            (acct_id, community_client),
            commit=True,
        )
        test_db.execute(
            """INSERT INTO account_activity
            (id, tracked_account_id, client_id, post_count_7d, avg_engagement_7d, recorded_at)
            VALUES (?, ?, ?, 10, 25.5, ?)""",
            (str(uuid.uuid4()), acct_id, community_client, datetime.now(timezone.utc).isoformat()),
            commit=True,
        )

        count = community_agent._analyze_and_publish("run-1", community_client)
        assert count >= 1

        insight = test_db.fetchone(
            "SELECT * FROM shared_insights WHERE source_agent='community' AND insight_type='COMPETITOR_MOVE'",
        )
        assert insight is not None
        assert "competitor.x" in insight["content"]

    def test_publishes_community_trends(self, community_agent, test_db, community_client):
        """Community trends are extracted via LLM and published."""
        # Insert enough conversations
        now = datetime.now(timezone.utc).isoformat()
        for i in range(10):
            test_db.execute(
                """INSERT INTO discovered_conversations
                (id, client_id, platform, source_type, source_query,
                 external_id, external_uri, author_handle, text_content,
                 engagement_score, relevance_score, status, discovered_at)
                VALUES (?, ?, 'bluesky', 'keyword', 'AI', ?, ?, ?, ?, ?, 0.8, 'queued', ?)""",
                (
                    str(uuid.uuid4()),
                    community_client,
                    f"ext_{i}",
                    f"uri_{i}",
                    f"author{i}.bsky.social",
                    f"Post about AI topic {i} with lots of discussion",
                    10 + i * 3,
                    now,
                ),
                commit=True,
            )

        with patch.object(community_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps(
                    {
                        "trends": [
                            {"topic": "AI Agents", "description": "Growing interest in autonomous AI agents"},
                            {"topic": "LLM Fine-tuning", "description": "More teams exploring fine-tuning"},
                        ]
                    }
                )
            )
            count = community_agent._analyze_and_publish("run-1", community_client)

        assert count >= 2
        trends = test_db.fetchall(
            "SELECT * FROM shared_insights WHERE source_agent='community' AND insight_type='COMMUNITY_TREND'",
        )
        assert len(trends) >= 2

    def test_no_data_no_insights(self, community_agent, community_client):
        """No data means no insights published."""
        count = community_agent._analyze_and_publish("run-1", community_client)
        assert count == 0


class TestCommunityRun:
    def test_full_run(self, community_agent, test_db, community_client, discovered_conversations):
        """Full community run completes without errors."""
        with patch.object(community_agent, "call_llm") as mock_llm:
            mock_llm.return_value = MagicMock(
                text=json.dumps({"trends": [{"topic": "Test", "description": "Test trend"}]})
            )
            result = community_agent.run("run-1", client_id=community_client)

        assert result.errors == []
        # user0 and user1 each appear 3 and 2 times respectively
        # user0 appears 3 times (indices 0,2,4), meets threshold
        assert result.accounts_discovered >= 1

    def test_run_handles_errors(self, community_agent, test_db, community_client):
        """Run handles errors gracefully."""
        with patch.object(community_agent, "_auto_discover_accounts", side_effect=RuntimeError("db error")):
            result = community_agent.run("run-1", client_id=community_client)

        assert len(result.errors) == 1
        assert "db error" in result.errors[0]


class TestSharedInsightTypes:
    def test_new_insight_types_exist(self):
        """New insight types are defined in shared_insights."""
        from ortobahn.shared_insights import (
            COMMUNITY_TREND,
            COMPETITOR_MOVE,
            ENGAGEMENT_PATTERN,
        )

        assert COMMUNITY_TREND == "COMMUNITY_TREND"
        assert COMPETITOR_MOVE == "COMPETITOR_MOVE"
        assert ENGAGEMENT_PATTERN == "ENGAGEMENT_PATTERN"

    def test_agent_relevance_includes_community(self):
        """AGENT_RELEVANCE includes the community agent."""
        from ortobahn.shared_insights import AGENT_RELEVANCE

        assert "community" in AGENT_RELEVANCE
        assert "engagement" in AGENT_RELEVANCE
        assert "COMMUNITY_TREND" in AGENT_RELEVANCE["community"]
        assert "COMPETITOR_MOVE" in AGENT_RELEVANCE["community"]

    def test_all_insight_types_includes_new(self):
        """ALL_INSIGHT_TYPES includes new community types."""
        from ortobahn.shared_insights import ALL_INSIGHT_TYPES

        assert "COMMUNITY_TREND" in ALL_INSIGHT_TYPES
        assert "COMPETITOR_MOVE" in ALL_INSIGHT_TYPES
        assert "ENGAGEMENT_PATTERN" in ALL_INSIGHT_TYPES
