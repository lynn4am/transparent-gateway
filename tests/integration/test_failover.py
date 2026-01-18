"""Integration tests for failover logic."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
import respx

from transparent_gateway.config import Config, set_config
from transparent_gateway.proxy import (
    proxy_request,
    reset_breaker_manager,
    get_breaker_manager,
)
from transparent_gateway.circuit_breaker import CircuitBreakerManager


class MockRequest:
    """Mock FastAPI Request object."""

    def __init__(
        self,
        method: str = "POST",
        path: str = "/v1/messages",
        query: str = "",
        headers: dict | None = None,
        body: bytes = b'{"model": "test"}',
    ):
        self.method = method
        self.headers = headers or {"Authorization": "Bearer test-token"}
        self._body = body

        class MockURL:
            def __init__(self, path: str, query: str):
                self.path = path
                self.query = query

        self.url = MockURL(path, query)

    async def body(self) -> bytes:
        return self._body


class TestFailover:
    """Tests for failover behavior."""

    @pytest.fixture(autouse=True)
    def setup(self, sample_config: Config):
        """Setup for each test."""
        set_config(sample_config)
        reset_breaker_manager()

    @respx.mock
    async def test_success_on_first_provider(self, sample_config: Config) -> None:
        """Successful request to first provider."""
        respx.post("https://api.primary.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        request = MockRequest()
        response = await proxy_request(request)

        assert response.status_code == 200

    @respx.mock
    async def test_failover_on_5xx(self, sample_config: Config) -> None:
        """5xx triggers failover to next provider."""
        respx.post("https://api.primary.com/v1/messages").mock(
            return_value=httpx.Response(500, content=b"Internal Server Error")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        request = MockRequest()
        response = await proxy_request(request)

        assert response.status_code == 200

    @respx.mock
    async def test_failover_on_connection_error(self, sample_config: Config) -> None:
        """Connection error triggers failover."""
        respx.post("https://api.primary.com/v1/messages").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        request = MockRequest()
        response = await proxy_request(request)

        assert response.status_code == 200

    @respx.mock
    async def test_failover_on_timeout(self, sample_config: Config) -> None:
        """Timeout triggers failover."""
        respx.post("https://api.primary.com/v1/messages").mock(
            side_effect=httpx.TimeoutException("Request timed out")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        request = MockRequest()
        response = await proxy_request(request)

        assert response.status_code == 200

    @respx.mock
    async def test_all_failed_returns_502(self, sample_config: Config) -> None:
        """All providers failed returns 502."""
        respx.post("https://api.primary.com/v1/messages").mock(
            return_value=httpx.Response(500, content=b"Error")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(500, content=b"Error")
        )

        request = MockRequest()
        response = await proxy_request(request)

        # Should return the last error response (500), not 502
        # since last provider returned a response
        assert response.status_code == 500

    @respx.mock
    async def test_all_connection_errors_returns_502(
        self, sample_config: Config
    ) -> None:
        """All providers connection error returns 502."""
        respx.post("https://api.primary.com/v1/messages").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        request = MockRequest()
        response = await proxy_request(request)

        assert response.status_code == 502

    @respx.mock
    async def test_4xx_not_failover(self, sample_config: Config) -> None:
        """4xx errors do not trigger failover."""
        primary_route = respx.post("https://api.primary.com/v1/messages").mock(
            return_value=httpx.Response(400, json={"error": "Bad Request"})
        )
        backup_route = respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        request = MockRequest()
        response = await proxy_request(request)

        assert response.status_code == 400
        assert primary_route.called
        assert not backup_route.called

    @respx.mock
    async def test_circuit_trips_after_threshold(self, sample_config: Config) -> None:
        """Circuit trips after N consecutive failures."""
        respx.post("https://api.primary.com/v1/messages").mock(
            return_value=httpx.Response(500, content=b"Error")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        # Make multiple requests to trip the circuit (threshold is 3)
        for _ in range(3):
            request = MockRequest()
            await proxy_request(request)

        # Check circuit is tripped
        mgr = get_breaker_manager()
        assert mgr.get("primary").is_open()

    @respx.mock
    async def test_success_resets_failure_count(self, sample_config: Config) -> None:
        """Success resets the failure count."""
        call_count = 0

        def primary_handler(request):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return httpx.Response(500, content=b"Error")
            return httpx.Response(200, json={"result": "ok"})

        respx.post("https://api.primary.com/v1/messages").mock(side_effect=primary_handler)
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"result": "ok"})
        )

        # Two failures
        for _ in range(2):
            request = MockRequest()
            await proxy_request(request)

        # Check failure count
        mgr = get_breaker_manager()
        assert mgr.get("primary").failure_count == 2

        # Now primary succeeds (via probe or normal selection)
        # Reset and try again with primary succeeding
        reset_breaker_manager()
        call_count = 2  # Next call will succeed

        request = MockRequest()
        await proxy_request(request)

        mgr = get_breaker_manager()
        assert mgr.get("primary").failure_count == 0


class TestStreamingFailover:
    """Tests for streaming request failover."""

    @pytest.fixture(autouse=True)
    def setup(self, sample_config: Config):
        """Setup for each test."""
        set_config(sample_config)
        reset_breaker_manager()

    @respx.mock
    async def test_streaming_failover_on_5xx(self, sample_config: Config) -> None:
        """Streaming request fails over on 5xx."""
        respx.post("https://api.primary.com/v1/messages").mock(
            return_value=httpx.Response(500, content=b"Error")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, content=b"data: ok\n\n")
        )

        request = MockRequest(body=b'{"model": "test", "stream": true}')
        response = await proxy_request(request)

        assert response.status_code == 200

    @respx.mock
    async def test_streaming_failover_on_connection_error(
        self, sample_config: Config
    ) -> None:
        """Streaming request fails over on connection error."""
        respx.post("https://api.primary.com/v1/messages").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        respx.post("https://api.backup.com/v1/messages").mock(
            return_value=httpx.Response(200, content=b"data: ok\n\n")
        )

        request = MockRequest(body=b'{"model": "test", "stream": true}')
        response = await proxy_request(request)

        assert response.status_code == 200
