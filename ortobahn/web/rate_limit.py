"""Rate limiting middleware for FastAPI using sliding window algorithm."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from threading import Lock

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter tracking requests per IP address.

    Stores request timestamps in memory and enforces configurable limits.
    Automatically cleans up old entries to prevent memory bloat.
    """

    def __init__(
        self,
        app,
        enabled: bool = True,
        default_rpm: int = 60,
        window_seconds: int = 60,
    ):
        """Initialize rate limiter.

        Args:
            app: FastAPI application instance
            enabled: Whether rate limiting is active
            default_rpm: Default requests per minute (per IP)
            window_seconds: Time window in seconds for rate calculation
        """
        super().__init__(app)
        self.enabled = enabled
        self.default_rpm = default_rpm
        self.window_seconds = window_seconds

        # Store list of request timestamps per IP
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

        # Track last cleanup to prevent excessive lock contention
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # Clean up every 60 seconds

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, checking proxy headers."""
        # Check X-Forwarded-For (set by load balancers/proxies)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # X-Forwarded-For can be a comma-separated list; take first IP
            return forwarded.split(",")[0].strip()

        # Check X-Real-IP (set by some proxies)
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return "unknown"

    def _cleanup_old_entries(self, current_time: float) -> None:
        """Remove old timestamps beyond the window to free memory.

        Only runs periodically to avoid excessive lock contention.
        """
        if current_time - self._last_cleanup < self._cleanup_interval:
            return

        cutoff = current_time - self.window_seconds
        with self._lock:
            for ip in list(self._requests.keys()):
                # Filter out old timestamps
                self._requests[ip] = [ts for ts in self._requests[ip] if ts > cutoff]
                # Remove IP entirely if no recent requests
                if not self._requests[ip]:
                    del self._requests[ip]

            self._last_cleanup = current_time

    def _should_allow_request(self, ip: str, current_time: float, limit: int) -> tuple[bool, int, float]:
        """Check if request should be allowed under rate limit.

        Returns:
            (allowed, remaining, reset_time) tuple
        """
        cutoff = current_time - self.window_seconds

        with self._lock:
            # Filter to only recent requests within the window
            recent_requests = [ts for ts in self._requests[ip] if ts > cutoff]
            self._requests[ip] = recent_requests

            count = len(recent_requests)
            allowed = count < limit
            remaining = max(0, limit - count - 1) if allowed else 0

            # Calculate reset time (when oldest request will expire)
            if recent_requests:
                oldest_ts = min(recent_requests)
                reset_time = oldest_ts + self.window_seconds
            else:
                reset_time = current_time + self.window_seconds

            # Record this request if allowed
            if allowed:
                self._requests[ip].append(current_time)

            return allowed, remaining, reset_time

    def _get_rate_limit(self, request: Request) -> int:
        """Get rate limit for this request.

        Can be extended to support per-endpoint or per-user limits.
        """
        # Future enhancement: check user authentication and return custom limits
        # For now, use default for all requests
        return self.default_rpm

    def _should_skip_rate_limit(self, request: Request) -> bool:
        """Determine if rate limiting should be skipped for this request."""
        path = request.url.path

        # Always allow health checks
        if path == "/health":
            return True

        # Skip static files
        if path.startswith("/static/"):
            return True

        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request through rate limiter."""
        # Skip if disabled or not applicable
        if not self.enabled or self._should_skip_rate_limit(request):
            return await call_next(request)

        current_time = time.time()
        ip = self._get_client_ip(request)
        limit = self._get_rate_limit(request)

        # Periodic cleanup of old entries
        self._cleanup_old_entries(current_time)

        # Check rate limit
        allowed, remaining, reset_time = self._should_allow_request(ip, current_time, limit)

        # Build response
        if allowed:
            response = await call_next(request)
        else:
            # Rate limit exceeded - return 429
            retry_after = int(reset_time - current_time)
            logger.warning(f"Rate limit exceeded for IP {ip}: {limit} req/{self.window_seconds}s")
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit of {limit} requests per {self.window_seconds} seconds exceeded",
                    "retry_after": retry_after,
                },
            )
            response.headers["Retry-After"] = str(retry_after)

        # Add rate limit headers to all responses
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_time))

        return response
