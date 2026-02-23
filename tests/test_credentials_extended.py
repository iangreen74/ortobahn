"""Extended tests for encrypted per-tenant credential management.

Covers edge cases, error handling, build_platform_clients for each platform,
Fernet key derivation, corrupted data, and tenant isolation.
"""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import MagicMock, patch

import cryptography.fernet
import pytest

from ortobahn.credentials import (
    _derive_fernet_key,
    build_platform_clients,
    decrypt_credentials,
    encrypt_credentials,
    get_all_platform_credentials,
    get_platform_credentials,
    save_platform_credentials,
)

SECRET = "test-secret-key-for-credentials-testing"
SECRET_ALT = "different-secret-key-for-credentials-alt"


# ---------------------------------------------------------------------------
# Fernet key derivation
# ---------------------------------------------------------------------------


class TestFernetKeyDerivation:
    def test_key_is_32_bytes_base64(self):
        key = _derive_fernet_key(SECRET)
        # Fernet requires 32 bytes url-safe base64 encoded
        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32

    def test_same_input_same_key(self):
        k1 = _derive_fernet_key(SECRET)
        k2 = _derive_fernet_key(SECRET)
        assert k1 == k2

    def test_different_input_different_key(self):
        k1 = _derive_fernet_key(SECRET)
        k2 = _derive_fernet_key(SECRET_ALT)
        assert k1 != k2

    def test_key_derived_from_sha256(self):
        digest = hashlib.sha256(SECRET.encode()).digest()
        expected = base64.urlsafe_b64encode(digest)
        actual = _derive_fernet_key(SECRET)
        assert actual == expected

    def test_empty_string_key(self):
        # Should not raise -- empty string is still a valid input
        key = _derive_fernet_key("")
        assert len(base64.urlsafe_b64decode(key)) == 32


# ---------------------------------------------------------------------------
# Encrypt / Decrypt round-trip
# ---------------------------------------------------------------------------


class TestEncryptionExtended:
    def test_roundtrip_simple(self):
        creds = {"handle": "user.bsky.social", "app_password": "abcd-efgh"}
        encrypted = encrypt_credentials(creds, SECRET)
        assert decrypt_credentials(encrypted, SECRET) == creds

    def test_roundtrip_nested_dict(self):
        creds = {
            "tokens": {"access": "tok-1", "refresh": "tok-2"},
            "expiry": 1234567890,
        }
        encrypted = encrypt_credentials(creds, SECRET)
        assert decrypt_credentials(encrypted, SECRET) == creds

    def test_roundtrip_empty_dict(self):
        creds = {}
        encrypted = encrypt_credentials(creds, SECRET)
        assert decrypt_credentials(encrypted, SECRET) == creds

    def test_roundtrip_special_characters(self):
        creds = {"password": "p@$$w0rd!&<>\"'", "emoji": "test"}
        encrypted = encrypt_credentials(creds, SECRET)
        assert decrypt_credentials(encrypted, SECRET) == creds

    def test_roundtrip_unicode(self):
        creds = {"name": "Testname", "bio": "Hello World"}
        encrypted = encrypt_credentials(creds, SECRET)
        assert decrypt_credentials(encrypted, SECRET) == creds

    def test_encrypted_is_not_plaintext(self):
        creds = {"password": "super-secret-password"}
        encrypted = encrypt_credentials(creds, SECRET)
        assert "super-secret-password" not in encrypted
        assert "password" not in encrypted

    def test_same_plaintext_different_ciphertext(self):
        """Fernet uses random IV, so encrypting the same value twice gives different output."""
        creds = {"key": "value"}
        e1 = encrypt_credentials(creds, SECRET)
        e2 = encrypt_credentials(creds, SECRET)
        # Extremely unlikely to be the same due to random IV
        assert e1 != e2
        # But both decrypt to the same value
        assert decrypt_credentials(e1, SECRET) == creds
        assert decrypt_credentials(e2, SECRET) == creds

    def test_wrong_secret_raises_invalid_token(self):
        creds = {"key": "value"}
        encrypted = encrypt_credentials(creds, SECRET)
        with pytest.raises(cryptography.fernet.InvalidToken):
            decrypt_credentials(encrypted, SECRET_ALT)

    def test_corrupted_ciphertext_raises(self):
        creds = {"key": "value"}
        encrypted = encrypt_credentials(creds, SECRET)
        corrupted = encrypted[:10] + "XXXX" + encrypted[14:]
        with pytest.raises(cryptography.fernet.InvalidToken):
            decrypt_credentials(corrupted, SECRET)

    def test_empty_string_ciphertext_raises(self):
        with pytest.raises((cryptography.fernet.InvalidToken, ValueError)):
            decrypt_credentials("", SECRET)

    def test_non_base64_ciphertext_raises(self):
        with pytest.raises((cryptography.fernet.InvalidToken, ValueError)):
            decrypt_credentials("not-valid-fernet-token!!!", SECRET)

    def test_truncated_ciphertext_raises(self):
        creds = {"key": "value"}
        encrypted = encrypt_credentials(creds, SECRET)
        truncated = encrypted[:20]
        with pytest.raises(cryptography.fernet.InvalidToken):
            decrypt_credentials(truncated, SECRET)

    def test_large_credentials_roundtrip(self):
        creds = {f"key_{i}": f"value_{i}" * 100 for i in range(50)}
        encrypted = encrypt_credentials(creds, SECRET)
        assert decrypt_credentials(encrypted, SECRET) == creds


