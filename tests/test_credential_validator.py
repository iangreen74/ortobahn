"""Tests for credential validation on save."""

from __future__ import annotations

from unittest.mock import patch

from ortobahn.credential_validator import _save_status, validate_credentials
from ortobahn.credentials import save_platform_credentials


class TestSaveStatus:
    def test_saves_valid(self, test_db):
        """Saves valid status to platform_credentials."""
        test_db.create_client({"id": "test_cred", "name": "Test"}, start_trial=False)
        save_platform_credentials(test_db, "test_cred", "bluesky", {"handle": "test.bsky"}, "secret")
        _save_status(test_db, "test_cred", "bluesky", {"status": "valid", "message": "OK"})
        row = test_db.fetchone(
            "SELECT credential_status, credential_status_message FROM platform_credentials "
            "WHERE client_id=? AND platform=?",
            ("test_cred", "bluesky"),
        )
        assert row["credential_status"] == "valid"
        assert row["credential_status_message"] == "OK"

    def test_saves_invalid(self, test_db):
        """Saves invalid status."""
        test_db.create_client({"id": "test_cred2", "name": "Test"}, start_trial=False)
        save_platform_credentials(test_db, "test_cred2", "twitter", {"api_key": "x"}, "secret")
        _save_status(test_db, "test_cred2", "twitter", {"status": "invalid", "message": "Bad token"})
        row = test_db.fetchone(
            "SELECT credential_status FROM platform_credentials WHERE client_id=? AND platform=?",
            ("test_cred2", "twitter"),
        )
        assert row["credential_status"] == "invalid"


class TestValidateCredentials:
    def test_no_credentials(self, test_db):
        """Returns invalid when no credentials stored."""
        test_db.create_client({"id": "empty_cred", "name": "Test"}, start_trial=False)
        result = validate_credentials(test_db, "empty_cred", "bluesky", "secret")
        assert result["status"] == "invalid"
        assert "No credentials" in result["message"]

    def test_unknown_platform(self, test_db):
        """Returns error for unknown platform."""
        result = validate_credentials(test_db, "x", "fakebook", "secret")
        assert result["status"] == "error"
        assert "Unknown" in result["message"]

    @patch("ortobahn.credential_validator._test_bluesky")
    def test_bluesky_valid(self, mock_test, test_db):
        """Bluesky validation calls test function."""
        mock_test.return_value = {"status": "valid", "message": "Logged in as test.bsky"}
        test_db.create_client({"id": "bs_test", "name": "Test"}, start_trial=False)
        save_platform_credentials(
            test_db, "bs_test", "bluesky", {"handle": "test.bsky", "app_password": "xxx"}, "secret"
        )
        result = validate_credentials(test_db, "bs_test", "bluesky", "secret")
        assert result["status"] == "valid"
        mock_test.assert_called_once()

    @patch("ortobahn.credential_validator._test_twitter")
    def test_twitter_valid(self, mock_test, test_db):
        """Twitter validation works."""
        mock_test.return_value = {"status": "valid", "message": "OK"}
        test_db.create_client({"id": "tw_test", "name": "Test"}, start_trial=False)
        save_platform_credentials(
            test_db,
            "tw_test",
            "twitter",
            {"api_key": "k", "api_secret": "s", "access_token": "t", "access_token_secret": "ts"},
            "secret",
        )
        result = validate_credentials(test_db, "tw_test", "twitter", "secret")
        assert result["status"] == "valid"

    @patch("ortobahn.credential_validator._test_linkedin")
    def test_linkedin_valid(self, mock_test, test_db):
        """LinkedIn validation works."""
        mock_test.return_value = {"status": "valid", "message": "Connected as Test"}
        test_db.create_client({"id": "li_test", "name": "Test"}, start_trial=False)
        save_platform_credentials(
            test_db, "li_test", "linkedin", {"access_token": "t", "person_urn": "urn:li:person:123"}, "secret"
        )
        result = validate_credentials(test_db, "li_test", "linkedin", "secret")
        assert result["status"] == "valid"

    def test_exception_caught(self, test_db):
        """Exceptions during validation are caught and returned as error."""
        test_db.create_client({"id": "err_test", "name": "Test"}, start_trial=False)
        save_platform_credentials(test_db, "err_test", "bluesky", {"handle": "x", "app_password": "y"}, "secret")
        with patch("ortobahn.credential_validator._test_bluesky", side_effect=RuntimeError("Network error")):
            result = validate_credentials(test_db, "err_test", "bluesky", "secret")
        assert result["status"] == "error"
        assert "Network error" in result["message"]


class TestPlatformTesters:
    def test_bluesky_missing_fields(self):
        """Bluesky rejects missing fields."""
        from ortobahn.credential_validator import _test_bluesky

        result = _test_bluesky({"handle": "", "app_password": ""})
        assert result["status"] == "invalid"

    def test_twitter_missing_fields(self):
        """Twitter rejects missing fields."""
        from ortobahn.credential_validator import _test_twitter

        result = _test_twitter({"api_key": "k"})
        assert result["status"] == "invalid"
        assert "Missing" in result["message"]

    def test_linkedin_missing_fields(self):
        """LinkedIn rejects missing fields."""
        from ortobahn.credential_validator import _test_linkedin

        result = _test_linkedin({"access_token": ""})
        assert result["status"] == "invalid"

    def test_reddit_missing_fields(self):
        """Reddit rejects missing fields."""
        from ortobahn.credential_validator import _test_reddit

        result = _test_reddit({})
        assert result["status"] == "invalid"

    def test_medium_missing_fields(self):
        """Medium rejects missing fields."""
        from ortobahn.credential_validator import _test_medium

        result = _test_medium({})
        assert result["status"] == "invalid"

    def test_substack_missing_fields(self):
        """Substack rejects missing fields."""
        from ortobahn.credential_validator import _test_substack

        result = _test_substack({})
        assert result["status"] == "invalid"
