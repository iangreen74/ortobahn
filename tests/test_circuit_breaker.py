"""Tests for circuit breaker module."""

from __future__ import annotations

import time

import pytest

from ortobahn.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    clear_registry,
    get_breaker,
)


class TestCircuitBreakerStates:
    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout_seconds=0.1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_closes_on_success(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout_seconds=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_reopens_on_failure(self):
        cb = CircuitBreaker("test", failure_threshold=1, reset_timeout_seconds=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_manual_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


class TestCircuitBreakerDecorator:
    def test_decorator_passes_through_on_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)

        @cb
        def good_fn():
            return 42

        assert good_fn() == 42

    def test_decorator_raises_on_open(self):
        cb = CircuitBreaker("test", failure_threshold=1)
        cb.record_failure()

        @cb
        def good_fn():
            return 42

        with pytest.raises(CircuitOpenError):
            good_fn()

    def test_decorator_records_failure(self):
        cb = CircuitBreaker("test", failure_threshold=3)

        @cb
        def bad_fn():
            raise ConnectionError("timeout")

        with pytest.raises(ConnectionError):
            bad_fn()

        assert cb.failure_count == 1

    def test_decorator_records_success(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()

        @cb
        def good_fn():
            return "ok"

        good_fn()
        assert cb.failure_count == 0


class TestRegistry:
    def setup_method(self):
        clear_registry()

    def test_get_breaker_creates_new(self):
        cb = get_breaker("service-a")
        assert cb.name == "service-a"

    def test_get_breaker_returns_same_instance(self):
        cb1 = get_breaker("service-b")
        cb2 = get_breaker("service-b")
        assert cb1 is cb2

    def test_clear_registry(self):
        get_breaker("service-c")
        clear_registry()
        cb = get_breaker("service-c")
        assert cb.failure_count == 0


class TestCircuitOpenError:
    def test_error_attributes(self):
        err = CircuitOpenError("test", time.monotonic() + 30)
        assert err.name == "test"
        assert "OPEN" in str(err)
