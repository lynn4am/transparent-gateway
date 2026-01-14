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
src/transparent_gateway/
├── main.py              # FastAPI app, routes: /{path}, /_health, /_reset_circuit
├── config.py            # YAML config loader (Config, Provider, CircuitBreakerConfig)
├── proxy.py             # Request forwarding with failover logic
├── circuit_breaker.py   # Per-provider breakers (N failures → open → auto-reset)
└── logging_config.py    # Structured JSON logging with rotation
```

### Key Functions

| File | Function | Purpose |
|------|----------|---------|
| `proxy.py` | `proxy_request()` | Main entry, dispatches to normal/stream handler |
| `proxy.py` | `select_provider()` | Provider selection with half-open probe logic |
| `proxy.py` | `_try_provider()` | Forward request to single provider |
| `proxy.py` | `check_auth()` | Validate gateway access token |
| `circuit_breaker.py` | `CircuitBreaker` | Single provider's breaker state |
| `circuit_breaker.py` | `CircuitBreakerManager` | Manages all breakers |

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

## Request Flow

1. Auth check (token in any header value)
2. Select provider:
   - 5% chance: probe a random tripped provider (half-open)
   - Otherwise: first non-tripped provider in priority order
   - Last provider never trips (always available as fallback)
3. Forward request (replace gateway token → provider token)
4. Success (< 500): record success (resets failure count), return response
5. Failure (≥ 500 or network error): record failure, try next provider
   - N consecutive failures → circuit opens (except last provider)
6. All failed → 502 Bad Gateway

## Circuit Breaker Strategy

- **Threshold**: N consecutive failures trips the circuit (configurable)
- **Auto-reset**: After reset_timeout seconds, circuit closes automatically
- **Fallback guarantee**: Last provider never trips, always available
- **Half-open probe**: 5% of requests probe a random tripped provider
  - Probe success → circuit closes, provider recovers
  - Probe failure → continue with next available provider

---

## Debugging Guide (Important for Monitoring)

When acting as a daemon to monitor this service, use the following guide to understand and debug issues.

### Log Location

```
logs/gateway.log       # Main log file (JSON format)
                       # Rotation: 10MB max, 5 backups (gateway.log.1, .2, ...)
```

### Log Message Types

Each log entry has a `msg` field indicating the event type:

| msg | Meaning | Key Fields |
|-----|---------|------------|
| `request_start` | Request received | `method`, `path`, `model`, `stream` |
| `request_forward` | Forwarding to provider | `provider` |
| `request_success` | Provider returned success | `provider`, `status`, `duration_ms` |
| `request_failure` | Provider failed | `provider`, `status`, `error_type`, `error_msg` |
| `circuit_breaker` | Breaker state changed | `provider`, `action` (tripped/reset/recovered) |
| `all_providers_failed` | All providers failed | `error_type`, `error_msg` |

### Log Fields Reference

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | ISO timestamp |
| `level` | string | INFO, WARNING, ERROR |
| `req_id` | string | 8-char hex, unique per request (use to trace full request lifecycle) |
| `msg` | string | Event type (see above) |
| `provider` | string | Provider name |
| `status` | int | HTTP status code |
| `duration_ms` | float | Request duration in milliseconds |
| `error_type` | string | Error category: `http_error`, `timeout`, `connection_error`, `unknown` |
| `error_msg` | string | Detailed error message |
| `action` | string | Circuit breaker action: `tripped`, `reset`, `recovered` |

### Common Debug Commands

```bash
# Watch logs in real-time
tail -f logs/gateway.log | jq .

# Trace a specific request by req_id
grep '"req_id":"abc123"' logs/gateway.log | jq .

# Find all errors
grep '"level":"ERROR"' logs/gateway.log | jq .

# Find all circuit breaker events
grep '"msg":"circuit_breaker"' logs/gateway.log | jq .

# Find failed requests (all providers failed)
grep '"msg":"all_providers_failed"' logs/gateway.log | jq .

# Find requests to specific provider
grep '"provider":"primary"' logs/gateway.log | jq .

# Find slow requests (> 5000ms)
cat logs/gateway.log | jq 'select(.duration_ms > 5000)'

# Count errors by type
grep '"level":"ERROR"' logs/gateway.log | jq -r '.error_type' | sort | uniq -c

