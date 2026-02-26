"""Tests for AI Brand Interview Onboarding."""

from __future__ import annotations

import json

import pytest

from ortobahn.web.routes.onboard import (
    _build_summary,
    _get_interview_state,
    _parse_step_answer,
    _render_messages,
    STEP_QUESTIONS,
)


class TestParseStepAnswer:
    def test_step1_extracts_company_and_website(self):
        answers = {}
        result = _parse_step_answer(1, "Acme Corp acme.com", answers)
        assert result["company"] == "Acme Corp"
        assert result["website"] == "https://acme.com"

    def test_step1_company_only(self):
        answers = {}
        result = _parse_step_answer(1, "Acme Corp", answers)
        assert result["company"] == "Acme Corp"
        assert "website" not in result

    def test_step1_website_with_https(self):
        answers = {}
        result = _parse_step_answer(1, "Acme https://acme.com", answers)
        assert result["company"] == "Acme"
        assert result["website"] == "https://acme.com"

    def test_step2_stores_industry(self):
        answers = {}
        result = _parse_step_answer(2, "SaaS targeting developers", answers)
        assert result["industry"] == "SaaS targeting developers"
        assert result["target_audience"] == "SaaS targeting developers"

    def test_step3_stores_brand_voice(self):
        answers = {}
        result = _parse_step_answer(3, "casual and witty", answers)
        assert result["brand_voice"] == "casual and witty"

    def test_step4_stores_goals(self):
        answers = {}
        result = _parse_step_answer(4, "brand awareness and thought leadership", answers)
        assert result["goals"] == "brand awareness and thought leadership"


class TestBuildSummary:
    def test_full_summary(self):
        answers = {
            "company": "Acme Corp",
            "website": "https://acme.com",
            "industry": "SaaS",
            "target_audience": "developers",
            "brand_voice": "casual",
            "goals": "awareness",
        }
        summary = _build_summary(answers)
        assert "Acme Corp" in summary
        assert "acme.com" in summary
        assert "SaaS" in summary
        assert "developers" in summary
        assert "casual" in summary
        assert "awareness" in summary

    def test_empty_answers(self):
        assert _build_summary({}) == "No details collected yet."

    def test_partial_answers(self):
        answers = {"company": "Test"}
        summary = _build_summary(answers)
        assert "Test" in summary
        assert "Website" not in summary


class TestRenderMessages:
    def test_renders_ai_message(self):
        messages = [{"role": "ai", "text": "Hello!"}]
        html = _render_messages(messages, 1)
        assert "msg-ai" in html
        assert "Hello!" in html
        assert 'data-step="1"' in html

    def test_renders_user_message(self):
        messages = [{"role": "user", "text": "My company"}]
        html = _render_messages(messages, 2)
        assert "msg-user" in html
        assert "My company" in html

    def test_escapes_html(self):
        messages = [{"role": "ai", "text": "<script>alert('xss')</script>"}]
        html = _render_messages(messages, 1)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_includes_step_data(self):
        html = _render_messages([], 3)
        assert 'data-step="3"' in html


class TestStepQuestions:
    def test_all_five_steps_defined(self):
        for i in range(1, 6):
            assert i in STEP_QUESTIONS

    def test_step5_has_summary_placeholder(self):
        assert "{summary}" in STEP_QUESTIONS[5]


class TestInterviewState:
    def test_default_state(self):
        """Mock request with no cookie returns default state."""

        class FakeRequest:
            cookies = {}

        state = _get_interview_state(FakeRequest())
        assert state["step"] == 1
        assert state["answers"] == {}
        assert state["messages"] == []

    def test_reads_cookie(self):
        """State is read from cookie."""
        data = {"step": 3, "answers": {"company": "Test"}, "messages": []}

        class FakeRequest:
            cookies = {"ortobahn_interview": json.dumps(data)}

        state = _get_interview_state(FakeRequest())
        assert state["step"] == 3
        assert state["answers"]["company"] == "Test"

    def test_handles_corrupt_cookie(self):
        """Corrupt cookie returns default state."""

        class FakeRequest:
            cookies = {"ortobahn_interview": "not-json"}

        state = _get_interview_state(FakeRequest())
        assert state["step"] == 1


class TestInterviewCompletion:
    def test_creates_client_from_answers(self, test_db):
        """Verify that interview answers can create a valid client."""
        answers = {
            "company": "Interview Test Co",
            "website": "https://test.com",
            "industry": "SaaS",
            "target_audience": "developers",
            "brand_voice": "professional",
            "goals": "brand awareness",
        }

        from ortobahn.web.routes.onboard import _match_industry, _normalize_url

        trend_defaults = _match_industry(answers["industry"])

        client_id = test_db.create_client(
            {
                "name": answers["company"],
                "description": "Onboarded via AI brand interview",
                "industry": answers["industry"],
                "target_audience": answers["target_audience"],
                "brand_voice": answers["brand_voice"],
                "website": _normalize_url(answers["website"]),
                "status": "pending",
            }
        )

        test_db.update_client(
            client_id,
            {
                "news_category": trend_defaults["news_category"],
                "news_keywords": trend_defaults["news_keywords"],
            },
        )

        client = test_db.get_client(client_id)
        assert client is not None
        assert client["name"] == "Interview Test Co"
        assert client["industry"] == "SaaS"
        assert client["brand_voice"] == "professional"
        assert client["website"] == "https://test.com"
        assert client["news_category"] == "technology"
