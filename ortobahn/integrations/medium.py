"""Medium API integration for article publishing."""

from __future__ import annotations

import logging

import httpx

from ortobahn.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

logger = logging.getLogger("ortobahn.integrations.medium")

_BASE_URL = "https://api.medium.com/v1"


def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception is an HTTP 401 or 403 (auth/authz)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 403)
    return False


class MediumClient:
    """Publish articles to Medium via their API v1."""

    def __init__(self, integration_token: str):
        self.token = integration_token
        self._user_id: str | None = None
        self._breaker = get_breaker("medium", failure_threshold=5, reset_timeout_seconds=120)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _check_breaker(self) -> None:
        """Raise CircuitOpenError if the breaker is OPEN."""
        state = self._breaker.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(
                self._breaker.name,
                self._breaker._last_failure_time + self._breaker.reset_timeout,
            )

    def _get_user_id(self) -> str:
        if self._user_id:
            return self._user_id
        self._check_breaker()
        try:
            resp = httpx.get(f"{_BASE_URL}/me", headers=self._headers(), timeout=15)
            resp.raise_for_status()
            self._user_id = resp.json()["data"]["id"]
            self._breaker.record_success()
            return self._user_id
        except CircuitOpenError:
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise

    def post(
        self,
        title: str,
        body_markdown: str,
        tags: list[str] | None = None,
        publish_status: str = "draft",
    ) -> tuple[str, str]:
        """Create an article on Medium. Returns (url, post_id)."""
        user_id = self._get_user_id()
        self._check_breaker()
        payload: dict[str, str | list[str]] = {
            "title": title,
            "contentFormat": "markdown",
            "content": body_markdown,
            "publishStatus": publish_status,
        }
        if tags:
            payload["tags"] = tags[:5]  # Medium allows max 5 tags

        try:
            resp = httpx.post(
                f"{_BASE_URL}/users/{user_id}/posts",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            self._breaker.record_success()
            return data["url"], data["id"]
        except CircuitOpenError:
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise

    def get_post(self, post_id: str) -> dict | None:
        """Check if a post exists by fetching the user's publications list.

        Medium API v1 does not have a GET /posts/:id endpoint, so we look up
        the user's publications and check for the post_id there. Returns the
        publication dict if found, or None if inconclusive.
        """
        self._check_breaker()
        try:
            user_id = self._get_user_id()
            resp = httpx.get(
                f"{_BASE_URL}/users/{user_id}/publications",
                headers=self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            publications = resp.json().get("data", [])
            self._breaker.record_success()
            for pub in publications:
                if pub.get("id") == post_id:
                    return pub
        except CircuitOpenError:
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            logger.warning(f"Failed to look up Medium post {post_id}: {e}")
        return None

    def verify_post(self, url: str) -> bool | None:
        """Lightweight check that a published article URL is reachable.

        Sends an HTTP HEAD request to the article URL.
        Returns True if reachable (2xx), False if definitively not found (404/410),
        or None if verification was inconclusive (network error, other status).
        """
        self._check_breaker()
        try:
            resp = httpx.head(url, timeout=10, follow_redirects=True)
            if resp.status_code < 400:
                self._breaker.record_success()
                return True
            if resp.status_code in (404, 410):
                self._breaker.record_success()
                return False
            return None
        except CircuitOpenError:
            raise
        except Exception as e:
            self._breaker.record_failure()
            logger.warning(f"Failed to verify Medium article URL {url}: {e}")
            return None
