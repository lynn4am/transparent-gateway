import time


class CircuitBreaker:
    """熔断器实现，支持连续失败阈值"""

    def __init__(self, timeout: int, failure_threshold: int = 5):
        self.timeout = timeout
        self.failure_threshold = failure_threshold
        self._tripped_at: float | None = None
        self._failure_count: int = 0

    def is_open(self) -> bool:
        """检查熔断器是否处于打开状态（不可用）"""
        if self._tripped_at is None:
            return False
        elapsed = time.time() - self._tripped_at
        if elapsed >= self.timeout:
            # 熔断时间已过，自动恢复
            self._tripped_at = None
            self._failure_count = 0
            return False
        return True

    def record_failure(self) -> None:
        """记录一次失败，连续失败达到阈值时触发熔断"""
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._tripped_at = time.time()

    def record_success(self) -> None:
        """记录一次成功，重置失败计数"""
        self._failure_count = 0

    def trip(self) -> None:
        """立即触发熔断（保留用于向后兼容）"""
        self._tripped_at = time.time()

    def reset(self) -> None:
        """重置熔断器（手动恢复）"""
        self._tripped_at = None
        self._failure_count = 0

    def remaining_time(self) -> float | None:
        """返回熔断剩余时间（秒），如果未熔断返回 None"""
        if self._tripped_at is None:
            return None
        remaining = self.timeout - (time.time() - self._tripped_at)
        return max(0, remaining)

    @property
    def failure_count(self) -> int:
        """返回当前连续失败次数"""
        return self._failure_count


class CircuitBreakerManager:
    """管理多个供应商的熔断器"""

    def __init__(self, timeout: int, failure_threshold: int = 5):
        self.timeout = timeout
        self.failure_threshold = failure_threshold
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, provider_name: str) -> CircuitBreaker:
        """获取指定供应商的熔断器"""
        if provider_name not in self._breakers:
            self._breakers[provider_name] = CircuitBreaker(
                self.timeout, self.failure_threshold
            )
        return self._breakers[provider_name]

    def status(self) -> dict[str, dict]:
        """返回所有熔断器的状态"""
        return {
            name: {
                "is_open": breaker.is_open(),
                "failure_count": breaker.failure_count,
                "remaining_time": breaker.remaining_time(),
            }
            for name, breaker in self._breakers.items()
        }

    def reset_all(self) -> None:
        """重置所有熔断器"""
        for breaker in self._breakers.values():
            breaker.reset()
