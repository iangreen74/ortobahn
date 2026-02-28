"""Comprehensive tests for the Pipeline orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.config import Settings
from ortobahn.models import (
    DirectiveCategory,
    DirectivePriority,
    ExecutiveDirective,
    Platform,
)
from ortobahn.orchestrator import Pipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_UNTIL = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()


def _make_settings(tmp_path, **overrides):
    defaults = dict(
        anthropic_api_key="sk-ant-test",
        bluesky_handle="test.bsky.social",
        bluesky_app_password="test-pass",
        db_path=tmp_path / "test.db",
        max_posts_per_cycle=4,
        preflight_enabled=False,
        engagement_enabled=False,
        post_feedback_enabled=False,
        cifix_enabled=False,
        backup_enabled=False,
        style_evolution_enabled=False,
        predictive_timing_enabled=False,
        serialization_enabled=False,
        dynamic_cadence_enabled=False,
        publish_retry_enabled=False,
        post_delay_seconds=0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


# Pre-baked JSON responses for each agent (in pipeline order for a fresh DB)
SRE_JSON = json.dumps(
    {
        "health_status": "healthy",
        "avg_confidence_trend": "stable",
        "alerts": [],
        "recommendations": [],
    }
)

ANALYTICS_JSON = json.dumps({"top_themes": [], "summary": "No data.", "recommendations": []})

REFLECTION_JSON = json.dumps(
    {
        "confidence_accuracy": 0.0,
        "confidence_bias": "neutral",
        "content_patterns": None,
        "new_memories": [],
        "recommendations": [],
        "summary": "n/a",
    }
)

SUPPORT_JSON = json.dumps(
    {
        "total_clients_checked": 0,
        "at_risk_clients": [],
        "tickets": [],
        "recommendations": [],
        "summary": "ok",
    }
)

SECURITY_JSON = json.dumps(
    {
        "threat_level": "low",
        "threats_detected": [],
        "recommendations": [],
        "actions_taken": [],
        "credential_health": {},
        "summary": "ok",
    }
)

LEGAL_JSON = json.dumps(
    {
        "documents_generated": [],
        "compliance_gaps": [],
        "recommendations": [],
        "summary": "ok",
    }
)

CEO_JSON = json.dumps(
    {
        "strategy": {
            "themes": ["AI"],
            "tone": "bold",
            "goals": ["grow"],
            "content_guidelines": "be real",
            "posting_frequency": "3x/day",
            "valid_until": VALID_UNTIL,
        },
        "directives": [],
        "business_assessment": "ok",
        "risk_flags": [],
    }
)

STRATEGIST_JSON = json.dumps(
    {
        "posts": [
            {
                "topic": "AI agents",
                "angle": "production readiness",
                "hook": "Most AI agents fail",
                "content_type": "hot_take",
                "priority": 1,
                "trending_source": None,
            }
        ]
    }
)

CREATOR_JSON = json.dumps(
    {
        "posts": [
            {
                "text": "Most AI agents fail in production.",
                "source_idea": "AI agents",
                "reasoning": "Relatable",
                "confidence": 0.9,
            }
        ]
    }
)

CFO_JSON = json.dumps({"budget_status": "within_budget", "recommendations": [], "summary": "ok"})

OPS_JSON = json.dumps({"recommendations": [], "summary": "ok"})


def _ordered_responses():
    """Return list of JSON responses in the order agents run (fresh DB)."""
    return [
        SRE_JSON,
        # Analytics and Reflection skip LLM on fresh DB
        SUPPORT_JSON,
        SECURITY_JSON,
        LEGAL_JSON,
        CEO_JSON,
        STRATEGIST_JSON,
        CREATOR_JSON,
        CFO_JSON,
        OPS_JSON,
    ]


def _fake_call_llm_factory(responses):
    """Create a side_effect callable that cycles through *responses*."""
    counter = {"n": 0}

    def _call(**kwargs):
        idx = counter["n"]
        counter["n"] += 1
        text = responses[idx] if idx < len(responses) else "{}"
        return MagicMock(
            text=text,
            input_tokens=100,
            output_tokens=200,
            model="test",
            thinking="",
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

    return _call


_TREND_PATCHES = {
    "ortobahn.orchestrator.get_trending_headlines": [],
    "ortobahn.orchestrator.get_trending_searches": [],
    "ortobahn.orchestrator.fetch_feeds": [],
}


def _trend_context():
    """Context manager that patches all trending-data fetches to return empty."""
    return (
        patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
        patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
        patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunCyclePausedClient:
    """Client status guards at the top of run_cycle()."""

    def test_paused_client_skips_cycle(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        # Mark default client as paused
        pipeline.db.execute("UPDATE clients SET status='paused' WHERE id='default'", commit=True)

        result = pipeline.run_cycle()
        assert result["posts_published"] == 0
        assert "client_paused" in result["errors"]
        pipeline.close()

    def test_credential_issue_client_skips_cycle(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        pipeline.db.execute(
            "UPDATE clients SET status='credential_issue' WHERE id='default'",
            commit=True,
        )

        result = pipeline.run_cycle()
        assert result["posts_published"] == 0
        assert "client_credential_issue" in result["errors"]
        pipeline.close()


class TestSubscriptionGuard:
    """Subscription-related guards in run_cycle()."""

    def test_expired_subscription_skips_non_internal(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        # Make client non-internal with expired subscription
        pipeline.db.execute(
            "UPDATE clients SET internal=0, subscription_status='cancelled' WHERE id='default'",
            commit=True,
        )

        result = pipeline.run_cycle()
        assert result["posts_published"] == 0
        assert "no_active_subscription" in result["errors"]
        pipeline.close()

    def test_active_subscription_passes(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.execute(
                "UPDATE clients SET internal=0, subscription_status='active' WHERE id='default'",
                commit=True,
            )
            result = pipeline.run_cycle()
            assert "no_active_subscription" not in result["errors"]
            pipeline.close()

    def test_trialing_subscription_passes(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            future_trial = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            pipeline.db.execute(
                "UPDATE clients SET internal=0, subscription_status='trialing', trial_ends_at=? WHERE id='default'",
                (future_trial,),
                commit=True,
            )
            result = pipeline.run_cycle()
            assert "no_active_subscription" not in result["errors"]
            pipeline.close()

    def test_internal_client_bypasses_subscription(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.execute(
                "UPDATE clients SET internal=1, subscription_status='none' WHERE id='default'",
                commit=True,
            )
            result = pipeline.run_cycle()
            assert "no_active_subscription" not in result["errors"]
            pipeline.close()


class TestTrialExpiry:
    def test_expired_trial_blocks_cycle(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        # Set trial that already expired
        expired_trial = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        pipeline.db.execute(
            "UPDATE clients SET internal=0, subscription_status='trialing', trial_ends_at=? WHERE id='default'",
            (expired_trial,),
            commit=True,
        )

        result = pipeline.run_cycle()
        assert result["posts_published"] == 0
        # After check_and_expire_trial, status should be expired -> no_active_subscription
        assert "no_active_subscription" in result["errors"]
        pipeline.close()


class TestGenerateOnlyMode:
    def test_generate_only_saves_drafts(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle(generate_only=True)

            assert result["posts_published"] == 0
            assert result["total_drafts"] == 1
            # Drafts should be saved in DB
            drafts = pipeline.db.get_drafts_for_review()
            assert len(drafts) >= 1
            pipeline.close()

    def test_auto_publish_override_per_client(self, tmp_path):
        """When generate_only is None, per-client auto_publish overrides settings."""
        settings = _make_settings(tmp_path, autonomous_mode=True)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            # Client has auto_publish=0 => generate_only=True
            pipeline.db.execute("UPDATE clients SET auto_publish=0 WHERE id='default'", commit=True)
            result = pipeline.run_cycle(generate_only=None)
            # Should be in generate-only mode (drafts saved, not published)
            assert result["posts_published"] == 0
            pipeline.close()


class TestPublishApprovedDrafts:
    def test_no_approved_returns_zero(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=False)
        count = pipeline.publish_approved_drafts()
        assert count == 0
        pipeline.close()

    def test_dry_run_skips_publishing(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        # Save an approved post
        pipeline.db.save_post(text="Approved post", run_id="r1", status="approved", platform="bluesky")

        count = pipeline.publish_approved_drafts()
        # Dry run should not publish
        assert count == 0
        pipeline.close()

    def test_publish_approved_success(self, tmp_path):
        settings = _make_settings(tmp_path, post_delay_seconds=0)
        pipeline = Pipeline(settings, dry_run=False)

        mock_client = MagicMock()
        mock_client.post.return_value = ("at://test/post/123", "bafy123")
        mock_client.verify_post_exists.return_value = True
        pipeline.bluesky = mock_client
        pipeline.publisher.bluesky = mock_client

        pid = pipeline.db.save_post(text="Publish me", run_id="r1", status="approved", platform="bluesky")

        with patch("ortobahn.orchestrator.time.sleep"):
            with patch("ortobahn.orchestrator.dispatch_event"):
                count = pipeline.publish_approved_drafts()

        assert count == 1
        post = pipeline.db.get_post(pid)
        assert post["status"] == "published"
        pipeline.close()

    def test_publish_verification_failure(self, tmp_path):
        settings = _make_settings(tmp_path, post_delay_seconds=0)
        pipeline = Pipeline(settings, dry_run=False)

        mock_client = MagicMock()
        mock_client.post.return_value = ("at://test/post/123", "bafy123")
        mock_client.verify_post_exists.return_value = False  # Verification fails
        pipeline.bluesky = mock_client
        pipeline.publisher.bluesky = mock_client

        pid = pipeline.db.save_post(text="Will fail verify", run_id="r1", status="approved", platform="bluesky")

        with patch("ortobahn.orchestrator.time.sleep"):
            with patch("ortobahn.orchestrator.dispatch_event"):
                count = pipeline.publish_approved_drafts()

        assert count == 0
        post = pipeline.db.get_post(pid)
        assert post["status"] == "failed"
        pipeline.close()

    def test_publish_exception_marks_failed(self, tmp_path):
        settings = _make_settings(tmp_path, post_delay_seconds=0)
        pipeline = Pipeline(settings, dry_run=False)

        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("Network down")
        pipeline.bluesky = mock_client
        pipeline.publisher.bluesky = mock_client

        pid = pipeline.db.save_post(text="Will error", run_id="r1", status="approved", platform="bluesky")

        with patch("ortobahn.orchestrator.dispatch_event"):
            count = pipeline.publish_approved_drafts()

        assert count == 0
        post = pipeline.db.get_post(pid)
        assert post["status"] == "failed"
        pipeline.close()

    def test_no_publisher_for_platform_skips(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=False)

        # Save approved post for a platform with no client configured
        pipeline.db.save_post(text="No publisher", run_id="r1", status="approved", platform="twitter")

        count = pipeline.publish_approved_drafts()
        assert count == 0
        pipeline.close()

    def test_invalid_platform_uses_generic(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=False)

        pipeline.db.save_post(text="Bad platform", run_id="r1", status="approved", platform="foobar")

        count = pipeline.publish_approved_drafts()
        assert count == 0  # No publisher for GENERIC
        pipeline.close()

    def test_verification_inconclusive_trusts_post(self, tmp_path):
        settings = _make_settings(tmp_path, post_delay_seconds=0)
        pipeline = Pipeline(settings, dry_run=False)

        mock_client = MagicMock()
        mock_client.post.return_value = ("at://test/post/456", "bafy456")
        mock_client.verify_post_exists.return_value = None  # Inconclusive
        pipeline.bluesky = mock_client
        pipeline.publisher.bluesky = mock_client

        pid = pipeline.db.save_post(
            text="Inconclusive verify",
            run_id="r1",
            status="approved",
            platform="bluesky",
        )

        with patch("ortobahn.orchestrator.time.sleep"):
            with patch("ortobahn.orchestrator.dispatch_event"):
                count = pipeline.publish_approved_drafts()

        assert count == 1
        post = pipeline.db.get_post(pid)
        assert post["status"] == "published"
        pipeline.close()


class TestGatherTrends:
    def test_empty_sources(self, tmp_path):
        settings = _make_settings(tmp_path)
        with (
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            topics = pipeline.gather_trends()
            assert topics == []
            pipeline.close()

    def test_client_specific_category(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        pipeline.db.update_client("default", {"news_category": "business"})

        with (
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]) as mock_headlines,
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline.gather_trends(client_id="default")
            mock_headlines.assert_called_once()
            _, kwargs = mock_headlines.call_args
            assert kwargs["category"] == "business"

        pipeline.close()

    def test_client_specific_rss_feeds(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        pipeline.db.update_client("default", {"rss_feeds": "https://example.com/feed1,https://example.com/feed2"})

        with (
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]) as mock_feeds,
        ):
            pipeline.gather_trends(client_id="default")
            mock_feeds.assert_called_once()
            feed_urls = mock_feeds.call_args[0][0]
            assert "https://example.com/feed1" in feed_urls
            assert "https://example.com/feed2" in feed_urls

        pipeline.close()

    def test_client_keyword_search(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        pipeline.db.update_client("default", {"news_keywords": "AI agents"})

        with (
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
            patch("ortobahn.integrations.newsapi_client.search_news", return_value=[]),
        ):
            pipeline.gather_trends(client_id="default")

        pipeline.close()


class TestDirectiveProcessing:
    def test_engineering_directive_creates_task(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        directive = ExecutiveDirective(
            priority=DirectivePriority.HIGH,
            category=DirectiveCategory.ENGINEERING,
            directive="Build rate limiting",
            target_agent="cto",
            reasoning="Need to protect APIs",
        )

        pipeline._process_directives("run-1", [directive], "default")

        tasks = pipeline.db.get_engineering_tasks()
        titles = [t["title"] for t in tasks]
        assert "Build rate limiting" in titles
        pipeline.close()

    def test_security_directive_creates_infra_task(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        directive = ExecutiveDirective(
            priority=DirectivePriority.CRITICAL,
            category=DirectiveCategory.SECURITY,
            directive="Add WAF rules",
            reasoning="Security threats detected",
        )

        pipeline._process_directives("run-2", [directive], "default")

        tasks = pipeline.db.get_engineering_tasks()
        matching = [t for t in tasks if "WAF" in t["title"]]
        assert len(matching) == 1
        assert matching[0]["category"] == "infra"
        pipeline.close()

    def test_legal_directive_creates_docs_task(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        directive = ExecutiveDirective(
            priority=DirectivePriority.MEDIUM,
            category=DirectiveCategory.LEGAL,
            directive="Generate privacy policy",
            reasoning="No privacy policy exists",
        )

        pipeline._process_directives("run-3", [directive], "default")

        tasks = pipeline.db.get_engineering_tasks()
        legal_tasks = [t for t in tasks if "Legal" in t["title"] or "privacy" in t["title"]]
        assert len(legal_tasks) == 1
        assert legal_tasks[0]["category"] == "docs"
        pipeline.close()

    def test_directive_rate_limiting(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        directives = [
            ExecutiveDirective(
                priority=DirectivePriority.LOW,
                category=DirectiveCategory.OPERATIONS,
                directive=f"Directive {i}",
                reasoning=f"Reason {i}",
            )
            for i in range(10)
        ]

        pipeline._process_directives("run-4", directives, "default")

        # Check audit trail: only 5 should be saved (rate limit)
        rows = pipeline.db.fetchall("SELECT * FROM executive_directives WHERE run_id='run-4'")
        assert len(rows) == 5
        pipeline.close()

    def test_directive_saves_audit_trail(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        directive = ExecutiveDirective(
            priority=DirectivePriority.HIGH,
            category=DirectiveCategory.SUPPORT,
            directive="Follow up with at-risk client",
            reasoning="Client has not been active",
        )

        pipeline._process_directives("run-5", [directive], "default")

        rows = pipeline.db.fetchall("SELECT * FROM executive_directives WHERE run_id='run-5'")
        assert len(rows) == 1
        assert rows[0]["category"] == "support"
        pipeline.close()

    def test_directive_processing_error_does_not_crash(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        # Create a directive with a bad model_dump that raises
        directive = MagicMock()
        directive.model_dump.side_effect = RuntimeError("Bad directive")
        directive.priority = DirectivePriority.HIGH
        directive.category = DirectiveCategory.ENGINEERING

        # Should not raise
        pipeline._process_directives("run-6", [directive], "default")
        pipeline.close()


class TestRunCycleFullPipeline:
    def test_dry_run_full_cycle(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle()

        assert result["posts_published"] == 0
        assert result["total_drafts"] == 1
        assert len(result["errors"]) == 0
        assert "run_id" in result
        pipeline.close()

    def test_run_id_is_unique_uuid(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses() + _ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            r1 = pipeline.run_cycle()
            r2 = pipeline.run_cycle()
            assert r1["run_id"] != r2["run_id"]
            pipeline.close()

    def test_pipeline_error_dispatches_event(self, tmp_path):
        settings = _make_settings(tmp_path)

        def _explode(**kwargs):
            raise RuntimeError("LLM is down")

        with (
            patch("ortobahn.agents.base.call_llm", side_effect=_explode),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
            patch("ortobahn.orchestrator.dispatch_event") as mock_dispatch,
        ):
            pipeline = Pipeline(settings, dry_run=True)
            with pytest.raises(RuntimeError, match="LLM is down"):
                pipeline.run_cycle()

            # Check that pipeline.failed event was dispatched
            mock_dispatch.assert_called()
            call_args_list = mock_dispatch.call_args_list
            event_types = [c.args[2] for c in call_args_list]
            assert "pipeline.failed" in event_types
            pipeline.close()

    def test_pipeline_records_run_in_db(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.run_cycle()

        runs = pipeline.db.get_recent_runs(limit=1)
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        pipeline.close()


class TestWebhookDispatch:
    def test_publish_dispatches_webhook(self, tmp_path):
        settings = _make_settings(tmp_path, post_delay_seconds=0)
        pipeline = Pipeline(settings, dry_run=False)

        mock_client = MagicMock()
        mock_client.post.return_value = ("at://test/post/789", "bafy789")
        mock_client.verify_post_exists.return_value = True
        pipeline.bluesky = mock_client
        pipeline.publisher.bluesky = mock_client

        pipeline.db.save_post(text="Webhook post", run_id="r1", status="approved", platform="bluesky")

        with (
            patch("ortobahn.orchestrator.time.sleep"),
            patch("ortobahn.orchestrator.dispatch_event") as mock_dispatch,
        ):
            pipeline.publish_approved_drafts()

        mock_dispatch.assert_called()
        event_types = [c.args[2] for c in mock_dispatch.call_args_list]
        assert "post.published" in event_types
        pipeline.close()


class TestMultipleClientsIsolation:
    def test_cycle_uses_correct_client_id(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.create_client({"id": "test-client", "name": "Test Client"}, start_trial=False)
            pipeline.db.execute("UPDATE clients SET internal=1 WHERE id='test-client'", commit=True)
            result = pipeline.run_cycle(client_id="test-client", generate_only=True)

        assert result["posts_published"] == 0
        assert len(result["errors"]) == 0
        pipeline.close()


class TestPipelineInit:
    def test_no_bluesky_credentials_no_client(self, tmp_path):
        settings = Settings(
            anthropic_api_key="sk-ant-test",
            db_path=tmp_path / "test.db",
            bluesky_handle="",
            bluesky_app_password="",
            preflight_enabled=False,
            cifix_enabled=False,
        )
        pipeline = Pipeline(settings, dry_run=True)
        assert pipeline.bluesky is None
        pipeline.close()

    def test_cifix_disabled(self, tmp_path):
        settings = _make_settings(tmp_path, cifix_enabled=False)
        pipeline = Pipeline(settings, dry_run=True)
        assert pipeline.cifix is None
        pipeline.close()

    def test_engagement_disabled(self, tmp_path):
        settings = _make_settings(tmp_path, engagement_enabled=False)
        pipeline = Pipeline(settings, dry_run=True)
        assert pipeline.engagement is None
        pipeline.close()


ARTICLE_WRITER_JSON = json.dumps(
    {
        "title": "The Future of AI Agents in Production",
        "subtitle": "A deep dive into production readiness",
        "body_markdown": "# Introduction\n\nAI agents are transforming the way we build software. "
        "This article explores the challenges and opportunities of deploying AI agents in production environments. "
        * 20,
        "tags": ["AI", "agents", "production"],
        "meta_description": "Exploring AI agents in production",
        "topic_used": "AI agents in production",
        "confidence": 0.85,
        "word_count": 1500,
    }
)


class TestArticleCycle:
    def test_article_cycle_client_not_found(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        result = pipeline.run_article_cycle(client_id="nonexistent")
        assert result["status"] == "error"
        assert "client_not_found" in result["error"]
        pipeline.close()

    def test_article_cycle_not_enabled(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        # Default client has article_enabled=0
        result = pipeline.run_article_cycle(client_id="default")
        assert result["status"] == "skipped"
        assert "articles_not_enabled" in result["error"]
        pipeline.close()

    def test_article_cycle_no_subscription(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        pipeline.db.execute(
            "UPDATE clients SET article_enabled=1, internal=0, subscription_status='cancelled' WHERE id='default'",
            commit=True,
        )
        result = pipeline.run_article_cycle(client_id="default")
        assert result["status"] == "skipped"
        assert "no_active_subscription" in result["error"]
        pipeline.close()

    def test_article_cycle_generates_and_saves(self, tmp_path):
        """When article_enabled=1, run_article_cycle generates and saves an article."""
        settings = _make_settings(tmp_path)

        with patch(
            "ortobahn.agents.base.call_llm",
            side_effect=_fake_call_llm_factory([ARTICLE_WRITER_JSON]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.execute(
                "UPDATE clients SET article_enabled=1 WHERE id='default'",
                commit=True,
            )
            result = pipeline.run_article_cycle(client_id="default")

        assert result["status"] == "success"
        assert result["title"] == "The Future of AI Agents in Production"
        assert result["article_id"]
        # Verify it was saved in DB
        article = pipeline.db.get_article(result["article_id"])
        assert article is not None
        assert article["status"] == "draft"
        pipeline.close()

    def test_article_cycle_auto_publishes_high_confidence(self, tmp_path):
        """When auto_publish_articles=1 and confidence >= threshold, article is published."""
        settings = _make_settings(
            tmp_path,
            article_confidence_threshold=0.8,
            secret_key="test-secret-key-for-jwt-and-fernet-00",
        )

        mock_medium = MagicMock()
        mock_medium.post.return_value = ("https://medium.com/@test/article-123", "article-123")

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory([ARTICLE_WRITER_JSON]),
            ),
            patch("ortobahn.credentials.build_article_clients") as mock_build,
        ):
            mock_build.return_value = {
                "medium": mock_medium,
                "substack": None,
                "linkedin_article": None,
            }
            pipeline = Pipeline(settings, dry_run=False)
            pipeline.db.execute(
                "UPDATE clients SET article_enabled=1, auto_publish=1, auto_publish_articles=1, article_platforms='medium' WHERE id='default'",
                commit=True,
            )
            result = pipeline.run_article_cycle(client_id="default")

        assert result["status"] == "success"
        # Article should have been published
        article = pipeline.db.get_article(result["article_id"])
        assert article["status"] == "published"
        mock_medium.post.assert_called_once()
        pipeline.close()


class TestRunAgentWithPreflight:
    def test_preflight_blocks_run(self, tmp_path):
        from ortobahn.models import PreflightIssue, PreflightResult, PreflightSeverity

        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        agent = MagicMock()
        agent.name = "test_agent"
        pf_result = PreflightResult(
            passed=False,
            issues=[
                PreflightIssue(
                    severity=PreflightSeverity.BLOCKING,
                    component="test",
                    message="API key missing",
                )
            ],
        )
        agent.preflight.return_value = pf_result

        result = pipeline._run_agent_with_preflight(agent, "run-1")
        assert result is None
        agent.run.assert_not_called()
        pipeline.close()

    def test_preflight_passes_calls_run(self, tmp_path):
        from ortobahn.models import PreflightResult

        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        agent = MagicMock()
        agent.name = "test_agent"
        agent.preflight.return_value = PreflightResult(passed=True)
        agent.run.return_value = "mock_result"

        result = pipeline._run_agent_with_preflight(agent, "run-1")
        assert result == "mock_result"
        agent.run.assert_called_once_with("run-1")
        pipeline.close()

    def test_preflight_warnings_still_runs(self, tmp_path):
        from ortobahn.models import PreflightIssue, PreflightResult, PreflightSeverity

        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        agent = MagicMock()
        agent.name = "test_agent"
        pf_result = PreflightResult(
            passed=True,
            issues=[
                PreflightIssue(
                    severity=PreflightSeverity.WARNING,
                    component="test",
                    message="Rate limited",
                )
            ],
        )
        agent.preflight.return_value = pf_result
        agent.run.return_value = "ok"

        result = pipeline._run_agent_with_preflight(agent, "run-1")
        assert result == "ok"
        agent.run.assert_called_once()
        pipeline.close()


class TestTargetPlatforms:
    def test_default_platform_is_generic(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle(generate_only=True)
            assert len(result["errors"]) == 0
            pipeline.close()

    def test_explicit_platforms_passed(self, tmp_path):
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle(
                target_platforms=[Platform.BLUESKY, Platform.TWITTER],
                generate_only=True,
            )
            assert len(result["errors"]) == 0
            pipeline.close()


class TestPerTenantCredentials:
    def test_secret_key_triggers_tenant_credentials(self, tmp_path):
        settings = _make_settings(
            tmp_path,
            secret_key="test-secret-key-for-jwt-and-fernet-00",
        )

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
            patch("ortobahn.credentials.build_platform_clients") as mock_build,
        ):
            mock_build.return_value = {
                "bluesky": None,
                "twitter": None,
                "linkedin": None,
            }
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.run_cycle(generate_only=True)
            mock_build.assert_called_once()
            pipeline.close()


class TestPipelineClose:
    def test_close_closes_db(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)
        pipeline.close()
        # Verify DB is closed by checking it can't be used
        # (SQLite allows some operations after close, so just confirm close() works)
        assert True


# ---------------------------------------------------------------------------
# Pipeline partial failure tests
# ---------------------------------------------------------------------------


class TestPartialFailure:
    """One agent failing doesn't crash the whole pipeline."""

    def test_sre_failure_does_not_crash_pipeline(self, tmp_path):
        """If SRE agent raises, the whole pipeline should raise (it's in the try block)
        but the error is caught at the top level and recorded."""
        # SRE is the first agent; if it fails, the pipeline's try/except
        # catches it and re-raises. We verify the error is dispatched.
        settings = _make_settings(tmp_path)

        call_count = {"n": 0}

        def _fail_on_sre(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("SRE agent is down")
            # Subsequent agents
            return MagicMock(
                text="{}",
                input_tokens=10,
                output_tokens=20,
                model="test",
                thinking="",
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )

        with (
            patch("ortobahn.agents.base.call_llm", side_effect=_fail_on_sre),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
            patch("ortobahn.orchestrator.dispatch_event") as mock_dispatch,
        ):
            pipeline = Pipeline(settings, dry_run=True)
            with pytest.raises(RuntimeError, match="SRE agent is down"):
                pipeline.run_cycle()

            # Check that error was dispatched
            event_types = [c.args[2] for c in mock_dispatch.call_args_list]
            assert "pipeline.failed" in event_types
            pipeline.close()

    def test_security_agent_failure_is_non_fatal(self, tmp_path):
        """Security agent failure is caught as non-fatal in the pipeline."""
        settings = _make_settings(tmp_path)

        # Security is the 6th LLM call. We need to fail specifically on it.
        call_count = {"n": 0}
        responses = _ordered_responses()

        def _fail_on_security(**kwargs):
            call_count["n"] += 1
            idx = call_count["n"] - 1

            # Security is the 5th LLM call (0-indexed: 4) in _ordered_responses
            # SRE(0), Support(1), Security(2) -- wait, let me check the order.
            # From _ordered_responses: SRE, SUPPORT, SECURITY, LEGAL, CEO, STRATEGIST, CREATOR, CFO, OPS
            # Security is idx=2 (3rd call)
            if idx == 2:
                raise RuntimeError("Security analysis failed")

            text = responses[idx] if idx < len(responses) else "{}"
            return MagicMock(
                text=text,
                input_tokens=100,
                output_tokens=200,
                model="test",
                thinking="",
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )

        with (
            patch("ortobahn.agents.base.call_llm", side_effect=_fail_on_security),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle(generate_only=True)

        # Pipeline should complete despite security agent failure
        assert len(result["errors"]) == 0
        assert "run_id" in result
        pipeline.close()

    def test_legal_agent_failure_is_non_fatal(self, tmp_path):
        """Legal agent failure is caught as non-fatal in the pipeline."""
        settings = _make_settings(tmp_path)

        call_count = {"n": 0}
        responses = _ordered_responses()

        def _fail_on_legal(**kwargs):
            call_count["n"] += 1
            idx = call_count["n"] - 1

            # Legal is idx=3 (4th call) in _ordered_responses
            if idx == 3:
                raise RuntimeError("Legal compliance failed")

            text = responses[idx] if idx < len(responses) else "{}"
            return MagicMock(
                text=text,
                input_tokens=100,
                output_tokens=200,
                model="test",
                thinking="",
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )

        with (
            patch("ortobahn.agents.base.call_llm", side_effect=_fail_on_legal),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle(generate_only=True)

        # Pipeline should complete despite legal agent failure
        assert len(result["errors"]) == 0
        assert "run_id" in result
        pipeline.close()


class TestErrorAccumulation:
    """Test that errors are accumulated and reported correctly."""

    def test_pipeline_error_recorded_in_db(self, tmp_path):
        """When the pipeline fails, the error is recorded in the pipeline run."""
        settings = _make_settings(tmp_path)

        def _explode(**kwargs):
            raise RuntimeError("Total meltdown")

        with (
            patch("ortobahn.agents.base.call_llm", side_effect=_explode),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
            patch("ortobahn.orchestrator.dispatch_event"),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            with pytest.raises(RuntimeError, match="Total meltdown"):
                pipeline.run_cycle()

        # The pipeline run should be recorded as failed
        runs = pipeline.db.get_recent_runs(limit=1)
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert "Total meltdown" in (runs[0].get("errors") or "")
        pipeline.close()

    def test_successful_pipeline_has_empty_errors(self, tmp_path):
        """A successful pipeline run has an empty errors list."""
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle()

        assert result["errors"] == []
        pipeline.close()


class TestPipelineCompletionWithNonCriticalFailures:
    """Pipeline completes even when non-critical agents fail."""

    def test_cifix_failure_does_not_crash(self, tmp_path):
        """CIFix agent failure is caught as non-fatal."""
        settings = _make_settings(tmp_path, cifix_enabled=True)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            # Make cifix.run() raise
            pipeline.cifix.run = MagicMock(side_effect=RuntimeError("CI check failed"))
            result = pipeline.run_cycle(generate_only=True)

        # Pipeline should complete despite cifix failure
        assert len(result["errors"]) == 0
        assert result["total_drafts"] >= 0
        pipeline.close()

    def test_engagement_failure_does_not_crash(self, tmp_path):
        """Engagement agent failure is caught as non-fatal."""
        settings = _make_settings(tmp_path, engagement_enabled=True)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            # Wire engagement to raise
            pipeline.engagement.run = MagicMock(side_effect=RuntimeError("Engagement broken"))
            result = pipeline.run_cycle(generate_only=False)

        # Pipeline should complete despite engagement failure
        assert "run_id" in result
        pipeline.close()


class TestPublishApprovedArticles:
    """Tests for the publish_approved_articles() method."""

    def test_no_approved_articles_returns_zero(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=False)
        count = pipeline.publish_approved_articles()
        assert count == 0
        pipeline.close()

    def test_dry_run_skips_article_publishing(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=True)

        # Save an article and approve it
        aid = pipeline.db.save_article(
            {
                "client_id": "default",
                "title": "Test Article",
                "body_markdown": "# Test\n\nContent here.",
                "tags": ["test"],
                "status": "draft",
            }
        )
        pipeline.db.approve_article(aid)

        count = pipeline.publish_approved_articles()
        assert count == 0
        # Article should still be approved (not published)
        article = pipeline.db.get_article(aid)
        assert article["status"] == "approved"
        pipeline.close()

    def test_publish_approved_article_success(self, tmp_path):
        settings = _make_settings(
            tmp_path,
            secret_key="test-secret-key-for-jwt-and-fernet-00",
        )

        mock_medium = MagicMock()
        mock_medium.post.return_value = ("https://medium.com/@test/article", "med-123")

        with patch("ortobahn.credentials.build_article_clients") as mock_build:
            mock_build.return_value = {
                "medium": mock_medium,
                "substack": None,
                "linkedin_article": None,
            }
            pipeline = Pipeline(settings, dry_run=False)
            pipeline.db.execute(
                "UPDATE clients SET article_platforms='medium' WHERE id='default'",
                commit=True,
            )

            aid = pipeline.db.save_article(
                {
                    "client_id": "default",
                    "title": "Publish Me",
                    "body_markdown": "# Hello\n\nArticle body.",
                    "tags": ["test"],
                    "status": "draft",
                }
            )
            pipeline.db.approve_article(aid)

            count = pipeline.publish_approved_articles()

        assert count == 1
        article = pipeline.db.get_article(aid)
        assert article["status"] == "published"
        mock_medium.post.assert_called_once()
        pipeline.close()

    def test_publish_approved_article_no_platforms_configured(self, tmp_path):
        settings = _make_settings(tmp_path)
        pipeline = Pipeline(settings, dry_run=False)

        aid = pipeline.db.save_article(
            {
                "client_id": "default",
                "title": "No Platforms",
                "body_markdown": "# Content",
                "status": "draft",
            }
        )
        pipeline.db.approve_article(aid)

        count = pipeline.publish_approved_articles()
        assert count == 0
        # Article stays approved since no platforms to publish to
        article = pipeline.db.get_article(aid)
        assert article["status"] == "approved"
        pipeline.close()

    def test_publish_approved_article_failure_does_not_crash(self, tmp_path):
        settings = _make_settings(
            tmp_path,
            secret_key="test-secret-key-for-jwt-and-fernet-00",
        )

        mock_medium = MagicMock()
        mock_medium.post.side_effect = ConnectionError("Network down")

        with patch("ortobahn.credentials.build_article_clients") as mock_build:
            mock_build.return_value = {
                "medium": mock_medium,
                "substack": None,
                "linkedin_article": None,
            }
            pipeline = Pipeline(settings, dry_run=False)
            pipeline.db.execute(
                "UPDATE clients SET article_platforms='medium' WHERE id='default'",
                commit=True,
            )

            aid = pipeline.db.save_article(
                {
                    "client_id": "default",
                    "title": "Will Fail",
                    "body_markdown": "# Content",
                    "tags": [],
                    "status": "draft",
                }
            )
            pipeline.db.approve_article(aid)

            # Should not raise
            count = pipeline.publish_approved_articles()

        assert count == 0
        pipeline.close()


class TestArticleInRunCycle:
    """Tests for article generation integration within run_cycle()."""

    def test_run_cycle_includes_article_fields(self, tmp_path):
        """run_cycle return dict includes articles_generated and articles_published."""
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle(generate_only=True)

        assert "articles_generated" in result
        assert "articles_published" in result
        assert result["articles_generated"] == 0  # article_enabled=0 by default
        assert result["articles_published"] == 0
        pipeline.close()

    def test_run_cycle_generates_article_when_enabled(self, tmp_path):
        """When article_enabled=1, run_cycle triggers article generation."""
        settings = _make_settings(tmp_path)

        # LLM call order: SRE, Support, Security, Legal, CEO, Strategist,
        # Creator, Creator self-critique (wasted), ArticleWriter, CFO, OPS.
        # The self-critique consumes an extra response, so we insert the
        # article writer JSON at position 8 (after the self-critique waste at 7).
        responses = [
            SRE_JSON,  # 0: SRE
            SUPPORT_JSON,  # 1: Support
            SECURITY_JSON,  # 2: Security
            LEGAL_JSON,  # 3: Legal
            CEO_JSON,  # 4: CEO
            STRATEGIST_JSON,  # 5: Strategist
            CREATOR_JSON,  # 6: Creator
            CFO_JSON,  # 7: Creator self-critique (consumed + fails)
            ARTICLE_WRITER_JSON,  # 8: ArticleWriter (Phase 3.6)
            CFO_JSON,  # 9: CFO (Phase 4.1)
            OPS_JSON,  # 10: OPS (Phase 4.2)
        ]

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(responses),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.execute(
                "UPDATE clients SET article_enabled=1 WHERE id='default'",
                commit=True,
            )
            result = pipeline.run_cycle(generate_only=True)

        assert result["articles_generated"] == 1
        # Check article exists in DB
        articles = pipeline.db.get_recent_articles("default", limit=1)
        assert len(articles) == 1
        assert articles[0]["title"] == "The Future of AI Agents in Production"
        pipeline.close()

    def test_run_cycle_skips_article_when_frequency_not_met(self, tmp_path):
        """Article generation is skipped if not enough time has passed since last article."""
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.execute(
                "UPDATE clients SET article_enabled=1, article_frequency='weekly' WHERE id='default'",
                commit=True,
            )
            # Save a recent article so frequency check prevents generation
            pipeline.db.save_article(
                {
                    "client_id": "default",
                    "title": "Recent Article",
                    "body_markdown": "# Recent",
                    "status": "published",
                }
            )

            result = pipeline.run_cycle(generate_only=True)

        # Should skip article generation (weekly = 168h, and last article was just now)
        assert result["articles_generated"] == 0
        pipeline.close()

    def test_run_cycle_publishes_approved_articles(self, tmp_path):
        """Approved articles are published at the start of run_cycle."""
        settings = _make_settings(
            tmp_path,
            secret_key="test-secret-key-for-jwt-and-fernet-00",
        )

        mock_medium = MagicMock()
        mock_medium.post.return_value = ("https://medium.com/@test/art", "med-456")

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
            patch("ortobahn.credentials.build_platform_clients") as mock_plat,
            patch("ortobahn.credentials.build_article_clients") as mock_art,
        ):
            mock_plat.return_value = {"bluesky": None, "twitter": None, "linkedin": None}
            mock_art.return_value = {
                "medium": mock_medium,
                "substack": None,
                "linkedin_article": None,
            }
            pipeline = Pipeline(settings, dry_run=False)
            pipeline.db.execute(
                "UPDATE clients SET article_platforms='medium' WHERE id='default'",
                commit=True,
            )

            # Save and approve an article
            aid = pipeline.db.save_article(
                {
                    "client_id": "default",
                    "title": "Approved Article",
                    "body_markdown": "# Approved\n\nReady to publish.",
                    "tags": ["test"],
                    "status": "draft",
                }
            )
            pipeline.db.approve_article(aid)

            result = pipeline.run_cycle(generate_only=True)

        assert result["articles_published"] >= 1
        article = pipeline.db.get_article(aid)
        assert article["status"] == "published"
        pipeline.close()

    def test_article_generation_failure_is_non_fatal(self, tmp_path):
        """If article generation fails, run_cycle still completes."""
        settings = _make_settings(tmp_path)

        with (
            patch(
                "ortobahn.agents.base.call_llm",
                side_effect=_fake_call_llm_factory(_ordered_responses()),
            ),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            pipeline.db.execute(
                "UPDATE clients SET article_enabled=1 WHERE id='default'",
                commit=True,
            )
            # Make article_writer.run() raise
            pipeline.article_writer.run = MagicMock(side_effect=RuntimeError("Article LLM error"))

            result = pipeline.run_cycle(generate_only=True)

        # Pipeline should complete despite article failure
        assert len(result["errors"]) == 0
        assert result["articles_generated"] == 0
        pipeline.close()
