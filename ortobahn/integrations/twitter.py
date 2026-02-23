"""Twitter / X API client wrapper using tweepy."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import tweepy

logger = logging.getLogger("ortobahn.twitter")


@dataclass
class TweetMetrics:
    tweet_id: str
    like_count: int = 0
    retweet_count: int = 0
    reply_count: int = 0
    impression_count: int = 0


class TwitterClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_token_secret: str,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_token_secret = access_token_secret
        self._client: tweepy.Client | None = None

    def _get_client(self) -> tweepy.Client:
        if self._client is None:
            self._client = tweepy.Client(
                consumer_key=self.api_key,
                consumer_secret=self.api_secret,
                access_token=self.access_token,
                access_token_secret=self.access_token_secret,
            )
            logger.info("Authenticated with Twitter API v2")
        return self._client

    def _call_with_retry(self, fn, *args, **kwargs):
        """Call a Twitter API function, retrying once on auth failure."""
        self._get_client()
        try:
            return fn(*args, **kwargs)
        except (tweepy.Unauthorized, tweepy.Forbidden) as e:
            logger.warning(f"Twitter auth error, re-authenticating: {e}")
            self._client = None
            self._get_client()
            return fn(*args, **kwargs)

    def post(self, text: str) -> tuple[str, str]:
        """Post a tweet. Returns (tweet_url, tweet_id)."""
        client = self._get_client()
        response = self._call_with_retry(client.create_tweet, text=text)
        tweet_id = str(response.data["id"])
        tweet_url = f"https://x.com/i/status/{tweet_id}"
        logger.info(f"Posted to Twitter: {text[:50]}...")
        return tweet_url, tweet_id

    def get_post_metrics(self, tweet_id: str) -> TweetMetrics:
        """Get engagement metrics for a tweet."""
        client = self._get_client()
        try:
            response = self._call_with_retry(
                client.get_tweet,
                tweet_id,
                tweet_fields=["public_metrics"],
            )
            if response.data and response.data.public_metrics:
                m = response.data.public_metrics
                return TweetMetrics(
                    tweet_id=tweet_id,
                    like_count=m.get("like_count", 0),
                    retweet_count=m.get("retweet_count", 0),
                    reply_count=m.get("reply_count", 0),
                    impression_count=m.get("impression_count", 0),
                )
        except Exception as e:
            logger.warning(f"Failed to get Twitter metrics for {tweet_id}: {e}")
        return TweetMetrics(tweet_id=tweet_id)

    def verify_post_exists(self, tweet_id: str) -> bool | None:
        """Verify that a tweet actually exists on Twitter.

        Returns True if found, False if definitively not found, None if
        verification was inconclusive (e.g. auth error, network error).
        """
        client = self._get_client()
        try:
            response = self._call_with_retry(
                client.get_tweet,
                tweet_id,
                tweet_fields=["id"],
            )
            return bool(response.data)
        except Exception as e:
            logger.warning(f"Failed to verify tweet {tweet_id}: {e}")
            return None

    def get_profile(self) -> dict:
        """Get our profile info (follower count, etc)."""
        client = self._get_client()
        try:
            response = self._call_with_retry(
                client.get_me,
                user_fields=["public_metrics"],
            )
            if response.data:
                m = response.data.public_metrics or {}
                return {
                    "username": response.data.username,
                    "followers_count": m.get("followers_count", 0),
                    "following_count": m.get("following_count", 0),
                    "tweet_count": m.get("tweet_count", 0),
                }
        except Exception as e:
            logger.warning(f"Failed to get Twitter profile: {e}")
        return {}
