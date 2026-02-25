"""Reddit API client wrapper using PRAW."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import praw
from prawcore.exceptions import (
    OAuthException,
    ResponseException,
)

from ortobahn.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

logger = logging.getLogger("ortobahn.reddit")


@dataclass
class RedditMetrics:
    post_id: str
    score: int = 0
    num_comments: int = 0
    upvote_ratio: float = 0.0


def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception is an auth/authz error from Reddit."""
    if isinstance(exc, OAuthException):
        return True
    if isinstance(exc, ResponseException):
        # ResponseException wraps HTTP errors; 401/403 are auth-related
        try:
            return exc.response.status_code in (401, 403)
        except AttributeError:
            pass
    return False


class RedditClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        user_agent: str = "ortobahn:v1.0 (by /u/ortobahn)",
        default_subreddit: str = "",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self.user_agent = user_agent
        self.default_subreddit = default_subreddit
        self._reddit: praw.Reddit | None = None
        self._breaker = get_breaker("reddit", failure_threshold=5, reset_timeout_seconds=120)

    def _get_reddit(self) -> praw.Reddit:
        if self._reddit is None:
            self._reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                username=self.username,
                password=self.password,
                user_agent=self.user_agent,
            )
            logger.info("Authenticated with Reddit as %s", self.username)
        return self._reddit

    def _check_breaker(self) -> None:
        """Raise CircuitOpenError if the breaker is OPEN."""
        state = self._breaker.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(
                self._breaker.name,
                self._breaker._last_failure_time + self._breaker.reset_timeout,
            )

    def post(self, text: str, subreddit: str = "", title: str = "") -> tuple[str, str]:
        """Submit a self-post. Returns (post_url, post_id)."""
        self._check_breaker()
        try:
            reddit = self._get_reddit()
            sub_name = subreddit or self.default_subreddit
            if not sub_name:
                raise ValueError("No subreddit specified and no default_subreddit configured")
            sub = reddit.subreddit(sub_name)
            # Use first line as title if not provided
            if not title:
                title = text.split("\n")[0][:300]
            submission = sub.submit(title=title, selftext=text)
            url = f"https://reddit.com{submission.permalink}"
            logger.info("Posted to r/%s: %s", sub_name, url)
            self._breaker.record_success()
            return url, str(submission.id)
        except (CircuitOpenError, ValueError):
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise

    def get_post_metrics(self, post_id: str) -> RedditMetrics:
        """Get metrics for a Reddit post."""
        self._check_breaker()
        try:
            reddit = self._get_reddit()
            submission = reddit.submission(id=post_id)
            metrics = RedditMetrics(
                post_id=post_id,
                score=submission.score,
                num_comments=submission.num_comments,
                upvote_ratio=submission.upvote_ratio,
            )
            self._breaker.record_success()
            return metrics
        except CircuitOpenError:
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise

    def verify_post_exists(self, post_id: str) -> bool:
        """Check if a Reddit post exists."""
        self._check_breaker()
        try:
            reddit = self._get_reddit()
            submission = reddit.submission(id=post_id)
            _ = submission.title  # Force fetch
            self._breaker.record_success()
            return True
        except CircuitOpenError:
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            return False

    def get_profile(self) -> dict:
        """Get the authenticated user's profile info."""
        reddit = self._get_reddit()
        user = reddit.user.me()
        return {
            "username": str(user.name),
            "karma": user.link_karma + user.comment_karma,
            "created_utc": user.created_utc,
        }
