import json
from collections.abc import AsyncIterator

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from transparent_gateway.config import get_config, Provider
from transparent_gateway.circuit_breaker import CircuitBreakerManager


# 需要跳过的 hop-by-hop headers
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}

# 全局熔断器管理器（延迟初始化）
_breaker_manager: CircuitBreakerManager | None = None


def get_breaker_manager() -> CircuitBreakerManager:
    global _breaker_manager
    if _breaker_manager is None:
        config = get_config()
        _breaker_manager = CircuitBreakerManager(config.circuit_breaker_timeout)
    return _breaker_manager


def filter_headers(headers: dict) -> dict:
    """过滤掉 hop-by-hop headers"""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


def replace_token_in_headers(
    headers: dict, access_token: str, provider_token: str
) -> dict:
    """在 headers 中替换 access_token 为 provider_token"""
    new_headers = {}
    for key, value in headers.items():
        if access_token in value:
            new_headers[key] = value.replace(access_token, provider_token)
        else:
            new_headers[key] = value
    return new_headers


def verify_access_token(headers: dict, access_token: str) -> bool:
    """验证请求头中是否包含 access_token"""
    if not access_token:
        return True  # 未配置 access_token，跳过验证
    for value in headers.values():
        if access_token in value:
            return True
    return False


def is_stream_request(body: bytes) -> bool:
    """检测请求是否为 streaming 请求"""
    try:
        data = json.loads(body)
        return data.get("stream", False) is True
    except (json.JSONDecodeError, AttributeError):
        return False


def build_target_url(provider: Provider, request: Request) -> str:
    """构建目标 URL"""
    target_url = f"{provider.base_url}{request.url.path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return target_url


def prepare_headers(
    original_headers: dict, access_token: str, provider_token: str
) -> dict:
    """准备转发的 headers"""
    headers = filter_headers(original_headers)
    return replace_token_in_headers(headers, access_token, provider_token)


def is_failure_response(status_code: int) -> bool:
    """判断响应是否为失败（需要触发熔断）"""
    return status_code >= 500


async def stream_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    timeout: float,
) -> AsyncIterator[bytes]:
    """流式转发响应"""
    async with client.stream(
        method=method,
        url=url,
        headers=headers,
        content=body,
        timeout=timeout,
    ) as response:
        async for chunk in response.aiter_bytes():
            yield chunk


async def proxy_request(request: Request) -> Response | StreamingResponse:
    """
    透明代理请求，实现故障转移和熔断机制，支持 streaming
    """
    config = get_config()
    breaker_manager = get_breaker_manager()

    # 获取原始 headers
    original_headers = dict(request.headers)

    # 验证 access_token
    if not verify_access_token(original_headers, config.access_token):
        return Response(
            content=b'{"error": "Unauthorized", "detail": "Invalid access token"}',
            status_code=401,
            media_type="application/json",
        )

    body = await request.body()
    is_streaming = is_stream_request(body)
    last_error: Exception | None = None
    last_response: httpx.Response | None = None

    # 对于 streaming 请求，需要保持 client 存活
    if is_streaming:
        return await _handle_streaming_request(
            request, body, original_headers, config, breaker_manager
        )
    else:
        return await _handle_normal_request(
            request, body, original_headers, config, breaker_manager
        )


async def _handle_normal_request(
    request: Request,
    body: bytes,
    original_headers: dict,
    config,
    breaker_manager: CircuitBreakerManager,
) -> Response:
    """处理普通（非 streaming）请求"""
    last_error: Exception | None = None
    last_response: httpx.Response | None = None

    async with httpx.AsyncClient() as client:
        for provider in config.providers:
            breaker = breaker_manager.get(provider.name)

            if breaker.is_open():
                continue

            try:
                url = build_target_url(provider, request)
                headers = prepare_headers(
                    original_headers, config.access_token, provider.auth_token
                )

                response = await client.request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=config.request_timeout,
                )

                if not is_failure_response(response.status_code):
                    return Response(
                        content=response.content,
                        status_code=response.status_code,
                        headers=filter_headers(dict(response.headers)),
                    )

                breaker.trip()
                last_response = response

            except httpx.RequestError as e:
                breaker.trip()
                last_error = e

    if last_response is not None:
        return Response(
            content=last_response.content,
            status_code=last_response.status_code,
            headers=filter_headers(dict(last_response.headers)),
        )

    error_detail = str(last_error) if last_error else "All providers unavailable"
    return Response(
        content=f'{{"error": "Bad Gateway", "detail": "{error_detail}"}}'.encode(),
        status_code=502,
        media_type="application/json",
    )


async def _handle_streaming_request(
    request: Request,
    body: bytes,
    original_headers: dict,
    config,
    breaker_manager: CircuitBreakerManager,
) -> Response | StreamingResponse:
    """处理 streaming 请求"""
    # 找到第一个可用的 provider
    for provider in config.providers:
        breaker = breaker_manager.get(provider.name)

        if breaker.is_open():
            continue

        url = build_target_url(provider, request)
        headers = prepare_headers(
            original_headers, config.access_token, provider.auth_token
        )

        # 创建一个长连接 client 用于 streaming
        client = httpx.AsyncClient()

        try:
            # 先发送请求获取响应头，检查是否成功
            response = await client.send(
                client.build_request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    content=body,
                ),
                stream=True,
            )

            if is_failure_response(response.status_code):
                await response.aclose()
                await client.aclose()
                breaker.trip()
                continue

            # 成功，返回 streaming response
            async def generate():
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                finally:
                    await response.aclose()
                    await client.aclose()

            return StreamingResponse(
                generate(),
                status_code=response.status_code,
                headers=filter_headers(dict(response.headers)),
            )

        except httpx.RequestError:
            await client.aclose()
            breaker.trip()
            continue

    # 所有 provider 都不可用
    return Response(
        content=b'{"error": "Bad Gateway", "detail": "All providers unavailable"}',
        status_code=502,
        media_type="application/json",
    )
