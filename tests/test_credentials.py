"""Tests for encrypted credential management."""

from __future__ import annotations

import pytest

from ortobahn.credentials import (
    decrypt_credentials,
    encrypt_credentials,
    get_all_platform_credentials,
    get_platform_credentials,
    save_platform_credentials,
)

SECRET = "test-secret-key-for-credentials-testing"


class TestEncryption:
    def test_roundtrip(self):
        creds = {"handle": "test.bsky.social", "app_password": "xxxx-yyyy"}
        encrypted = encrypt_credentials(creds, SECRET)
        decrypted = decrypt_credentials(encrypted, SECRET)
        assert decrypted == creds

    def test_different_secrets_fail(self):
        creds = {"key": "value"}
        encrypted = encrypt_credentials(creds, SECRET)
        with pytest.raises(Exception):
            decrypt_credentials(encrypted, "wrong-secret")

    def test_encrypted_not_plaintext(self):
        creds = {"password": "super-secret"}
        encrypted = encrypt_credentials(creds, SECRET)
        assert "super-secret" not in encrypted


class TestPlatformCredentials:
    def test_save_and_get(self, test_db):
        test_db.create_client({"id": "client1", "name": "Client1"})
        creds = {"handle": "test.bsky.social", "app_password": "pw123"}

        save_platform_credentials(test_db, "client1", "bluesky", creds, SECRET)
        result = get_platform_credentials(test_db, "client1", "bluesky", SECRET)
        assert result == creds

    def test_get_nonexistent(self, test_db):
        test_db.create_client({"id": "client2", "name": "Client2"})
        result = get_platform_credentials(test_db, "client2", "bluesky", SECRET)
        assert result is None

    def test_upsert(self, test_db):
        test_db.create_client({"id": "client3", "name": "Client3"})
        creds_v1 = {"handle": "old.bsky.social", "app_password": "old"}
        creds_v2 = {"handle": "new.bsky.social", "app_password": "new"}

        save_platform_credentials(test_db, "client3", "bluesky", creds_v1, SECRET)
        save_platform_credentials(test_db, "client3", "bluesky", creds_v2, SECRET)

        result = get_platform_credentials(test_db, "client3", "bluesky", SECRET)
        assert result == creds_v2

    def test_get_all_platforms(self, test_db):
        test_db.create_client({"id": "client4", "name": "Client4"})
        save_platform_credentials(test_db, "client4", "bluesky", {"handle": "x"}, SECRET)
        save_platform_credentials(test_db, "client4", "twitter", {"api_key": "y"}, SECRET)

        all_creds = get_all_platform_credentials(test_db, "client4", SECRET)
        assert "bluesky" in all_creds
        assert "twitter" in all_creds
        assert all_creds["bluesky"]["handle"] == "x"
        assert all_creds["twitter"]["api_key"] == "y"

    def test_isolation_between_clients(self, test_db):
        test_db.create_client({"id": "clientA", "name": "A"})
        test_db.create_client({"id": "clientB", "name": "B"})
        save_platform_credentials(test_db, "clientA", "bluesky", {"handle": "a"}, SECRET)
        save_platform_credentials(test_db, "clientB", "bluesky", {"handle": "b"}, SECRET)

        result_a = get_platform_credentials(test_db, "clientA", "bluesky", SECRET)
        result_b = get_platform_credentials(test_db, "clientB", "bluesky", SECRET)
        assert result_a["handle"] == "a"
        assert result_b["handle"] == "b"
