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

The gateway is a FastAPI application with five core modules:

- **main.py**: FastAPI app with catch-all route `/{path:path}` that proxies all HTTP methods, plus `/_health` and `/_reset_circuit` management endpoints
- **config.py**: Loads YAML configuration with gateway settings and provider list. Uses lazy-loaded global singleton (`get_config()`)
- **circuit_breaker.py**: Per-provider circuit breakers that require N consecutive failures before tripping (default 5), auto-reset after timeout
- **proxy.py**: Core proxy logic with two code paths:
  - `_handle_normal_request`: Buffered request/response, tries providers in priority order
  - `_handle_streaming_request`: SSE streaming with long-lived httpx client
- **logging_config.py**: Structured JSON logging with rotation

## Request Flow

1. Generate request ID for tracing
2. Token verification: checks if `access_token` appears in any header value
3. Circuit breaker check: skips providers with open breakers
4. Header preparation: filters hop-by-hop headers, replaces gateway token with provider token
5. Forward to provider, record failure on 5xx or network error
6. Failover to next provider if current fails
7. Circuit breaker opens after N consecutive failures

## Configuration

Config loaded from `config.yaml` (or `CONFIG_PATH` env var):
- `gateway.access_token`: Client authentication token
- `gateway.circuit_breaker_timeout`: Breaker reset time in seconds (default: 600)
- `gateway.circuit_breaker_threshold`: Consecutive failures before tripping (default: 5)
- `gateway.request_timeout`: Request timeout in seconds (default: 60)
- `providers[]`: Ordered list with `name`, `base_url`, `auth_token`

## Logging

Logs are written to `logs/gateway.log` in JSON format with automatic rotation (10MB max, 5 backups).

### Log Format

Each log line is a JSON object with these base fields:
- `ts`: ISO 8601 timestamp (UTC)
- `level`: Log level (INFO, ERROR, WARNING, DEBUG)
- `logger`: Logger name
- `msg`: Log message type
- `req_id`: Request ID for tracing (8 char hex)

### Log Message Types

| msg | level | Additional Fields | Description |
|-----|-------|-------------------|-------------|
| `request_start` | INFO | `method`, `path`, `query`, `model`, `stream` | Request received |
| `request_forward` | INFO | `provider`, `target_url`, `attempt` | Forwarding to provider |
| `request_success` | INFO | `provider`, `status`, `duration_ms` | Provider returned success |
| `request_failure` | ERROR | `provider`, `error_type`, `error_msg`, `status`?, `duration_ms` | Provider failed |
| `circuit_breaker` | WARNING | `provider`, `event`, `failure_count` | Circuit breaker state change |
| `auth_failed` | WARNING | `reason` | Authentication failed |
| `all_providers_failed` | ERROR | `error` | All providers exhausted |

### Example Log Lines

```json
{"ts":"2026-01-14T10:00:00.000Z","level":"INFO","logger":"transparent_gateway","msg":"request_start","req_id":"a1b2c3d4","method":"POST","path":"/v1/messages","model":"claude-opus-4-5","stream":true}
{"ts":"2026-01-14T10:00:00.001Z","level":"INFO","logger":"transparent_gateway","msg":"request_forward","req_id":"a1b2c3d4","provider":"anthropic-primary","target_url":"https://api.anthropic.com/v1/messages","attempt":1}
{"ts":"2026-01-14T10:00:01.234Z","level":"INFO","logger":"transparent_gateway","msg":"request_success","req_id":"a1b2c3d4","provider":"anthropic-primary","status":200,"duration_ms":1233.45}
```

### Debugging with Logs

Use `req_id` to trace a single request through the system:
```bash
grep '"req_id":"a1b2c3d4"' logs/gateway.log | jq .
```

Find all failures:
```bash
grep '"level":"ERROR"' logs/gateway.log | jq .
```

Check circuit breaker events:
```bash
grep '"msg":"circuit_breaker"' logs/gateway.log | jq .
```
