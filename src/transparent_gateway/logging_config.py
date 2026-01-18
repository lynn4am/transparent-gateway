import json
import logging
import sys
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# 请求上下文，用于追踪单个请求
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def generate_request_id() -> str:
    """生成请求 ID"""
    return uuid.uuid4().hex[:8]


class StructuredFormatter(logging.Formatter):
    """结构化 JSON 日志格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # 添加请求 ID（如果存在）
        request_id = request_id_var.get()
        if request_id:
            log_data["req_id"] = request_id

        # 添加额外字段（通过 extra 参数传入）
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


class GatewayLogger:
    """网关日志记录器，提供结构化日志方法"""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, **fields: Any) -> None:
        """内部日志方法"""
        extra = {"extra_fields": fields} if fields else {}
        self._logger.log(level, msg, extra=extra)

    def info(self, msg: str, **fields: Any) -> None:
        self._log(logging.INFO, msg, **fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._log(logging.WARNING, msg, **fields)

    def debug(self, msg: str, **fields: Any) -> None:
        self._log(logging.DEBUG, msg, **fields)

    # 业务日志方法
    def request_start(
        self,
        method: str,
        path: str,
        query: str | None = None,
        model: str | None = None,
        stream: bool = False,
    ) -> None:
        """记录请求开始"""
        self.info(
            "request_start",
            method=method,
            path=path,
            query=query,
            model=model,
            stream=stream,
        )

    def request_forward(
        self,
        provider: str,
        target_url: str,
        attempt: int = 1,
        probe: bool = False,
    ) -> None:
        """记录请求转发"""
        self.info(
            "request_forward",
            provider=provider,
            target_url=target_url,
            attempt=attempt,
            probe=probe,
        )

    def request_success(
        self,
        provider: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """记录请求成功"""
        self.info(
            "request_success",
            provider=provider,
            status=status_code,
            duration_ms=round(duration_ms, 2),
        )

    def request_failure(
        self,
        provider: str,
        error_type: str,
        error_msg: str,
        status_code: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """记录请求失败"""
        fields: dict[str, Any] = {
            "provider": provider,
            "error_type": error_type,
            "error_msg": error_msg[:500],  # 限制错误消息长度
        }
        if status_code is not None:
            fields["status"] = status_code
        if duration_ms is not None:
            fields["duration_ms"] = round(duration_ms, 2)
        self.error("request_failure", **fields)

    def circuit_breaker_event(
        self,
        provider: str,
        action: str,  # "opened", "closed", "half_open"
        failure_count: int | None = None,
    ) -> None:
        """记录熔断器事件"""
        fields: dict[str, Any] = {"provider": provider, "action": action}
        if failure_count is not None:
            fields["failure_count"] = failure_count
        self.warning("circuit_breaker", **fields)


def setup_logging(
    log_dir: str = "logs",
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    console_output: bool = True,
) -> GatewayLogger:
    """
    配置日志系统

    Args:
        log_dir: 日志目录
        log_level: 日志级别
        max_bytes: 单个日志文件最大大小（默认 10MB）
        backup_count: 保留的历史日志文件数量（默认 5 个）
        console_output: 是否输出到控制台

    Returns:
        GatewayLogger 实例
    """
    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 获取根日志记录器
    logger = logging.getLogger("transparent_gateway")
    logger.setLevel(getattr(logging, log_level.upper()))
    logger.handlers.clear()

    # JSON 格式化器
    json_formatter = StructuredFormatter()

    # 文件处理器（带轮转）
    file_handler = RotatingFileHandler(
        filename=log_path / "gateway.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)
    logger.addHandler(file_handler)

    # 控制台处理器（可选）
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(json_formatter)
        logger.addHandler(console_handler)

    return GatewayLogger(logger)


# 全局日志实例
_gateway_logger: GatewayLogger | None = None
_gateway_logger_lock = threading.Lock()


def get_logger() -> GatewayLogger:
    """获取全局日志实例（线程安全的单例）"""
    global _gateway_logger
    if _gateway_logger is None:
        with _gateway_logger_lock:
            if _gateway_logger is None:
                _gateway_logger = setup_logging()
    return _gateway_logger


def reset_logger() -> None:
    """重置全局日志实例（仅用于测试）"""
    global _gateway_logger
    with _gateway_logger_lock:
        _gateway_logger = None


def set_logger(logger: GatewayLogger) -> None:
    """设置全局日志实例（仅用于测试）"""
    global _gateway_logger
    with _gateway_logger_lock:
        _gateway_logger = logger
