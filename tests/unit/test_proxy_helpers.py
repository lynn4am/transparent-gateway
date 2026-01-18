"""Tests for proxy.py helper functions."""
import pytest
from unittest.mock import patch, Mock

from transparent_gateway.config import Provider
from transparent_gateway.circuit_breaker import CircuitBreakerManager
from transparent_gateway.logging_config import GatewayLogger
from transparent_gateway.proxy import (
    filter_headers,
    replace_token,
    check_auth,
    parse_body,
    select_provider,
    _classify_error,
)
import httpx


class TestFilterHeaders:
    """Tests for filter_headers function."""

    def test_removes_hop_by_hop(self) -> None:
        """Hop-by-hop headers are removed."""
        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Host": "example.com",
            "Authorization": "Bearer token",
        }
        filtered = filter_headers(headers)
        assert "Content-Type" in filtered
        assert "Authorization" in filtered
        assert "Connection" not in filtered
        assert "Host" not in filtered

    def test_case_insensitive(self) -> None:
        """Header filtering is case-insensitive."""
        headers = {"CONTENT-LENGTH": "100", "X-Custom": "value"}
        filtered = filter_headers(headers)
        assert "CONTENT-LENGTH" not in filtered
        assert "X-Custom" in filtered

    def test_removes_content_encoding(self) -> None:
        """content-encoding header is removed."""
        headers = {"Content-Encoding": "gzip", "Accept": "application/json"}
        filtered = filter_headers(headers)
        assert "Content-Encoding" not in filtered
        assert "Accept" in filtered

    def test_empty_headers(self) -> None:
        """Empty headers returns empty dict."""
        assert filter_headers({}) == {}

    def test_preserves_custom_headers(self) -> None:
        """Custom headers are preserved."""
        headers = {"X-Custom-Header": "value", "X-Request-Id": "123"}
        filtered = filter_headers(headers)
        assert filtered == headers


class TestReplaceToken:
    """Tests for replace_token function."""

    def test_replaces_in_values(self) -> None:
        """Token is replaced in header values."""
        headers = {"Authorization": "Bearer old-token"}
        result = replace_token(headers, "old-token", "new-token")
        assert result["Authorization"] == "Bearer new-token"

    def test_empty_old_token_returns_unchanged(self) -> None:
        """Empty old token returns headers unchanged."""
        headers = {"Authorization": "Bearer something"}
        result = replace_token(headers, "", "new")
        assert result["Authorization"] == "Bearer something"

    def test_no_match_returns_unchanged(self) -> None:
        """No match returns value unchanged."""
        headers = {"X-Other": "value"}
        result = replace_token(headers, "token", "new")
        assert result["X-Other"] == "value"

    def test_replaces_multiple_occurrences(self) -> None:
        """Multiple occurrences in same value are replaced."""
        headers = {"X-Auth": "token-token-end"}
        result = replace_token(headers, "token", "new")
        assert result["X-Auth"] == "new-new-end"

    def test_replaces_in_multiple_headers(self) -> None:
        """Token in multiple headers is replaced."""
        headers = {"Authorization": "Bearer tk", "X-Token": "tk"}
        result = replace_token(headers, "tk", "new-tk")
        assert result["Authorization"] == "Bearer new-tk"
        assert result["X-Token"] == "new-tk"


class TestCheckAuth:
    """Tests for check_auth function."""

    def test_empty_token_always_passes(self) -> None:
        """Empty required token always passes."""
        assert check_auth({"Authorization": "anything"}, "") is True
        assert check_auth({}, "") is True

    def test_token_in_authorization_header(self) -> None:
        """Token found in Authorization header passes."""
        assert check_auth({"Authorization": "Bearer secret"}, "secret") is True

    def test_token_in_any_header(self) -> None:
        """Token found in any header value passes."""
        assert check_auth({"X-Custom": "Bearer secret"}, "secret") is True

    def test_token_not_found_fails(self) -> None:
        """Missing token fails."""
        assert check_auth({"Authorization": "Bearer other"}, "secret") is False

    def test_empty_headers_fails(self) -> None:
        """Empty headers with required token fails."""
        assert check_auth({}, "secret") is False

    def test_partial_match_passes(self) -> None:
        """Partial token match in value passes."""
        assert check_auth({"X-Auth": "prefix-secret-suffix"}, "secret") is True


