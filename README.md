# Transparent Gateway

透明 API 网关，支持多供应商自动故障转移和熔断保护。

## 功能特性

- **透明代理** - 完整转发请求，客户端无感知
- **自动故障转移** - 主供应商失败时自动切换到备用
- **熔断保护** - 连续失败自动熔断，避免雪崩
- **半开探测** - 自动尝试恢复已熔断的供应商
- **保底机制** - 最后一个供应商永不熔断，确保可用性
- **流式支持** - 完整支持 SSE 流式响应

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 创建配置

复制示例配置并修改：

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

```yaml
gateway:
  access_token: "your-gateway-token"  # 留空则跳过验证
  timeout: 300

  circuit_breaker:
    failure_threshold: 5    # 连续失败 5 次触发熔断
    reset_timeout: 600      # 熔断 10 分钟后自动恢复

providers:
  - name: "primary"
    base_url: "https://api.anthropic.com"
    token: "sk-ant-xxx"

  - name: "backup"
    base_url: "https://backup.example.com"
    token: "sk-backup-xxx"
```

### 3. 启动服务

```bash
# 生产环境
uvicorn transparent_gateway.main:app --host 0.0.0.0 --port 8000

# 开发环境（热重载）
uvicorn transparent_gateway.main:app --port 3001 --reload
```

## 使用示例

### 发送请求

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: your-gateway-token" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 流式请求

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: your-gateway-token" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 管理端点

```bash
# 健康检查
curl http://localhost:8000/_health

# 重置所有熔断器
curl -X POST http://localhost:8000/_reset_circuit
```

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `gateway.access_token` | 网关访问令牌，留空跳过验证 | - |
| `gateway.timeout` | 请求超时（秒） | 60 |
| `gateway.circuit_breaker.failure_threshold` | 触发熔断的连续失败次数 | 5 |
| `gateway.circuit_breaker.reset_timeout` | 熔断持续时间（秒） | 600 |
| `gateway.circuit_breaker.probe_probability` | 探测已熔断供应商的概率 | 0.05 |
| `providers[].name` | 供应商名称 | - |
| `providers[].base_url` | 供应商 API 地址 | - |
| `providers[].token` | 供应商 API 令牌 | - |

---

# 进阶内容

## 工作原理

### 请求流程

```
客户端请求
    │
    ├─ Token 验证 ────────────────── 失败 → 401
    │
    ├─ 选择供应商
    │   ├─ 5% 概率：探测一个已熔断的供应商（半开状态）
    │   └─ 其他：按优先级选择第一个未熔断的供应商
    │
    ├─ 转发请求（替换 token）
    │
    ├─ 处理响应
    │   ├─ 成功（< 500）→ 重置失败计数，返回响应
    │   └─ 失败（≥ 500 或网络错误）→ 记录失败，尝试下一个
    │
    └─ 全部失败 → 502 Bad Gateway
```

### 熔断策略

| 特性 | 说明 |
|------|------|
| **触发条件** | 连续 N 次失败（5xx 或网络错误） |
| **自动恢复** | 熔断超时后自动关闭熔断器 |
| **半开探测** | 按配置概率探测已熔断供应商，成功则恢复（默认 5%） |
| **保底机制** | 最后一个供应商永不熔断，确保始终可用 |

### 成功与失败判定

- **成功**：HTTP 状态码 < 500
- **失败**：HTTP 状态码 ≥ 500，或网络错误（超时、连接失败等）

## 代码结构

```
src/transparent_gateway/
├── main.py              # FastAPI 应用入口和路由定义
├── config.py            # YAML 配置加载
├── proxy.py             # 请求转发和故障转移逻辑
├── circuit_breaker.py   # 熔断器实现
└── logging_config.py    # 结构化 JSON 日志
```

### 核心模块说明

#### main.py

定义三个路由：
- `/{path:path}` - 主代理路由，转发所有请求
- `/_health` - 健康检查，返回供应商和熔断状态
- `/_reset_circuit` - 手动重置所有熔断器

#### config.py

配置管理，支持：
- 从 `config.yaml` 或 `CONFIG_PATH` 环境变量加载配置
- 解析供应商列表和熔断器参数
- 全局配置单例

#### proxy.py

核心转发逻辑：
- `select_provider()` - 选择供应商（含半开探测逻辑）
- `proxy_request()` - 主入口，区分普通/流式请求
- `_try_provider()` - 转发到单个供应商
- `check_auth()` - 验证网关令牌

#### circuit_breaker.py

熔断器实现：
- `CircuitBreaker` - 单个供应商的熔断器
- `CircuitBreakerManager` - 管理所有供应商的熔断器
- 支持失败计数、熔断判定、超时恢复

#### logging_config.py

结构化日志：
- JSON 格式输出到 `logs/gateway.log`
- 日志轮转（10MB，保留 5 个备份）
- 请求 ID 跟踪，便于问题排查

## 日志分析

日志位于 `logs/gateway.log`，JSON 格式。

```bash
# 追踪单个请求
grep '"req_id":"abc123"' logs/gateway.log | jq .

# 查看错误
grep '"level":"ERROR"' logs/gateway.log | jq .

# 熔断事件
grep '"msg":"circuit_breaker"' logs/gateway.log | jq .

# 最近 10 条日志
tail -10 logs/gateway.log | jq .
```

### 日志字段

| 字段 | 说明 |
|------|------|
| `ts` | 时间戳 |
| `level` | 日志级别 |
| `req_id` | 请求 ID（同一请求的所有日志共享） |
| `msg` | 消息类型 |
| `provider` | 供应商名称 |
| `status` | HTTP 状态码 |
| `duration_ms` | 耗时（毫秒） |
| `error_type` | 错误类型 |
| `error_msg` | 错误信息 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `CONFIG_PATH` | 配置文件路径，默认 `config.yaml` |

## 开发

### 安装开发依赖

```bash
uv sync --extra dev
```

### 运行测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 带覆盖率报告
uv run pytest --cov=transparent_gateway tests/
```

## License

MIT
