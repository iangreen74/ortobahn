"""Tests for the Preflight Intelligence system."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from ortobahn.models import PreflightSeverity
from ortobahn.preflight import (
    check_budget_headroom,
    check_dns_for_urls,
    check_platform_credentials,
    resolve_host,
    run_pipeline_preflight,
)

# ---------------------------------------------------------------------------
# resolve_host
# ---------------------------------------------------------------------------


class TestResolveHost:
    def test_known_host_succeeds(self) -> None:
        """A call that returns results should return True."""
        with patch("ortobahn.preflight.socket.getaddrinfo", return_value=[(None,)]):
            assert resolve_host("example.com") is True

    def test_nonexistent_host_fails(self) -> None:
        """socket.gaierror should map to False."""
        with patch(
            "ortobahn.preflight.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            assert resolve_host("nonexistent.invalid") is False

    def test_timeout_fails(self) -> None:
        """An OSError (timeout) should map to False."""
        with patch(
            "ortobahn.preflight.socket.getaddrinfo",
            side_effect=OSError("timed out"),
        ):
            assert resolve_host("slow.example.com", timeout=0.1) is False


# ---------------------------------------------------------------------------
# check_dns_for_urls
# ---------------------------------------------------------------------------


class TestCheckDnsForUrls:
    def test_empty_list(self) -> None:
        assert check_dns_for_urls([]) == []

    def test_unresolvable_domain(self) -> None:
        with patch("ortobahn.preflight.resolve_host", return_value=False):
            issues = check_dns_for_urls(["https://badhost.invalid/path"])
        assert len(issues) == 1
        assert issues[0].severity == PreflightSeverity.WARNING
        assert "badhost.invalid" in issues[0].message

    def test_deduplication(self) -> None:
        """Same host appearing in two URLs should only be checked once."""
        call_count = 0

        def counting_resolve(host: str, timeout: float = 5.0) -> bool:
            nonlocal call_count
            call_count += 1
            return True

        with patch("ortobahn.preflight.resolve_host", side_effect=counting_resolve):
            issues = check_dns_for_urls(
                [
                    "https://example.com/a",
                    "https://example.com/b",
                ]
            )
        assert call_count == 1
        assert issues == []

    def test_skips_localhost(self) -> None:
        """localhost and loopback addresses should be skipped entirely."""
        with patch("ortobahn.preflight.resolve_host") as mock_resolve:
            issues = check_dns_for_urls(
                [
                    "http://localhost:8000/api",
                    "http://127.0.0.1:3000/health",
                ]
            )
        mock_resolve.assert_not_called()
        assert issues == []


# ---------------------------------------------------------------------------
# check_budget_headroom
# ---------------------------------------------------------------------------


class TestCheckBudgetHeadroom:
    def test_unlimited_budget(self, test_db) -> None:
        """monthly_budget=0 means unlimited — no issues."""
        issues = check_budget_headroom(test_db, "default", 0.0)
        assert issues == []

    def test_budget_exhausted(self, test_db) -> None:
        with patch.object(test_db, "get_current_month_spend", return_value=100.0):
            issues = check_budget_headroom(test_db, "default", 100.0)
        assert len(issues) == 1
        assert issues[0].severity == PreflightSeverity.BLOCKING
        assert "exhausted" in issues[0].message.lower()

    def test_budget_low_warning(self, test_db) -> None:
        with patch.object(test_db, "get_current_month_spend", return_value=92.0):
            issues = check_budget_headroom(test_db, "default", 100.0)
        assert len(issues) == 1
        assert issues[0].severity == PreflightSeverity.WARNING

    def test_budget_healthy(self, test_db) -> None:
        with patch.object(test_db, "get_current_month_spend", return_value=50.0):
            issues = check_budget_headroom(test_db, "default", 100.0)
        assert issues == []


# ---------------------------------------------------------------------------
# check_platform_credentials
# ---------------------------------------------------------------------------


class TestCheckPlatformCredentials:
    @patch("ortobahn.healthcheck.check_bluesky")
    @patch("ortobahn.healthcheck.check_twitter")
    @patch("ortobahn.healthcheck.check_linkedin")
    @patch("ortobahn.healthcheck.check_config")
    def test_all_ok(
        self,
        mock_config,
        mock_linkedin,
        mock_twitter,
        mock_bluesky,
        test_settings,
    ) -> None:
        ok = MagicMock(ok=True, message="OK")
        mock_config.return_value = ok
        mock_bluesky.return_value = ok
        mock_twitter.return_value = ok
        mock_linkedin.return_value = ok

        issues = check_platform_credentials(test_settings)
        assert issues == []

    @patch("ortobahn.healthcheck.check_bluesky")
    @patch("ortobahn.healthcheck.check_twitter")
    @patch("ortobahn.healthcheck.check_linkedin")
    @patch("ortobahn.healthcheck.check_config")
    def test_config_failure_blocking(
        self,
        mock_config,
        mock_linkedin,
        mock_twitter,
        mock_bluesky,
        test_settings,
    ) -> None:
        mock_config.return_value = MagicMock(ok=False, message="ANTHROPIC_API_KEY is not set")
        ok = MagicMock(ok=True, message="OK")
        mock_bluesky.return_value = ok
        mock_twitter.return_value = ok
        mock_linkedin.return_value = ok

        issues = check_platform_credentials(test_settings)
        blockers = [i for i in issues if i.severity == PreflightSeverity.BLOCKING]
        assert len(blockers) == 1
        assert "config" in blockers[0].component


# ---------------------------------------------------------------------------
# run_pipeline_preflight (integration)
# ---------------------------------------------------------------------------


class TestRunPipelinePreflight:
    @patch("ortobahn.preflight.check_api_reachability", return_value=[])
    @patch("ortobahn.preflight.check_platform_credentials", return_value=[])
    def test_clean_pass(
        self,
        mock_creds,
        mock_api,
        test_settings,
        test_db,
    ) -> None:
        result = run_pipeline_preflight(test_settings, test_db, "default")
        assert result.passed is True
        assert result.issues == []
        assert result.duration_ms >= 0

    @patch("ortobahn.preflight.check_api_reachability", return_value=[])
    @patch("ortobahn.preflight.check_platform_credentials")
    def test_blocks_on_config_failure(
        self,
        mock_creds,
        mock_api,
        test_settings,
        test_db,
    ) -> None:
        from ortobahn.models import PreflightIssue

        mock_creds.return_value = [
            PreflightIssue(
                severity=PreflightSeverity.BLOCKING,
                component="config",
                message="ANTHROPIC_API_KEY is not set",
            )
        ]
        result = run_pipeline_preflight(test_settings, test_db, "default")
        assert result.passed is False
        assert len(result.blocking_issues) == 1
        assert result.blocking_issues[0].component == "config"