class TestParseBody:
    """Tests for parse_body function."""

    def test_valid_json_with_model(self) -> None:
        """Valid JSON extracts model."""
        body = b'{"model": "gpt-4", "stream": true}'
        model, stream = parse_body(body)
        assert model == "gpt-4"
        assert stream is True

    def test_stream_default_false(self) -> None:
        """Missing stream defaults to False."""
        body = b'{"model": "gpt-4"}'
        _, stream = parse_body(body)
        assert stream is False

    def test_stream_false_explicit(self) -> None:
        """Explicit stream: false works."""
        body = b'{"model": "gpt-4", "stream": false}'
        _, stream = parse_body(body)
        assert stream is False

    def test_invalid_json(self) -> None:
        """Invalid JSON returns None, False."""
        model, stream = parse_body(b"not json")
        assert model is None
        assert stream is False

    def test_empty_body(self) -> None:
        """Empty body returns None, False."""
        model, stream = parse_body(b"")
        assert model is None
        assert stream is False

    def test_non_dict_json(self) -> None:
        """Non-dict JSON returns None, False."""
        model, stream = parse_body(b'["array"]')
        assert model is None
        assert stream is False

    def test_null_json(self) -> None:
        """null JSON returns None, False."""
        model, stream = parse_body(b"null")
        assert model is None
        assert stream is False

    def test_missing_model(self) -> None:
        """Missing model returns None."""
        body = b'{"stream": true}'
        model, stream = parse_body(body)
        assert model is None
        assert stream is True

    def test_unicode_body(self) -> None:
        """Unicode in body is handled."""
        body = '{"model": "模型"}'.encode("utf-8")
        model, _ = parse_body(body)
        assert model == "模型"


class TestSelectProvider:
    """Tests for select_provider function."""

    def test_selects_first_available(
        self,
        sample_providers: list[Provider],
        breaker_manager: CircuitBreakerManager,
        mock_logger: GatewayLogger,
    ) -> None:
        """Selects first non-tripped provider."""
        idx, provider, is_probe = select_provider(
            sample_providers, breaker_manager, mock_logger
        )
        assert idx == 0
        assert provider.name == "primary"
        assert is_probe is False

    def test_skips_tripped_provider(
        self,
        sample_providers: list[Provider],
        breaker_manager: CircuitBreakerManager,
        mock_logger: GatewayLogger,
    ) -> None:
        """Skips tripped providers."""
        # Trip primary
        breaker = breaker_manager.get("primary")
        for _ in range(3):
            breaker.record_failure()

        idx, provider, is_probe = select_provider(
            sample_providers, breaker_manager, mock_logger
        )
        assert provider.name == "backup"
        assert is_probe is False

    def test_last_provider_never_skipped(
        self,
        sample_providers: list[Provider],
        breaker_manager: CircuitBreakerManager,
        mock_logger: GatewayLogger,
    ) -> None:
        """Last provider is always available."""
        # Trip both providers
        for _ in range(3):
            breaker_manager.get("primary").record_failure()
        for _ in range(10):
            breaker_manager.get("backup").record_failure()

        # Should still get backup since it's last
        idx, provider, is_probe = select_provider(
            sample_providers, breaker_manager, mock_logger
        )
        assert provider.name == "backup"

    @patch("transparent_gateway.proxy.random.random")
    def test_probe_tripped_provider(
        self,
        mock_random: Mock,
        sample_providers: list[Provider],
        breaker_manager: CircuitBreakerManager,
        mock_logger: GatewayLogger,
    ) -> None:
        """Probability to probe tripped provider."""
        mock_random.return_value = 0.01  # < 0.05

        # Trip primary
        for _ in range(3):
            breaker_manager.get("primary").record_failure()

        idx, provider, is_probe = select_provider(
            sample_providers, breaker_manager, mock_logger
        )
        assert provider.name == "primary"
        assert is_probe is True

    @patch("transparent_gateway.proxy.random.random")
    def test_no_probe_when_random_high(
        self,
        mock_random: Mock,
        sample_providers: list[Provider],
        breaker_manager: CircuitBreakerManager,
        mock_logger: GatewayLogger,
    ) -> None:
        """No probe when random value is high."""
        mock_random.return_value = 0.5  # > 0.05

        # Trip primary
        for _ in range(3):
            breaker_manager.get("primary").record_failure()

        idx, provider, is_probe = select_provider(
            sample_providers, breaker_manager, mock_logger
        )
        assert provider.name == "backup"
        assert is_probe is False

    def test_custom_probe_probability(
        self,
        sample_providers: list[Provider],
        breaker_manager: CircuitBreakerManager,
        mock_logger: GatewayLogger,
    ) -> None:
        """Custom probe probability is respected."""
        # Trip primary
        for _ in range(3):
            breaker_manager.get("primary").record_failure()

        # With 0% probability, should never probe
        with patch("transparent_gateway.proxy.random.random", return_value=0.01):
            idx, provider, is_probe = select_provider(
                sample_providers, breaker_manager, mock_logger, probe_probability=0.0
            )
            assert provider.name == "backup"
            assert is_probe is False

        # With 100% probability, should always probe
        with patch("transparent_gateway.proxy.random.random", return_value=0.5):
            idx, provider, is_probe = select_provider(
                sample_providers, breaker_manager, mock_logger, probe_probability=1.0
            )
            assert provider.name == "primary"
            assert is_probe is True


class TestClassifyError:
    """Tests for _classify_error function."""

    def test_timeout_error(self) -> None:
        """TimeoutException is classified as timeout."""
        exc = httpx.TimeoutException("timeout")
        assert _classify_error(exc) == "timeout"

    def test_connect_error(self) -> None:
        """ConnectError is classified as connection_error."""
        exc = httpx.ConnectError("connection failed")
        assert _classify_error(exc) == "connection_error"

    def test_other_request_error(self) -> None:
        """Other RequestErrors are classified as request_error."""
        exc = httpx.RequestError("some error")
        assert _classify_error(exc) == "request_error"
