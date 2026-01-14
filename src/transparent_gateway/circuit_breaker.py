import time


class CircuitBreaker:
    """简单的熔断器实现"""

    def __init__(self, timeout: int):
        self.timeout = timeout
        self._tripped_at: float | None = None

    def is_open(self) -> bool:
        """检查熔断器是否处于打开状态（不可用）"""
        if self._tripped_at is None:
            return False
        elapsed = time.time() - self._tripped_at
        if elapsed >= self.timeout:
            # 熔断时间已过，自动恢复
            self._tripped_at = None
            return False
        return True

    def trip(self) -> None:
        """触发熔断"""
        self._tripped_at = time.time()

    def reset(self) -> None:
        """重置熔断器（手动恢复）"""
        self._tripped_at = None

    def remaining_time(self) -> float | None:
        """返回熔断剩余时间（秒），如果未熔断返回 None"""
        if self._tripped_at is None:
            return None
        remaining = self.timeout - (time.time() - self._tripped_at)
        return max(0, remaining)


class CircuitBreakerManager:
    """管理多个供应商的熔断器"""

    def __init__(self, timeout: int):
        self.timeout = timeout
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, provider_name: str) -> CircuitBreaker:
        """获取指定供应商的熔断器"""
        if provider_name not in self._breakers:
            self._breakers[provider_name] = CircuitBreaker(self.timeout)
        return self._breakers[provider_name]

    def status(self) -> dict[str, dict]:
        """返回所有熔断器的状态"""
        return {
            name: {
                "is_open": breaker.is_open(),
                "remaining_time": breaker.remaining_time(),
            }
            for name, breaker in self._breakers.items()
        }

    def reset_all(self) -> None:
        """重置所有熔断器"""
        for breaker in self._breakers.values():
            breaker.reset()
