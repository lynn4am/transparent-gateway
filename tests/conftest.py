"""Shared test fixtures for transparent-gateway tests."""
import logging
import pytest
import yaml
from pathlib import Path

from transparent_gateway.config import (
    Config,
    Provider,
    CircuitBreakerConfig,
    load_config,
    reset_config,
    set_config,
)
from transparent_gateway.circuit_breaker import CircuitBreaker, CircuitBreakerManager
from transparent_gateway.logging_config import GatewayLogger, reset_logger, set_logger
from transparent_gateway.proxy import reset_breaker_manager, set_breaker_manager


@pytest.fixture
def sample_providers() -> list[Provider]:
    """Sample provider list for testing."""
    return [
        Provider(name="primary", base_url="https://api.primary.com", token="pk-primary"),
        Provider(name="backup", base_url="https://api.backup.com", token="pk-backup"),
    ]


@pytest.fixture
def sample_config(sample_providers: list[Provider]) -> Config:
    """Sample config for testing."""
    return Config(
        access_token="test-token",
        timeout=30.0,
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=3,
            reset_timeout=60,
            probe_probability=0.05,
        ),
        providers=sample_providers,
    )


@pytest.fixture
def config_file(tmp_path: Path, sample_config: Config) -> Path:
    """Create a temporary config file."""
    config_data = {
        "gateway": {
            "access_token": sample_config.access_token,
            "timeout": sample_config.timeout,
            "circuit_breaker": {
                "failure_threshold": sample_config.circuit_breaker.failure_threshold,
                "reset_timeout": sample_config.circuit_breaker.reset_timeout,
                "probe_probability": sample_config.circuit_breaker.probe_probability,
            },
        },
        "providers": [
            {"name": p.name, "base_url": p.base_url, "token": p.token}
            for p in sample_config.providers
        ],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_data))
    return config_path


@pytest.fixture
def breaker_manager() -> CircuitBreakerManager:
    """Fresh breaker manager for each test."""
    return CircuitBreakerManager(timeout=60, failure_threshold=3)


@pytest.fixture
def mock_logger() -> GatewayLogger:
    """Mock logger that captures calls."""
    logger = logging.getLogger("test_gateway")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    return GatewayLogger(logger)


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset all global state before each test."""
    reset_config()
    reset_breaker_manager()
    reset_logger()
    yield
    reset_config()
    reset_breaker_manager()
    reset_logger()
