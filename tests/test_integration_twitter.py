"""Tests for Twitter integration (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import tweepy


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
        assert metrics["like_count"] == 10
        assert metrics["retweet_count"] == 3
        assert metrics["reply_count"] == 1
        assert metrics["impression_count"] == 500

    @patch("tweepy.Client")
    def test_get_metrics_failure_returns_empty(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_tweet.side_effect = Exception("API error")

        client = TwitterClient("key", "secret", "token", "token_secret")
        metrics = client.get_post_metrics("123")
        assert metrics["like_count"] == 0
        assert metrics["retweet_count"] == 0

    @patch("tweepy.Client")
    def test_lazy_auth(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        client = TwitterClient("key", "secret", "token", "token_secret")
        assert client._client is None

        client._get_client()
        assert mock_tweepy_cls.called

    # --- verify_post_exists tests ---

    @patch("tweepy.Client")
    def test_verify_post_exists_found(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_tweet.return_value = MagicMock(data=MagicMock(id="999"))

        client = TwitterClient("key", "secret", "token", "token_secret")
        result = client.verify_post_exists("999")

        assert result is True
        mock_client.get_tweet.assert_called_once_with("999", tweet_fields=["id"])

    @patch("tweepy.Client")
    def test_verify_post_exists_not_found(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_tweet.return_value = MagicMock(data=None)

        client = TwitterClient("key", "secret", "token", "token_secret")
        result = client.verify_post_exists("999")

        assert result is False

    @patch("tweepy.Client")
    def test_verify_post_exists_api_error(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_tweet.side_effect = Exception("Network timeout")

        client = TwitterClient("key", "secret", "token", "token_secret")
        result = client.verify_post_exists("999")

        assert result is None

    # --- get_profile tests ---

    @patch("tweepy.Client")
    def test_get_profile(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_me.return_value = MagicMock(
            data=MagicMock(
                username="ortobahn",
                public_metrics={
                    "followers_count": 120,
                    "following_count": 50,
                    "tweet_count": 300,
                },
            )
        )

        client = TwitterClient("key", "secret", "token", "token_secret")
        profile = client.get_profile()

        assert profile["username"] == "ortobahn"
        assert profile["followers_count"] == 120
        assert profile["following_count"] == 50
        assert profile["tweet_count"] == 300
        mock_client.get_me.assert_called_once_with(user_fields=["public_metrics"])

    @patch("tweepy.Client")
    def test_get_profile_failure_returns_empty(self, mock_tweepy_cls):
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_me.side_effect = Exception("API error")

        client = TwitterClient("key", "secret", "token", "token_secret")
        profile = client.get_profile()

        assert profile == {}

    # --- auth retry tests ---

    @patch("tweepy.Client")
    def test_auth_retry_on_unauthorized(self, mock_tweepy_cls):
        """_call_with_retry re-initializes client on 401 and retries."""
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client

        call_count = 0

        def create_tweet_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise tweepy.Unauthorized(MagicMock(status_code=401))
            return MagicMock(data={"id": "42"})

        mock_client.create_tweet.side_effect = create_tweet_side_effect

        client = TwitterClient("key", "secret", "token", "token_secret")
        url, tweet_id = client.post("retry test")

        assert tweet_id == "42"
        assert call_count == 2
        # _client was reset and re-initialized (2 instantiations)
        assert mock_tweepy_cls.call_count == 2

    @patch("tweepy.Client")
    def test_auth_retry_on_forbidden(self, mock_tweepy_cls):
        """_call_with_retry re-initializes client on 403 and retries."""
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise tweepy.Forbidden(MagicMock(status_code=403))
            return MagicMock(data=MagicMock(id="555"))

        mock_client.get_tweet.side_effect = side_effect

        client = TwitterClient("key", "secret", "token", "token_secret")
        result = client.verify_post_exists("555")

        assert result is True
        assert call_count == 2

    @patch("tweepy.Client")
    def test_auth_retry_non_auth_error_propagates(self, mock_tweepy_cls):
        """Non-auth errors are not retried and propagate immediately."""
        from ortobahn.integrations.twitter import TwitterClient

        mock_client = MagicMock()
        mock_tweepy_cls.return_value = mock_client
        mock_client.get_me.side_effect = tweepy.TwitterServerError(MagicMock(status_code=500))

        client = TwitterClient("key", "secret", "token", "token_secret")
        # get_profile catches all exceptions, so it returns {}
        profile = client.get_profile()
        assert profile == {}
        # But the function was only called once (no retry)
        assert mock_client.get_me.call_count == 1
