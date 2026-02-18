"""Sliding-window rate limiter implemented as ASGI middleware.

Uses in-memory storage with automatic TTL cleanup — no external
dependencies required.  Endpoints are grouped into tiers with different
request-per-minute limits.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# ---------------------------------------------------------------------------
# Tier configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateTier:
    """A named rate-limit tier with a requests-per-minute cap."""

    name: str
    requests_per_minute: int
    prefixes: tuple[str, ...]


# Default tiers — evaluated top-to-bottom; first match wins.
DEFAULT_TIERS: tuple[RateTier, ...] = (
    RateTier(name="public", requests_per_minute=120, prefixes=("/health", "/api/public/", "/glass")),
    RateTier(name="auth", requests_per_minute=10, prefixes=("/api/auth/login", "/api/auth/register")),
    RateTier(name="onboard", requests_per_minute=5, prefixes=("/api/onboard",)),
)

# Anything that doesn't match an explicit tier uses the general limit.
GENERAL_TIER_NAME = "general"


# ---------------------------------------------------------------------------
# Sliding-window bucket
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    """Sliding-window counter for a single (ip, tier) pair."""

    timestamps: list[float] = field(default_factory=list)

    def hit(self, now: float, window: float) -> int:
        """Record a request and return the count within *window* seconds."""
        # Prune expired entries first
        cutoff = now - window
        self.timestamps = [ts for ts in self.timestamps if ts > cutoff]
        self.timestamps.append(now)
        return len(self.timestamps)

    def oldest_in_window(self, now: float, window: float) -> float:
        """Return the oldest timestamp still within the window."""
        cutoff = now - window
        self.timestamps = [ts for ts in self.timestamps if ts > cutoff]
        if self.timestamps:
            return self.timestamps[0]
        return now


# ---------------------------------------------------------------------------
# In-memory store with periodic cleanup
# ---------------------------------------------------------------------------


class RateLimitStore:
    """Thread-safe-ish in-memory store keyed by ``(client_ip, tier_name)``."""

    def __init__(self, cleanup_interval: float = 60.0) -> None:
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)
        self._cleanup_interval = cleanup_interval
        self._last_cleanup: float = 0.0

    def check(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int, float]:
        """Check whether *key* is within its rate limit.

        Returns ``(allowed, current_count, retry_after_seconds)``.
        """
        now = time.monotonic()
        self._maybe_cleanup(now, window)

        bucket = self._buckets[key]
        count = bucket.hit(now, window)
        if count > limit:
            oldest = bucket.oldest_in_window(now, window)
            retry_after = max(0.0, window - (now - oldest))
            return False, count, retry_after
        return True, count, 0.0

    def _maybe_cleanup(self, now: float, window: float) -> None:
        """Periodically drop buckets with no recent activity."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - window
        empty_keys = [k for k, b in self._buckets.items() if not b.timestamps or b.timestamps[-1] <= cutoff]
        for k in empty_keys:
            del self._buckets[k]

    @property
    def buckets(self) -> dict[str, _Bucket]:
        """Expose buckets for testing/introspection."""
        return self._buckets


# ---------------------------------------------------------------------------
# ASGI Middleware
# ---------------------------------------------------------------------------


def _client_ip(scope: Scope) -> str:
    """Extract the client IP from the ASGI scope."""
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


def _match_tier(path: str, tiers: Sequence[RateTier]) -> RateTier | None:
    """Return the first tier whose prefix matches *path*, or ``None``."""
    for tier in tiers:
        for prefix in tier.prefixes:
            if path == prefix or path.startswith(prefix):
                return tier
    return None


class RateLimitMiddleware:
    """ASGI middleware that enforces per-IP sliding-window rate limits.

    Parameters
    ----------
    app:
        The wrapped ASGI application.
    enabled:
        Kill-switch — when *False* the middleware is a no-op passthrough.
    default_rpm:
        Requests-per-minute for the general (unmatched) tier.
    tiers:
        Sequence of ``RateTier`` objects evaluated in order.
    store:
        Optional external ``RateLimitStore`` (useful for testing).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        default_rpm: int = 60,
        tiers: Sequence[RateTier] | None = None,
        store: RateLimitStore | None = None,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.default_rpm = default_rpm
        self.tiers = tiers if tiers is not None else DEFAULT_TIERS
        self.store = store or RateLimitStore()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "/")
        ip = _client_ip(scope)

        tier = _match_tier(path, self.tiers)
        if tier is not None:
            tier_name = tier.name
            limit = tier.requests_per_minute
        else:
            tier_name = GENERAL_TIER_NAME
            limit = self.default_rpm

        key = f"{ip}:{tier_name}"
        allowed, count, retry_after = self.store.check(key, limit)

        if not allowed:
            response = JSONResponse(
                {"detail": "Rate limit exceeded. Try again later."},
                status_code=429,
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
