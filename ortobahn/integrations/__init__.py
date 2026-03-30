"""Integration connectors for social media platforms."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class PlatformType(Enum):
    """Supported platform types."""
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    MEDIUM = "medium"
    SUBSTACK = "substack"
    GHOST = "ghost"


@dataclass
class RateLimitConfig:
    """Rate limit configuration per platform."""
    requests_per_minute: int
    requests_per_hour: int
    retry_after: int = 60


RATE_LIMITS = {
    PlatformType.LINKEDIN: RateLimitConfig(100, 1000),
    PlatformType.TWITTER: RateLimitConfig(300, 900),
    PlatformType.FACEBOOK: RateLimitConfig(200, 4800),
    PlatformType.MEDIUM: RateLimitConfig(60, 1000),
    PlatformType.SUBSTACK: RateLimitConfig(100, 2000),
    PlatformType.GHOST: RateLimitConfig(150, 3600),
}


class PlatformAdapter(ABC):
    """Abstract base class for platform adapters."""

    def __init__(self, credentials: Dict[str, str]):
        self.credentials = credentials
        self.client = httpx.AsyncClient(timeout=30.0)
        self.rate_limit = RATE_LIMITS[self.platform_type]
        self._request_times: list[datetime] = []

    @property
    @abstractmethod
    def platform_type(self) -> PlatformType:
        """Return the platform type."""
        pass

    @abstractmethod
    async def publish(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """Publish content to the platform."""
        pass

    @abstractmethod
    async def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Get OAuth authorization URL."""
        pass

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, str]:
        """Exchange authorization code for access token."""
        pass

    async def _check_rate_limit(self) -> None:
        """Check and enforce rate limits."""
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        self._request_times = [t for t in self._request_times if t > minute_ago]
        
        if len(self._request_times) >= self.rate_limit.requests_per_minute:
            wait_time = 60 - (now - self._request_times[0]).total_seconds()
            logger.warning(f"Rate limit reached for {self.platform_type.value}, waiting {wait_time}s")
            await asyncio.sleep(wait_time)
        
        self._request_times.append(now)

    async def _retry_request(self, func, *args, max_retries: int = 3, **kwargs) -> Any:
        """Retry request with exponential backoff."""
        for attempt in range(max_retries):
            try:
                await self._check_rate_limit()
                return await func(*args, **kwargs)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", self.rate_limit.retry_after))
                    logger.warning(f"Rate limited, retrying after {retry_after}s")
                    await asyncio.sleep(retry_after)
                elif e.response.status_code >= 500 and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Server error, retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    raise
            except httpx.RequestError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Request error: {e}, retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        raise Exception(f"Max retries exceeded for {self.platform_type.value}")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


class WebhookReceiver:
    """Base class for webhook receivers."""

    def __init__(self, secret: str):
        self.secret = secret

    @abstractmethod
    async def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature."""
        pass

    @abstractmethod
    async def process_event(self, event: Dict[str, Any]) -> None:
        """Process webhook event."""
        pass


__all__ = [
    "PlatformType",
    "PlatformAdapter",
    "WebhookReceiver",
    "RateLimitConfig",
    "RATE_LIMITS",
]
