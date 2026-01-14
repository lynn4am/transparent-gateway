# Transparent Gateway

透明 API 网关，支持多供应商故障转移和熔断机制。

## 功能特性

- **透明代理**：完整转发请求方法、请求头、请求体、查询参数
- **多供应商支持**：按优先级配置多个 API 供应商
- **自动故障转移**：主供应商失败时自动切换到备用供应商
- **熔断机制**：供应商失败后自动熔断，避免重复请求失败的服务
- **Token 替换**：自动将网关 token 替换为供应商 token
- **Streaming 支持**：支持 SSE 流式响应转发

## 快速开始

### 安装

```bash
# 使用 uv
uv sync

# 或使用 pip
pip install -e .
```

### 配置

创建 `config.yaml` 文件：

```yaml
gateway:
  # 用户访问网关需要的 token
  access_token: "your-gateway-access-token"

  # 熔断时间（秒），默认 600 秒（10 分钟）
  circuit_breaker_timeout: 600

  # 请求超时（秒）
  request_timeout: 30

# 供应商列表（按优先级排序，第一个优先）
providers:
  - name: "anthropic-primary"
    base_url: "https://api.anthropic.com"
    auth_token: "sk-ant-api03-xxxxx"

  - name: "anthropic-backup"
    base_url: "https://api.backup-provider.com"
    auth_token: "sk-backup-xxxxx"
```

### 启动

```bash
# 默认读取 config.yaml
uvicorn transparent_gateway.main:app --host 0.0.0.0 --port 8000

# 指定配置文件路径
CONFIG_PATH=/path/to/config.yaml uvicorn transparent_gateway.main:app --host 0.0.0.0 --port 8000
```

## 使用

### 普通请求

```bash
curl http://localhost:8000/v1/messages \
  --header "x-api-key: your-gateway-access-token" \
  --header "anthropic-version: 2023-06-01" \
  --header "content-type: application/json" \
  --data '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello, Claude"}
    ]
  }'
```

### Streaming 请求

```bash
curl http://localhost:8000/v1/messages \
  --header "x-api-key: your-gateway-access-token" \
  --header "anthropic-version: 2023-06-01" \
  --header "content-type: application/json" \
  --data '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "stream": true,
    "messages": [
      {"role": "user", "content": "Hello, Claude"}
    ]
  }'
```

## 管理端点

### 健康检查

```bash
curl http://localhost:8000/_health
```

响应示例：

```json
{
  "status": "ok",
  "providers": ["anthropic-primary", "anthropic-backup"],
  "circuit_breakers": {
    "anthropic-primary": {
      "is_open": false,
      "remaining_time": null
    }
  }
}
```

### 重置熔断器

```bash
curl -X POST http://localhost:8000/_reset_circuit
```

## 工作原理

```
客户端请求
    │
    ▼
┌─────────────────┐
│  Token 验证     │ ──── 验证失败 ──→ 401 Unauthorized
└─────────────────┘
    │ 验证通过
    ▼
┌─────────────────┐
│  检查供应商 A   │ ──── 已熔断 ──→ 跳过
│  熔断状态       │
└─────────────────┘
    │ 未熔断
    ▼
┌─────────────────┐
│  转发到供应商 A │ ──── 成功 ──→ 返回响应
│  (替换 token)   │
└─────────────────┘
    │ 失败 (5xx/网络错误)
    ▼
┌─────────────────┐
│  触发熔断       │
│  (10 分钟)      │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  转发到供应商 B │ ──→ 返回响应（成功或失败）
└─────────────────┘
```

## 熔断规则

- **触发条件**：5xx 响应或网络错误（超时、连接失败等）
- **熔断时间**：默认 10 分钟，可通过 `circuit_breaker_timeout` 配置
- **自动恢复**：熔断时间结束后自动尝试该供应商
- **手动重置**：通过 `/_reset_circuit` 端点重置所有熔断器

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `gateway.access_token` | 网关访问 token，为空则跳过验证 | - |
| `gateway.circuit_breaker_timeout` | 熔断时间（秒） | 600 |
| `gateway.request_timeout` | 请求超时（秒） | 30 |
| `providers[].name` | 供应商名称（用于标识） | - |
| `providers[].base_url` | 供应商 API 基础 URL | - |
| `providers[].auth_token` | 供应商 API token | - |

## License

MIT
