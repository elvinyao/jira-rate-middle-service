# jira-rate-middle-service

下面是对括号内需求的精炼版：

# 目标

用 **Python + FastAPI** 搭建一个 **Jira Web API 代理网关**：接收其他客户端请求，转发到 Jira，并按请求类型与“客户端分类”做流量管理。

# 范围与功能

* **代理转发**：将上游请求透明转发至 Jira。
* **流量管理**

  * 按方法分类：**GET**（读）与 **非 GET**（写）分别设置策略。
  * 按“客户端分类”独立限流/并发（如按 client\_id/tenant）。
* **示例端点（用于演示）**

  1. `GET /rest/api/3/issue/{issueIdOrKey}`
  2. `POST /rest/api/3/issue`
* **追踪与上下文**：入口生成或透传 `trace_id`，贯穿全链路（包含转发到 Jira）。

# 日志要求（3 个 logger）

1. **uvicorn 日志**：运行/访问日志（框架级）。
2. **业务日志**：网关内部处理与决策（如命中哪条限流规则）。
3. **对 Jira 的外呼日志**：记录 FastAPI→Jira 的每次请求，至少包含
   `trace_id、client_id、method、path、status、latency、retries、jira_base_url` 等。

# 交付物（最简示意框架）

* 目录与主文件骨架（FastAPI app、路由、流量管理中间件/依赖、日志初始化）。
* 两个示例路由对应上述 Jira 端点（占位实现，便于替换为真实转发）。
* 配置样例（客户端分类与 GET/非 GET 各自的限流参数）。
* 简要运行说明（启动、环境变量、日志与限流验证方法）。

# Jira API Gateway - 运行说明

## 项目结构
```
jira-gateway/
├── main.py              # 主应用入口
├── config.py            # 配置管理
├── rate_limiter.py      # 限流器实现
├── logger_setup.py      # 日志配置
├── requirements.txt     # 依赖包
├── .env.example        # 环境变量示例
├── .env                # 实际环境变量（需创建）
└── README.md           # 本文档
```

## 快速启动

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 文件，填入真实的 Jira 配置
```

### 3. 启动服务
```bash
# 开发模式（自动重载）
python main.py

# 或生产模式
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 功能特性

### 流量管理策略

#### 客户端分类
- **Premium**: 高配额（600 读/分钟，120 写/分钟）
- **Standard**: 中等配额（300 读/分钟，60 写/分钟）
- **Basic**: 基础配额（100 读/分钟，20 写/分钟）

#### 限流机制
1. **令牌桶算法**: 控制请求速率
2. **并发限制**: 限制同时处理的请求数
3. **突发容量**: 允许短时间内的流量突发

### 日志系统

#### 三个独立的日志器
1. **uvicorn.access**: 框架级访问日志
2. **business**: 业务处理日志（限流决策等）
3. **jira_outbound**: Jira 外呼日志（JSON 格式）

#### Jira 外呼日志字段
- `trace_id`: 请求追踪 ID
- `client_id`: 客户端标识
- `method`: HTTP 方法
- `path`: 请求路径
- `status`: 响应状态码
- `latency_ms`: 延迟（毫秒）
- `retries`: 重试次数
- `jira_base_url`: Jira 基础 URL

## API 使用示例

### 1. 获取 Issue（GET 请求）
```bash
curl -X GET "http://localhost:8000/rest/api/3/issue/PROJ-123" \
  -H "X-Client-ID: client-001" \
  -H "X-Trace-ID: trace-123456"
```

### 2. 创建 Issue（POST 请求）
```bash
curl -X POST "http://localhost:8000/rest/api/3/issue" \
  -H "X-Client-ID: client-002" \
  -H "Content-Type: application/json" \
  -d '{
    "fields": {
      "project": {"key": "PROJ"},
      "summary": "Test Issue",
      "issuetype": {"name": "Task"}
    }
  }'
```

### 3. 健康检查
```bash
curl http://localhost:8000/health
```

### 4. 查看限流指标
```bash
curl http://localhost:8000/metrics
```

## 测试限流

### 测试读限流（GET）
```bash
# 使用 basic 客户端快速发送请求（限制: 100/分钟）
for i in {1..150}; do
  curl -X GET "http://localhost:8000/rest/api/3/issue/TEST-$i" \
    -H "X-Client-ID: test-client" \
    -H "X-Trace-ID: test-read-$i" &
done
wait
# 预期：前 100 个请求成功，之后返回 429 错误
```

