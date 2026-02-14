"""Tests for Twitter integration (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestTwitterClient:
    @patch("tweepy.Client")
    def test_post_returns_url_and_id(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.create_tweet.return_value = MagicMock(data={"id": "123456789"})

        client = TwitterClient("key", "secret", "token", "token_secret")
        url, tweet_id = client.post("Hello world")

        assert tweet_id == "123456789"
        assert "123456789" in url
        mock_client.create_tweet.assert_called_once_with(text="Hello world")

    @patch("tweepy.Client")
    def test_get_metrics(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_tweet.return_value = MagicMock(
            data=MagicMock(
                public_metrics={
                    "like_count": 10,
                    "retweet_count": 3,
                    "reply_count": 1,
                    "impression_count": 500,
                }
            )
        )

        client = TwitterClient("key", "secret", "token", "token_secret")
        metrics = client.get_post_metrics("123")
        assert metrics.like_count == 10
        assert metrics.retweet_count == 3
        assert metrics.reply_count == 1
        assert metrics.impression_count == 500

    @patch("tweepy.Client")
    def test_get_metrics_failure_returns_empty(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_tweet.side_effect = Exception("API error")

        client = TwitterClient("key", "secret", "token", "token_secret")
        metrics = client.get_post_metrics("123")
        assert metrics.like_count == 0
        assert metrics.retweet_count == 0

    @patch("tweepy.Client")
    def test_lazy_auth(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        client = TwitterClient("key", "secret", "token", "token_secret")
        assert client._client is None

        client._get_client()
        assert mock_tweepy_cls.called
