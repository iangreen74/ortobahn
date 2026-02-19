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
