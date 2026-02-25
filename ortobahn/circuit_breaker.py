"""Circuit breaker pattern for external API calls.

States:
  CLOSED    — normal operation, requests pass through
  OPEN      — fail-fast, raises CircuitOpenError immediately
  HALF_OPEN — probe: allows one request to test recovery
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from functools import wraps

logger = logging.getLogger("ortobahn.circuit_breaker")


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and requests are blocked."""

    def __init__(self, name: str, reset_at: float):
        self.name = name
        self.reset_at = reset_at
        remaining = max(0, reset_at - time.monotonic())
        super().__init__(f"Circuit '{name}' is OPEN (reset in {remaining:.0f}s)")


class CircuitBreaker:
    """Thread-safe circuit breaker with automatic state transitions."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout_seconds: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout_seconds

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() >= self._last_failure_time + self.reset_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info("Circuit '%s' transitioning to HALF_OPEN", self.name)
            return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit '%s' CLOSED (recovered)", self.name)
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit '%s' re-OPENED from HALF_OPEN", self.name)
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' OPENED after %d failures",
                    self.name,
                    self._failure_count,
                )

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def __call__(self, fn):
        """Use as a decorator to wrap a function with circuit breaker logic."""

        @wraps(fn)
        def wrapper(*args, **kwargs):
            state = self.state
            if state == CircuitState.OPEN:
                raise CircuitOpenError(
                    self.name, self._last_failure_time + self.reset_timeout
                )
            try:
                result = fn(*args, **kwargs)
                self.record_success()
                return result
            except CircuitOpenError:
                raise
            except Exception:
                self.record_failure()
                raise

        return wrapper


# ---------------------------------------------------------------------------
# Global registry — one breaker per logical service
# ---------------------------------------------------------------------------

_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(
    name: str,
    failure_threshold: int = 5,
    reset_timeout_seconds: float = 60.0,
) -> CircuitBreaker:
    """Get or create a named circuit breaker (singleton per name)."""
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(
                name,
                failure_threshold=failure_threshold,
                reset_timeout_seconds=reset_timeout_seconds,
            )
        return _registry[name]


def clear_registry() -> None:
    """Clear all breakers (for testing)."""
    with _registry_lock:
        _registry.clear()
