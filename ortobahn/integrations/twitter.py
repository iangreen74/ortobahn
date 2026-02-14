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

    def post(self, text: str) -> tuple[str, str]:
        """Post a tweet. Returns (tweet_url, tweet_id)."""
        client = self._get_client()
        response = client.create_tweet(text=text)
        tweet_id = str(response.data["id"])
        tweet_url = f"https://x.com/i/status/{tweet_id}"
        logger.info(f"Posted to Twitter: {text[:50]}...")
        return tweet_url, tweet_id

    def get_post_metrics(self, tweet_id: str) -> TweetMetrics:
        """Get engagement metrics for a tweet."""
        client = self._get_client()
        try:
            response = client.get_tweet(
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
