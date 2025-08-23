完成！我已经为你搭建了一个完整的 **Jira Web API 代理网关**。以下是交付内容概览：

## 📁 项目结构

1. **main.py** - 主应用程序，包含：
   - FastAPI 应用配置
   - 请求转发逻辑
   - 示例路由（GET/POST Issue）
   - 健康检查和指标端点

2. **config.py** - 配置管理：
   - 客户端分类（premium/standard/basic）
   - 限流参数配置
   - 环境变量管理

3. **rate_limiter.py** - 限流器实现：
   - 令牌桶算法
   - 并发控制
   - 按客户端类型和操作类型（读/写）独立限流

4. **logger_setup.py** - 日志系统：
   - 三个独立日志器（uvicorn/business/jira_outbound）
   - JSON 格式化的外呼日志

5. **requirements.txt** - Python 依赖
6. **.env.example** - 环境变量示例
7. **README.md** - 完整的运行说明文档
8. **Dockerfile & docker-compose.yml** - Docker 部署配置

## 🎯 核心功能实现

### 流量管理
- **按方法分类**：GET（读）和非 GET（写）操作独立配额
- **按客户端分类**：三级客户端（premium/standard/basic）不同配额
- **限流机制**：
  - 令牌桶算法控制请求速率
  - 并发数限制
  - 突发流量支持

### 日志系统
1. **Uvicorn 日志**：框架级访问日志
2. **业务日志**：记录限流决策、请求处理等
3. **Jira 外呼日志**：JSON 格式，包含完整的请求信息

### 请求追踪
- 支持 `X-Trace-ID` 头传递或自动生成
- 贯穿全链路，包括转发到 Jira

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 Jira 配置

# 3. 启动服务
python main.py

# 4. 测试请求
curl -X GET "http://localhost:8000/rest/api/3/issue/TEST-1" \
  -H "X-Client-ID: client-001"
```

## 📊 限流配置示例

| 客户端类型 | 读请求/分钟 | 写请求/分钟 | 读并发 | 写并发 |
|----------|-----------|-----------|--------|--------|
| Premium  | 600       | 120       | 50     | 20     |
| Standard | 300       | 60        | 30     | 10     |
| Basic    | 100       | 20        | 10     | 5      |

## 🔧 主要特性

1. **透明代理**：完整转发请求到 Jira
2. **智能重试**：指数退避策略
3. **实时指标**：`/metrics` 端点查看当前限流状态
4. **健康检查**：`/health` 端点
5. **灵活配置**：支持环境变量和配置文件

这个网关已经具备生产环境所需的基本功能，你可以根据实际需求进行扩展，比如添加 Redis 支持、认证机制、更多 Jira 端点等。