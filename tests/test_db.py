"""Tests for database operations."""

from datetime import datetime, timedelta, timezone


class TestDatabaseTables:
    def test_tables_created(self, test_db):
        rows = test_db.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row["name"] for row in rows]
        assert "strategies" in tables
        assert "posts" in tables
        assert "metrics" in tables
        assert "agent_logs" in tables
        assert "pipeline_runs" in tables
        assert "clients" in tables

    def test_default_client_seeded(self, test_db):
        client = test_db.get_client("default")
        assert client is not None
        assert client["name"] == "Ortobahn"


class TestStrategies:
    def test_save_and_get_strategy(self, test_db):
        valid_until = (datetime.utcnow() + timedelta(days=7)).isoformat()
        test_db.save_strategy(
            {
                "themes": ["AI", "tech"],
                "tone": "bold",
                "goals": ["grow"],
                "content_guidelines": "be real",
                "posting_frequency": "daily",
                "valid_until": valid_until,
            },
            run_id="run-1",
        )

        result = test_db.get_active_strategy()
        assert result is not None
        assert result["themes"] == ["AI", "tech"]
        assert result["tone"] == "bold"

    def test_no_active_strategy(self, test_db):
        assert test_db.get_active_strategy() is None

    def test_expired_strategy_not_returned(self, test_db):
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        test_db.save_strategy(
            {
                "themes": ["old"],
                "tone": "stale",
                "goals": ["none"],
                "content_guidelines": "expired",
                "posting_frequency": "never",
                "valid_until": past,
            },
            run_id="run-old",
        )

        assert test_db.get_active_strategy() is None


class TestPosts:
    def test_save_and_publish_post(self, test_db):
        pid = test_db.save_post(text="Hello", run_id="run-1", confidence=0.9, status="draft")
        test_db.update_post_published(pid, "at://test/post/1", "bafy123")

        posts = test_db.get_recent_published_posts(days=7)
        assert len(posts) == 1
        assert posts[0]["text"] == "Hello"
        assert posts[0]["bluesky_uri"] == "at://test/post/1"

    def test_failed_post(self, test_db):
        pid = test_db.save_post(text="Fail", run_id="run-1", status="draft")
        test_db.update_post_failed(pid, "Network error")

        # Failed posts should not appear in published posts
        posts = test_db.get_recent_published_posts(days=7)
        assert len(posts) == 0


class TestMetrics:
    def test_save_metrics(self, test_db):
        pid = test_db.save_post(text="Test", run_id="run-1", status="published")
        test_db.update_post_published(pid, "at://test", "bafy")
        test_db.save_metrics(pid, like_count=10, repost_count=3, reply_count=2)

        posts = test_db.get_recent_posts_with_metrics(limit=10)
        assert len(posts) == 1
        assert posts[0]["like_count"] == 10

    def test_save_metrics_upserts(self, test_db):
        """Saving metrics for the same post should update, not accumulate."""
        pid = test_db.save_post(text="Upsert test", run_id="run-1", status="published")
        test_db.update_post_published(pid, "at://test", "bafy")
        test_db.save_metrics(pid, like_count=5, repost_count=1)
        test_db.save_metrics(pid, like_count=8, repost_count=3)

        # Should have exactly one metrics row, with the latest values
        rows = test_db.fetchall("SELECT * FROM metrics WHERE post_id=?", (pid,))
        assert len(rows) == 1
        assert rows[0]["like_count"] == 8
        assert rows[0]["repost_count"] == 3

    def test_dashboard_no_duplicate_rows(self, test_db):
        """Dashboard query should return one row per post even with multiple metric saves."""
        pid = test_db.save_post(text="No dupes", run_id="run-1", status="published")
        test_db.update_post_published(pid, "at://test", "bafy")
        test_db.save_metrics(pid, like_count=5)
        posts = test_db.get_recent_posts_with_metrics(limit=10)
        assert len(posts) == 1
        assert posts[0]["like_count"] == 5

    def test_analytics_report_uses_latest_metrics(self, test_db):
        """Analytics report should use latest metrics, not accumulate."""
        pid = test_db.save_post(text="Analytics test", run_id="r1", status="published")
        test_db.update_post_published(pid, "at://1", "bafy1")
        test_db.save_metrics(pid, like_count=5, repost_count=2, reply_count=1)

        report = test_db.build_analytics_report()
        assert report.total_likes == 5

        # Update metrics (upsert)
        test_db.save_metrics(pid, like_count=10, repost_count=4, reply_count=2)

        report = test_db.build_analytics_report()
        assert report.total_likes == 10  # Should be 10, not 15


