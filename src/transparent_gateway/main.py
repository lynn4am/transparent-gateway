from fastapi import FastAPI, Request

from transparent_gateway.proxy import proxy_request, get_breaker_manager
from transparent_gateway.config import get_config
from transparent_gateway.logging_config import setup_logging

# 初始化日志系统
setup_logging()

app = FastAPI(title="Transparent Gateway")


@app.get("/_health")
async def health():
    """健康检查端点"""
    config = get_config()
    breaker_manager = get_breaker_manager()
    return {
        "status": "ok",
        "providers": [p.name for p in config.providers],
        "circuit_breakers": breaker_manager.status(),
    }


@app.post("/_reset_circuit")
async def reset_circuit():
    """手动重置所有熔断器"""
    breaker_manager = get_breaker_manager()
    breaker_manager.reset_all()
    return {"status": "all circuit breakers reset"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def gateway(request: Request):
    """透明代理所有请求"""
    return await proxy_request(request)
