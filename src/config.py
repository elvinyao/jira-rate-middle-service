# config.py
import os
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局配置"""
    # Jira 配置
    JIRA_BASE_URL: str = os.getenv("JIRA_BASE_URL", "https://your-domain.atlassian.net")
    JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "your-api-token")
    
    # 重试配置
    MAX_RETRIES: int = 3
    
    # 调试模式
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # 日志级别
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    class Config:
        env_file = ".env"


# 客户端分类配置
CLIENT_CONFIGS: Dict[str, Dict[str, Any]] = {
    # 客户端 ID -> 配置
    "client-001": {
        "type": "premium",
        "name": "Premium Client 1"
    },
    "client-002": {
        "type": "standard",
        "name": "Standard Client 1"
    },
    "client-003": {
        "type": "basic",
        "name": "Basic Client 1"
    },
    "test-client": {
        "type": "basic",
        "name": "Test Client"
    }
}

# 限流配置（按客户端类型）
RATE_LIMIT_CONFIGS: Dict[str, Dict[str, Any]] = {
    "premium": {
        "read": {
            "requests_per_minute": 600,  # 每分钟请求数
            "concurrent_limit": 50,      # 并发限制
            "burst_size": 100           # 突发容量
        },
        "write": {
            "requests_per_minute": 120,
            "concurrent_limit": 20,
            "burst_size": 30
        }
    },
    "standard": {
        "read": {
            "requests_per_minute": 300,
            "concurrent_limit": 30,
            "burst_size": 50
        },
        "write": {
            "requests_per_minute": 60,
            "concurrent_limit": 10,
            "burst_size": 15
        }
    },
    "basic": {
        "read": {
            "requests_per_minute": 100,
            "concurrent_limit": 10,
            "burst_size": 20
        },
        "write": {
            "requests_per_minute": 20,
            "concurrent_limit": 5,
            "burst_size": 5
        }
    }
}


def get_client_config(client_id: str) -> Optional[Dict[str, Any]]:
    """获取客户端配置"""
    return CLIENT_CONFIGS.get(client_id)


def get_rate_limit_config(client_type: str, is_read: bool) -> Dict[str, Any]:
    """获取限流配置"""
    operation_type = "read" if is_read else "write"
    return RATE_LIMIT_CONFIGS.get(client_type, {}).get(operation_type, {})


# 创建全局配置实例
settings = Settings()