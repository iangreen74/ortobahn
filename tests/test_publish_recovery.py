"""Tests for Publisher Error Classification and Recovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.memory import MemoryStore
from ortobahn.publish_recovery import ErrorCategory, PublishErrorClassifier, PublishRecoveryManager


@pytest.fixture
def memory_store(test_db):
    return MemoryStore(db=test_db)


@pytest.fixture
def recovery(test_db, memory_store):
    return PublishRecoveryManager(db=test_db, memory_store=memory_store, max_retries=2)


class TestPublishErrorClassifier:
    def test_classifies_timeout_as_transient(self):
        assert PublishErrorClassifier.classify_error(Exception("Connection timed out")) == ErrorCategory.TRANSIENT

    def test_classifies_500_as_transient(self):
        assert (
            PublishErrorClassifier.classify_error(Exception("HTTP 503 Service Unavailable")) == ErrorCategory.TRANSIENT
        )

    def test_classifies_429_as_transient(self):
        assert PublishErrorClassifier.classify_error(Exception("429 Too Many Requests")) == ErrorCategory.TRANSIENT

    def test_classifies_401_as_auth(self):
        assert PublishErrorClassifier.classify_error(Exception("401 Unauthorized")) == ErrorCategory.AUTH

    def test_classifies_expired_token_as_auth(self):
        assert PublishErrorClassifier.classify_error(Exception("Token expired, please refresh")) == ErrorCategory.AUTH

    def test_classifies_policy_rejection_as_content_violation(self):
        assert (
            PublishErrorClassifier.classify_error(Exception("Post rejected: policy violation"))
            == ErrorCategory.CONTENT_VIOLATION
        )

    def test_classifies_spam_as_content_violation(self):
        assert (
            PublishErrorClassifier.classify_error(Exception("Detected as spam content"))
            == ErrorCategory.CONTENT_VIOLATION
        )

    def test_classifies_daily_limit_as_quota(self):
        assert PublishErrorClassifier.classify_error(Exception("Daily limit exceeded")) == ErrorCategory.QUOTA

    def test_classifies_unknown_error(self):
        assert PublishErrorClassifier.classify_error(Exception("Something weird happened")) == ErrorCategory.UNKNOWN


class TestTransientRecovery:
    def test_retry_succeeds_on_second_attempt(self, test_db, recovery):
        post_id = test_db.save_post(text="Test", run_id="run-1", client_id="default")
        mock_draft = MagicMock()
        mock_draft.text = "Test post"
        mock_client = MagicMock()
        mock_client.post.return_value = ("at://test/uri", "cid123")

        with patch("ortobahn.publish_recovery.time.sleep"):
            result = recovery.attempt_recovery(
                post_id, mock_draft, ErrorCategory.TRANSIENT, mock_client, "default", "run-1"
            )

        assert result["recovered"] is True
        assert "retry_success" in result["action"]

    def test_retry_exhausted(self, test_db, recovery):
        post_id = test_db.save_post(text="Test", run_id="run-1", client_id="default")
        mock_draft = MagicMock()
        mock_draft.text = "Test post"
        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("Still failing")

        with patch("ortobahn.publish_recovery.time.sleep"):
            result = recovery.attempt_recovery(
                post_id, mock_draft, ErrorCategory.TRANSIENT, mock_client, "default", "run-1"
            )

        assert result["recovered"] is False
        assert result["action"] == "retries_exhausted"
        post = test_db.get_post(post_id)
        assert post["status"] == "failed"
        assert post["failure_category"] == "transient"


class TestAuthRecovery:
    def test_flags_credential_issue_and_skips(self, test_db, recovery):
        post_id = test_db.save_post(text="Test", run_id="run-1", client_id="default")

        result = recovery.attempt_recovery(post_id, MagicMock(), ErrorCategory.AUTH, MagicMock(), "default", "run-1")

        assert result["recovered"] is False
        assert result["should_skip_remaining"] is True
        client = test_db.get_client("default")
        assert client["status"] == "credential_issue"


class TestContentViolationRecovery:
    def test_creates_memory_and_marks_failed(self, test_db, memory_store, recovery):
        post_id = test_db.save_post(text="Bad content", run_id="run-1", client_id="default")
        mock_draft = MagicMock()
        mock_draft.text = "This is the bad content"

        result = recovery.attempt_recovery(
            post_id, mock_draft, ErrorCategory.CONTENT_VIOLATION, MagicMock(), "default", "run-1"
        )

        assert result["recovered"] is False
        assert result["action"] == "content_violation_recorded"
        post = test_db.get_post(post_id)
        assert post["failure_category"] == "content_violation"

        memories = memory_store.recall("creator", "default")
        assert any("content_violation" in (m.content.get("signal", "") or "") for m in memories)


class TestQuotaRecovery:
    def test_skips_remaining_posts(self, test_db, recovery):
        post_id = test_db.save_post(text="Test", run_id="run-1", client_id="default")

        result = recovery.attempt_recovery(post_id, MagicMock(), ErrorCategory.QUOTA, MagicMock(), "default", "run-1")

        assert result["recovered"] is False
        assert result["should_skip_remaining"] is True
        post = test_db.get_post(post_id)
        assert post["status"] == "skipped"
        assert post["failure_category"] == "quota"


class TestUnknownRecovery:
    def test_marks_failed_and_continues(self, test_db, recovery):
        post_id = test_db.save_post(text="Test", run_id="run-1", client_id="default")

        result = recovery.attempt_recovery(post_id, MagicMock(), ErrorCategory.UNKNOWN, MagicMock(), "default", "run-1")

        assert result["recovered"] is False
        assert result["action"] == "unknown_marked_failed"
        assert result["should_skip_remaining"] is False
        post = test_db.get_post(post_id)
        assert post["status"] == "failed"
        assert post["failure_category"] == "unknown"