### 测试写限流（POST）
```bash
# 使用 basic 客户端测试写操作（限制: 20/分钟）
for i in {1..30}; do
  curl -X POST "http://localhost:8000/rest/api/3/issue" \
    -H "X-Client-ID: test-client" \
    -H "Content-Type: application/json" \
    -H "X-Trace-ID: test-write-$i" \
    -d '{"fields": {"summary": "Test '$i'"}}' &
done
wait
# 预期：前 20 个请求成功，之后返回 429 错误
```

### 测试并发限制
```bash
# 同时发送多个请求测试并发限制
seq 1 20 | xargs -P 20 -I {} curl -X GET \
  "http://localhost:8000/rest/api/3/issue/CONCURRENT-{}" \
  -H "X-Client-ID: test-client" \
  -H "X-Trace-ID: concurrent-{}"
```

## 监控和观察

### 查看不同日志
```bash
# 启动服务并观察日志输出
python main.py 2>&1 | grep -E "BUSINESS|UVICORN|jira_outbound"

# 只看业务日志
python main.py 2>&1 | grep "BUSINESS"

# 只看 Jira 外呼日志（JSON 格式）
python main.py 2>&1 | grep "jira_outbound" | jq '.'
```

### 实时监控指标
```bash
# 每 2 秒查看一次限流指标
watch -n 2 'curl -s http://localhost:8000/metrics | jq .'
```

## 配置说明

### 客户端配置（config.py）
```python
CLIENT_CONFIGS = {
    "client-001": {"type": "premium", "name": "Premium Client 1"},
    "client-002": {"type": "standard", "name": "Standard Client 1"},
    "client-003": {"type": "basic", "name": "Basic Client 1"}
}
```

### 限流配置（config.py）
```python
RATE_LIMIT_CONFIGS = {
    "premium": {
        "read": {
            "requests_per_minute": 600,   # 每分钟请求数
            "concurrent_limit": 50,        # 最大并发数
            "burst_size": 100             # 突发容量
        },
        "write": {
            "requests_per_minute": 120,
            "concurrent_limit": 20,
            "burst_size": 30
        }
    }
    # ... 其他客户端类型
}
```

## 扩展建议

### 1. 添加更多 Jira 端点
在 `main.py` 中添加新路由：
```python
@app.put("/rest/api/3/issue/{issue_id_or_key}")
async def update_issue(
    issue_id_or_key: str,
    request: Request,
    rate_limit_info: Dict = Depends(check_rate_limit)
):
    body = await request.json()
    return await forward_to_jira(
        method="PUT",
        path=f"/rest/api/3/issue/{issue_id_or_key}",
        trace_id=rate_limit_info["trace_id"],
        client_id=rate_limit_info["client_id"],
        json_body=body
    )
```

### 2. 持久化存储
- 将限流状态存储到 Redis
- 支持分布式部署

### 3. 监控集成
- 接入 Prometheus 指标
- 添加 Grafana 仪表板
- 集成 ELK 日志分析

### 4. 安全增强
- 添加 JWT 认证
- 实现 API Key 管理
- 添加请求签名验证

### 5. 高级功能
- 动态配置更新（无需重启）
- WebSocket 支持
- 请求缓存
- 熔断器模式

## 故障排查

### 常见问题

1. **429 Too Many Requests**
   - 检查客户端配额设置
   - 查看 `/metrics` 确认当前使用情况
   - 检查业务日志了解限流原因

2. **502 Bad Gateway**
   - 检查 Jira 连接配置
   - 验证 API Token 是否有效
   - 查看 jira_outbound 日志

3. **400 Bad Request**
   - 确保请求包含 X-Client-ID 头
   - 检查请求体格式是否正确

4. **403 Forbidden**
   - 客户端 ID 未在配置中注册
   - 检查 CLIENT_CONFIGS 配置

## 性能优化建议

1. **使用连接池**: 已配置 httpx 连接池，可根据需要调整大小
2. **异步处理**: 充分利用 FastAPI 的异步特性
3. **缓存策略**: 对频繁访问的数据添加缓存
4. **负载均衡**: 使用 Nginx/HAProxy 进行负载均衡
5. **监控告警**: 设置关键指标的告警阈值

## 许可证
MIT

## 联系方式
如有问题，请提交 Issue 或联系维护团队。