# ---------------------------------------------------------------------------
# Platform credential storage (DB operations)
# ---------------------------------------------------------------------------


class TestPlatformCredentialStorage:
    def test_save_returns_id(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        cred_id = save_platform_credentials(test_db, "c1", "bluesky", {"handle": "x"}, SECRET)
        assert len(cred_id) > 0

    def test_upsert_returns_existing_id(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        id1 = save_platform_credentials(test_db, "c1", "bluesky", {"handle": "old"}, SECRET)
        id2 = save_platform_credentials(test_db, "c1", "bluesky", {"handle": "new"}, SECRET)
        assert id1 == id2

        result = get_platform_credentials(test_db, "c1", "bluesky", SECRET)
        assert result["handle"] == "new"

    def test_save_multiple_platforms(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(test_db, "c1", "bluesky", {"handle": "bsky"}, SECRET)
        save_platform_credentials(test_db, "c1", "twitter", {"api_key": "tw"}, SECRET)
        save_platform_credentials(test_db, "c1", "linkedin", {"access_token": "li"}, SECRET)

        all_creds = get_all_platform_credentials(test_db, "c1", SECRET)
        assert len(all_creds) == 3
        assert all_creds["bluesky"]["handle"] == "bsky"
        assert all_creds["twitter"]["api_key"] == "tw"
        assert all_creds["linkedin"]["access_token"] == "li"

    def test_get_nonexistent_returns_none(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        result = get_platform_credentials(test_db, "c1", "bluesky", SECRET)
        assert result is None

    def test_get_all_empty_returns_empty_dict(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        all_creds = get_all_platform_credentials(test_db, "c1", SECRET)
        assert all_creds == {}


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_client_a_cannot_see_client_b_creds(self, test_db):
        test_db.create_client({"id": "a", "name": "A"})
        test_db.create_client({"id": "b", "name": "B"})

        save_platform_credentials(test_db, "a", "bluesky", {"handle": "a.bsky"}, SECRET)
        save_platform_credentials(test_db, "b", "bluesky", {"handle": "b.bsky"}, SECRET)

        a_creds = get_platform_credentials(test_db, "a", "bluesky", SECRET)
        b_creds = get_platform_credentials(test_db, "b", "bluesky", SECRET)
        assert a_creds["handle"] == "a.bsky"
        assert b_creds["handle"] == "b.bsky"

    def test_get_all_scoped_to_client(self, test_db):
        test_db.create_client({"id": "a", "name": "A"})
        test_db.create_client({"id": "b", "name": "B"})

        save_platform_credentials(test_db, "a", "bluesky", {"handle": "a"}, SECRET)
        save_platform_credentials(test_db, "b", "twitter", {"key": "b"}, SECRET)

        a_all = get_all_platform_credentials(test_db, "a", SECRET)
        b_all = get_all_platform_credentials(test_db, "b", SECRET)
        assert "bluesky" in a_all
        assert "twitter" not in a_all
        assert "twitter" in b_all
        assert "bluesky" not in b_all

    def test_update_one_client_does_not_affect_other(self, test_db):
        test_db.create_client({"id": "a", "name": "A"})
        test_db.create_client({"id": "b", "name": "B"})

        save_platform_credentials(test_db, "a", "bluesky", {"handle": "a-old"}, SECRET)
        save_platform_credentials(test_db, "b", "bluesky", {"handle": "b-old"}, SECRET)

        # Update only client A
        save_platform_credentials(test_db, "a", "bluesky", {"handle": "a-new"}, SECRET)

        a_creds = get_platform_credentials(test_db, "a", "bluesky", SECRET)
        b_creds = get_platform_credentials(test_db, "b", "bluesky", SECRET)
        assert a_creds["handle"] == "a-new"
        assert b_creds["handle"] == "b-old"


# ---------------------------------------------------------------------------
# build_platform_clients
# ---------------------------------------------------------------------------


def _mock_settings(**overrides):
    defaults = dict(
        bluesky_handle="",
        bluesky_app_password="",
        twitter_api_key="",
        twitter_api_secret="",
        twitter_access_token="",
        twitter_access_token_secret="",
        linkedin_access_token="",
        linkedin_person_urn="",
    )
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    settings.has_twitter.return_value = bool(
        defaults.get("twitter_api_key")
        and defaults.get("twitter_api_secret")
        and defaults.get("twitter_access_token")
        and defaults.get("twitter_access_token_secret")
    )
    settings.has_linkedin.return_value = bool(
        defaults.get("linkedin_access_token") and defaults.get("linkedin_person_urn")
    )
    return settings


class TestBuildPlatformClients:
    def test_no_creds_returns_all_none(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        settings = _mock_settings()
        result = build_platform_clients(test_db, "c1", SECRET, settings)
        assert result["bluesky"] is None
        assert result["twitter"] is None
        assert result["linkedin"] is None

    def test_bluesky_from_tenant_creds(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(
            test_db,
            "c1",
            "bluesky",
            {"handle": "tenant.bsky.social", "app_password": "pw123"},
            SECRET,
        )
        settings = _mock_settings()

        with patch("ortobahn.integrations.bluesky.BlueskyClient") as MockBS:
            result = build_platform_clients(test_db, "c1", SECRET, settings)
            MockBS.assert_called_once_with("tenant.bsky.social", "pw123")
            assert result["bluesky"] is not None

    def test_bluesky_fallback_to_global(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        settings = _mock_settings(
            bluesky_handle="global.bsky.social",
            bluesky_app_password="global-pw",
        )

        with patch("ortobahn.integrations.bluesky.BlueskyClient") as MockBS:
            result = build_platform_clients(test_db, "c1", SECRET, settings)
            MockBS.assert_called_once_with("global.bsky.social", "global-pw")
            assert result["bluesky"] is not None

    def test_twitter_from_tenant_creds(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(
            test_db,
            "c1",
            "twitter",
            {
                "api_key": "tk",
                "api_secret": "ts",
                "access_token": "at",
                "access_token_secret": "ats",
            },
            SECRET,
        )
        settings = _mock_settings()

        with patch("ortobahn.integrations.twitter.TwitterClient") as MockTW:
            result = build_platform_clients(test_db, "c1", SECRET, settings)
            MockTW.assert_called_once_with(
                api_key="tk",
                api_secret="ts",
                access_token="at",
                access_token_secret="ats",
            )
            assert result["twitter"] is not None

    def test_twitter_fallback_to_global(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        settings = _mock_settings(
            twitter_api_key="gk",
            twitter_api_secret="gs",
            twitter_access_token="gat",
            twitter_access_token_secret="gats",
        )

        with patch("ortobahn.integrations.twitter.TwitterClient") as MockTW:
            build_platform_clients(test_db, "c1", SECRET, settings)
            MockTW.assert_called_once_with(
                api_key="gk",
                api_secret="gs",
                access_token="gat",
                access_token_secret="gats",
            )

    def test_twitter_missing_partial_creds_returns_none(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        # Only api_key, missing rest
        save_platform_credentials(
            test_db,
            "c1",
            "twitter",
            {"api_key": "tk", "api_secret": "", "access_token": "", "access_token_secret": ""},
            SECRET,
        )
        settings = _mock_settings()

        result = build_platform_clients(test_db, "c1", SECRET, settings)
        assert result["twitter"] is None

    def test_linkedin_from_tenant_creds(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(
            test_db,
            "c1",
            "linkedin",
            {"access_token": "li-tok", "person_urn": "urn:li:person:123"},
            SECRET,
        )
        settings = _mock_settings()

        with patch("ortobahn.integrations.linkedin.LinkedInClient") as MockLI:
            result = build_platform_clients(test_db, "c1", SECRET, settings)
            MockLI.assert_called_once_with(
                access_token="li-tok",
                person_urn="urn:li:person:123",
            )
            assert result["linkedin"] is not None

    def test_linkedin_fallback_to_global(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        settings = _mock_settings(
            linkedin_access_token="g-li",
            linkedin_person_urn="urn:li:person:global",
        )

        with patch("ortobahn.integrations.linkedin.LinkedInClient") as MockLI:
            build_platform_clients(test_db, "c1", SECRET, settings)
            MockLI.assert_called_once_with(
                access_token="g-li",
                person_urn="urn:li:person:global",
            )

    def test_linkedin_missing_person_urn_returns_none(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(
            test_db,
            "c1",
            "linkedin",
            {"access_token": "tok", "person_urn": ""},
            SECRET,
        )
        settings = _mock_settings()

        result = build_platform_clients(test_db, "c1", SECRET, settings)
        assert result["linkedin"] is None

    def test_tenant_creds_override_global(self, test_db):
        """Tenant creds take priority over global settings."""
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(
            test_db,
            "c1",
            "bluesky",
            {"handle": "tenant.bsky.social", "app_password": "tenant-pw"},
            SECRET,
        )
        settings = _mock_settings(
            bluesky_handle="global.bsky.social",
            bluesky_app_password="global-pw",
        )

        with patch("ortobahn.integrations.bluesky.BlueskyClient") as MockBS:
            build_platform_clients(test_db, "c1", SECRET, settings)
            MockBS.assert_called_once_with("tenant.bsky.social", "tenant-pw")

    def test_all_platforms_configured(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(test_db, "c1", "bluesky", {"handle": "h", "app_password": "p"}, SECRET)
        save_platform_credentials(
            test_db,
            "c1",
            "twitter",
            {
                "api_key": "k",
                "api_secret": "s",
                "access_token": "at",
                "access_token_secret": "ats",
            },
            SECRET,
        )
        save_platform_credentials(
            test_db,
            "c1",
            "linkedin",
            {"access_token": "lt", "person_urn": "urn:li:person:1"},
            SECRET,
        )
        settings = _mock_settings()

        with (
            patch("ortobahn.integrations.bluesky.BlueskyClient"),
            patch("ortobahn.integrations.twitter.TwitterClient"),
            patch("ortobahn.integrations.linkedin.LinkedInClient"),
        ):
            result = build_platform_clients(test_db, "c1", SECRET, settings)
            assert result["bluesky"] is not None
            assert result["twitter"] is not None
            assert result["linkedin"] is not None


# ---------------------------------------------------------------------------
# Credential rotation (last_rotated_at updated on upsert)
# ---------------------------------------------------------------------------


class TestCredentialRotation:
    def test_upsert_updates_last_rotated(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        save_platform_credentials(test_db, "c1", "bluesky", {"handle": "v1"}, SECRET)
        row1 = test_db.fetchone(
            "SELECT last_rotated_at FROM platform_credentials WHERE client_id='c1' AND platform='bluesky'"
        )
        ts1 = row1["last_rotated_at"]

        # Upsert
        save_platform_credentials(test_db, "c1", "bluesky", {"handle": "v2"}, SECRET)
        row2 = test_db.fetchone(
            "SELECT last_rotated_at FROM platform_credentials WHERE client_id='c1' AND platform='bluesky'"
        )
        ts2 = row2["last_rotated_at"]
        # last_rotated_at should be updated (or equal if instant)
        assert ts2 >= ts1


# ---------------------------------------------------------------------------
# Different encryption keys per environment
# ---------------------------------------------------------------------------


class TestKeyIsolation:
    def test_prod_key_cannot_read_staging_creds(self, test_db):
        test_db.create_client({"id": "c1", "name": "C1"})
        prod_key = "production-secret-key-xxxxxxxxxxxxx"
        staging_key = "staging-secret-key-xxxxxxxxxxxxxxx"

        save_platform_credentials(test_db, "c1", "bluesky", {"handle": "h"}, staging_key)

        # Trying to read with a different key should raise
        with pytest.raises(cryptography.fernet.InvalidToken):
            get_platform_credentials(test_db, "c1", "bluesky", prod_key)
