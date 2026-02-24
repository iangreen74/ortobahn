"""Tests for Reddit integration (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestRedditClient:
    @patch("praw.Reddit")
    def test_post_success(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_subreddit = MagicMock()
        mock_reddit.subreddit.return_value = mock_subreddit
        mock_submission = MagicMock()
        mock_submission.id = "abc123"
        mock_submission.permalink = "/r/test/comments/abc123/my_post/"
        mock_subreddit.submit.return_value = mock_submission

        client = RedditClient("cid", "csecret", "user", "pass", default_subreddit="test")
        url, post_id = client.post("Hello world", title="My Post")

        assert post_id == "abc123"
        assert "abc123" in url
        assert url == "https://reddit.com/r/test/comments/abc123/my_post/"
        mock_subreddit.submit.assert_called_once_with(title="My Post", selftext="Hello world")

    @patch("praw.Reddit")
    def test_post_no_subreddit(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit

        client = RedditClient("cid", "csecret", "user", "pass")
        import pytest

        with pytest.raises(ValueError, match="No subreddit"):
            client.post("Hello world")

    @patch("praw.Reddit")
    def test_post_with_default_subreddit(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_subreddit = MagicMock()
        mock_reddit.subreddit.return_value = mock_subreddit
        mock_submission = MagicMock()
        mock_submission.id = "def456"
        mock_submission.permalink = "/r/mydefault/comments/def456/hello/"
        mock_subreddit.submit.return_value = mock_submission

        client = RedditClient("cid", "csecret", "user", "pass", default_subreddit="mydefault")
        url, post_id = client.post("Hello world", title="Hello")

        assert post_id == "def456"
        mock_reddit.subreddit.assert_called_once_with("mydefault")

    @patch("praw.Reddit")
    def test_post_auto_title(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_subreddit = MagicMock()
        mock_reddit.subreddit.return_value = mock_subreddit
        mock_submission = MagicMock()
        mock_submission.id = "ghi789"
        mock_submission.permalink = "/r/test/comments/ghi789/first_line/"
        mock_subreddit.submit.return_value = mock_submission

        client = RedditClient("cid", "csecret", "user", "pass", default_subreddit="test")
        url, post_id = client.post("First line as title\nBody text here")

        # Title should be the first line
        mock_subreddit.submit.assert_called_once_with(
            title="First line as title", selftext="First line as title\nBody text here"
        )

    @patch("praw.Reddit")
    def test_get_post_metrics(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_submission = MagicMock()
        mock_submission.score = 42
        mock_submission.num_comments = 7
        mock_submission.upvote_ratio = 0.95
        mock_reddit.submission.return_value = mock_submission

        client = RedditClient("cid", "csecret", "user", "pass")
        metrics = client.get_post_metrics("abc123")

        assert metrics.post_id == "abc123"
        assert metrics.score == 42
        assert metrics.num_comments == 7
        assert metrics.upvote_ratio == 0.95

    @patch("praw.Reddit")
    def test_verify_post_exists_true(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_submission = MagicMock()
        mock_submission.title = "Some title"
        mock_reddit.submission.return_value = mock_submission

        client = RedditClient("cid", "csecret", "user", "pass")
        result = client.verify_post_exists("abc123")

        assert result is True
        mock_reddit.submission.assert_called_once_with(id="abc123")

    @patch("praw.Reddit")
    def test_verify_post_exists_false(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_submission = MagicMock()
        mock_submission.title = property(lambda self: (_ for _ in ()).throw(Exception("Not found")))
        # Make accessing .title raise an exception
        type(mock_submission).title = property(lambda self: (_ for _ in ()).throw(Exception("Not found")))
        mock_reddit.submission.return_value = mock_submission

        client = RedditClient("cid", "csecret", "user", "pass")
        result = client.verify_post_exists("nonexistent")

        assert result is False

    @patch("praw.Reddit")
    def test_get_profile(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        mock_reddit = MagicMock()
        mock_reddit_cls.return_value = mock_reddit
        mock_user = MagicMock()
        mock_user.name = "ortobahn"
        mock_user.link_karma = 100
        mock_user.comment_karma = 200
        mock_user.created_utc = 1700000000.0
        mock_reddit.user.me.return_value = mock_user

        client = RedditClient("cid", "csecret", "user", "pass")
        profile = client.get_profile()

        assert profile["username"] == "ortobahn"
        assert profile["karma"] == 300
        assert profile["created_utc"] == 1700000000.0

    @patch("praw.Reddit")
    def test_auth_lazy(self, mock_reddit_cls):
        from ortobahn.integrations.reddit import RedditClient

        client = RedditClient("cid", "csecret", "user", "pass")
        assert client._reddit is None

        client._get_reddit()
        assert mock_reddit_cls.called
