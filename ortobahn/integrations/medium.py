"""Medium API integration for article publishing."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("ortobahn.integrations.medium")

_BASE_URL = "https://api.medium.com/v1"


class MediumClient:
    """Publish articles to Medium via their API v1."""

    def __init__(self, integration_token: str):
        self.token = integration_token
        self._user_id: str | None = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        resp = httpx.get(f"{_BASE_URL}/me", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        self._user_id = resp.json()["data"]["id"]
        return self._user_id

    def post(
        self,
        title: str,
        body_markdown: str,
        tags: list[str] | None = None,
        publish_status: str = "draft",
    ) -> tuple[str, str]:
        """Create an article on Medium. Returns (url, post_id)."""
        user_id = self._get_user_id()
        payload: dict[str, str | list[str]] = {
            "title": title,
            "contentFormat": "markdown",
            "content": body_markdown,
            "publishStatus": publish_status,
        }
        if tags:
            payload["tags"] = tags[:5]  # Medium allows max 5 tags

        resp = httpx.post(
            f"{_BASE_URL}/users/{user_id}/posts",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return data["url"], data["id"]

    def get_post(self, post_id: str) -> dict | None:
        """Verify a post exists. Medium API v1 doesn't have a GET post endpoint,
        so we return None (inconclusive)."""
        return None
