"""Substack integration for article publishing (undocumented web API)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("ortobahn.integrations.substack")


class SubstackClient:
    """Publish articles to Substack via their undocumented web API.

    Creates drafts by default (conservative). Requires either
    email/password auth or a session cookie.
    """

    def __init__(
        self,
        subdomain: str,
        email: str = "",
        password: str = "",
        session_cookie: str = "",
    ):
        self.subdomain = subdomain
        self.base_url = f"https://{subdomain}.substack.com"
        self._email = email
        self._password = password
        self._session_cookie = session_cookie
        self._authenticated = False

    def _authenticate(self) -> None:
        """Authenticate via email/password or use existing session cookie."""
        if self._session_cookie:
            self._authenticated = True
            return

        if not self._email or not self._password:
            raise RuntimeError("Substack requires either session_cookie or email+password")

        resp = httpx.post(
            f"{self.base_url}/api/v1/login",
            json={"email": self._email, "password": self._password},
            timeout=15,
        )
        resp.raise_for_status()
        # Extract session cookie from response
        for cookie in resp.cookies.jar:
            if cookie.name == "substack.sid":
                self._session_cookie = cookie.value or ""
                break
        self._authenticated = True

    def _cookies(self) -> dict:
        if not self._authenticated:
            self._authenticate()
        return {"substack.sid": self._session_cookie}

    def post(
        self,
        title: str,
        body_markdown: str,
        tags: list[str] | None = None,
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create a draft (or published post) on Substack. Returns (url, draft_id)."""
        # Convert markdown to Substack's expected format (HTML-like body)
        import markdown as md_lib

        html_body = md_lib.markdown(body_markdown, extensions=["extra"])

        payload: dict[str, Any] = {
            "draft_title": title,
            "draft_body": html_body,
            "draft_bylines": [],
            "type": "newsletter",
        }
        if tags:
            payload["draft_section_id"] = None  # Tags mapped to sections in Substack

        resp = httpx.post(
            f"{self.base_url}/api/v1/drafts",
            json=payload,
            cookies=self._cookies(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        draft_id = str(data.get("id", ""))
        slug = data.get("slug", draft_id)

        if publish and draft_id:
            try:
                pub_resp = httpx.post(
                    f"{self.base_url}/api/v1/drafts/{draft_id}/publish",
                    json={"send": True},
                    cookies=self._cookies(),
                    timeout=30,
                )
                pub_resp.raise_for_status()
            except Exception:
                logger.warning("Failed to publish Substack draft; leaving as draft")

        url = f"{self.base_url}/p/{slug}" if slug else ""
        return url, draft_id
