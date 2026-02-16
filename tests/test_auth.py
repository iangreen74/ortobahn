"""Tests for authentication module."""

from __future__ import annotations

import time

from ortobahn.auth import (
    create_session_token,
    decode_session_token,
    generate_api_key,
    hash_api_key,
    key_prefix,
)


class TestApiKeys:
    def test_generate_key_format(self):
        key = generate_api_key()
        assert key.startswith("otb_")
        assert len(key) == 44  # "otb_" + 40 hex chars

    def test_generate_key_unique(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100

    def test_hash_deterministic(self):
        key = generate_api_key()
        assert hash_api_key(key) == hash_api_key(key)

    def test_hash_different_keys(self):
        k1 = generate_api_key()
        k2 = generate_api_key()
        assert hash_api_key(k1) != hash_api_key(k2)

    def test_key_prefix(self):
        key = "otb_abcdef1234567890abcdef1234567890abcdef12"
        assert key_prefix(key) == "otb_abcdef12"


class TestJWT:
    SECRET = "test-secret-key-1234567890"

    def test_create_and_decode(self):
        token = create_session_token("client-123", self.SECRET)
        result = decode_session_token(token, self.SECRET)
        assert result == "client-123"

    def test_wrong_secret(self):
        token = create_session_token("client-123", self.SECRET)
        result = decode_session_token(token, "wrong-secret")
        assert result is None

    def test_expired_token(self):
        token = create_session_token("client-123", self.SECRET, expires_hours=0)
        # Token with 0 hours expiry should be expired immediately or very soon
        time.sleep(1)
        result = decode_session_token(token, self.SECRET)
        assert result is None

    def test_invalid_token(self):
        result = decode_session_token("not.a.valid.jwt", self.SECRET)
        assert result is None


class TestDbApiKeys:
    def test_create_and_retrieve(self, test_db):
        test_db.create_client({"id": "testclient", "name": "Test"})
        key_id = test_db.create_api_key("testclient", "hash123", "otb_abc", "mykey")
        assert key_id

        keys = test_db.get_api_keys_for_client("testclient")
        assert len(keys) == 1
        assert keys[0]["key_prefix"] == "otb_abc"
        assert keys[0]["name"] == "mykey"
        assert keys[0]["active"] == 1

    def test_revoke(self, test_db):
        test_db.create_client({"id": "testclient", "name": "Test"})
        key_id = test_db.create_api_key("testclient", "hash456", "otb_def", "revokeme")

        test_db.revoke_api_key(key_id)
        keys = test_db.get_api_keys_for_client("testclient")
        assert keys[0]["active"] == 0
