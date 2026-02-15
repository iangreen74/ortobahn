"""Tests for LLM wrapper and JSON parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.llm import LLMResponse, call_llm, parse_json_response
from ortobahn.models import Strategy


class TestParseJsonResponse:
    def test_clean_json(self):
        text = '{"themes": ["AI"], "tone": "bold", "goals": ["grow"], "content_guidelines": "ok", "posting_frequency": "daily", "valid_until": "2026-03-01T00:00:00"}'
        result = parse_json_response(text, Strategy)
        assert result.themes == ["AI"]

    def test_markdown_fenced_json(self):
        text = '```json\n{"themes": ["AI"], "tone": "bold", "goals": ["grow"], "content_guidelines": "ok", "posting_frequency": "daily", "valid_until": "2026-03-01T00:00:00"}\n```'
        result = parse_json_response(text, Strategy)
        assert result.themes == ["AI"]

    def test_json_with_preamble(self):
        text = 'Here is the strategy:\n{"themes": ["AI"], "tone": "bold", "goals": ["grow"], "content_guidelines": "ok", "posting_frequency": "daily", "valid_until": "2026-03-01T00:00:00"}'
        result = parse_json_response(text, Strategy)
        assert result.themes == ["AI"]

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON found"):
            parse_json_response("no json here at all", Strategy)

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_json_response('{"bad": "data"}', Strategy)


class TestCallLLM:
    @patch("ortobahn.llm.anthropic.Anthropic")
    def test_successful_call(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello"

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 100
        mock_client.messages.create.return_value = mock_response

        result = call_llm("system", "user", api_key="sk-ant-test")
        assert isinstance(result, LLMResponse)
        assert result.text == "Hello"
        assert result.input_tokens == 50
        assert result.thinking == ""

    @patch("ortobahn.llm.time.sleep")
    @patch("ortobahn.llm.anthropic.Anthropic")
    def test_retry_on_rate_limit(self, mock_anthropic_cls, mock_sleep):
        import anthropic

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "OK"

        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 20

        # First call: rate limit, second call: success
        mock_client.messages.create.side_effect = [
            anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            ),
            mock_response,
        ]

        result = call_llm("system", "user", api_key="sk-ant-test", retries=2)
        assert result.text == "OK"
        assert mock_sleep.called

    @patch("ortobahn.llm.anthropic.Anthropic")
    def test_call_with_thinking(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Let me reason about this..."

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '{"result": "deep thought"}'

        mock_response = MagicMock()
        mock_response.content = [thinking_block, text_block]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 500
        # Thinking path uses streaming
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_message.return_value = mock_response
        mock_client.messages.stream.return_value = mock_stream

        result = call_llm("system", "user", api_key="sk-ant-test", thinking_budget=5000)
        assert result.text == '{"result": "deep thought"}'
        assert result.thinking == "Let me reason about this..."
        assert result.input_tokens == 200

        # Verify thinking was passed to API via stream
        call_kwargs = mock_client.messages.stream.call_args
        assert call_kwargs.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 5000}
        assert call_kwargs.kwargs["max_tokens"] == 4096 + 5000

    @patch("ortobahn.llm.time.sleep")
    @patch("ortobahn.llm.anthropic.Anthropic")
    def test_exhausted_retries(self, mock_anthropic_cls, mock_sleep):
        import anthropic

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )

        with pytest.raises(RuntimeError, match="failed after all retries"):
            call_llm("system", "user", api_key="sk-ant-test", retries=2)
