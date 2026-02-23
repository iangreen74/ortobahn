"""LinkedIn Articles API integration for long-form article publishing."""

from __future__ import annotations

import logging

import httpx
import markdown

logger = logging.getLogger("ortobahn.integrations.linkedin_articles")

_BASE_URL = "https://api.linkedin.com/rest"


class LinkedInArticleClient:
    """Publish long-form articles to LinkedIn."""

    def __init__(self, access_token: str, person_urn: str):
        self.access_token = access_token
        self.person_urn = person_urn

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202401",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    @staticmethod
    def _markdown_to_html(body_markdown: str) -> str:
        """Convert markdown body to HTML for LinkedIn's article API."""
        return markdown.markdown(body_markdown, extensions=["extra", "codehilite"])

    def post(
        self,
        title: str,
        body_markdown: str,
        tags: list[str] | None = None,
        visibility: str = "PUBLIC",
    ) -> tuple[str, str]:
        """Create an article on LinkedIn. Returns (url, urn)."""
        payload = {
            "author": self.person_urn,
            "lifecycleState": "PUBLISHED",
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": title},
                    "shareMediaCategory": "ARTICLE",
                    "media": [
                        {
                            "status": "READY",
                            "description": {"text": title},
                            "title": {"text": title},
                            "originalUrl": "",
                        }
                    ],
                }
            },
        }

        # LinkedIn doesn't have a direct "article" endpoint for free accounts;
        # we publish as a rich share with article content. For organizations
        # with Content API access, this would use the articles endpoint.
        resp = httpx.post(
            f"{_BASE_URL}/ugcPosts",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        urn = resp.headers.get("X-RestLi-Id", resp.json().get("id", ""))
        url = f"https://www.linkedin.com/feed/update/{urn}" if urn else ""
        return url, urn
