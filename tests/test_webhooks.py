"""Tests for webhook registration, dispatch, and lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ortobahn.webhooks import (
    EVENT_PIPELINE_COMPLETED,
    EVENT_POST_FAILED,
    EVENT_POST_PUBLISHED,
    delete_webhook,
    dispatch_event,
    list_webhooks,
    register_webhook,
)


@pytest.fixture
def webhook_db(test_db):
    """Ensure a client exists for webhook tests."""
    client = test_db.get_client("default")
    if not client:
        test_db.create_client({"id": "default", "name": "Test Default"})
    return test_db


class TestRegisterWebhook:
    def test_creates_webhook(self, webhook_db):
        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")
        assert wh_id
        row = webhook_db.fetchone("SELECT * FROM webhooks WHERE id=?", (wh_id,))
        assert row is not None
        assert row["client_id"] == "default"
        assert row["url"] == "https://example.com/hook"
        assert row["events"] == "*"
        assert row["active"] == 1
        assert row["secret"]  # auto-generated

    def test_creates_with_specific_events(self, webhook_db):
        wh_id = register_webhook(
            webhook_db,
            "default",
            "https://example.com/hook",
            events=["post.published", "post.failed"],
        )
        row = webhook_db.fetchone("SELECT * FROM webhooks WHERE id=?", (wh_id,))
        assert row["events"] == "post.published,post.failed"

    def test_creates_with_custom_secret(self, webhook_db):
        wh_id = register_webhook(
            webhook_db,
            "default",
            "https://example.com/hook",
            secret="my-custom-secret",
        )
        row = webhook_db.fetchone("SELECT * FROM webhooks WHERE id=?", (wh_id,))
        assert row["secret"] == "my-custom-secret"


class TestListWebhooks:
    def test_returns_registered_webhooks(self, webhook_db):
        register_webhook(webhook_db, "default", "https://example.com/hook1")
        register_webhook(webhook_db, "default", "https://example.com/hook2")
        webhooks = list_webhooks(webhook_db, "default")
        assert len(webhooks) == 2
        urls = {w["url"] for w in webhooks}
        assert "https://example.com/hook1" in urls
        assert "https://example.com/hook2" in urls

    def test_empty_for_other_client(self, webhook_db):
        register_webhook(webhook_db, "default", "https://example.com/hook")
        webhooks = list_webhooks(webhook_db, "nonexistent")
        assert webhooks == []


class TestDeleteWebhook:
    def test_removes_webhook(self, webhook_db):
        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")
        assert list_webhooks(webhook_db, "default")
        delete_webhook(webhook_db, wh_id, "default")
        assert list_webhooks(webhook_db, "default") == []

    def test_does_not_delete_other_clients_webhook(self, webhook_db):
        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")
        delete_webhook(webhook_db, wh_id, "other-client")
        assert len(list_webhooks(webhook_db, "default")) == 1


class TestDispatchEvent:
    @patch("ortobahn.webhooks.httpx.post")
    def test_sends_to_matching_webhooks(self, mock_post, webhook_db):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        register_webhook(webhook_db, "default", "https://example.com/hook")
        count = dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {"post_id": "abc"})
        assert count == 1
        mock_post.assert_called_once()

        # Verify payload
        call_kwargs = mock_post.call_args
        body = json.loads(call_kwargs.kwargs.get("content", call_kwargs[1].get("content", "")))
        assert body["event"] == EVENT_POST_PUBLISHED
        assert body["data"]["post_id"] == "abc"
        assert body["client_id"] == "default"

    @patch("ortobahn.webhooks.httpx.post")
    def test_skips_non_matching_event_filters(self, mock_post, webhook_db):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        register_webhook(
            webhook_db,
            "default",
            "https://example.com/hook",
            events=["post.failed"],
        )
        count = dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {"post_id": "abc"})
        assert count == 0
        mock_post.assert_not_called()

    @patch("ortobahn.webhooks.httpx.post")
    def test_wildcard_matches_all_events(self, mock_post, webhook_db):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        register_webhook(webhook_db, "default", "https://example.com/hook")  # events="*"
        for event in [EVENT_POST_PUBLISHED, EVENT_POST_FAILED, EVENT_PIPELINE_COMPLETED]:
            dispatch_event(webhook_db, "default", event, {})
        assert mock_post.call_count == 3

    @patch("ortobahn.webhooks.httpx.post")
    def test_increments_failure_count_on_error(self, mock_post, webhook_db):
        mock_post.side_effect = httpx.ConnectError("connection refused")

        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")
        count = dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {})
        assert count == 0

        row = webhook_db.fetchone("SELECT failure_count, active FROM webhooks WHERE id=?", (wh_id,))
        assert row["failure_count"] == 1
        assert row["active"] == 1  # Still active after 1 failure

    @patch("ortobahn.webhooks.httpx.post")
    def test_disables_webhook_after_10_failures(self, mock_post, webhook_db):
        mock_post.side_effect = httpx.ConnectError("connection refused")

        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")

        # Set failure_count to 9 so the next failure triggers disable
        webhook_db.execute("UPDATE webhooks SET failure_count=9 WHERE id=?", (wh_id,), commit=True)

        dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {})

        row = webhook_db.fetchone("SELECT failure_count, active FROM webhooks WHERE id=?", (wh_id,))
        assert row["failure_count"] == 10
        assert row["active"] == 0

    @patch("ortobahn.webhooks.httpx.post")
    def test_resets_failure_count_on_success(self, mock_post, webhook_db):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")
        webhook_db.execute("UPDATE webhooks SET failure_count=5 WHERE id=?", (wh_id,), commit=True)

        dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {})

        row = webhook_db.fetchone("SELECT failure_count FROM webhooks WHERE id=?", (wh_id,))
        assert row["failure_count"] == 0

    @patch("ortobahn.webhooks.httpx.post")
    def test_hmac_signature_is_correct(self, mock_post, webhook_db):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        secret = "test-secret-key-12345678"
        register_webhook(
            webhook_db,
            "default",
            "https://example.com/hook",
            secret=secret,
        )

        dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {"post_id": "xyz"})

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("content", call_kwargs[1].get("content", ""))
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))

        # Verify the signature header exists and is correct
        sig_header = headers.get("X-Ortobahn-Signature", "")
        assert sig_header.startswith("sha256=")

        expected_sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected_sig}"

    @patch("ortobahn.webhooks.httpx.post")
    def test_does_not_send_to_inactive_webhooks(self, mock_post, webhook_db):
        wh_id = register_webhook(webhook_db, "default", "https://example.com/hook")
        webhook_db.execute("UPDATE webhooks SET active=0 WHERE id=?", (wh_id,), commit=True)

        count = dispatch_event(webhook_db, "default", EVENT_POST_PUBLISHED, {})
        assert count == 0
        mock_post.assert_not_called()
