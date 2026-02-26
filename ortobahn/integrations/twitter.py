"""Twitter / X API client wrapper using tweepy."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
import tweepy

from ortobahn.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

logger = logging.getLogger("ortobahn.twitter")


def _download_image(url: str) -> bytes | None:
    """Download image from URL. Returns None on failure."""
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except Exception:
        logger.warning("Failed to download image: %s", url, exc_info=True)
        return None


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
        self._breaker = get_breaker("twitter", failure_threshold=5, reset_timeout_seconds=120)

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

    def _is_auth_error(self, exc: Exception) -> bool:
        """Return True if the exception is an authentication/authorization error."""
        return isinstance(exc, (tweepy.Unauthorized, tweepy.Forbidden))

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

    def _call_with_breaker(self, fn, *args, **kwargs):
        """Wrap _call_with_retry with circuit breaker logic."""
        state = self._breaker.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(
                self._breaker.name,
                self._breaker._last_failure_time + self._breaker.reset_timeout,
            )
        try:
            result = self._call_with_retry(fn, *args, **kwargs)
            self._breaker.record_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as e:
            if not self._is_auth_error(e):
                self._breaker.record_failure()
            raise

    def post(self, text: str, image_url: str | None = None) -> tuple[str, str]:
        """Post a tweet, optionally with image. Returns (tweet_url, tweet_id)."""
        client = self._get_client()
        kwargs: dict = {"text": text}

        if image_url:
            try:
                image_bytes = _download_image(image_url)
                if image_bytes:
                    import io

                    # Use v1.1 API for media upload (v2 doesn't support it directly)
                    auth = tweepy.OAuth1UserHandler(
                        self.api_key,
                        self.api_secret,
                        self.access_token,
                        self.access_token_secret,
                    )
                    api = tweepy.API(auth)
                    media = api.media_upload(filename="image.png", file=io.BytesIO(image_bytes))
                    kwargs["media_ids"] = [media.media_id]
            except Exception:
                logger.warning("Twitter image attach failed, posting text-only", exc_info=True)

        response = self._call_with_breaker(client.create_tweet, **kwargs)
        tweet_id = str(response.data["id"])
        tweet_url = f"https://x.com/i/status/{tweet_id}"
        logger.info(f"Posted to Twitter: {text[:50]}...")
        return tweet_url, tweet_id

    def get_post_metrics(self, tweet_id: str) -> TweetMetrics:
        """Get engagement metrics for a tweet."""
        client = self._get_client()
        try:
            response = self._call_with_breaker(
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
        except CircuitOpenError:
            raise
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
            response = self._call_with_breaker(
                client.get_tweet,
                tweet_id,
                tweet_fields=["id"],
            )
            return bool(response.data)
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning(f"Failed to verify tweet {tweet_id}: {e}")
            return None

    def get_profile(self) -> dict:
        """Get our profile info (follower count, etc)."""
        client = self._get_client()
        try:
            response = self._call_with_breaker(
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
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning(f"Failed to get Twitter profile: {e}")
        return {}
