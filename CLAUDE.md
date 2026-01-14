# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Transparent API Gateway with multi-provider failover and circuit breaker. Proxies requests to configured providers in priority order, automatically failing over to backups on errors.

## Commands

```bash
uv sync                                    # Install dependencies
uvicorn transparent_gateway.main:app       # Run (default port 8000)
uvicorn transparent_gateway.main:app --port 3001 --reload  # Dev mode
```

## Architecture

```
main.py          FastAPI app, routes: /{path}, /_health, /_reset_circuit
config.py        YAML config loader (Config, Provider, CircuitBreakerConfig)
proxy.py         Request forwarding with failover
circuit_breaker.py  Per-provider breakers (N failures → open → auto-reset)
logging_config.py   JSON logs with rotation
```

## Configuration (config.yaml)

```yaml
gateway:
  access_token: "token"      # Client auth (empty = skip)
  timeout: 60                # Request timeout (seconds)
  circuit_breaker:
    failure_threshold: 5     # Consecutive failures to trip
    reset_timeout: 600       # Seconds before auto-reset

providers:                   # Priority order (first = highest)
  - name: "primary"
    base_url: "https://api.example.com"
    token: "sk-xxx"
  - name: "backup"
    base_url: "https://backup.example.com"
    token: "sk-yyy"
```

## Logging

JSON logs in `logs/gateway.log` (10MB max, 5 backups).

**Fields:** `ts`, `level`, `req_id`, `msg`, `provider`, `status`, `duration_ms`, `error_type`, `error_msg`

**Messages:** `request_start`, `request_forward`, `request_success`, `request_failure`, `circuit_breaker`, `all_providers_failed`

**Debug:**
```bash
grep '"req_id":"abc123"' logs/gateway.log | jq .   # Trace request
grep '"level":"ERROR"' logs/gateway.log | jq .     # Find errors
grep '"msg":"circuit_breaker"' logs/gateway.log    # Breaker events
```

## Request Flow

1. Auth check (token in any header value)
2. For each provider (priority order):
   - Skip if circuit open
   - Forward request (replace gateway token → provider token)
   - Success (< 500): return response
   - Failure (≥ 500 or network error): record failure, try next
   - N consecutive failures → circuit opens
3. All failed → 502 Bad Gateway
