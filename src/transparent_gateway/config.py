import os
import threading
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
    probe_probability: float = 0.05


@dataclass
class Config:
    access_token: str
    timeout: float
    circuit_breaker: CircuitBreakerConfig
    providers: list[Provider]


def load_config(config_path: str | None = None) -> Config:
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    gw = data.get("gateway", {})
    cb = gw.get("circuit_breaker", {})

    providers = [
        Provider(p["name"], p["base_url"].rstrip("/"), p["token"])
        for p in data.get("providers", [])
    ]
    if not providers:
        raise ValueError("At least one provider required")

    return Config(
        access_token=gw.get("access_token", ""),
        timeout=gw.get("timeout", 60.0),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=cb.get("failure_threshold", 5),
            reset_timeout=cb.get("reset_timeout", 600),
            probe_probability=cb.get("probe_probability", 0.05),
        ),
        providers=providers,
    )


_config: Config | None = None
_config_lock = threading.Lock()


def get_config() -> Config:
    """获取全局配置（线程安全的单例）"""
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = load_config()
    return _config


def reset_config() -> None:
    """重置全局配置（仅用于测试）"""
    global _config
    with _config_lock:
        _config = None


def set_config(config: Config) -> None:
    """设置全局配置（仅用于测试）"""
    global _config
    with _config_lock:
        _config = config
