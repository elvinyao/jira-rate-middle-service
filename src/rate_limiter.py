# rate_limiter.py
import asyncio
import time
from typing import Dict, Any, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta

from config import get_rate_limit_config
from logger_setup import get_logger

business_logger = get_logger("business")


class TokenBucket:
    """令牌桶算法实现"""
    
    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # 每秒产生的令牌数
        self.capacity = capacity  # 桶容量
        self.tokens = capacity  # 当前令牌数
        self.last_update = time.time()
        self.lock = asyncio.Lock()
    
    async def consume(self, tokens: int = 1) -> bool:
        """尝试消费令牌"""
        async with self.lock:
            now = time.time()
            # 计算新增的令牌数
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            # 检查是否有足够的令牌
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    async def get_available_tokens(self) -> int:
        """获取可用令牌数"""
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            return min(self.capacity, self.tokens + elapsed * self.rate)


class RateLimiter:
    """限流器"""
    
    def __init__(self):
        # 令牌桶存储: {client_id: {operation_type: TokenBucket}}
        self.buckets: Dict[str, Dict[str, TokenBucket]] = defaultdict(dict)
        
        # 并发计数器: {client_id: {operation_type: int}}
        self.concurrent_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"read": 0, "write": 0})
        
        # 请求历史（用于统计）: {client_id: deque of (timestamp, operation_type)}
        self.request_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        
        # 锁
        self.concurrent_locks: Dict[str, Dict[str, asyncio.Lock]] = defaultdict(
            lambda: {"read": asyncio.Lock(), "write": asyncio.Lock()}
        )
    
    def _get_or_create_bucket(self, client_id: str, client_type: str, is_read: bool) -> TokenBucket:
        """获取或创建令牌桶"""
        operation_type = "read" if is_read else "write"
        
        # 检查是否已存在
        if operation_type not in self.buckets[client_id]:
            config = get_rate_limit_config(client_type, is_read)
            if config:
                # 计算每秒速率
                rate = config["requests_per_minute"] / 60.0
                capacity = config["burst_size"]
                self.buckets[client_id][operation_type] = TokenBucket(rate, capacity)
                
                business_logger.debug(
                    f"Created token bucket for {client_id} ({operation_type}): "
                    f"rate={rate:.2f}/s, capacity={capacity}"
                )
        
        return self.buckets[client_id].get(operation_type)
    
    async def check_limit(
        self,
        client_id: str,
        client_type: str,
        is_read: bool,
        trace_id: str
    ) -> bool:
        """检查是否允许请求"""
        operation_type = "read" if is_read else "write"
        config = get_rate_limit_config(client_type, is_read)
        
        if not config:
            business_logger.warning(f"No rate limit config for {client_type}/{operation_type}")
            return False
        
        # 1. 检查并发限制
        concurrent_limit = config["concurrent_limit"]
        async with self.concurrent_locks[client_id][operation_type]:
            current_concurrent = self.concurrent_counts[client_id][operation_type]
            
            if current_concurrent >= concurrent_limit:
                business_logger.info(
                    f"Concurrent limit reached for {client_id} ({operation_type}): "
                    f"{current_concurrent}/{concurrent_limit}, trace_id: {trace_id}"
                )
                return False
        
        # 2. 检查速率限制（令牌桶）
        bucket = self._get_or_create_bucket(client_id, client_type, is_read)
        if bucket:
            allowed = await bucket.consume()
            if not allowed:
                available = await bucket.get_available_tokens()
                business_logger.info(
                    f"Rate limit reached for {client_id} ({operation_type}): "
                    f"available tokens={available:.2f}, trace_id: {trace_id}"
                )
                return False
        
        # 3. 记录请求
        self.request_history[client_id].append((time.time(), operation_type))
        
        # 4. 增加并发计数（需要在请求完成后减少）
        self.concurrent_counts[client_id][operation_type] += 1
        
        # 启动异步任务来减少并发计数（模拟请求完成）
        asyncio.create_task(self._decrease_concurrent(client_id, operation_type))
        
        return True
    
    async def _decrease_concurrent(self, client_id: str, operation_type: str):
        """减少并发计数（模拟请求完成后）"""
        # 等待一段时间模拟请求处理
        await asyncio.sleep(0.5)  # 实际应用中应该在请求完成后调用
        
        async with self.concurrent_locks[client_id][operation_type]:
            if self.concurrent_counts[client_id][operation_type] > 0:
                self.concurrent_counts[client_id][operation_type] -= 1
    
    async def get_metrics(self) -> Dict[str, Any]:
        """获取限流指标"""
        metrics = {
            "clients": {},
            "timestamp": datetime.now().isoformat()
        }
        
        for client_id in self.buckets.keys():
            client_metrics = {
                "concurrent": dict(self.concurrent_counts[client_id]),
                "buckets": {}
            }
            
            # 获取令牌桶状态
            for op_type, bucket in self.buckets[client_id].items():
                if bucket:
                    client_metrics["buckets"][op_type] = {
                        "available_tokens": await bucket.get_available_tokens(),
                        "capacity": bucket.capacity,
                        "rate_per_second": bucket.rate
                    }
            
            # 计算最近的请求统计
            now = time.time()
            last_minute_requests = {"read": 0, "write": 0}
            
            for timestamp, op_type in self.request_history[client_id]:
                if now - timestamp <= 60:
                    last_minute_requests[op_type] += 1
            
            client_metrics["requests_last_minute"] = last_minute_requests
            metrics["clients"][client_id] = client_metrics
        
        return metrics