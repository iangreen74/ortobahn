"""Bluesky / AT Protocol client wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from atproto import Client

from ortobahn.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

logger = logging.getLogger("ortobahn.bluesky")


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
class PostMetrics:
    uri: str
    cid: str
    like_count: int = 0
    repost_count: int = 0
    reply_count: int = 0
    quote_count: int = 0


class BlueskyClient:
    def __init__(self, handle: str, app_password: str):
        self.handle = handle
        self.app_password = app_password
        self.client = Client()
        self._logged_in = False
        self._breaker = get_breaker("bluesky", failure_threshold=5, reset_timeout_seconds=120)

    def login(self, force: bool = False):
        if force or not self._logged_in:
            self.client = Client()
            self.client.login(self.handle, self.app_password)
            self._logged_in = True
            logger.info(f"Logged in to Bluesky as {self.handle}")

    def _is_auth_error(self, exc: Exception) -> bool:
        """Return True if the exception is an authentication error."""
        error_str = str(exc).lower()
        return "auth" in error_str or "token" in error_str or "expired" in error_str

    def _call_with_retry(self, fn, *args, **kwargs):
        """Call a Bluesky API function, retrying once on auth failure."""
        self.login()
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if self._is_auth_error(e):
                logger.warning(f"Bluesky auth error, re-authenticating: {e}")
                self._logged_in = False
                self.login(force=True)
                return fn(*args, **kwargs)
            raise

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
        """Post text (optionally with image) to Bluesky. Returns (uri, cid)."""
        kwargs: dict = {"text": text}

        if image_url:
            try:
                image_bytes = _download_image(image_url)
                if image_bytes:
                    blob = self.client.upload_blob(image_bytes)
                    from atproto import models as atproto_models

                    kwargs["embed"] = atproto_models.AppBskyEmbedImages.Main(
                        images=[atproto_models.AppBskyEmbedImages.Image(alt=text[:100], image=blob.blob)]
                    )
            except Exception:
                logger.warning("Bluesky image attach failed, posting text-only", exc_info=True)

        response = self._call_with_breaker(self.client.send_post, **kwargs)
        logger.info(f"Posted to Bluesky: {text[:50]}...")
        return response.uri, response.cid

    def get_post_metrics(self, uri: str) -> PostMetrics:
        """Get engagement metrics for a specific post."""
        try:
            response = self._call_with_breaker(self.client.app.bsky.feed.get_posts, params={"uris": [uri]})
            if response.posts:
                post = response.posts[0]
                return PostMetrics(
                    uri=uri,
                    cid=post.cid,
                    like_count=post.like_count or 0,
                    repost_count=post.repost_count or 0,
                    reply_count=post.reply_count or 0,
                    quote_count=post.quote_count if hasattr(post, "quote_count") else 0,
                )
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning(f"Failed to get metrics for {uri}: {e}")

        return PostMetrics(uri=uri, cid="")

    def verify_post_exists(self, uri: str) -> bool | None:
        """Verify that a post actually exists on Bluesky.

        Returns True if found, False if definitively not found, None if
        verification was inconclusive (e.g. auth error, network error).
        """
        try:
            response = self._call_with_breaker(self.client.app.bsky.feed.get_posts, params={"uris": [uri]})
            return bool(response.posts)
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning(f"Failed to verify post {uri}: {e}")
            return None

    def get_recent_post_uris(self, limit: int = 20) -> list[str]:
        """Get URIs of recent posts from our own feed."""
        try:
            response = self._call_with_breaker(
                self.client.app.bsky.feed.get_author_feed,
                params={"actor": self.handle, "limit": limit},
            )
            return [item.post.uri for item in response.feed]
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning(f"Failed to get recent posts: {e}")
            return []

    def search_posts(
        self,
        query: str,
        limit: int = 25,
        sort: str = "latest",
        lang: str | None = "en",
    ) -> list[dict]:
        """Search Bluesky posts via app.bsky.feed.searchPosts.

        Free full-text search — Bluesky's primary discovery mechanism.
        Returns list of dicts with post data.
        """
        try:
            params: dict = {"q": query, "limit": min(limit, 100), "sort": sort}
            if lang:
                params["lang"] = lang
            response = self._call_with_breaker(
                self.client.app.bsky.feed.search_posts,
                params=params,
            )
            results = []
            for post in response.posts or []:
                record = post.record
                results.append(
                    {
                        "uri": post.uri,
                        "cid": post.cid,
                        "author_handle": post.author.handle,
                        "author_display_name": getattr(post.author, "display_name", "") or "",
                        "text": getattr(record, "text", "") if record else "",
                        "reply_count": post.reply_count or 0,
                        "like_count": post.like_count or 0,
                        "repost_count": post.repost_count or 0,
                        "indexed_at": post.indexed_at or "",
                        "parent_uri": (
                            getattr(record.reply.parent, "uri", None)
                            if record and hasattr(record, "reply") and record.reply
                            else None
                        ),
                    }
                )
            return results
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning("Bluesky search_posts failed for %r: %s", query, e)
            return []

    def search_actors(self, query: str, limit: int = 10) -> list[dict]:
        """Search for Bluesky users/accounts."""
        try:
            response = self._call_with_breaker(
                self.client.app.bsky.actor.search_actors,
                params={"q": query, "limit": min(limit, 25)},
            )
            results = []
            for actor in response.actors or []:
                results.append(
                    {
                        "handle": actor.handle,
                        "display_name": getattr(actor, "display_name", "") or "",
                        "description": getattr(actor, "description", "") or "",
                        "followers_count": getattr(actor, "followers_count", 0) or 0,
                        "follows_count": getattr(actor, "follows_count", 0) or 0,
                        "posts_count": getattr(actor, "posts_count", 0) or 0,
                    }
                )
            return results
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning("Bluesky search_actors failed for %r: %s", query, e)
            return []

    def get_post_thread(self, uri: str, depth: int = 3) -> list[dict]:
        """Get a post's thread (parent chain + replies) for conversation context."""
        try:
            response = self._call_with_breaker(
                self.client.app.bsky.feed.get_post_thread,
                params={"uri": uri, "depth": depth},
            )
            posts = []
            thread = response.thread
            if thread and hasattr(thread, "post"):
                post = thread.post
                record = post.record
                posts.append(
                    {
                        "uri": post.uri,
                        "author_handle": post.author.handle,
                        "text": getattr(record, "text", "") if record else "",
                        "like_count": post.like_count or 0,
                        "reply_count": post.reply_count or 0,
                    }
                )
                # Collect replies
                for reply in getattr(thread, "replies", None) or []:
                    if hasattr(reply, "post"):
                        rp = reply.post
                        rr = rp.record
                        posts.append(
                            {
                                "uri": rp.uri,
                                "author_handle": rp.author.handle,
                                "text": getattr(rr, "text", "") if rr else "",
                                "like_count": rp.like_count or 0,
                                "reply_count": rp.reply_count or 0,
                            }
                        )
            return posts
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning("Bluesky get_post_thread failed for %s: %s", uri, e)
            return []

    def get_profile(self) -> dict:
        """Get our profile info (follower count, etc)."""
        try:
            profile = self._call_with_breaker(
                self.client.app.bsky.actor.get_profile,
                params={"actor": self.handle},
            )
            return {
                "handle": profile.handle,
                "display_name": profile.display_name,
                "followers_count": profile.followers_count,
                "follows_count": profile.follows_count,
                "posts_count": profile.posts_count,
            }
        except CircuitOpenError:
            raise
        except Exception as e:
            logger.warning(f"Failed to get profile: {e}")
            return {}
