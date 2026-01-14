import logging

from fastapi import FastAPI, Request

from transparent_gateway.proxy import proxy_request, get_breaker_manager
from transparent_gateway.config import get_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = FastAPI(title="Transparent Gateway")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def gateway(request: Request):
    """透明代理所有请求"""
    return await proxy_request(request)


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
