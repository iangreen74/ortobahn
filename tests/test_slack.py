"""Comprehensive tests for the Slack webhook integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ortobahn.integrations.slack import (
    _alert_cooldowns,
    clear_alert_cooldowns,
    format_deploy_alert,
    format_sre_alert,
    format_watchdog_alert,
    send_slack_message,
    send_slack_message_deduped,
)


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    """Reset deduplication state before and after every test."""
    clear_alert_cooldowns()
    yield
    clear_alert_cooldowns()


# ---------------------------------------------------------------------------
# send_slack_message
# ---------------------------------------------------------------------------


class TestSendSlackMessage:
    @patch("ortobahn.integrations.slack.requests.post")
    def test_send_slack_message_success(self, mock_post):
        """200 response returns True."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = send_slack_message("https://hooks.slack.com/test", "hello")

        assert result is True
        mock_post.assert_called_once_with(
            "https://hooks.slack.com/test",
            json={"text": "hello"},
            timeout=10,
        )

    @patch("ortobahn.integrations.slack.requests.post")
    def test_send_slack_message_failure(self, mock_post):
        """Exception from requests.post returns False."""
        mock_post.side_effect = ConnectionError("network down")

        result = send_slack_message("https://hooks.slack.com/test", "hello")

        assert result is False

    def test_send_slack_message_empty_url(self):
        """Empty webhook_url returns False without making any HTTP call."""
        result = send_slack_message("", "hello")
        assert result is False

    @patch("ortobahn.integrations.slack.requests.post")
    def test_send_slack_message_http_error(self, mock_post):
        """Non-200 response (raise_for_status raises) returns False."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        mock_post.return_value = mock_resp

        result = send_slack_message("https://hooks.slack.com/test", "hello")
        assert result is False


# ---------------------------------------------------------------------------
# send_slack_message_deduped
# ---------------------------------------------------------------------------


class TestSendSlackMessageDeduped:
    @patch("ortobahn.integrations.slack.requests.post")
    def test_deduped_sends_first_time(self, mock_post):
        """First call with a given fingerprint sends the message."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert!",
            fingerprint="fp-1",
            cooldown_minutes=60,
        )

        assert result is True
        mock_post.assert_called_once()

    @patch("ortobahn.integrations.slack.requests.post")
    def test_deduped_suppresses_within_cooldown(self, mock_post):
        """Second call within cooldown window is suppressed."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        # First call succeeds
        send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert!",
            fingerprint="fp-dup",
            cooldown_minutes=60,
        )
        mock_post.reset_mock()

        # Second call (within cooldown) should be suppressed
        result = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert again!",
            fingerprint="fp-dup",
            cooldown_minutes=60,
        )

        assert result is False
        mock_post.assert_not_called()

    @patch("ortobahn.integrations.slack.requests.post")
    def test_deduped_sends_after_cooldown(self, mock_post):
        """After the cooldown expires, the message is sent again."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        # First call
        send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert!",
            fingerprint="fp-expire",
            cooldown_minutes=5,
        )

        # Manually backdate the cooldown timestamp to simulate passage of time
        _alert_cooldowns["fp-expire"] = datetime.now(timezone.utc) - timedelta(minutes=10)

        mock_post.reset_mock()

        # This call should go through because the cooldown has expired
        result = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert again!",
            fingerprint="fp-expire",
            cooldown_minutes=5,
        )

        assert result is True
        mock_post.assert_called_once()

    @patch("ortobahn.integrations.slack.requests.post")
    def test_deduped_different_fingerprints_independent(self, mock_post):
        """Different fingerprints do not suppress each other."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        r1 = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert A",
            fingerprint="fp-a",
            cooldown_minutes=60,
        )
        r2 = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert B",
            fingerprint="fp-b",
            cooldown_minutes=60,
        )

        assert r1 is True
        assert r2 is True
        assert mock_post.call_count == 2

    @patch("ortobahn.integrations.slack.requests.post")
    def test_deduped_failed_send_does_not_set_cooldown(self, mock_post):
        """If the underlying send fails, no cooldown entry is recorded."""
        mock_post.side_effect = ConnectionError("network down")

        result = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert!",
            fingerprint="fp-fail",
            cooldown_minutes=60,
        )

        assert result is False
        assert "fp-fail" not in _alert_cooldowns


# ---------------------------------------------------------------------------
# clear_alert_cooldowns
# ---------------------------------------------------------------------------


class TestClearAlertCooldowns:
    @patch("ortobahn.integrations.slack.requests.post")
    def test_clear_alert_cooldowns(self, mock_post):
        """clear_alert_cooldowns resets state, allowing re-sending."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        # Send first
        send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert!",
            fingerprint="fp-clear",
            cooldown_minutes=60,
        )
        mock_post.reset_mock()

        # Clear cooldowns
        clear_alert_cooldowns()
        assert len(_alert_cooldowns) == 0

        # Now re-sending with same fingerprint should work
        result = send_slack_message_deduped(
            "https://hooks.slack.com/test",
            "alert!",
            fingerprint="fp-clear",
            cooldown_minutes=60,
        )
        assert result is True
        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# format_sre_alert
# ---------------------------------------------------------------------------


