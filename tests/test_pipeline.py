"""Integration test: full pipeline with mocked externals."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from ortobahn.config import Settings
from ortobahn.orchestrator import Pipeline

# Valid JSON responses for each agent
CEO_JSON = json.dumps(
    {
        "themes": ["AI autonomy", "tech culture"],
        "tone": "bold and direct",
        "goals": ["grow followers"],
        "content_guidelines": "be specific",
        "posting_frequency": "3x/day",
        "valid_until": (datetime.utcnow() + timedelta(days=7)).isoformat(),
    }
)

STRATEGIST_JSON = json.dumps(
    {
        "posts": [
            {
                "topic": "AI agents",
                "angle": "production readiness",
                "hook": "Most AI agents fail in production",
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
                "text": "Most AI agents fail in production. Not because the model is bad, but because nobody tested the edge cases.",
                "source_idea": "AI agents",
                "reasoning": "Relatable dev pain",
                "confidence": 0.9,
            }
        ]
    }
)

ANALYTICS_JSON = json.dumps(
    {
        "top_themes": [],
        "summary": "No data yet.",
        "recommendations": [],
    }
)

SRE_JSON = json.dumps(
    {
        "health_status": "healthy",
        "avg_confidence_trend": "stable",
        "alerts": [],
        "recommendations": [],
    }
)

CFO_JSON = json.dumps(
    {
        "budget_status": "within_budget",
        "recommendations": [],
        "summary": "No cost data yet.",
    }
)

OPS_JSON = json.dumps(
    {
        "recommendations": [],
        "summary": "No operational data yet.",
    }
)


def _make_settings(tmp_path):
    return Settings(
        anthropic_api_key="sk-ant-test",
        bluesky_handle="test.bsky.social",
        bluesky_app_password="test-pass",
        db_path=tmp_path / "test.db",
        max_posts_per_cycle=4,
    )


class TestFullPipeline:
    def test_dry_run_pipeline(self, tmp_path):
        """Full pipeline cycle with all LLM calls mocked."""
        settings = _make_settings(tmp_path)

        # Track which call we're on to return the right response
        # Order: SRE, CEO, Strategist, Creator, CFO, Ops
        call_count = {"n": 0}
        responses_in_order = [SRE_JSON, CEO_JSON, STRATEGIST_JSON, CREATOR_JSON, CFO_JSON, OPS_JSON]

        def fake_call_llm(**kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            text = responses_in_order[idx] if idx < len(responses_in_order) else "{}"
            return MagicMock(
                text=text, input_tokens=100, output_tokens=200, model="test", thinking="",
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            )

        with (
            patch("ortobahn.agents.base.call_llm", side_effect=fake_call_llm),
            patch("ortobahn.orchestrator.get_trending_headlines", return_value=[]),
            patch("ortobahn.orchestrator.get_trending_searches", return_value=[]),
            patch("ortobahn.orchestrator.fetch_feeds", return_value=[]),
        ):
            pipeline = Pipeline(settings, dry_run=True)
            result = pipeline.run_cycle()
            pipeline.close()

        assert result["posts_published"] == 0  # Dry run
        assert result["total_drafts"] == 1
        assert len(result["errors"]) == 0

    def test_cli_status_command(self):
        """Smoke test: CLI status command exits cleanly."""
        result = subprocess.run(
            [sys.executable, "-m", "ortobahn", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "ORTOBAHN" in result.stdout

    def test_cli_help(self):
        """Smoke test: CLI --help exits cleanly."""
        result = subprocess.run(
            [sys.executable, "-m", "ortobahn", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Autonomous AI Marketing Engine" in result.stdout
