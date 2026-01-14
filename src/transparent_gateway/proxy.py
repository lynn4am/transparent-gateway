import json
import random
import time

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from transparent_gateway.config import get_config
from transparent_gateway.circuit_breaker import CircuitBreakerManager
from transparent_gateway.logging_config import get_logger, generate_request_id, request_id_var

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host",
              "content-length", "content-encoding"}

PROBE_PROBABILITY = 0.05  # 5% 概率探测熔断的供应商

_breaker_manager: CircuitBreakerManager | None = None


def get_breaker_manager() -> CircuitBreakerManager:
    global _breaker_manager
    if _breaker_manager is None:
        config = get_config()
        _breaker_manager = CircuitBreakerManager(
            config.circuit_breaker.reset_timeout,
            config.circuit_breaker.failure_threshold,
        )
    return _breaker_manager


def filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def replace_token(headers: dict, old: str, new: str) -> dict:
    if not old:
        return headers
    return {k: v.replace(old, new) if old in v else v for k, v in headers.items()}


def check_auth(headers: dict, token: str) -> bool:
    if not token:
        return True
    return any(token in v for v in headers.values())


def parse_body(body: bytes) -> tuple[str | None, bool]:
    try:
        data = json.loads(body)
        return data.get("model"), data.get("stream", False) is True
    except:
        return None, False


def select_provider(providers, breaker_mgr, logger):
    """
    选择供应商，策略：
    1. 5% 概率探测一个熔断的供应商（半开）
    2. 按顺序选择第一个未熔断的供应商
    3. 最后一个供应商永不熔断（保底）
    """
    # 5% 概率探测熔断的供应商
    if random.random() < PROBE_PROBABILITY:
        open_providers = [
            (i, p) for i, p in enumerate(providers[:-1])  # 排除最后一个
            if breaker_mgr.get(p.name).is_open()
        ]
        if open_providers:
            idx, provider = random.choice(open_providers)
            logger.info("probe_attempt", provider=provider.name)
            return idx, provider, True  # True = 这是探测请求

    # 正常选择：按顺序找第一个未熔断的
    for i, provider in enumerate(providers):
        is_last = (i == len(providers) - 1)
        breaker = breaker_mgr.get(provider.name)

        # 最后一个供应商永不熔断
        if is_last or not breaker.is_open():
            return i, provider, False

    # 不应该到这里，因为最后一个永不熔断
    return len(providers) - 1, providers[-1], False


async def proxy_request(request: Request) -> Response | StreamingResponse:
    request_id_var.set(generate_request_id())
    config = get_config()
    logger = get_logger()

    headers = dict(request.headers)
    if not check_auth(headers, config.access_token):
        logger.warning("auth_failed", reason="invalid_token")
        return Response(b'{"error":"Unauthorized"}', 401, media_type="application/json")

    body = await request.body()
    model, is_stream = parse_body(body)

    logger.request_start(
        method=request.method,
        path=request.url.path,
        query=request.url.query or None,
        model=model,
        stream=is_stream,
    )

    breaker_mgr = get_breaker_manager()

    if is_stream:
        return await _stream_request(request, headers, body, config, breaker_mgr)
    return await _normal_request(request, headers, body, config, breaker_mgr)


async def _try_provider(client, request, headers, body, config, provider, breaker_mgr, logger, is_probe):
    """尝试一个供应商，返回 (Response, success)"""
    breaker = breaker_mgr.get(provider.name)
    is_last = provider == config.providers[-1]

    start = time.time()
    url = f"{provider.base_url}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"

    req_headers = replace_token(filter_headers(headers), config.access_token, provider.token)
    logger.request_forward(provider.name, url, attempt=1, probe=is_probe)

    try:
        resp = await client.request(
            request.method, url, headers=req_headers,
            content=body, timeout=config.timeout
        )
        duration = (time.time() - start) * 1000

        if resp.status_code < 500:
            breaker.record_success()
            if is_probe:
                logger.info("probe_success", provider=provider.name)
            logger.request_success(provider.name, resp.status_code, duration)
            return Response(resp.content, resp.status_code,
                          headers=filter_headers(dict(resp.headers))), True

        # 5xx 错误
        if not is_last:  # 最后一个供应商不记录失败（不熔断）
            breaker.record_failure()
            if breaker.is_open():
                logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)

        logger.request_failure(provider.name, "http_error",
                             resp.content.decode(errors="replace")[:200],
                             resp.status_code, duration)
        return Response(resp.content, resp.status_code,
                       headers=filter_headers(dict(resp.headers))), False

    except httpx.RequestError as e:
        duration = (time.time() - start) * 1000
        if not is_last:
            breaker.record_failure()
            if breaker.is_open():
                logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)
        logger.request_failure(provider.name, type(e).__name__, str(e), duration_ms=duration)
        return None, False


