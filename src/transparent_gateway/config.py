import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Provider:
    name: str
    base_url: str
    auth_token: str


@dataclass
class GatewayConfig:
    access_token: str
    circuit_breaker_timeout: int
    circuit_breaker_threshold: int
    request_timeout: float
    providers: list[Provider]


def load_config(config_path: str | None = None) -> GatewayConfig:
    """从 YAML 文件加载配置"""
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    gateway = data.get("gateway", {})
    providers_data = data.get("providers", [])

    if not providers_data:
        raise ValueError("至少需要配置一个供应商")

    providers = [
        Provider(
            name=p["name"],
            base_url=p["base_url"].rstrip("/"),
            auth_token=p["auth_token"],
        )
        for p in providers_data
    ]

    return GatewayConfig(
        access_token=gateway.get("access_token", ""),
        circuit_breaker_timeout=gateway.get("circuit_breaker_timeout", 600),
        circuit_breaker_threshold=gateway.get("circuit_breaker_threshold", 5),
        request_timeout=gateway.get("request_timeout", 30.0),
        providers=providers,
    )


# 全局配置（延迟加载）
_config: GatewayConfig | None = None


def get_config() -> GatewayConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config
