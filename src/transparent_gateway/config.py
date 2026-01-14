import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Provider:
    name: str
    base_url: str
    token: str


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    reset_timeout: int = 600


@dataclass
class Config:
    access_token: str
    timeout: float
    circuit_breaker: CircuitBreakerConfig
    providers: list[Provider]


def load_config(config_path: str | None = None) -> Config:
    """加载配置文件"""
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    gateway = data.get("gateway", {})
    providers_data = data.get("providers", [])

    if not providers_data:
        raise ValueError("At least one provider is required")

    # 解析供应商列表（支持新旧两种格式）
    providers = []
    for p in providers_data:
        providers.append(Provider(
            name=p["name"],
            base_url=p["base_url"].rstrip("/"),
            token=p.get("token") or p.get("auth_token", ""),
        ))

    # 解析熔断器配置（支持新旧两种格式）
    cb_config = gateway.get("circuit_breaker", {})
    circuit_breaker = CircuitBreakerConfig(
        failure_threshold=cb_config.get("failure_threshold")
            or gateway.get("circuit_breaker_threshold", 5),
        reset_timeout=cb_config.get("reset_timeout")
            or gateway.get("circuit_breaker_timeout", 600),
    )

    return Config(
        access_token=gateway.get("access_token", ""),
        timeout=gateway.get("timeout") or gateway.get("request_timeout", 60.0),
        circuit_breaker=circuit_breaker,
        providers=providers,
    )


# 全局配置
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