async def _normal_request(request, headers, body, config, breaker_mgr) -> Response:
    logger = get_logger()
    providers = config.providers
    last_resp = None

    async with httpx.AsyncClient() as client:
        # 先尝试选择的供应商（可能是探测）
        idx, provider, is_probe = select_provider(providers, breaker_mgr, logger)
        resp, ok = await _try_provider(client, request, headers, body, config,
                                        provider, breaker_mgr, logger, is_probe)
        if ok:
            return resp
        if resp:
            last_resp = resp

        # 如果是探测失败，继续正常流程
        # 尝试剩余的供应商
        for i, p in enumerate(providers):
            if i == idx:  # 跳过已尝试的
                continue

            is_last = (i == len(providers) - 1)
            breaker = breaker_mgr.get(p.name)

            if not is_last and breaker.is_open():
                continue

            resp, ok = await _try_provider(client, request, headers, body, config,
                                           p, breaker_mgr, logger, False)
            if ok:
                return resp
            if resp:
                last_resp = resp

    if last_resp:
        return last_resp

    logger.error("all_providers_failed", error="unavailable")
    return Response(b'{"error":"Bad Gateway"}', 502, media_type="application/json")


async def _stream_request(request, headers, body, config, breaker_mgr) -> Response | StreamingResponse:
    logger = get_logger()
    providers = config.providers

    # 选择供应商
    idx, provider, is_probe = select_provider(providers, breaker_mgr, logger)

    # 构建尝试顺序：先选中的，再其他的
    attempt_order = [(idx, provider, is_probe)]
    for i, p in enumerate(providers):
        if i != idx:
            is_last = (i == len(providers) - 1)
            breaker = breaker_mgr.get(p.name)
            if is_last or not breaker.is_open():
                attempt_order.append((i, p, False))

    for idx, provider, is_probe in attempt_order:
        breaker = breaker_mgr.get(provider.name)
        is_last = (idx == len(providers) - 1)

        start = time.time()
        url = f"{provider.base_url}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"

        req_headers = replace_token(filter_headers(headers), config.access_token, provider.token)
        logger.request_forward(provider.name, url, attempt=1, probe=is_probe)

        client = httpx.AsyncClient(timeout=config.timeout)
        try:
            resp = await client.send(
                client.build_request(request.method, url, headers=req_headers, content=body),
                stream=True
            )
            duration = (time.time() - start) * 1000

            if resp.status_code >= 500:
                content = await resp.aread()
                await resp.aclose()
                await client.aclose()

                if not is_last:
                    breaker.record_failure()
                    if breaker.is_open():
                        logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)

                logger.request_failure(provider.name, "http_error",
                                      content.decode(errors="replace")[:200],
                                      resp.status_code, duration)
                continue

            breaker.record_success()
            if is_probe:
                logger.info("probe_success", provider=provider.name)
            logger.request_success(provider.name, resp.status_code, duration)

            async def stream():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()

            return StreamingResponse(stream(), resp.status_code,
                                   headers=filter_headers(dict(resp.headers)))

        except httpx.RequestError as e:
            duration = (time.time() - start) * 1000
            await client.aclose()

            if not is_last:
                breaker.record_failure()
                if breaker.is_open():
                    logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)

            logger.request_failure(provider.name, type(e).__name__, str(e), duration_ms=duration)

    logger.error("all_providers_failed", error="unavailable")
    return Response(b'{"error":"Bad Gateway"}', 502, media_type="application/json")
