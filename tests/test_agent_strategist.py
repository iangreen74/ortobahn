"""Tests for Strategist Agent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.strategist import StrategistAgent, _get_top_performing_posts
from ortobahn.models import Client, ContentPlan, Platform, PostType, Strategy, TrendingTopic

VALID_PLAN_JSON = json.dumps(
    {
        "posts": [
            {
                "topic": "AI agents in production",
                "angle": "most companies aren't ready",
                "hook": "Your AI agent is only as good as your error handling",
                "content_type": "hot_take",
                "priority": 1,
                "trending_source": "rss",
            },
            {
                "topic": "Open source AI",
                "angle": "the real moat is community",
                "hook": "Open weights aren't enough",
                "content_type": "insight",
                "priority": 2,
                "trending_source": None,
            },
        ]
    }
)


def _make_strategy(**overrides):
    defaults = dict(
        themes=["AI", "tech"],
        tone="bold",
        goals=["grow"],
        content_guidelines="be real",
        posting_frequency="3x/day",
        valid_until=datetime.utcnow() + timedelta(days=7),
    )
    defaults.update(overrides)
    return Strategy(**defaults)


def _make_client(**overrides):
    defaults = dict(
        id="test-client",
        name="TestCorp",
        description="AI infrastructure company",
        target_audience="CTOs and tech leads",
        brand_voice="direct and insightful",
        products="AI platform",
        content_pillars="AI, engineering, leadership",
    )
    defaults.update(overrides)
    return Client(**defaults)


def _plan_json_with_posts(posts):
    """Helper to build a ContentPlan JSON string from a list of post dicts."""
    return json.dumps({"posts": posts})


# ---------------------------------------------------------------------------
# _get_top_performing_posts unit tests
# ---------------------------------------------------------------------------


class TestGetTopPerformingPosts:
    def test_returns_top_posts_sorted_by_engagement(self):
        db = MagicMock()
        db.get_recent_posts_with_metrics.return_value = [
            {"status": "published", "text": "Post A", "like_count": 10, "repost_count": 5, "reply_count": 2},
            {"status": "published", "text": "Post B", "like_count": 1, "repost_count": 0, "reply_count": 0},
            {"status": "published", "text": "Post C", "like_count": 20, "repost_count": 10, "reply_count": 5},
        ]
        result = _get_top_performing_posts(db, "client-1", limit=2)
        assert len(result) == 2
        assert result[0]["total_engagement"] == 35  # Post C
        assert result[1]["total_engagement"] == 17  # Post A

    def test_skips_non_published_posts(self):
        db = MagicMock()
        db.get_recent_posts_with_metrics.return_value = [
            {"status": "draft", "text": "Draft post", "like_count": 100, "repost_count": 50, "reply_count": 25},
            {"status": "published", "text": "Published post", "like_count": 5, "repost_count": 2, "reply_count": 1},
            {"status": "failed", "text": "Failed post", "like_count": 0, "repost_count": 0, "reply_count": 0},
        ]
        result = _get_top_performing_posts(db, "client-1")
        assert len(result) == 1
        assert result[0]["total_engagement"] == 8

    def test_handles_empty_results(self):
        db = MagicMock()
        db.get_recent_posts_with_metrics.return_value = []
        result = _get_top_performing_posts(db, "client-1")
        assert result == []

    def test_handles_none_metric_values(self):
        db = MagicMock()
        db.get_recent_posts_with_metrics.return_value = [
            {"status": "published", "text": "Post", "like_count": None, "repost_count": None, "reply_count": None},
        ]
        result = _get_top_performing_posts(db, "client-1")
        assert len(result) == 1
        assert result[0]["total_engagement"] == 0

    def test_truncates_content_preview_to_120_chars(self):
        db = MagicMock()
        long_text = "x" * 200
        db.get_recent_posts_with_metrics.return_value = [
            {"status": "published", "text": long_text, "like_count": 5, "repost_count": 0, "reply_count": 0},
        ]
        result = _get_top_performing_posts(db, "client-1")
        assert len(result[0]["content_preview"]) == 120


# ---------------------------------------------------------------------------
# StrategistAgent.run tests
# ---------------------------------------------------------------------------


class TestStrategistAgent:
    def test_creates_content_plan(self, test_db, mock_llm_response):
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(
                run_id="run-1",
                strategy=_make_strategy(),
                trending=[TrendingTopic(title="AI news", source="rss")],
            )

        assert isinstance(result, ContentPlan)
        assert len(result.posts) == 2
        # Should be sorted by priority
        assert result.posts[0].priority <= result.posts[1].priority

    def test_works_without_trends(self, test_db, mock_llm_response):
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy(), trending=[])

        assert isinstance(result, ContentPlan)

    def test_works_with_none_trends(self, test_db, mock_llm_response):
        """trending=None should produce a plan without errors."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy(), trending=None)

        assert isinstance(result, ContentPlan)

    def test_multi_platform_strategy(self, test_db, mock_llm_response):
        """Strategy with multiple target platforms should include them in prompt."""
        strategy = _make_strategy(
            target_platforms=[Platform.BLUESKY, Platform.TWITTER, Platform.LINKEDIN],
        )
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            result = agent.run(run_id="run-1", strategy=strategy)

        assert isinstance(result, ContentPlan)
        # Verify the platform names appeared in the user message passed to LLM
        call_args = mock_call.call_args
        user_msg = call_args.kwargs.get("user_message") or call_args[0][1]
        assert "bluesky" in user_msg
        assert "twitter" in user_msg
        assert "linkedin" in user_msg

    def test_strategy_themes_in_prompt(self, test_db, mock_llm_response):
        """The strategy themes should be present in the user message sent to the LLM."""
        strategy = _make_strategy(themes=["cybersecurity", "quantum computing"])
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=strategy)

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "cybersecurity" in user_msg
        assert "quantum computing" in user_msg

    def test_tone_and_goals_in_prompt(self, test_db, mock_llm_response):
        """Tone and goals from strategy should appear in prompt."""
        strategy = _make_strategy(tone="sarcastic", goals=["brand awareness", "lead gen"])
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=strategy)

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "sarcastic" in user_msg
        assert "brand awareness" in user_msg
        assert "lead gen" in user_msg

    def test_trending_topics_included_in_prompt(self, test_db, mock_llm_response):
        """Multiple trending topics should all appear in the user message."""
        trends = [
            TrendingTopic(title="GPT-5 released", source="newsapi", description="OpenAI launches GPT-5"),
            TrendingTopic(title="Rust in kernel", source="rss", description="Linux adopts more Rust"),
        ]
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=_make_strategy(), trending=trends)

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "GPT-5 released" in user_msg
        assert "Rust in kernel" in user_msg
        assert "[newsapi]" in user_msg
        assert "[rss]" in user_msg

    def test_posts_sorted_by_priority(self, test_db, mock_llm_response):
        """Posts should be returned sorted by priority ascending."""
        unsorted_json = json.dumps(
            {
                "posts": [
                    {
                        "topic": "Low priority",
                        "angle": "a",
                        "hook": "h",
                        "content_type": "insight",
                        "priority": 5,
                        "trending_source": None,
                    },
                    {
                        "topic": "High priority",
                        "angle": "b",
                        "hook": "h",
                        "content_type": "hot_take",
                        "priority": 1,
                        "trending_source": None,
                    },
                    {
                        "topic": "Mid priority",
                        "angle": "c",
                        "hook": "h",
                        "content_type": "question",
                        "priority": 3,
                        "trending_source": None,
                    },
                ]
            }
        )
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=unsorted_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy())

        priorities = [p.priority for p in result.posts]
        assert priorities == [1, 3, 5]

    def test_invalid_json_raises_value_error(self, test_db, mock_llm_response):
        """Non-JSON LLM response should raise ValueError from parse_json_response."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text="This is not JSON at all, just random text.")

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            with pytest.raises(ValueError):
                agent.run(run_id="run-1", strategy=_make_strategy())

    def test_missing_required_fields_raises(self, test_db, mock_llm_response):
        """JSON with missing required fields (e.g. no 'hook') should raise."""
        bad_json = json.dumps(
            {
                "posts": [
                    {
                        "topic": "AI",
                        "angle": "interesting",
                        # missing hook, content_type, priority
                    }
                ]
            }
        )
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=bad_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            with pytest.raises(ValueError):
                agent.run(run_id="run-1", strategy=_make_strategy())

    def test_json_wrapped_in_markdown_code_fence(self, test_db, mock_llm_response):
        """LLM sometimes wraps JSON in ```json ... ``` fences; parse_json_response should handle it."""
        fenced = f"```json\n{VALID_PLAN_JSON}\n```"
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=fenced)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy())

        assert isinstance(result, ContentPlan)
        assert len(result.posts) == 2

    def test_client_context_personalizes_prompt(self, test_db, mock_llm_response):
        """When a client is provided, the system prompt should contain client details."""
        client = _make_client()
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=_make_strategy(), client=client)

        call_args = mock_call.call_args
        system_prompt = call_args.kwargs.get("system_prompt") or call_args[0][0]
        assert "TestCorp" in system_prompt
        assert "CTOs and tech leads" in system_prompt
        assert "direct and insightful" in system_prompt

    def test_no_client_uses_raw_template_prompt(self, test_db, mock_llm_response):
        """Without a client, system_prompt should be the raw template (unsubstituted $variables)."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=_make_strategy())

        call_args = mock_call.call_args
        system_prompt = call_args.kwargs.get("system_prompt") or call_args[0][0]
        # When no client, system_prompt=None in strategist.run, but BaseAgent.call_llm
        # falls back to self.system_prompt (raw template with unsubstituted $variables)
        assert "$client_name" in system_prompt

    def test_memory_context_injected_into_prompt(self, test_db, mock_llm_response):
        """If get_memory_context returns data, it should appear in the user message."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        memory_text = "Posts about AI agents get 2x engagement on Tuesdays."
        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call,
            patch.object(agent, "get_memory_context", return_value=memory_text),
        ):
            agent.run(run_id="run-1", strategy=_make_strategy())

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "Agent Memory" in user_msg
        assert memory_text in user_msg

    def test_empty_memory_context_not_in_prompt(self, test_db, mock_llm_response):
        """If get_memory_context returns empty string, no memory section should appear."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call,
            patch.object(agent, "get_memory_context", return_value=""),
        ):
            agent.run(run_id="run-1", strategy=_make_strategy())

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "Agent Memory" not in user_msg

    def test_top_performing_posts_in_prompt(self, test_db, mock_llm_response):
        """When DB has published posts with metrics, they appear in the prompt."""
        # Create published posts with metrics in the real test DB
        pid = test_db.save_post(text="Great AI post", run_id="r1", status="published")
        test_db.update_post_published(pid, "at://test/1", "bafy1")
        test_db.save_metrics(pid, like_count=20, repost_count=10, reply_count=5)

        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=_make_strategy())

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "Top-performing recent posts" in user_msg
        assert "Great AI post" in user_msg

    def test_top_posts_db_error_handled_gracefully(self, test_db, mock_llm_response):
        """If fetching top posts raises an exception, run() still succeeds."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with (
            patch("ortobahn.agents.base.call_llm", return_value=fake),
            patch(
                "ortobahn.agents.strategist._get_top_performing_posts",
                side_effect=RuntimeError("DB connection lost"),
            ),
        ):
            result = agent.run(run_id="run-1", strategy=_make_strategy())

        assert isinstance(result, ContentPlan)

    def test_log_decision_called(self, test_db, mock_llm_response):
        """Agent should log its decision to the database after running."""
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            agent.run(
                run_id="run-log",
                strategy=_make_strategy(),
                trending=[TrendingTopic(title="test", source="rss")],
            )

        # Verify log was persisted (agent_logs table)
        logs = test_db.fetchall(
            "SELECT * FROM agent_logs WHERE run_id=? AND agent_name=?",
            ("run-log", "strategist"),
        )
        assert len(logs) == 1
        assert "Strategy themes" in logs[0]["input_summary"]
        assert "Planned" in logs[0]["output_summary"]

    def test_all_content_types_accepted(self, test_db, mock_llm_response):
        """Plan with various PostType values should all parse correctly."""
        posts = []
        for i, ct in enumerate(PostType, start=1):
            posts.append(
                {
                    "topic": f"Topic {ct.value}",
                    "angle": "angle",
                    "hook": "hook",
                    "content_type": ct.value,
                    "priority": min(i, 5),
                    "trending_source": None,
                }
            )
        plan_json = _plan_json_with_posts(posts)
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=plan_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy())

        assert len(result.posts) == len(PostType)

    def test_single_post_plan(self, test_db, mock_llm_response):
        """LLM returning a plan with just one post should work fine."""
        single = _plan_json_with_posts(
            [
                {
                    "topic": "Solo topic",
                    "angle": "only angle",
                    "hook": "only hook",
                    "content_type": "question",
                    "priority": 1,
                    "trending_source": None,
                }
            ]
        )
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=single)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy())

        assert len(result.posts) == 1
        assert result.posts[0].topic == "Solo topic"

    def test_empty_posts_list_parses(self, test_db, mock_llm_response):
        """An empty posts list is valid JSON and should parse to empty ContentPlan."""
        empty_json = json.dumps({"posts": []})
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=empty_json)

        with patch("ortobahn.agents.base.call_llm", return_value=fake):
            result = agent.run(run_id="run-1", strategy=_make_strategy())

        assert isinstance(result, ContentPlan)
        assert len(result.posts) == 0

    def test_content_guidelines_in_prompt(self, test_db, mock_llm_response):
        """Content guidelines from strategy should appear in the prompt."""
        strategy = _make_strategy(content_guidelines="Never use corporate jargon. Be authentic.")
        agent = StrategistAgent(db=test_db, api_key="sk-ant-test")
        fake = mock_llm_response(text=VALID_PLAN_JSON)

        with patch("ortobahn.agents.base.call_llm", return_value=fake) as mock_call:
            agent.run(run_id="run-1", strategy=strategy)

        user_msg = mock_call.call_args.kwargs.get("user_message") or mock_call.call_args[0][1]
        assert "Never use corporate jargon" in user_msg
