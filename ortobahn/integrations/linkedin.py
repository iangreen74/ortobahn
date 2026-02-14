"""LinkedIn API v2 client wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger("ortobahn.linkedin")

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"


@dataclass
class LinkedInPostMetrics:
    post_urn: str
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    impression_count: int = 0


class LinkedInClient:
    def __init__(self, access_token: str, person_urn: str):
        """
        access_token: OAuth 2.0 token with w_member_social scope.
        person_urn: e.g. "urn:li:person:AbCdEf123" or "urn:li:organization:12345".
        """
        self.access_token = access_token
        self.person_urn = person_urn
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def post(self, text: str) -> tuple[str, str]:
        """Create a text post on LinkedIn. Returns (post_url, post_urn)."""
        payload = {
            "author": self.person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        resp = requests.post(
            f"{LINKEDIN_API_BASE}/ugcPosts",
            headers=self._headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        post_urn = resp.headers.get("X-RestLi-Id", resp.json().get("id", ""))
        post_url = f"https://www.linkedin.com/feed/update/{post_urn}"
        logger.info(f"Posted to LinkedIn: {text[:50]}...")
        return post_url, post_urn

    def get_post_metrics(self, post_urn: str) -> LinkedInPostMetrics:
        """Get metrics for a LinkedIn post."""
        try:
            resp = requests.get(
                f"{LINKEDIN_API_BASE}/socialActions/{post_urn}",
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return LinkedInPostMetrics(
                post_urn=post_urn,
                like_count=data.get("likesSummary", {}).get("totalLikes", 0),
                comment_count=data.get("commentsSummary", {}).get("totalFirstLevelComments", 0),
            )
        except Exception as e:
            logger.warning(f"Failed to get LinkedIn metrics for {post_urn}: {e}")
        return LinkedInPostMetrics(post_urn=post_urn)