# Recent errors (last 20)
grep '"level":"ERROR"' logs/gateway.log | tail -20 | jq .
```

### Debugging Scenarios

#### Scenario 1: High Error Rate

**Symptoms**: Many `all_providers_failed` messages

**Debug steps**:
1. Check which providers are failing:
   ```bash
   grep '"msg":"request_failure"' logs/gateway.log | jq '{provider, error_type, error_msg}' | tail -20
   ```
2. Check circuit breaker status:
   ```bash
   curl http://localhost:8000/_health | jq .circuit_breakers
   ```
3. Look at error types - timeout vs connection vs http_error

**Common causes**:
- `timeout`: Provider is slow, consider increasing `gateway.timeout`
- `connection_error`: Network issue or provider is down
- `http_error` with 5xx: Provider is returning errors

#### Scenario 2: Circuit Breaker Keeps Tripping

**Symptoms**: Frequent `circuit_breaker` events with `action: tripped`

**Debug steps**:
1. Find the tripping pattern:
   ```bash
   grep '"msg":"circuit_breaker"' logs/gateway.log | jq '{ts, provider, action}' | tail -20
   ```
2. Check what errors caused the trip:
   ```bash
   # Get req_id from circuit_breaker event, then trace full request
   grep '"req_id":"<id>"' logs/gateway.log | jq .
   ```

**Common causes**:
- Provider has intermittent issues
- `failure_threshold` too low (increase in config)
- Network instability

#### Scenario 3: Requests Going to Wrong Provider

**Symptoms**: Requests not using primary provider

**Debug steps**:
1. Check which provider is being selected:
   ```bash
   grep '"msg":"request_forward"' logs/gateway.log | jq '{ts, provider}' | tail -20
   ```
2. Check if primary is tripped:
   ```bash
   curl http://localhost:8000/_health | jq .circuit_breakers
   ```

**Explanation**: If primary is tripped, requests go to backup. Also 5% of requests probe tripped providers (half-open), so occasional traffic to tripped providers is normal.

#### Scenario 4: Slow Responses

**Symptoms**: High `duration_ms` values

**Debug steps**:
1. Find slow requests:
   ```bash
   cat logs/gateway.log | jq 'select(.duration_ms > 5000) | {ts, provider, duration_ms, path}'
   ```
2. Check if it's provider-specific:
   ```bash
   cat logs/gateway.log | jq 'select(.msg=="request_success") | {provider, duration_ms}' | \
     jq -s 'group_by(.provider) | map({provider: .[0].provider, avg: (map(.duration_ms) | add / length)})'
   ```

#### Scenario 5: Service Not Starting

**Debug steps**:
1. Check config syntax:
   ```bash
   python -c "import yaml; yaml.safe_load(open('config.yaml'))"
   ```
2. Check required fields exist (providers list not empty)
3. Run with verbose output:
   ```bash
   uvicorn transparent_gateway.main:app --log-level debug
   ```

### Health Check Endpoint

```bash
curl http://localhost:8000/_health | jq .
```

Response:
```json
{
  "status": "ok",
  "providers": ["primary", "backup"],
  "circuit_breakers": {
    "primary": {
      "is_open": false,
      "failure_count": 0,
      "remaining_time": null
    },
    "backup": {
      "is_open": true,
      "failure_count": 5,
      "remaining_time": 342.5
    }
  }
}
```

- `is_open: true` = circuit is tripped, provider is skipped
- `remaining_time` = seconds until auto-reset (null if not tripped)
- `failure_count` = consecutive failures (resets on success)

### Manual Recovery

```bash
# Reset all circuit breakers (force retry all providers)
curl -X POST http://localhost:8000/_reset_circuit
```

Use this when you know a provider has recovered but the circuit hasn't auto-reset yet.

### Key Code Locations for Debugging

| Issue | Look at |
|-------|---------|
| Auth failures | `proxy.py:check_auth()` |
| Provider selection logic | `proxy.py:select_provider()` |
| Request forwarding | `proxy.py:_try_provider()` |
| Circuit breaker logic | `circuit_breaker.py:CircuitBreaker` |
| Error classification | `proxy.py:_try_provider()` - the exception handling |
| Streaming issues | `proxy.py:_stream_request()` |
