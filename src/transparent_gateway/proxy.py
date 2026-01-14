import json
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from transparent_gateway.config import get_config, Provider
from transparent_gateway.circuit_breaker import CircuitBreakerManager
from transparent_gateway.logging_config import get_logger, generate_request_id, request_id_var

# Hop-by-hop headers (不应转发)
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host"}

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
    """返回 (model, is_stream)"""
    try:
        data = json.loads(body)
        return data.get("model"), data.get("stream", False) is True
    except:
        return None, False


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


async def _normal_request(request, headers, body, config, breaker_mgr) -> Response:
    logger = get_logger()
    last_error = None
    last_resp = None

    async with httpx.AsyncClient() as client:
        for i, provider in enumerate(config.providers):
            breaker = breaker_mgr.get(provider.name)
            if breaker.is_open():
                continue

            start = time.time()
            url = f"{provider.base_url}{request.url.path}"
            if request.url.query:
                url += f"?{request.url.query}"

            req_headers = replace_token(filter_headers(headers), config.access_token, provider.token)
            logger.request_forward(provider.name, url, i + 1)

            try:
                resp = await client.request(
                    request.method, url, headers=req_headers,
                    content=body, timeout=config.timeout
                )
                duration = (time.time() - start) * 1000

                if resp.status_code < 500:
                    breaker.record_success()
                    logger.request_success(provider.name, resp.status_code, duration)
                    return Response(resp.content, resp.status_code,
                                    headers=filter_headers(dict(resp.headers)))

                breaker.record_failure()
                logger.request_failure(provider.name, "http_error",
                                       resp.content.decode(errors="replace")[:200],
                                       resp.status_code, duration)
                if breaker.is_open():
                    logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)
                last_resp = resp

            except httpx.RequestError as e:
                duration = (time.time() - start) * 1000
                breaker.record_failure()
                logger.request_failure(provider.name, type(e).__name__, str(e), duration_ms=duration)
                if breaker.is_open():
                    logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)
                last_error = e

    if last_resp:
        return Response(last_resp.content, last_resp.status_code,
                        headers=filter_headers(dict(last_resp.headers)))

    logger.error("all_providers_failed", error=str(last_error) if last_error else "unavailable")
    return Response(b'{"error":"Bad Gateway"}', 502, media_type="application/json")


async def _stream_request(request, headers, body, config, breaker_mgr) -> Response | StreamingResponse:
    logger = get_logger()

    for i, provider in enumerate(config.providers):
        breaker = breaker_mgr.get(provider.name)
        if breaker.is_open():
            continue

        start = time.time()
        url = f"{provider.base_url}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"

        req_headers = replace_token(filter_headers(headers), config.access_token, provider.token)
        logger.request_forward(provider.name, url, i + 1)

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
                breaker.record_failure()
                logger.request_failure(provider.name, "http_error",
                                       content.decode(errors="replace")[:200],
                                       resp.status_code, duration)
                if breaker.is_open():
                    logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)
                continue

            breaker.record_success()
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
            breaker.record_failure()
            logger.request_failure(provider.name, type(e).__name__, str(e), duration_ms=duration)
            if breaker.is_open():
                logger.circuit_breaker_event(provider.name, "opened", breaker.failure_count)

    logger.error("all_providers_failed", error="unavailable")
    return Response(b'{"error":"Bad Gateway"}', 502, media_type="application/json")
