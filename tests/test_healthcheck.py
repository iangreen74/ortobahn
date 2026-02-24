"""Tests for health check functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ortobahn.config import Settings
from ortobahn.healthcheck import (
    HealthResult,
    check_anthropic,
    check_bluesky,
    check_config,
    check_database,
    check_linkedin,
    check_twitter,
    run_all_checks,
)


class TestCheckConfig:
    def test_valid_config(self, test_settings):
        """Valid settings should return ok=True."""
        result = check_config(test_settings, require_bluesky=True)
        assert result.ok is True
        assert result.name == "config"
        assert "valid" in result.message.lower()

    def test_invalid_config(self):
        """Invalid settings should return ok=False with error details."""
        bad = Settings(anthropic_api_key="", bluesky_handle="", bluesky_app_password="")
        result = check_config(bad, require_bluesky=True)
        assert result.ok is False
        assert result.name == "config"
        assert "ANTHROPIC_API_KEY" in result.message

    def test_skip_bluesky_validation(self):
        """With require_bluesky=False, missing Bluesky creds should not cause failure."""
        s = Settings(anthropic_api_key="sk-ant-test")
        result = check_config(s, require_bluesky=False)
        assert result.ok is True


class TestCheckDatabase:
    def test_database_ok(self, test_settings, tmp_path):
        """check_database should return ok=True for a functional database."""
        test_settings.db_path = tmp_path / "test.db"
        test_settings.database_url = ""
        result = check_database(test_settings)
        assert result.ok is True
        assert result.name == "database"

    def test_database_error(self, test_settings):
        """check_database should return ok=False when database creation fails."""
        test_settings.database_url = "postgresql://invalid:invalid@localhost:1/nope"
        test_settings.db_path = None
        result = check_database(test_settings)
        assert result.ok is False
        assert "error" in result.message.lower() or "Error" in result.message


class TestCheckAnthropic:
    def test_missing_api_key(self):
        """Should return ok=False when API key is not set."""
        s = Settings(anthropic_api_key="")
        result = check_anthropic(s)
        assert result.ok is False
        assert "not set" in result.message.lower()

    def test_valid_api_key(self, test_settings):
        """Should return ok=True when the API call succeeds."""
        mock_client = MagicMock()
        with patch.dict("sys.modules", {"anthropic": MagicMock()}) as _:
            import anthropic as mock_anthropic

            mock_anthropic.Anthropic.return_value = mock_client
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = check_anthropic(test_settings)

        assert result.ok is True
        assert result.name == "anthropic"
        mock_client.models.list.assert_called_once()

    def test_api_error(self, test_settings):
        """Should return ok=False when the API call raises an exception."""
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("Unauthorized")
        mock_module = MagicMock()
        mock_module.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_module}):
            result = check_anthropic(test_settings)

        assert result.ok is False
        assert "Unauthorized" in result.message


class TestCheckBluesky:
    def test_missing_credentials(self):
        """Should return ok=False when Bluesky credentials are not set."""
        s = Settings(bluesky_handle="", bluesky_app_password="")
        result = check_bluesky(s)
        assert result.ok is False
        assert "not set" in result.message.lower()

    @patch("ortobahn.integrations.bluesky.BlueskyClient")
    def test_successful_login(self, mock_bsky_cls, test_settings):
        """Should return ok=True when Bluesky login succeeds."""
        mock_client = MagicMock()
        mock_bsky_cls.return_value = mock_client

        result = check_bluesky(test_settings)

        assert result.ok is True
        assert test_settings.bluesky_handle in result.message
        mock_client.login.assert_called_once()

    @patch("ortobahn.integrations.bluesky.BlueskyClient")
    def test_login_failure(self, mock_bsky_cls, test_settings):
        """Should return ok=False when Bluesky login fails."""
        mock_client = MagicMock()
        mock_client.login.side_effect = Exception("Bad credentials")
        mock_bsky_cls.return_value = mock_client

        result = check_bluesky(test_settings)

        assert result.ok is False
        assert "failed" in result.message.lower()


class TestCheckTwitter:
    def test_not_configured(self):
        """Should return ok=True with 'Not configured' when Twitter is not set up."""
        s = Settings(anthropic_api_key="sk-ant-test")
        result = check_twitter(s)
        assert result.ok is True
        assert "not configured" in result.message.lower()

    @patch("ortobahn.integrations.twitter.TwitterClient")
    def test_successful_auth(self, mock_twitter_cls):
        """Should return ok=True when Twitter auth succeeds."""
        s = Settings(
            anthropic_api_key="sk-ant-test",
            twitter_api_key="key",
            twitter_api_secret="secret",
            twitter_access_token="token",
            twitter_access_token_secret="tsecret",
        )
        mock_client = MagicMock()
        mock_twitter_cls.return_value = mock_client

        result = check_twitter(s)

        assert result.ok is True
        assert "authenticated" in result.message.lower()

    @patch("ortobahn.integrations.twitter.TwitterClient")
    def test_auth_failure(self, mock_twitter_cls):
        """Should return ok=False when Twitter auth fails."""
        s = Settings(
            anthropic_api_key="sk-ant-test",
            twitter_api_key="key",
            twitter_api_secret="secret",
            twitter_access_token="token",
            twitter_access_token_secret="tsecret",
        )
        mock_client = MagicMock()
        mock_client._get_client.side_effect = Exception("Auth error")
        mock_twitter_cls.return_value = mock_client

        result = check_twitter(s)

        assert result.ok is False


class TestCheckLinkedin:
    def test_not_configured(self):
        """Should return ok=True with 'Not configured' when LinkedIn is not set up."""
        s = Settings(anthropic_api_key="sk-ant-test")
        result = check_linkedin(s)
        assert result.ok is True
        assert "not configured" in result.message.lower()

    @patch("requests.get")
    def test_successful_auth(self, mock_get):
        """Should return ok=True when LinkedIn API returns 200."""
        s = Settings(
            anthropic_api_key="sk-ant-test",
            linkedin_access_token="token",
            linkedin_person_urn="urn:li:person:abc",
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = check_linkedin(s)

        assert result.ok is True
        assert "authenticated" in result.message.lower()

    @patch("requests.get")
    def test_auth_failure(self, mock_get):
        """Should return ok=False when LinkedIn API call fails."""
        s = Settings(
            anthropic_api_key="sk-ant-test",
            linkedin_access_token="token",
            linkedin_person_urn="urn:li:person:abc",
        )
        mock_get.side_effect = Exception("Network error")

        result = check_linkedin(s)

        assert result.ok is False
        assert "failed" in result.message.lower()


class TestRunAllChecks:
    @patch("ortobahn.healthcheck.check_reddit")
    @patch("ortobahn.healthcheck.check_linkedin")
    @patch("ortobahn.healthcheck.check_twitter")
    @patch("ortobahn.healthcheck.check_bluesky")
    @patch("ortobahn.healthcheck.check_anthropic")
    @patch("ortobahn.healthcheck.check_database")
    @patch("ortobahn.healthcheck.check_config")
    def test_returns_list_of_results(
        self,
        mock_config,
        mock_db,
        mock_anthropic,
        mock_bluesky,
        mock_twitter,
        mock_linkedin,
        mock_reddit,
        test_settings,
    ):
        """run_all_checks should return a list of HealthResult objects."""
        for mock_fn, name in [
            (mock_config, "config"),
            (mock_db, "database"),
            (mock_anthropic, "anthropic"),
            (mock_bluesky, "bluesky"),
            (mock_twitter, "twitter"),
            (mock_linkedin, "linkedin"),
            (mock_reddit, "reddit"),
        ]:
            mock_fn.return_value = HealthResult(name, True, "OK")

        results = run_all_checks(test_settings)

        assert len(results) == 7
        assert all(isinstance(r, HealthResult) for r in results)
        names = [r.name for r in results]
        assert "config" in names
        assert "database" in names
        assert "anthropic" in names
        assert "bluesky" in names
        assert "twitter" in names
        assert "linkedin" in names
        assert "reddit" in names
