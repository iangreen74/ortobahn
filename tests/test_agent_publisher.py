"""Tests for Publisher Agent (no LLM, mocked Bluesky)."""

from __future__ import annotations

from ortobahn.agents.publisher import PublisherAgent
from ortobahn.models import DraftPost, DraftPosts, Platform


class TestPublisherAgent:
    def _make_drafts(self, texts_and_confidences, platform=Platform.BLUESKY):
        return DraftPosts(
            posts=[
                DraftPost(text=t, source_idea="test", reasoning="test", confidence=c, platform=platform)
                for t, c in texts_and_confidences
            ]
        )

    def test_publishes_bluesky_above_threshold(self, test_db, mock_bluesky_client):
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Great post", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert len(result.posts) == 1
        assert result.posts[0].status == "published"
        mock_bluesky_client.post.assert_called_once_with("Great post")

    def test_skips_below_threshold(self, test_db, mock_bluesky_client):
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Weak post", 0.3)])
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "skipped"
        mock_bluesky_client.post.assert_not_called()

    def test_dry_run_does_not_post(self, test_db, mock_bluesky_client):
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Dry run post", 0.9)])
        result = agent.run(run_id="run-1", drafts=drafts, dry_run=True)

        assert result.posts[0].status == "draft"
        mock_bluesky_client.post.assert_not_called()

    def test_handles_bluesky_publish_failure(self, test_db, mock_bluesky_client):
        mock_bluesky_client.post.side_effect = Exception("Network error")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Will fail", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "failed"
        assert "Network error" in result.posts[0].error

    def test_mixed_confidence_bluesky(self, test_db, mock_bluesky_client):
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts(
            [
                ("Good post", 0.9),
                ("Bad post", 0.3),
                ("OK post", 0.75),
            ],
            platform=Platform.BLUESKY,
        )
        result = agent.run(run_id="run-1", drafts=drafts)

        statuses = [p.status for p in result.posts]
        assert statuses == ["published", "skipped", "published"]

    def test_non_bluesky_saves_as_draft(self, test_db, mock_bluesky_client):
        """Platforms without a publisher save content as drafts."""
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Twitter post", 0.9)], platform=Platform.TWITTER)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "draft"
        assert result.posts[0].platform == Platform.TWITTER
        mock_bluesky_client.post.assert_not_called()

    def test_no_bluesky_client_saves_as_draft(self, test_db):
        """Without Bluesky client, even Bluesky posts are saved as drafts."""
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=None,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Post without client", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "draft"

    def test_multi_platform_bluesky_only(self, test_db, mock_bluesky_client):
        """With only Bluesky configured: Bluesky publishes, others save as draft."""
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = DraftPosts(
            posts=[
                DraftPost(
                    text="Bluesky post", source_idea="test", reasoning="test", confidence=0.9, platform=Platform.BLUESKY
                ),
                DraftPost(
                    text="Twitter post", source_idea="test", reasoning="test", confidence=0.9, platform=Platform.TWITTER
                ),
                DraftPost(
                    text="LinkedIn post",
                    source_idea="test",
                    reasoning="test",
                    confidence=0.9,
                    platform=Platform.LINKEDIN,
                ),
            ]
        )
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "published"
        assert result.posts[1].status == "draft"
        assert result.posts[2].status == "draft"

    def test_publishes_to_twitter(self, test_db):
        mock_twitter = type("MockTwitter", (), {"post": lambda self, text: ("https://x.com/i/status/123", "123")})()
        agent = PublisherAgent(
            db=test_db,
            twitter_client=mock_twitter,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Tweet this", 0.9)], platform=Platform.TWITTER)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "published"
        assert result.posts[0].uri == "https://x.com/i/status/123"

    def test_publishes_to_linkedin(self, test_db):
        mock_linkedin = type(
            "MockLinkedIn",
            (),
            {"post": lambda self, text: ("https://linkedin.com/feed/update/urn:li:share:123", "urn:li:share:123")},
        )()
        agent = PublisherAgent(
            db=test_db,
            linkedin_client=mock_linkedin,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("LinkedIn post", 0.9)], platform=Platform.LINKEDIN)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "published"
        assert "linkedin.com" in result.posts[0].uri

    def test_multi_platform_all_publish(self, test_db, mock_bluesky_client):
        """All three platforms publish when clients are configured."""
        mock_twitter = type("MockTwitter", (), {"post": lambda self, text: ("https://x.com/i/status/123", "123")})()
        mock_linkedin = type(
            "MockLinkedIn",
            (),
            {"post": lambda self, text: ("https://linkedin.com/feed/update/urn:li:share:123", "urn:li:share:123")},
        )()

        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            twitter_client=mock_twitter,
            linkedin_client=mock_linkedin,
            confidence_threshold=0.7,
        )
        drafts = DraftPosts(
            posts=[
                DraftPost(text="Bluesky", source_idea="t", reasoning="t", confidence=0.9, platform=Platform.BLUESKY),
                DraftPost(text="Twitter", source_idea="t", reasoning="t", confidence=0.9, platform=Platform.TWITTER),
                DraftPost(text="LinkedIn", source_idea="t", reasoning="t", confidence=0.9, platform=Platform.LINKEDIN),
            ]
        )
        result = agent.run(run_id="run-1", drafts=drafts)
        assert all(p.status == "published" for p in result.posts)

    def test_verification_failure_marks_post_failed(self, test_db, mock_bluesky_client):
        """If post-publish verification fails, the post should be marked as failed."""
        mock_bluesky_client.verify_post_exists.return_value = False
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Phantom post", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "failed"
        assert "verification" in result.posts[0].error.lower()

    def test_verification_success_publishes(self, test_db, mock_bluesky_client):
        """If verification passes, post should be published normally."""
        mock_bluesky_client.verify_post_exists.return_value = True
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Verified post", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "published"
        mock_bluesky_client.verify_post_exists.assert_called_once()

    def test_verification_inconclusive_trusts_post(self, test_db, mock_bluesky_client):
        """If verification is inconclusive (e.g. auth error), trust the post succeeded."""
        mock_bluesky_client.verify_post_exists.return_value = None
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        drafts = self._make_drafts([("Inconclusive post", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "published"


# ---------------------------------------------------------------------------
# Publisher Recovery Manager Tests
# ---------------------------------------------------------------------------


class TestPublisherRecovery:
    """Tests for recovery manager integration in the publisher agent."""

    def _make_drafts(self, texts_and_confidences, platform=Platform.BLUESKY):
        return DraftPosts(
            posts=[
                DraftPost(text=t, source_idea="test", reasoning="test", confidence=c, platform=platform)
                for t, c in texts_and_confidences
            ]
        )

    def test_recovery_retry_on_transient_error(self, test_db, mock_bluesky_client):
        """When recovery manager is wired and a transient error occurs, it retries."""
        from unittest.mock import MagicMock

        mock_bluesky_client.post.side_effect = ConnectionError("timeout")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )

        # Wire a mock recovery manager
        mock_recovery = MagicMock()
        mock_recovery.attempt_recovery.return_value = {
            "recovered": True,
            "action": "retry_success_attempt_1",
            "should_skip_remaining": False,
        }
        agent._recovery_manager = mock_recovery

        drafts = self._make_drafts([("Retry this post", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "published"
        mock_recovery.attempt_recovery.assert_called_once()
        # Verify the error category was classified
        call_kwargs = mock_recovery.attempt_recovery.call_args
        assert call_kwargs.kwargs["platform_client"] is mock_bluesky_client

    def test_recovery_not_called_when_disabled(self, test_db, mock_bluesky_client):
        """When _recovery_manager is None, failures are handled without recovery attempt."""
        mock_bluesky_client.post.side_effect = ConnectionError("Network down")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )
        # Explicitly confirm recovery is disabled
        assert agent._recovery_manager is None

        drafts = self._make_drafts([("No recovery", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "failed"
        assert "Network down" in result.posts[0].error

    def test_recovery_failure_marks_post_failed(self, test_db, mock_bluesky_client):
        """When recovery manager fails to recover, the post is marked failed."""
        from unittest.mock import MagicMock

        mock_bluesky_client.post.side_effect = RuntimeError("server error 500")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )

        mock_recovery = MagicMock()
        mock_recovery.attempt_recovery.return_value = {
            "recovered": False,
            "action": "retries_exhausted",
            "should_skip_remaining": False,
        }
        agent._recovery_manager = mock_recovery

        drafts = self._make_drafts([("Will fail", 0.9)], platform=Platform.BLUESKY)
        result = agent.run(run_id="run-1", drafts=drafts)

        assert result.posts[0].status == "failed"
        assert "server error 500" in result.posts[0].error

    def test_recovery_skip_remaining_on_auth_error(self, test_db, mock_bluesky_client):
        """When recovery says should_skip_remaining, subsequent posts are not attempted."""
        from unittest.mock import MagicMock

        mock_bluesky_client.post.side_effect = RuntimeError("401 unauthorized")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )

        mock_recovery = MagicMock()
        mock_recovery.attempt_recovery.return_value = {
            "recovered": False,
            "action": "credential_issue_flagged",
            "should_skip_remaining": True,
        }
        agent._recovery_manager = mock_recovery

        # Two drafts, but second should be skipped after first fails with skip_remaining
        drafts = self._make_drafts(
            [("First post", 0.9), ("Second post", 0.9)],
            platform=Platform.BLUESKY,
        )
        result = agent.run(run_id="run-1", drafts=drafts)

        # Only one post processed (the one that failed); second was skipped by break
        assert len(result.posts) == 1
        assert result.posts[0].status == "failed"
        # Recovery was called only once (for first post)
        assert mock_recovery.attempt_recovery.call_count == 1

    def test_cascading_failures_handled_gracefully(self, test_db, mock_bluesky_client):
        """Multiple posts failing is handled gracefully when recovery does not skip remaining."""
        from unittest.mock import MagicMock

        mock_bluesky_client.post.side_effect = RuntimeError("transient failure")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )

        mock_recovery = MagicMock()
        mock_recovery.attempt_recovery.return_value = {
            "recovered": False,
            "action": "retries_exhausted",
            "should_skip_remaining": False,
        }
        agent._recovery_manager = mock_recovery

        drafts = self._make_drafts(
            [("Post 1", 0.9), ("Post 2", 0.85), ("Post 3", 0.8)],
            platform=Platform.BLUESKY,
        )
        result = agent.run(run_id="run-1", drafts=drafts)

        # All three should be attempted and all should fail
        assert len(result.posts) == 3
        assert all(p.status == "failed" for p in result.posts)
        assert mock_recovery.attempt_recovery.call_count == 3

    def test_recovery_success_increments_published_count(self, test_db, mock_bluesky_client):
        """Recovered posts should increment the published count in the log."""
        from unittest.mock import MagicMock

        mock_bluesky_client.post.side_effect = RuntimeError("timeout")
        agent = PublisherAgent(
            db=test_db,
            bluesky_client=mock_bluesky_client,
            confidence_threshold=0.7,
        )

        mock_recovery = MagicMock()
        mock_recovery.attempt_recovery.return_value = {
            "recovered": True,
            "action": "retry_success_attempt_1",
            "should_skip_remaining": False,
        }
        agent._recovery_manager = mock_recovery

        drafts = self._make_drafts(
            [("Recovered 1", 0.9), ("Recovered 2", 0.85)],
            platform=Platform.BLUESKY,
        )
        result = agent.run(run_id="run-1", drafts=drafts)

        published = sum(1 for p in result.posts if p.status == "published")
        assert published == 2
