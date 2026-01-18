"""Tests for config.py module."""
import pytest
import yaml
from pathlib import Path

from transparent_gateway.config import (
    Config,
    Provider,
    CircuitBreakerConfig,
    load_config,
    get_config,
    set_config,
)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_config(self, config_file: Path) -> None:
        """Valid YAML loads correctly."""
        config = load_config(str(config_file))
        assert config.access_token == "test-token"
        assert config.timeout == 30.0
        assert len(config.providers) == 2
        assert config.providers[0].name == "primary"
        assert config.providers[1].name == "backup"

    def test_load_missing_file(self) -> None:
        """Missing config raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")

    def test_load_empty_providers(self, tmp_path: Path) -> None:
        """Config with no providers raises ValueError."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("gateway:\n  timeout: 60\nproviders: []")
        with pytest.raises(ValueError, match="At least one provider"):
            load_config(str(config_path))

    def test_default_values(self, tmp_path: Path) -> None:
        """Missing fields use defaults."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("""
providers:
  - name: test
    base_url: https://test.com
    token: tk
""")
        config = load_config(str(config_path))
        assert config.timeout == 60.0
        assert config.access_token == ""
        assert config.circuit_breaker.failure_threshold == 5
        assert config.circuit_breaker.reset_timeout == 600
        assert config.circuit_breaker.probe_probability == 0.05

    def test_base_url_trailing_slash_stripped(self, tmp_path: Path) -> None:
        """Base URLs have trailing slashes removed."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("""
providers:
  - name: test
    base_url: https://test.com/
    token: tk
""")
        config = load_config(str(config_path))
        assert config.providers[0].base_url == "https://test.com"

    def test_circuit_breaker_config(self, tmp_path: Path) -> None:
        """Circuit breaker config loads correctly."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("""
gateway:
  circuit_breaker:
    failure_threshold: 10
    reset_timeout: 300
    probe_probability: 0.1
providers:
  - name: test
    base_url: https://test.com
    token: tk
""")
        config = load_config(str(config_path))
        assert config.circuit_breaker.failure_threshold == 10
        assert config.circuit_breaker.reset_timeout == 300
        assert config.circuit_breaker.probe_probability == 0.1


class TestGetConfig:
    """Tests for get_config function."""

    def test_get_config_returns_same_instance(self, config_file: Path, monkeypatch) -> None:
        """get_config returns the same instance."""
        monkeypatch.setenv("CONFIG_PATH", str(config_file))
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_set_config_overrides(self, sample_config: Config) -> None:
        """set_config can override the global config."""
        set_config(sample_config)
        config = get_config()
        assert config is sample_config
        assert config.access_token == "test-token"


class TestProvider:
    """Tests for Provider dataclass."""

    def test_provider_attributes(self) -> None:
        """Provider has expected attributes."""
        provider = Provider(name="test", base_url="https://api.test.com", token="sk-123")
        assert provider.name == "test"
        assert provider.base_url == "https://api.test.com"
        assert provider.token == "sk-123"


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig dataclass."""

    def test_default_values(self) -> None:
        """CircuitBreakerConfig has correct defaults."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.reset_timeout == 600
        assert config.probe_probability == 0.05

    def test_custom_values(self) -> None:
        """CircuitBreakerConfig accepts custom values."""
        config = CircuitBreakerConfig(
            failure_threshold=10,
            reset_timeout=120,
            probe_probability=0.1,
        )
        assert config.failure_threshold == 10
        assert config.reset_timeout == 120
        assert config.probe_probability == 0.1