class TestFormatSREAlert:
    def test_format_sre_alert_healthy(self):
        result = format_sre_alert("healthy", [], [])
        assert ":white_check_mark:" in result
        assert "HEALTHY" in result

    def test_format_sre_alert_degraded(self):
        result = format_sre_alert("degraded", [], [])
        assert ":warning:" in result
        assert "DEGRADED" in result

    def test_format_sre_alert_critical(self):
        result = format_sre_alert("critical", [], [])
        assert ":rotating_light:" in result
        assert "CRITICAL" in result

    def test_format_sre_alert_unknown_status(self):
        result = format_sre_alert("unknown", [], [])
        assert ":question:" in result
        assert "UNKNOWN" in result

    def test_format_sre_alert_with_alerts_and_recommendations(self):
        """Full report with alerts (dict style) and recommendations."""
        alerts = [
            {"severity": "critical", "component": "api", "message": "Latency spike"},
            {"severity": "warning", "component": "db", "message": "Slow queries"},
        ]
        recommendations = ["Restart API pods", "Optimize DB indexes"]

        result = format_sre_alert("degraded", alerts, recommendations)

        # Status line
        assert ":warning:" in result
        assert "DEGRADED" in result

        # Alerts section
        assert "*Alerts:*" in result
        assert "[api]" in result
        assert "Latency spike" in result
        assert "[db]" in result
        assert "Slow queries" in result

        # Alert severity emojis
        assert ":rotating_light:" in result  # critical alert
        # The :warning: appears for both the status and the warning alert

        # Recommendations section
        assert "*Recommendations:*" in result
        assert "Restart API pods" in result
        assert "Optimize DB indexes" in result

    def test_format_sre_alert_with_object_alerts(self):
        """Alerts can also be objects with attribute access."""
        alert = MagicMock()
        alert.severity = "info"
        alert.component = "scheduler"
        alert.message = "Running smoothly"

        result = format_sre_alert("healthy", [alert], [])

        assert ":information_source:" in result
        assert "[scheduler]" in result
        assert "Running smoothly" in result


# ---------------------------------------------------------------------------
# format_watchdog_alert
# ---------------------------------------------------------------------------


class TestFormatWatchdogAlert:
    def test_format_watchdog_alert_ok(self):
        """No critical or warning findings => OK status."""
        finding = MagicMock()
        finding.severity = "ok"
        finding.probe = "health"
        finding.detail = "all good"
        finding.client_id = None

        result = format_watchdog_alert([finding], [])
        assert ":white_check_mark:" in result
        assert "OK" in result

    def test_format_watchdog_alert_warning(self):
        finding = MagicMock()
        finding.severity = "warning"
        finding.probe = "latency"
        finding.detail = "P95 > 500ms"
        finding.client_id = "client-1"

        result = format_watchdog_alert([finding], [])
        assert ":warning:" in result
        assert "WARNING" in result
        assert "[latency]" in result
        assert "(client: client-1)" in result
        assert "P95 > 500ms" in result

    def test_format_watchdog_alert_critical(self):
        finding = MagicMock()
        finding.severity = "critical"
        finding.probe = "disk"
        finding.detail = "90% full"
        finding.client_id = None

        result = format_watchdog_alert([finding], [])
        assert ":rotating_light:" in result
        assert "CRITICAL" in result

    def test_format_watchdog_alert_with_remediations(self):
        finding = MagicMock()
        finding.severity = "ok"
        finding.probe = "test"
        finding.detail = "fine"
        finding.client_id = None

        remediation_ok = MagicMock()
        remediation_ok.success = True
        remediation_ok.action = "Restarted service"
        remediation_ok.verified = True

        remediation_fail = MagicMock()
        remediation_fail.success = False
        remediation_fail.action = "Clear cache"
        remediation_fail.verified = False

        result = format_watchdog_alert([finding], [remediation_ok, remediation_fail])
        assert "*Auto-Remediations:*" in result
        assert ":white_check_mark:" in result
        assert "Restarted service" in result
        assert "(verified)" in result
        assert ":x:" in result
        assert "Clear cache" in result
        assert "(verification failed)" in result


# ---------------------------------------------------------------------------
# format_deploy_alert
# ---------------------------------------------------------------------------


class TestFormatDeployAlert:
    def test_deployed_status(self):
        result = format_deploy_alert("abc1234567", "production", "deployed")
        assert ":rocket:" in result
        assert "DEPLOYED" in result
        assert "`abc1234`" in result
        assert "production" in result

    def test_validated_status(self):
        result = format_deploy_alert("def5678", "staging", "validated")
        assert ":white_check_mark:" in result
        assert "VALIDATED" in result

    def test_rolled_back_status(self):
        result = format_deploy_alert("ghi9012", "production", "rolled_back")
        assert ":rotating_light:" in result
        assert "ROLLED_BACK" in result

    def test_smoke_failed_status(self):
        result = format_deploy_alert("jkl3456", "staging", "smoke_failed")
        assert ":x:" in result
        assert "SMOKE_FAILED" in result

    def test_unknown_status(self):
        result = format_deploy_alert("mno7890", "dev", "building")
        assert ":gear:" in result
        assert "BUILDING" in result

    def test_with_detail(self):
        result = format_deploy_alert("abc1234567", "prod", "deployed", detail="Rollout 100%")
        assert "Rollout 100%" in result

    def test_without_detail(self):
        result = format_deploy_alert("abc1234567", "prod", "deployed")
        lines = result.strip().split("\n")
        # Should have status line + environment + SHA (3 lines, no detail)
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# format_pipeline_alert (only if it exists)
# ---------------------------------------------------------------------------


class TestFormatPipelineAlert:
    def test_format_pipeline_alert_exists(self):
        """Verify format_pipeline_alert is importable if it exists."""
        try:
            from ortobahn.integrations.slack import format_pipeline_alert

            # If it exists, do a basic call
            result = format_pipeline_alert("run-123", "completed", 5)
            assert isinstance(result, str)
        except ImportError:
            pytest.skip("format_pipeline_alert not defined in slack module")
