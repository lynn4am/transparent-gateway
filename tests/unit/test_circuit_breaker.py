"""Tests for circuit_breaker.py module."""
import time
import pytest
from unittest.mock import Mock

from transparent_gateway.circuit_breaker import CircuitBreaker, CircuitBreakerManager


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_initial_state_closed(self) -> None:
        """New breaker starts closed."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=3)
        assert not breaker.is_open()
        assert breaker.failure_count == 0

    def test_trips_after_threshold(self) -> None:
        """Breaker opens after N failures."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        assert not breaker.is_open()
        breaker.record_failure()
        assert breaker.is_open()

    def test_success_resets_count(self) -> None:
        """Success resets failure count."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert breaker.failure_count == 0
        assert not breaker.is_open()

    def test_auto_reset_after_timeout(self) -> None:
        """Breaker auto-resets after timeout."""
        breaker = CircuitBreaker(timeout=1, failure_threshold=1)
        breaker.record_failure()
        assert breaker.is_open()
        time.sleep(1.1)
        assert not breaker.is_open()

    def test_auto_reset_callback(self) -> None:
        """Auto-reset triggers callback."""
        callback = Mock()
        breaker = CircuitBreaker(
            timeout=1,
            failure_threshold=1,
            on_auto_reset=callback,
            name="test-provider",
        )
        breaker.record_failure()
        assert breaker.is_open()
        time.sleep(1.1)
        assert not breaker.is_open()
        callback.assert_called_once_with("test-provider")

    def test_remaining_time(self) -> None:
        """remaining_time returns correct value."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=1)
        assert breaker.remaining_time() is None
        breaker.record_failure()
        remaining = breaker.remaining_time()
        assert remaining is not None
        assert 59 < remaining <= 60

    def test_remaining_time_decreases(self) -> None:
        """remaining_time decreases over time."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=1)
        breaker.record_failure()
        remaining1 = breaker.remaining_time()
        time.sleep(0.1)
        remaining2 = breaker.remaining_time()
        assert remaining2 is not None
        assert remaining1 is not None
        assert remaining2 < remaining1

    def test_manual_reset(self) -> None:
        """reset() clears state."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=1)
        breaker.record_failure()
        assert breaker.is_open()
        breaker.reset()
        assert not breaker.is_open()
        assert breaker.failure_count == 0

    def test_trip_method(self) -> None:
        """trip() immediately opens breaker."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=10)
        assert not breaker.is_open()
        breaker.trip()
        assert breaker.is_open()

    def test_failure_count_property(self) -> None:
        """failure_count returns current count."""
        breaker = CircuitBreaker(timeout=60, failure_threshold=5)
        assert breaker.failure_count == 0
        breaker.record_failure()
        assert breaker.failure_count == 1
        breaker.record_failure()
        assert breaker.failure_count == 2


class TestCircuitBreakerManager:
    """Tests for CircuitBreakerManager class."""

    def test_get_creates_breaker(self) -> None:
        """get() creates breaker on first access."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=3)
        breaker = mgr.get("provider1")
        assert isinstance(breaker, CircuitBreaker)

    def test_get_returns_same_breaker(self) -> None:
        """get() returns same breaker for same provider."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=3)
        b1 = mgr.get("provider1")
        b2 = mgr.get("provider1")
        assert b1 is b2

    def test_get_creates_different_breakers(self) -> None:
        """get() creates different breakers for different providers."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=3)
        b1 = mgr.get("provider1")
        b2 = mgr.get("provider2")
        assert b1 is not b2

    def test_status_returns_all(self) -> None:
        """status() returns all breaker states."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=1)
        mgr.get("p1").record_failure()
        mgr.get("p2")
        status = mgr.status()
        assert "p1" in status
        assert "p2" in status
        assert status["p1"]["is_open"] is True
        assert status["p2"]["is_open"] is False

    def test_status_includes_failure_count(self) -> None:
        """status() includes failure_count."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=5)
        mgr.get("p1").record_failure()
        mgr.get("p1").record_failure()
        status = mgr.status()
        assert status["p1"]["failure_count"] == 2

    def test_status_includes_remaining_time(self) -> None:
        """status() includes remaining_time."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=1)
        mgr.get("p1").record_failure()
        status = mgr.status()
        assert status["p1"]["remaining_time"] is not None
        assert status["p1"]["remaining_time"] > 0

    def test_reset_all(self) -> None:
        """reset_all() resets all breakers."""
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=1)
        mgr.get("p1").record_failure()
        mgr.get("p2").record_failure()
        assert mgr.get("p1").is_open()
        assert mgr.get("p2").is_open()
        mgr.reset_all()
        assert not mgr.get("p1").is_open()
        assert not mgr.get("p2").is_open()

    def test_on_auto_reset_callback(self) -> None:
        """Manager passes on_auto_reset to breakers."""
        callback = Mock()
        mgr = CircuitBreakerManager(
            timeout=1,
            failure_threshold=1,
            on_auto_reset=callback,
        )
        mgr.get("provider1").record_failure()
        assert mgr.get("provider1").is_open()
        time.sleep(1.1)
        assert not mgr.get("provider1").is_open()
        callback.assert_called_once_with("provider1")

    def test_breaker_inherits_config(self) -> None:
        """Breakers inherit timeout and threshold from manager."""
        mgr = CircuitBreakerManager(timeout=120, failure_threshold=10)
        breaker = mgr.get("test")
        assert breaker.timeout == 120
        assert breaker.failure_threshold == 10
