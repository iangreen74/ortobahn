"""Tests for CTO Agent integration in the Pipeline orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from ortobahn.config import Settings
from ortobahn.models import CTOResult
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCTOInitialization:
    def test_cto_initialized_when_enabled(self, tmp_path):
        """Pipeline.__init__ creates self.cto when cto_enabled=True."""
        settings = _make_settings(tmp_path, cto_enabled=True)
        pipeline = Pipeline(settings, dry_run=True)
        assert pipeline.cto is not None
        assert pipeline.cto.thinking_budget == settings.thinking_budget_cto
        pipeline.close()

    def test_cto_not_initialized_when_disabled(self, tmp_path):
        """Pipeline.__init__ sets self.cto=None when cto_enabled=False."""
        settings = _make_settings(tmp_path, cto_enabled=False)
        pipeline = Pipeline(settings, dry_run=True)
        assert pipeline.cto is None
        pipeline.close()


class TestCTOInPipeline:
    def test_cto_runs_when_backlog_tasks_exist(self, tmp_path):
        """When there are backlog tasks, CTO.run() is called."""
        settings = _make_settings(tmp_path, cto_enabled=True)

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

            # Create a backlog engineering task
            pipeline.db.create_engineering_task(
                {
                    "title": "Add feature X",
                    "description": "Implement feature X",
                    "priority": 2,
                    "category": "feature",
                    "created_by": "test",
                }
            )

            # Mock CTO.run() to return a success result
            mock_cto_result = CTOResult(
                task_id="test-task-id-12345678",
                status="success",
                summary="Implemented feature X",
            )
            pipeline.cto.run = MagicMock(return_value=mock_cto_result)

            result = pipeline.run_cycle(generate_only=True)

        # CTO.run() should have been called
        pipeline.cto.run.assert_called_once()
        assert len(result["errors"]) == 0
        pipeline.close()

    def test_cto_skipped_when_no_tasks(self, tmp_path):
        """When there are no backlog tasks, CTO.run() is not called."""
        settings = _make_settings(tmp_path, cto_enabled=True)

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

            # Mock CTO.run() — it should NOT be called
            pipeline.cto.run = MagicMock()

            result = pipeline.run_cycle(generate_only=True)

        pipeline.cto.run.assert_not_called()
        assert len(result["errors"]) == 0
        pipeline.close()

    def test_cto_error_is_non_fatal(self, tmp_path):
        """CTO.run() raises exception, pipeline still completes."""
        settings = _make_settings(tmp_path, cto_enabled=True)

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

            # Create a backlog task so CTO is triggered
            pipeline.db.create_engineering_task(
                {
                    "title": "Broken task",
                    "description": "This task will fail",
                    "priority": 1,
                    "category": "feature",
                    "created_by": "test",
                }
            )

            # Mock CTO.run() to raise an exception
            pipeline.cto.run = MagicMock(side_effect=RuntimeError("CTO agent crashed"))

            result = pipeline.run_cycle(generate_only=True)

        # Pipeline should complete despite CTO failure
        assert len(result["errors"]) == 0
        assert "run_id" in result
        pipeline.close()
