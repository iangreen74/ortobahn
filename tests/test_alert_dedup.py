"""Tests for Slack alert deduplication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ortobahn.integrations.slack import (
    _alert_cooldowns,
    clear_alert_cooldowns,
    send_slack_message_deduped,
)


class TestAlertDeduplication:
    def setup_method(self):
        clear_alert_cooldowns()

    def teardown_method(self):
        clear_alert_cooldowns()

    @patch("ortobahn.integrations.slack.send_slack_message", return_value=True)
    def test_first_message_always_sent(self, mock_send):
        result = send_slack_message_deduped("https://hook", "alert!", "fp1")
        assert result is True
        mock_send.assert_called_once_with("https://hook", "alert!")

    @patch("ortobahn.integrations.slack.send_slack_message", return_value=True)
    def test_duplicate_within_cooldown_suppressed(self, mock_send):
        send_slack_message_deduped("https://hook", "alert!", "fp1", cooldown_minutes=60)
        result = send_slack_message_deduped("https://hook", "alert again!", "fp1", cooldown_minutes=60)
        assert result is False
        assert mock_send.call_count == 1

    @patch("ortobahn.integrations.slack.send_slack_message", return_value=True)
    def test_different_fingerprints_both_sent(self, mock_send):
        send_slack_message_deduped("https://hook", "alert A", "fp-a")
        send_slack_message_deduped("https://hook", "alert B", "fp-b")
        assert mock_send.call_count == 2

    @patch("ortobahn.integrations.slack.send_slack_message", return_value=True)
    def test_expired_cooldown_resends(self, mock_send):
        send_slack_message_deduped("https://hook", "alert!", "fp1", cooldown_minutes=60)
        # Manually backdate the cooldown entry
        _alert_cooldowns["fp1"] = datetime.now(timezone.utc) - timedelta(minutes=61)
        result = send_slack_message_deduped("https://hook", "alert!", "fp1", cooldown_minutes=60)
        assert result is True
        assert mock_send.call_count == 2

    @patch("ortobahn.integrations.slack.send_slack_message", return_value=False)
    def test_failed_send_does_not_set_cooldown(self, mock_send):
        result = send_slack_message_deduped("https://hook", "alert!", "fp1")
        assert result is False
        assert "fp1" not in _alert_cooldowns

    def test_clear_cooldowns(self):
        _alert_cooldowns["test"] = datetime.now(timezone.utc)
        clear_alert_cooldowns()
        assert len(_alert_cooldowns) == 0
