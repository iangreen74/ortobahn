"""Tests for the article publishing pipeline — Medium verification, Substack
error handling, article publish recovery, and dashboard error visibility.

All HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from ortobahn.db import Database
from ortobahn.integrations.medium import MediumClient
from ortobahn.integrations.substack import SubstackClient
from ortobahn.publish_recovery import (
    ArticlePublishRecoveryManager,
    ErrorCategory,
    PublishErrorClassifier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_db(tmp_path):
    """Fresh SQLite DB for each test."""
    db = Database(tmp_path / "test_article.db")
    yield db
    db.close()


@pytest.fixture
def sample_article(test_db):
    """Insert a sample article and return its id."""
    # Ensure a client exists
    client = test_db.get_client("test-client")
    if not client:
        test_db.create_client({"id": "test-client", "name": "Test Client"})
    article_id = test_db.save_article(
        {
            "client_id": "test-client",
            "run_id": "run-123",
            "title": "Test Article",
            "subtitle": "A test",
            "body_markdown": "# Hello\n\nThis is a test article.",
            "tags": ["test", "ai"],
            "meta_description": "Test article desc",
            "topic_used": "testing",
            "confidence": 0.9,
            "word_count": 500,
            "status": "draft",
        }
    )
    return article_id


# ---------------------------------------------------------------------------
# 1. Medium verification tests
# ---------------------------------------------------------------------------


class TestMediumVerifyPost:
    """Test MediumClient.verify_post() and updated get_post()."""

    @patch("ortobahn.integrations.medium.httpx.head")
    def test_verify_post_success(self, mock_head):
        """verify_post returns True when the URL responds 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_head.return_value = mock_resp

        client = MediumClient("test-token")
        result = client.verify_post("https://medium.com/@user/test-article-123")

        assert result is True
        mock_head.assert_called_once_with(
            "https://medium.com/@user/test-article-123",
            timeout=10,
            follow_redirects=True,
        )

    @patch("ortobahn.integrations.medium.httpx.head")
    def test_verify_post_not_found(self, mock_head):
        """verify_post returns False on 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_head.return_value = mock_resp

        client = MediumClient("test-token")
        result = client.verify_post("https://medium.com/@user/gone-article")

        assert result is False

    @patch("ortobahn.integrations.medium.httpx.head")
    def test_verify_post_gone(self, mock_head):
        """verify_post returns False on 410 (Gone)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        mock_head.return_value = mock_resp

        client = MediumClient("test-token")
        result = client.verify_post("https://medium.com/@user/deleted-article")

        assert result is False

    @patch("ortobahn.integrations.medium.httpx.head")
    def test_verify_post_server_error_inconclusive(self, mock_head):
        """verify_post returns None on 500 (inconclusive)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_head.return_value = mock_resp

        client = MediumClient("test-token")
        result = client.verify_post("https://medium.com/@user/error-article")

        assert result is None

    @patch("ortobahn.integrations.medium.httpx.head")
    def test_verify_post_network_error_inconclusive(self, mock_head):
        """verify_post returns None when a network error occurs."""
        mock_head.side_effect = httpx.ConnectError("Connection refused")

        client = MediumClient("test-token")
        result = client.verify_post("https://medium.com/@user/unreachable")

        assert result is None

    @patch("ortobahn.integrations.medium.httpx.get")
    def test_get_post_found_in_publications(self, mock_get):
        """get_post returns publication dict when post_id matches."""
        client = MediumClient("test-token")
        client._user_id = "user-123"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "pub-abc", "name": "My Blog"},
                {"id": "target-post-id", "name": "Another"},
            ]
        }
        mock_get.return_value = mock_resp

        result = client.get_post("target-post-id")

        assert result is not None
        assert result["id"] == "target-post-id"

    @patch("ortobahn.integrations.medium.httpx.get")
    def test_get_post_not_found(self, mock_get):
        """get_post returns None when post_id is not in publications."""
        client = MediumClient("test-token")
        client._user_id = "user-123"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": "other-id", "name": "Blog"}]}
        mock_get.return_value = mock_resp

        result = client.get_post("missing-post-id")
        assert result is None

    @patch("ortobahn.integrations.medium.httpx.get")
    def test_get_post_api_error(self, mock_get):
        """get_post returns None on API error (graceful degradation)."""
        client = MediumClient("test-token")
        client._user_id = "user-123"

        mock_get.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=MagicMock(), response=MagicMock(status_code=403)
        )

        result = client.get_post("any-id")
        assert result is None


# ---------------------------------------------------------------------------
# 2. Substack error handling tests
# ---------------------------------------------------------------------------


class TestSubstackErrorHandling:
    """Test that Substack publish errors propagate to the caller."""

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_publish_error_propagates(self, mock_post):
        """When publish step fails, the error should propagate (not be swallowed)."""
        # First call: create draft succeeds
        draft_resp = MagicMock()
        draft_resp.status_code = 200
        draft_resp.raise_for_status = MagicMock()
        draft_resp.json.return_value = {"id": "draft-123", "slug": "test-article"}

        # Second call: publish fails
        publish_resp = MagicMock()
        publish_resp.status_code = 500
        publish_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )

        mock_post.side_effect = [draft_resp, publish_resp]

        client = SubstackClient("testblog", session_cookie="valid-session")

        with pytest.raises(httpx.HTTPStatusError):
            client.post("Test Title", "# Content", publish=True)

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_publish_false_no_error(self, mock_post):
        """When publish=False, draft creation works without hitting publish endpoint."""
        draft_resp = MagicMock()
        draft_resp.status_code = 200
        draft_resp.raise_for_status = MagicMock()
        draft_resp.json.return_value = {"id": "draft-456", "slug": "my-draft"}
        mock_post.return_value = draft_resp

        client = SubstackClient("testblog", session_cookie="valid-session")
        url, draft_id = client.post("Draft Title", "# Draft", publish=False)

        assert draft_id == "draft-456"
        assert "testblog.substack.com" in url
        # Only one call (no publish call)
        assert mock_post.call_count == 1

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_publish_success(self, mock_post):
        """When publish=True and both calls succeed, returns url and draft_id."""
        draft_resp = MagicMock()
        draft_resp.status_code = 200
        draft_resp.raise_for_status = MagicMock()
        draft_resp.json.return_value = {"id": "draft-789", "slug": "published-article"}

        publish_resp = MagicMock()
        publish_resp.status_code = 200
        publish_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [draft_resp, publish_resp]

        client = SubstackClient("testblog", session_cookie="valid-session")
        url, draft_id = client.post("Published Title", "# Content", publish=True)

        assert draft_id == "draft-789"
        assert "published-article" in url
        assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# 3. Error classification tests
# ---------------------------------------------------------------------------


class TestPublishErrorClassifier:
    """Test that errors are correctly classified into categories."""

    def test_timeout_is_transient(self):
        assert PublishErrorClassifier.classify_error(Exception("Connection timeout")) == ErrorCategory.TRANSIENT

    def test_rate_limit_is_transient(self):
        assert PublishErrorClassifier.classify_error(Exception("429 Too Many Requests")) == ErrorCategory.TRANSIENT

    def test_server_error_is_transient(self):
        assert PublishErrorClassifier.classify_error(Exception("502 Bad Gateway")) == ErrorCategory.TRANSIENT

    def test_unauthorized_is_auth(self):
        assert PublishErrorClassifier.classify_error(Exception("401 Unauthorized")) == ErrorCategory.AUTH

    def test_forbidden_is_auth(self):
        assert PublishErrorClassifier.classify_error(Exception("403 Forbidden")) == ErrorCategory.AUTH

    def test_content_blocked_is_violation(self):
        assert (
            PublishErrorClassifier.classify_error(Exception("Content blocked by policy"))
            == ErrorCategory.CONTENT_VIOLATION
        )

    def test_quota_exceeded(self):
        assert PublishErrorClassifier.classify_error(Exception("Daily limit exceeded")) == ErrorCategory.QUOTA

    def test_unknown_error(self):
        assert PublishErrorClassifier.classify_error(Exception("Something weird happened")) == ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# 4. Article publish recovery tests
# ---------------------------------------------------------------------------


class TestArticlePublishRecoveryManager:
    """Test ArticlePublishRecoveryManager handles failures correctly."""

    def test_auth_failure_marks_failed(self, test_db, sample_article):
        """Auth errors are classified and recorded in the DB."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="medium",
            platform_client=mock_client,
            exception=Exception("401 Unauthorized"),
        )

        assert result["recovered"] is False
        assert result["action"] == "auth_failure"

        # Verify DB was updated
        pubs = test_db.get_article_publications(sample_article)
        assert len(pubs) == 1
        assert pubs[0]["status"] == "failed"
        assert pubs[0]["failure_category"] == "auth"

    def test_content_violation_marks_failed(self, test_db, sample_article):
        """Content violations are classified and recorded."""
        pub_id = test_db.save_article_publication(sample_article, "substack", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="substack",
            platform_client=mock_client,
            exception=Exception("Content rejected: policy violation"),
        )

        assert result["recovered"] is False
        assert result["action"] == "content_violation"

        pubs = test_db.get_article_publications(sample_article)
        assert pubs[0]["failure_category"] == "content_violation"

    def test_quota_exceeded_marks_failed(self, test_db, sample_article):
        """Quota errors are classified and recorded."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="medium",
            platform_client=mock_client,
            exception=Exception("Daily limit exceeded for this account"),
        )

        assert result["recovered"] is False
        assert result["action"] == "quota_exceeded"

        pubs = test_db.get_article_publications(sample_article)
        assert pubs[0]["failure_category"] == "quota"

    def test_unknown_error_marks_failed(self, test_db, sample_article):
        """Unknown errors are classified and recorded."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="medium",
            platform_client=mock_client,
            exception=Exception("Something completely unexpected"),
        )

        assert result["recovered"] is False
        assert result["action"] == "unknown_failure"

        pubs = test_db.get_article_publications(sample_article)
        assert pubs[0]["failure_category"] == "unknown"

    @patch("ortobahn.publish_recovery.time.sleep")
    def test_transient_retry_success(self, mock_sleep, test_db, sample_article):
        """Transient errors trigger retry; successful retry marks as published."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        mock_client.post.return_value = ("https://medium.com/@user/retried-article", "post-456")
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="medium",
            platform_client=mock_client,
            exception=Exception("Connection timeout"),
        )

        assert result["recovered"] is True
        assert "retry_success" in result["action"]
        assert result["url"] == "https://medium.com/@user/retried-article"

        # Verify DB was updated to published
        pubs = test_db.get_article_publications(sample_article)
        assert pubs[0]["status"] == "published"
        assert pubs[0]["published_url"] == "https://medium.com/@user/retried-article"

        # Verify sleep was called (backoff)
        assert mock_sleep.call_count >= 1

    @patch("ortobahn.publish_recovery.time.sleep")
    def test_transient_retry_exhausted(self, mock_sleep, test_db, sample_article):
        """Transient errors exhaust retries and mark as failed."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("Connection timeout still")
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="medium",
            platform_client=mock_client,
            exception=Exception("Connection timeout"),
        )

        assert result["recovered"] is False
        assert result["action"] == "retries_exhausted"

        # Verify DB was updated
        pubs = test_db.get_article_publications(sample_article)
        assert pubs[0]["status"] == "failed"
        assert pubs[0]["failure_category"] == "transient"
        assert pubs[0]["retry_count"] == 2

        # Verify retries happened
        assert mock_client.post.call_count == 2

    @patch("ortobahn.publish_recovery.time.sleep")
    def test_transient_retry_success_on_second_attempt(self, mock_sleep, test_db, sample_article):
        """Transient error: first retry fails, second succeeds."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        recovery = ArticlePublishRecoveryManager(test_db, max_retries=2)

        mock_client = MagicMock()
        mock_client.post.side_effect = [
            Exception("Still timing out"),
            ("https://medium.com/@user/finally-published", "post-789"),
        ]
        article = test_db.get_article(sample_article)

        result = recovery.handle_failure(
            pub_id=pub_id,
            article=article,
            platform="medium",
            platform_client=mock_client,
            exception=Exception("Connection timeout"),
        )

        assert result["recovered"] is True
        assert result["action"] == "retry_success_attempt_2"


# ---------------------------------------------------------------------------
# 5. DB helper tests
# ---------------------------------------------------------------------------


class TestArticlePublicationDB:
    """Test DB methods for article publication failure tracking."""

    def test_update_article_publication_failed(self, test_db, sample_article):
        """update_article_publication_failed sets status, error, and category."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")

        test_db.update_article_publication_failed(pub_id, "Auth failed", failure_category="auth", retry_count=0)

        pubs = test_db.get_article_publications(sample_article)
        assert len(pubs) == 1
        assert pubs[0]["status"] == "failed"
        assert pubs[0]["error"] == "Auth failed"
        assert pubs[0]["failure_category"] == "auth"
        assert pubs[0]["retry_count"] == 0

    def test_get_failed_article_publications(self, test_db, sample_article):
        """get_failed_article_publications returns only failed publications."""
        # Create one failed and one published
        pub_id_1 = test_db.save_article_publication(sample_article, "medium", status="pending")
        pub_id_2 = test_db.save_article_publication(sample_article, "substack", status="pending")

        test_db.update_article_publication_failed(pub_id_1, "Timeout", "transient", retry_count=2)
        test_db.update_article_publication(pub_id_2, status="published", published_url="https://example.com")

        failed = test_db.get_failed_article_publications(client_id="test-client")
        assert len(failed) == 1
        assert failed[0]["platform"] == "medium"
        assert failed[0]["failure_category"] == "transient"

    def test_get_failed_article_publications_empty(self, test_db, sample_article):
        """get_failed_article_publications returns empty list when nothing failed."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        test_db.update_article_publication(pub_id, status="published", published_url="https://example.com")

        failed = test_db.get_failed_article_publications(client_id="test-client")
        assert failed == []


# ---------------------------------------------------------------------------
# 6. Migration test
# ---------------------------------------------------------------------------


class TestArticlePubRecoveryMigration:
    """Test that migration 028 adds the expected columns."""

    def test_failure_category_column_exists(self, test_db, sample_article):
        """The failure_category column should exist on article_publications."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        test_db.update_article_publication_failed(pub_id, "test error", "auth")

        row = test_db.fetchone("SELECT failure_category FROM article_publications WHERE id=?", (pub_id,))
        assert row is not None
        assert row["failure_category"] == "auth"

    def test_retry_count_column_exists(self, test_db, sample_article):
        """The retry_count column should exist on article_publications."""
        pub_id = test_db.save_article_publication(sample_article, "medium", status="pending")
        test_db.update_article_publication_failed(pub_id, "test error", "transient", retry_count=3)

        row = test_db.fetchone("SELECT retry_count FROM article_publications WHERE id=?", (pub_id,))
        assert row is not None
        assert row["retry_count"] == 3