class TestPipelineRuns:
    def test_start_and_complete_run(self, test_db):
        test_db.start_pipeline_run("run-1", mode="single")
        test_db.complete_pipeline_run("run-1", posts_published=3, total_input_tokens=500)

        runs = test_db.get_recent_runs(limit=1)
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["posts_published"] == 3

    def test_fail_run(self, test_db):
        test_db.start_pipeline_run("run-2", mode="single")
        test_db.fail_pipeline_run("run-2", ["Something broke"])

        runs = test_db.get_recent_runs(limit=1)
        assert runs[0]["status"] == "failed"


class TestAgentLogs:
    def test_log_and_retrieve(self, test_db):
        test_db.log_agent(
            run_id="run-1",
            agent_name="ceo",
            input_summary="test input",
            output_summary="test output",
            input_tokens=100,
            output_tokens=200,
        )
        logs = test_db.get_recent_agent_logs(limit=5)
        assert len(logs) == 1
        assert logs[0]["agent_name"] == "ceo"


class TestAnalyticsReport:
    def test_empty_report(self, test_db):
        report = test_db.build_analytics_report()
        assert report.total_posts == 0

    def test_report_with_posts(self, test_db):
        pid = test_db.save_post(text="Post 1", run_id="r1", status="published")
        test_db.update_post_published(pid, "at://1", "bafy1")
        test_db.save_metrics(pid, like_count=5, repost_count=2, reply_count=1)

        report = test_db.build_analytics_report()
        assert report.total_posts == 1
        assert report.total_likes == 5
        assert report.avg_engagement_per_post == 8.0


class TestClients:
    def test_create_and_get_client(self, test_db):
        cid = test_db.create_client({"id": "vs", "name": "Vaultscaler", "industry": "AI"})
        assert cid == "vs"
        client = test_db.get_client("vs")
        assert client is not None
        assert client["name"] == "Vaultscaler"
        assert client["industry"] == "AI"

    def test_get_nonexistent_client(self, test_db):
        assert test_db.get_client("nope") is None

    def test_get_all_clients(self, test_db):
        test_db.create_client({"id": "c1", "name": "Client A"})
        test_db.create_client({"id": "c2", "name": "Client B"})
        clients = test_db.get_all_clients()
        names = [c["name"] for c in clients]
        assert "Client A" in names
        assert "Client B" in names
        # Default client should also be there
        assert "Ortobahn" in names

    def test_update_client(self, test_db):
        test_db.create_client({"id": "upd", "name": "Old Name"})
        test_db.update_client("upd", {"name": "New Name", "industry": "Fintech"})
        client = test_db.get_client("upd")
        assert client["name"] == "New Name"
        assert client["industry"] == "Fintech"

    def test_auto_generated_id(self, test_db):
        cid = test_db.create_client({"name": "Auto ID Corp"})
        assert len(cid) > 0
        client = test_db.get_client(cid)
        assert client["name"] == "Auto ID Corp"


class TestClientScopedStrategies:
    def test_strategy_scoped_by_client(self, test_db):
        valid_until = (datetime.utcnow() + timedelta(days=7)).isoformat()
        test_db.save_strategy(
            {
                "themes": ["AI"],
                "tone": "bold",
                "goals": ["grow"],
                "content_guidelines": "ok",
                "posting_frequency": "daily",
                "valid_until": valid_until,
            },
            run_id="r1",
            client_id="default",
        )
        test_db.create_client({"id": "other", "name": "Other Corp"})
        test_db.save_strategy(
            {
                "themes": ["Finance"],
                "tone": "formal",
                "goals": ["profit"],
                "content_guidelines": "conservative",
                "posting_frequency": "weekly",
                "valid_until": valid_until,
            },
            run_id="r2",
            client_id="other",
        )

        default_strat = test_db.get_active_strategy(client_id="default")
        assert default_strat["themes"] == ["AI"]

        other_strat = test_db.get_active_strategy(client_id="other")
        assert other_strat["themes"] == ["Finance"]


class TestPostWithPlatform:
    def test_save_post_with_platform(self, test_db):
        pid = test_db.save_post(
            text="Twitter post",
            run_id="r1",
            platform="twitter",
            content_type="social_post",
            client_id="default",
        )
        post = test_db.get_post(pid)
        assert post["platform"] == "twitter"
        assert post["content_type"] == "social_post"
        assert post["client_id"] == "default"


