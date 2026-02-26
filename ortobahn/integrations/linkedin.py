"""LinkedIn API v2 client wrapper."""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass

import httpx

from ortobahn.circuit_breaker import CircuitOpenError, CircuitState, get_breaker

logger = logging.getLogger("ortobahn.linkedin")

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"


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
class LinkedInPostMetrics:
    post_urn: str
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    impression_count: int = 0


def _handle_auth_error(fn):
    """Decorator that catches 401 HTTPStatusError and marks the client as
    having a credential issue.  LinkedIn OAuth tokens require manual
    user re-auth so we cannot silently refresh -- we log a warning and
    set a flag that downstream agents (SRE, publisher) can inspect."""

    @functools.wraps(fn)
    def wrapper(self: LinkedInClient, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                logger.warning(
                    "LinkedIn API returned 401 -- access token is expired or "
                    "revoked.  Manual re-authentication is required."
                )
                self._credentials_valid = False
                raise
            raise

    return wrapper


def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception is an HTTP 401 or 403 (auth/authz)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 403)
    return False


class LinkedInClient:
    def __init__(self, access_token: str, person_urn: str):
        """
        access_token: OAuth 2.0 token with w_member_social scope.
        person_urn: e.g. "urn:li:person:AbCdEf123" or "urn:li:organization:12345".
        """
        self.access_token = access_token
        self.person_urn = person_urn
        self._credentials_valid = True
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        self._breaker = get_breaker("linkedin", failure_threshold=5, reset_timeout_seconds=120)

    def _check_breaker(self) -> None:
        """Raise CircuitOpenError if the breaker is OPEN."""
        state = self._breaker.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(
                self._breaker.name,
                self._breaker._last_failure_time + self._breaker.reset_timeout,
            )

    @_handle_auth_error
    def post(self, text: str, image_url: str | None = None) -> tuple[str, str]:
        """Create a post on LinkedIn, optionally with image. Returns (post_url, post_urn)."""
        self._check_breaker()

        share_content: dict = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "NONE",
        }

        if image_url:
            try:
                image_bytes = _download_image(image_url)
                if image_bytes:
                    asset_urn = self._upload_image(image_bytes)
                    if asset_urn:
                        share_content["shareMediaCategory"] = "IMAGE"
                        share_content["media"] = [
                            {
                                "status": "READY",
                                "media": asset_urn,
                            }
                        ]
            except Exception:
                logger.warning("LinkedIn image attach failed, posting text-only", exc_info=True)

        payload = {
            "author": self.person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        try:
            resp = httpx.post(
                f"{LINKEDIN_API_BASE}/ugcPosts",
                headers=self._headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            post_urn = resp.headers.get("X-RestLi-Id", resp.json().get("id", ""))
            post_url = f"https://www.linkedin.com/feed/update/{post_urn}"
            logger.info(f"Posted to LinkedIn: {text[:50]}...")
            self._breaker.record_success()
            return post_url, post_urn
        except CircuitOpenError:
            raise
        except Exception as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise

    def _upload_image(self, image_bytes: bytes) -> str | None:
        """Upload an image to LinkedIn and return the asset URN."""
        register_body = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": self.person_urn,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        }
        reg_resp = httpx.post(
            f"{LINKEDIN_API_BASE}/assets?action=registerUpload",
            headers=self._headers,
            json=register_body,
            timeout=30,
        )
        reg_resp.raise_for_status()
        reg_data = reg_resp.json()["value"]
        upload_url = reg_data["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"][
            "uploadUrl"
        ]
        asset_urn = reg_data["asset"]

        httpx.put(
            upload_url,
            content=image_bytes,
            headers={**self._headers, "Content-Type": "image/png"},
            timeout=60,
        )
        return asset_urn

    @_handle_auth_error
    def get_post_metrics(self, post_urn: str) -> LinkedInPostMetrics:
        """Get metrics for a LinkedIn post."""
        self._check_breaker()
        try:
            resp = httpx.get(
                f"{LINKEDIN_API_BASE}/socialActions/{post_urn}",
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self._breaker.record_success()
            return LinkedInPostMetrics(
                post_urn=post_urn,
                like_count=data.get("likesSummary", {}).get("totalLikes", 0),
                comment_count=data.get("commentsSummary", {}).get("totalFirstLevelComments", 0),
            )
        except httpx.HTTPStatusError as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise
        except CircuitOpenError:
            raise
        except Exception as e:
            self._breaker.record_failure()
            logger.warning(f"Failed to get LinkedIn metrics for {post_urn}: {e}")
        return LinkedInPostMetrics(post_urn=post_urn)

    @_handle_auth_error
    def verify_post_exists(self, post_urn: str) -> bool | None:
        """Verify that a post actually exists on LinkedIn.

        Uses the UGC Posts endpoint to look up the post by URN.
        Returns True if found (200), False if definitively not found (404),
        None if verification was inconclusive (e.g. network error).
        """
        self._check_breaker()
        try:
            resp = httpx.get(
                f"{LINKEDIN_API_BASE}/ugcPosts/{post_urn}",
                headers=self._headers,
                timeout=30,
            )
            if resp.status_code == 200:
                self._breaker.record_success()
                return True
            if resp.status_code == 404:
                self._breaker.record_success()
                return False
            resp.raise_for_status()
            return None
        except httpx.HTTPStatusError as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise
        except CircuitOpenError:
            raise
        except Exception as e:
            self._breaker.record_failure()
            logger.warning(f"Failed to verify LinkedIn post {post_urn}: {e}")
            return None

    @_handle_auth_error
    def get_profile(self) -> dict:
        """Get basic profile info from /v2/me.

        Returns a dict with first_name, last_name, and vanity_name.
        Used by SRE agent for platform health checks.
        """
        self._check_breaker()
        try:
            resp = httpx.get(
                f"{LINKEDIN_API_BASE}/me",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._breaker.record_success()
            return {
                "first_name": data.get("localizedFirstName", ""),
                "last_name": data.get("localizedLastName", ""),
                "vanity_name": data.get("vanityName", ""),
            }
        except httpx.HTTPStatusError as e:
            if not _is_auth_error(e):
                self._breaker.record_failure()
            raise
        except CircuitOpenError:
            raise
        except Exception as e:
            self._breaker.record_failure()
            logger.warning(f"Failed to get LinkedIn profile: {e}")
            return {}
