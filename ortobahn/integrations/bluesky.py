"""Bluesky / AT Protocol client wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from atproto import Client

logger = logging.getLogger("ortobahn.bluesky")


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

    def login(self):
        if not self._logged_in:
            self.client.login(self.handle, self.app_password)
            self._logged_in = True
            logger.info(f"Logged in to Bluesky as {self.handle}")

    def post(self, text: str) -> tuple[str, str]:
        """Post text to Bluesky. Returns (uri, cid)."""
        self.login()
        response = self.client.send_post(text=text)
        logger.info(f"Posted to Bluesky: {text[:50]}...")
        return response.uri, response.cid

    def get_post_metrics(self, uri: str) -> PostMetrics:
        """Get engagement metrics for a specific post."""
        self.login()
        try:
            # Use get_posts to fetch post details
            response = self.client.app.bsky.feed.get_posts(params={"uris": [uri]})
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
        except Exception as e:
            logger.warning(f"Failed to get metrics for {uri}: {e}")

        return PostMetrics(uri=uri, cid="")

    def get_recent_post_uris(self, limit: int = 20) -> list[str]:
        """Get URIs of recent posts from our own feed."""
        self.login()
        try:
            response = self.client.app.bsky.feed.get_author_feed(params={"actor": self.handle, "limit": limit})
            return [item.post.uri for item in response.feed]
        except Exception as e:
            logger.warning(f"Failed to get recent posts: {e}")
            return []

    def get_profile(self) -> dict:
        """Get our profile info (follower count, etc)."""
        self.login()
        try:
            profile = self.client.app.bsky.actor.get_profile(params={"actor": self.handle})
            return {
                "handle": profile.handle,
                "display_name": profile.display_name,
                "followers_count": profile.followers_count,
                "follows_count": profile.follows_count,
                "posts_count": profile.posts_count,
            }
        except Exception as e:
            logger.warning(f"Failed to get profile: {e}")
            return {}
