# logger_setup.py
import logging
import json
import sys
from typing import Any, Dict
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """JSON 格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为 JSON"""
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # 如果消息本身是字典，则合并
        if hasattr(record, "msg") and isinstance(record.msg, dict):
            log_obj.update(record.msg)
            log_obj["message"] = "structured_log"
        
        # 添加额外字段
        if hasattr(record, "trace_id"):
            log_obj["trace_id"] = record.trace_id
        if hasattr(record, "client_id"):
            log_obj["client_id"] = record.client_id
            
        # 添加异常信息
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_obj, ensure_ascii=False)


def setup_loggers():
    """设置日志系统"""
    
    # 1. Uvicorn 日志（访问日志）
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_handler = logging.StreamHandler(sys.stdout)
    uvicorn_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - UVICORN - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    uvicorn_logger.handlers = [uvicorn_handler]
    uvicorn_logger.setLevel(logging.INFO)
    uvicorn_logger.propagate = False
    
    # 2. 业务日志
    business_logger = logging.getLogger("business")
    business_handler = logging.StreamHandler(sys.stdout)
    business_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - BUSINESS - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    business_logger.handlers = [business_handler]
    business_logger.setLevel(logging.INFO)
    business_logger.propagate = False
    
    # 3. Jira 外呼日志（JSON 格式）
    jira_logger = logging.getLogger("jira_outbound")
    jira_handler = logging.StreamHandler(sys.stdout)
    jira_handler.setFormatter(JSONFormatter())
    jira_logger.handlers = [jira_handler]
    jira_logger.setLevel(logging.INFO)
    jira_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """获取日志记录器"""
    return logging.getLogger(name)