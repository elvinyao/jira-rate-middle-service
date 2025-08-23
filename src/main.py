# main.py
import os
import time
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any
from uuid import uuid4

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
import httpx
import uvicorn

from config import settings, get_client_config
from rate_limiter import RateLimiter
from logger_setup import setup_loggers, get_logger

# 初始化日志
setup_loggers()
business_logger = get_logger("business")
jira_logger = get_logger("jira_outbound")

# 初始化限流器
rate_limiter = RateLimiter()

# HTTP 客户端
http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global http_client
    # 启动时
    business_logger.info("Starting Jira API Gateway")
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
    )
    yield
    # 关闭时
    await http_client.aclose()
    business_logger.info("Jira API Gateway stopped")


app = FastAPI(
    title="Jira API Gateway",
    version="1.0.0",
    lifespan=lifespan
)


async def get_trace_id(x_trace_id: Optional[str] = Header(None)) -> str:
    """获取或生成 trace_id"""
    return x_trace_id or str(uuid4())


async def get_client_id(x_client_id: Optional[str] = Header(None)) -> str:
    """获取客户端标识"""
    if not x_client_id:
        raise HTTPException(status_code=400, detail="X-Client-ID header is required")
    return x_client_id


async def check_rate_limit(
    request: Request,
    client_id: str = Depends(get_client_id),
    trace_id: str = Depends(get_trace_id)
) -> Dict[str, Any]:
    """检查限流"""
    method = request.method
    path = str(request.url.path)
    
    # 获取客户端配置
    client_config = get_client_config(client_id)
    if not client_config:
        business_logger.warning(f"Unknown client_id: {client_id}, trace_id: {trace_id}")
        raise HTTPException(status_code=403, detail="Unknown client")
    
    # 判断请求类型
    is_read = method == "GET"
    limit_type = "read" if is_read else "write"
    
    # 检查限流
    allowed = await rate_limiter.check_limit(
        client_id=client_id,
        client_type=client_config["type"],
        is_read=is_read,
        trace_id=trace_id
    )
    
    if not allowed:
        business_logger.warning(
            f"Rate limit exceeded for client {client_id} ({client_config['type']}), "
            f"type: {limit_type}, trace_id: {trace_id}"
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {limit_type} operations"
        )
    
    business_logger.info(
        f"Request allowed - client: {client_id} ({client_config['type']}), "
        f"method: {method}, path: {path}, trace_id: {trace_id}"
    )
    
    return {
        "client_id": client_id,
        "client_type": client_config["type"],
        "trace_id": trace_id
    }


async def forward_to_jira(
    method: str,
    path: str,
    trace_id: str,
    client_id: str,
    headers: Optional[Dict] = None,
    json_body: Optional[Dict] = None,
    params: Optional[Dict] = None
) -> JSONResponse:
    """转发请求到 Jira"""
    jira_url = f"{settings.JIRA_BASE_URL}{path}"
    
    # 准备请求头
    forward_headers = {
        "Authorization": f"Bearer {settings.JIRA_API_TOKEN}",
        "Accept": "application/json",
        "X-Trace-ID": trace_id
    }
    if json_body is not None:
        forward_headers["Content-Type"] = "application/json"
    
    # 记录开始时间
    start_time = time.time()
    retry_count = 0
    last_error = None
    
    # 重试逻辑
    for attempt in range(settings.MAX_RETRIES):
        try:
            response = await http_client.request(
                method=method,
                url=jira_url,
                headers=forward_headers,
                json=json_body,
                params=params
            )
            
            # 计算延迟
            latency = round((time.time() - start_time) * 1000, 2)  # ms
            
            # 记录外呼日志
            jira_logger.info({
                "trace_id": trace_id,
                "client_id": client_id,
                "method": method,
                "path": path,
                "status": response.status_code,
                "latency_ms": latency,
                "retries": retry_count,
                "jira_base_url": settings.JIRA_BASE_URL
            })
            
            # 返回响应
            return JSONResponse(
                status_code=response.status_code,
                content=response.json() if response.text else {},
                headers={"X-Trace-ID": trace_id}
            )
            
        except httpx.RequestError as e:
            retry_count += 1
            last_error = str(e)
            business_logger.warning(
                f"Request to Jira failed (attempt {attempt + 1}/{settings.MAX_RETRIES}): {e}, "
                f"trace_id: {trace_id}"
            )
            if attempt < settings.MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)  # 指数退避
    
    # 所有重试失败
    latency = round((time.time() - start_time) * 1000, 2)
    jira_logger.error({
        "trace_id": trace_id,
        "client_id": client_id,
        "method": method,
        "path": path,
        "status": "error",
        "latency_ms": latency,
        "retries": retry_count,
        "error": last_error,
        "jira_base_url": settings.JIRA_BASE_URL
    })
    
    raise HTTPException(status_code=502, detail="Failed to connect to Jira")


# ============ 示例路由 ============

@app.get("/rest/api/3/issue/{issue_id_or_key}")
async def get_issue(
    issue_id_or_key: str,
    request: Request,
    rate_limit_info: Dict = Depends(check_rate_limit)
):
    """获取 Jira Issue"""
    return await forward_to_jira(
        method="GET",
        path=f"/rest/api/3/issue/{issue_id_or_key}",
        trace_id=rate_limit_info["trace_id"],
        client_id=rate_limit_info["client_id"],
        params=dict(request.query_params)
    )


@app.post("/rest/api/3/issue")
async def create_issue(
    request: Request,
    rate_limit_info: Dict = Depends(check_rate_limit)
):
    """创建 Jira Issue"""
    try:
        body = await request.json()
    except:
        body = None
    
    return await forward_to_jira(
        method="POST",
        path="/rest/api/3/issue",
        trace_id=rate_limit_info["trace_id"],
        client_id=rate_limit_info["client_id"],
        json_body=body
    )


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "jira-gateway"}


@app.get("/metrics")
async def get_metrics():
    """获取限流指标"""
    metrics = await rate_limiter.get_metrics()
    return metrics


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn.access": {
                    "level": "INFO",
                    "handlers": ["default"],
                    "propagate": False,
                },
            },
        }
    )