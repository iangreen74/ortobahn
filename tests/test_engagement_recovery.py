"""Tests for engagement agent error recovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.agents.engagement import EngagementAgent, EngagementReply
from ortobahn.circuit_breaker import CircuitOpenError, clear_registry
from ortobahn.db import Database
from ortobahn.publish_recovery import ErrorCategory


def _make_reply():
    return EngagementReply(
        notification_uri="at://did:plc:123/post/456",
        notification_text="Great post!",
        reply_text="Thanks for your feedback!",
        confidence=0.9,
        reasoning="Relevant mention",
    )


class TestReplyRetry:
    def setup_method(self):
        clear_registry()

    def test_successful_reply_no_retry(self, tmp_path):
        db = Database(tmp_path / "eng.db")
        mock_bs = MagicMock()
        agent = EngagementAgent(db, "sk-ant-test", bluesky_client=mock_bs)
        agent._post_reply = MagicMock(return_value="at://reply/123")

        result = agent._post_reply_with_retry(_make_reply())
        assert result == "at://reply/123"
        assert agent._post_reply.call_count == 1
        db.close()

    def test_transient_error_retries(self, tmp_path):
        db = Database(tmp_path / "eng2.db")
        mock_bs = MagicMock()
        agent = EngagementAgent(db, "sk-ant-test", bluesky_client=mock_bs)

        # First call fails with transient error, second succeeds
        agent._post_reply = MagicMock(
            side_effect=[ConnectionError("timeout"), "at://reply/ok"]
        )

        with patch("ortobahn.agents.engagement.time.sleep"):
            result = agent._post_reply_with_retry(_make_reply(), max_retries=2)

        assert result == "at://reply/ok"
        assert agent._post_reply.call_count == 2
        db.close()

    def test_auth_error_triggers_relogin(self, tmp_path):
        db = Database(tmp_path / "eng3.db")
        mock_bs = MagicMock()
        agent = EngagementAgent(db, "sk-ant-test", bluesky_client=mock_bs)

        # First call fails with auth error
        auth_err = Exception("401 Unauthorized")
        agent._post_reply = MagicMock(
            side_effect=[auth_err, "at://reply/ok"]
        )

        with patch("ortobahn.publish_recovery.PublishErrorClassifier.classify_error", return_value=ErrorCategory.AUTH):
            result = agent._post_reply_with_retry(_make_reply())

        mock_bs.login.assert_called_once_with(force=True)
        assert result == "at://reply/ok"
        db.close()

    def test_circuit_breaker_stops_replies(self, tmp_path):
        db = Database(tmp_path / "eng4.db")
        mock_bs = MagicMock()
        agent = EngagementAgent(db, "sk-ant-test", bluesky_client=mock_bs)

        # Trip the circuit breaker
        from ortobahn.circuit_breaker import get_breaker
        breaker = get_breaker("bluesky:engagement", failure_threshold=1)
        breaker.record_failure()

        with pytest.raises(CircuitOpenError):
            agent._post_reply_with_retry(_make_reply())
        db.close()


class TestEngagementRunWithRecovery:
    def setup_method(self):
        clear_registry()

    def test_circuit_open_breaks_reply_loop(self, tmp_path):
        """When circuit breaker opens, remaining replies are skipped."""
        db = Database(tmp_path / "engrun.db")
        mock_bs = MagicMock()
        agent = EngagementAgent(db, "sk-ant-test", bluesky_client=mock_bs)

        # Mock notifications and drafting
        agent._fetch_notifications = MagicMock(return_value=[{"uri": "1"}, {"uri": "2"}])
        agent._filter_already_replied = MagicMock(side_effect=lambda n, c: n)
        agent._draft_replies = MagicMock(return_value=[_make_reply(), _make_reply()])

        # Trip the breaker before running
        from ortobahn.circuit_breaker import get_breaker
        breaker = get_breaker("bluesky:engagement", failure_threshold=1)
        breaker.record_failure()

        result = agent.run(run_id="run-1", client_id="default")
        assert any("Circuit breaker" in e for e in result.errors)
        assert result.replies_posted == 0
        db.close()