class TestContentApproval:
    def test_get_drafts(self, test_db):
        test_db.save_post(text="Draft 1", run_id="r1", status="draft", platform="twitter")
        test_db.save_post(text="Draft 2", run_id="r1", status="draft", platform="linkedin")
        test_db.save_post(text="Published", run_id="r1", status="published")

        drafts = test_db.get_drafts_for_review()
        assert len(drafts) == 2

        twitter_drafts = test_db.get_drafts_for_review(platform="twitter")
        assert len(twitter_drafts) == 1
        assert twitter_drafts[0]["text"] == "Draft 1"

    def test_approve_post(self, test_db):
        pid = test_db.save_post(text="Approve me", run_id="r1", status="draft")
        test_db.approve_post(pid)
        post = test_db.get_post(pid)
        assert post["status"] == "approved"

    def test_reject_post(self, test_db):
        pid = test_db.save_post(text="Reject me", run_id="r1", status="draft")
        test_db.reject_post(pid)
        post = test_db.get_post(pid)
        assert post["status"] == "rejected"

    def test_edit_draft_text(self, test_db):
        pid = test_db.save_post(text="Original", run_id="r1", status="draft")
        test_db.update_post_text(pid, "Edited")
        post = test_db.get_post(pid)
        assert post["text"] == "Edited"

    def test_cannot_edit_published_post(self, test_db):
        pid = test_db.save_post(text="Published", run_id="r1", status="published")
        test_db.update_post_text(pid, "Should not change")
        post = test_db.get_post(pid)
        assert post["text"] == "Published"

    def test_get_all_posts_filtered(self, test_db):
        test_db.save_post(text="D1", run_id="r1", status="draft", platform="twitter", client_id="default")
        test_db.save_post(text="D2", run_id="r1", status="approved", platform="linkedin", client_id="default")

        all_posts = test_db.get_all_posts()
        assert len(all_posts) == 2

        drafts = test_db.get_all_posts(status="draft")
        assert len(drafts) == 1
        assert drafts[0]["text"] == "D1"

        twitter = test_db.get_all_posts(platform="twitter")
        assert len(twitter) == 1


class TestClientTrial:
    def test_create_client_starts_trial_by_default(self, test_db):
        """Non-internal clients get subscription_status='trialing' and trial_ends_at set."""
        cid = test_db.create_client({"name": "Trial Corp"})
        client = test_db.get_client(cid)
        assert client["subscription_status"] == "trialing"
        assert client["trial_ends_at"] is not None

    def test_create_client_no_trial_when_disabled(self, test_db):
        """Explicitly disabling start_trial leaves subscription_status as 'none'."""
        cid = test_db.create_client({"name": "Admin Corp"}, start_trial=False)
        client = test_db.get_client(cid)
        assert client["subscription_status"] == "none"
        assert client["trial_ends_at"] is None

    def test_trial_ends_approximately_14_days_from_now(self, test_db):
        cid = test_db.create_client({"name": "Timing Corp"})
        client = test_db.get_client(cid)
        trial_end = datetime.fromisoformat(client["trial_ends_at"])
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        assert timedelta(days=13) < (trial_end - now) < timedelta(days=15)


class TestChatMessages:
    def test_save_and_get_chat_messages(self, test_db):
        cid = test_db.create_client({"name": "ChatClient"})
        test_db.save_chat_message(cid, "user", "Hello")
        test_db.save_chat_message(cid, "assistant", "Hi!")

        messages = test_db.get_chat_history(cid, limit=10)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hi!"

    def test_chat_history_scoped_by_client(self, test_db):
        cid1 = test_db.create_client({"name": "Client1"})
        cid2 = test_db.create_client({"name": "Client2"})
        test_db.save_chat_message(cid1, "user", "Message for client 1")
        test_db.save_chat_message(cid2, "user", "Message for client 2")

        history1 = test_db.get_chat_history(cid1)
        assert len(history1) == 1
        assert history1[0]["content"] == "Message for client 1"

    def test_chat_history_limit(self, test_db):
        cid = test_db.create_client({"name": "ChatLimit"})
        for i in range(25):
            test_db.save_chat_message(cid, "user", f"Message {i}")

        history = test_db.get_chat_history(cid, limit=10)
        assert len(history) == 10

    def test_chat_history_chronological_order(self, test_db):
        cid = test_db.create_client({"name": "ChatOrder"})
        test_db.save_chat_message(cid, "user", "First")
        test_db.save_chat_message(cid, "assistant", "Second")
        test_db.save_chat_message(cid, "user", "Third")

        history = test_db.get_chat_history(cid)
        assert history[0]["content"] == "First"
        assert history[1]["content"] == "Second"
        assert history[2]["content"] == "Third"
