# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Transparent API Gateway with multi-provider failover and circuit breaker functionality. Transparently proxies requests to configured API providers (e.g., Anthropic API), automatically switching to backup providers on failures.

## Development Commands

```bash
# Install dependencies
uv sync

# Run the gateway (reads config.yaml by default)
uvicorn transparent_gateway.main:app --host 0.0.0.0 --port 8000

# Run with custom config path
CONFIG_PATH=/path/to/config.yaml uvicorn transparent_gateway.main:app --host 0.0.0.0 --port 8000

# Development mode with auto-reload
uvicorn transparent_gateway.main:app --reload
```

## Architecture

The gateway is a FastAPI application with four core modules:

- **main.py**: FastAPI app with catch-all route `/{path:path}` that proxies all HTTP methods, plus `/_health` and `/_reset_circuit` management endpoints
- **config.py**: Loads YAML configuration with gateway settings and provider list. Uses lazy-loaded global singleton (`get_config()`)
- **circuit_breaker.py**: Per-provider circuit breakers that trip on 5xx/network errors and auto-reset after timeout (default 10 min)
- **proxy.py**: Core proxy logic with two code paths:
  - `_handle_normal_request`: Buffered request/response, tries providers in priority order
  - `_handle_streaming_request`: SSE streaming with long-lived httpx client

## Request Flow

1. Token verification: checks if `access_token` appears in any header value
2. Circuit breaker check: skips providers with open breakers
3. Header preparation: filters hop-by-hop headers, replaces gateway token with provider token
4. Forward to provider, trip breaker on 5xx or network error
5. Failover to next provider if current fails

## Configuration

Config loaded from `config.yaml` (or `CONFIG_PATH` env var):
- `gateway.access_token`: Client authentication token
- `gateway.circuit_breaker_timeout`: Breaker reset time in seconds (default: 600)
- `gateway.request_timeout`: Request timeout in seconds (default: 30)
- `providers[]`: Ordered list with `name`, `base_url`, `auth_token`
