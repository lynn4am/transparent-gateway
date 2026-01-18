"""Integration tests for API endpoints."""
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
import httpx

from transparent_gateway.main import app
from transparent_gateway.config import Config, Provider, CircuitBreakerConfig, set_config
from transparent_gateway.proxy import set_breaker_manager
from transparent_gateway.circuit_breaker import CircuitBreakerManager


@pytest.fixture
def client(sample_config: Config) -> TestClient:
    """TestClient with mocked config."""
    set_config(sample_config)
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for /_health endpoint."""

    def test_health_returns_status(self, client: TestClient) -> None:
        """/_health returns ok status."""
        response = client.get("/_health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "providers" in data
        assert "circuit_breakers" in data

    def test_health_lists_providers(
        self, client: TestClient, sample_config: Config
    ) -> None:
        """/_health lists all providers."""
        response = client.get("/_health")
        data = response.json()
        assert data["providers"] == ["primary", "backup"]

    def test_health_shows_circuit_breaker_status(self, client: TestClient) -> None:
        """/_health shows circuit breaker status."""
        # First make a request to initialize breakers
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=3)
        mgr.get("primary").record_failure()
        set_breaker_manager(mgr)

        response = client.get("/_health")
        data = response.json()
        assert "primary" in data["circuit_breakers"]
        assert data["circuit_breakers"]["primary"]["failure_count"] == 1


class TestResetCircuitEndpoint:
    """Tests for /_reset_circuit endpoint."""

    def test_reset_returns_success(self, client: TestClient) -> None:
        """/_reset_circuit returns success."""
        response = client.post("/_reset_circuit")
        assert response.status_code == 200
        assert "reset" in response.json()["status"]

    def test_reset_clears_breakers(self, client: TestClient) -> None:
        """/_reset_circuit clears all circuit breakers."""
        # Trip a breaker first
        mgr = CircuitBreakerManager(timeout=60, failure_threshold=1)
        mgr.get("primary").record_failure()
        assert mgr.get("primary").is_open()
        set_breaker_manager(mgr)

        # Reset
        response = client.post("/_reset_circuit")
        assert response.status_code == 200

        # Check breaker is reset
        health_response = client.get("/_health")
        data = health_response.json()
        assert data["circuit_breakers"]["primary"]["is_open"] is False


class TestProxyEndpoint:
    """Tests for proxy endpoints."""

    def test_proxy_auth_failure(self, client: TestClient) -> None:
        """Missing auth token returns 401."""
        response = client.post("/v1/messages", json={"model": "test"})
        assert response.status_code == 401

    def test_proxy_auth_success_with_token(self, client: TestClient) -> None:
        """Valid auth token allows request."""
        with patch("transparent_gateway.proxy.httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"result": "ok"}'
            mock_response.headers = {"Content-Type": "application/json"}

            mock_instance = AsyncMock()
            mock_instance.request.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.post(
                "/v1/messages",
                json={"model": "claude-3"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200

    def test_proxy_passes_body(self, client: TestClient) -> None:
        """Request body is passed to provider."""
        with patch("transparent_gateway.proxy.httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"result": "ok"}'
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.post(
                "/v1/messages",
                json={"model": "claude-3", "prompt": "Hello"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200

            # Verify the request was made with correct content
            call_args = mock_instance.request.call_args
            assert b"claude-3" in call_args.kwargs.get("content", b"")

    def test_proxy_replaces_token(self, client: TestClient) -> None:
        """Gateway token is replaced with provider token."""
        with patch("transparent_gateway.proxy.httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.content = b'{"result": "ok"}'
            mock_response.headers = {}

            mock_instance = AsyncMock()
            mock_instance.request.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.post(
                "/v1/messages",
                json={"model": "test"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200

            # Verify the Authorization header was replaced (case-insensitive)
            call_args = mock_instance.request.call_args
            headers = call_args.kwargs.get("headers", {})
            # Headers may be lowercase
            auth_value = headers.get("Authorization") or headers.get("authorization", "")
            assert "pk-primary" in auth_value
