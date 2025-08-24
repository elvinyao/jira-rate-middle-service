下面给你一套**可以直接拿去喂 Claude Code 或 Gemini CLI 的“改造工作流 + 代码骨架 + 验收脚本”**。它覆盖你要的四个日志特性与两层错误体系，并给出事前检查（pre-check）清单。你可以按“工作流步骤 → 贴入提示词 → 让助手生成补丁（patch）→ 跑起验证”的方式执行。

---

# A. 改造目标对照清单（落地后应满足）

1. **Logger 初始化**：支持外部参数设置

* `--log-level`（或 `LOG_LEVEL` 环境变量），如 `DEBUG/INFO/WARN/ERROR`
* `--log-format`（或 `LOG_FORMAT`），`json` 或 `plain`

2. **三类日志**（每条日志含 `logType` 字段）

* `business`：业务事件/失败原因（给运营/审计）
* `internal_access`：**FastAPI 入站访问**（替换/接管 `uvicorn.access` 的输出格式）
* `external_access`：**出站 HTTP 调用**（Jira/Confluence/Mattermost/LDAP 等）

3. **traceId 贯穿**

* 业务服务**创建任务时生成 `traceId`**，FastAPI 中间件从请求头读或生成
* 通过 `contextvars` 注入日志、向下游 HTTP 透传（`X-Trace-Id`/`X-Task-Id`）

4. **Tomcat 风格的出站访问日志**

* 专门 logger（就叫 `external_access`），**逐次**记录对外 REST 请求的：方法、URL、状态码、字节数、时长、重试次数、失败原因摘要、traceId/taskId

5. **错误处理**

* 分层异常：`BusinessError`、`SystemError`（都继承 `AppError`）
* 必要属性：`code`、`http_status`、`retryable`、`log_level`、`user_message_template`
* **全局处理器**统一渲染：系统错误日志（技术细节）、业务错误日志（业务语义）、用户可见错误消息（简短可操作）

6. **事前检查**

* 业务校验（参数/权限/配置）
* 对外部系统调用的前置检查（凭据/基址/速率限制/幂等键）
* 内部中间服务连通/契约检查
* **Jira 专项**：基址、auth、JQL 允许运行、写操作幂等键策略

---

# B. 在 Claude Code / Gemini CLI 中的“分步提示词工作流”

> 用法：每步把“**提示词**”整体贴给助手；如果你的工具支持“应用补丁/创建文件”，就让它直接写文件；否则让它回出完整代码后你复制粘贴。

## 步骤 1：创建通用上下文与日志装配

**提示词（复制给 Claude/Gemini）：**

> **任务**：在仓库新增 `common/context.py` 与 `common/logging_setup.py`。要求：
>
> 1. 使用 `contextvars` 存 `traceId/taskId`，提供 `set_context/clear_context`。
> 2. `setup_logging(level, fmt)` 可被 CLI 参数或环境变量驱动：`log-level` (默认 INFO)、`log-format`=`json|plain` (默认 json)。
> 3. 定义三个命名 logger：`business`、`internal_access`、`external_access`。所有日志都包含字段：`ts,lvl,logger,logType,traceId,taskId,msg`。
> 4. 提供 `make_uvicorn_log_config(level, fmt)`，将 `uvicorn` 与 `uvicorn.access` 的 handler/formatter 替换为我们的（从而规范入站访问日志为 `internal_access` 风格，并注入 traceId）。
> 5. `JsonFormatter` 输出 JSON；`PlainFormatter` 输出单行文本。
>    **请生成文件内容。**

（助手应生成与下述骨架等价的代码；如需参考，见后文“代码骨架要点”）

## 步骤 2：为 FastAPI 接入中间件与全局异常

**提示词：**

> **任务**：修改 FastAPI 启动模块（如 `api/bootstrap.py` 或 `app.py`）：
>
> 1. 启动时读取 `--log-level/--log-format` 或 `LOG_LEVEL/LOG_FORMAT`，调用 `setup_logging()`。
> 2. 注册 HTTP 中间件：从 `X-Trace-Id`/`X-Task-Id` 读取或生成 UUID，调用 `set_context()`；响应头回写。
> 3. 使用 `make_uvicorn_log_config()` 提供给 `uvicorn.run(..., log_config=...)`。
> 4. 注册 `@app.exception_handler(AppError)` 和兜底 `@app.exception_handler(Exception)`：
>
>    * `BusinessError` 记一条 `business`（WARN）+ 返回 4xx 用户消息
>    * `SystemError` 记一条 `external_access` 或 `system`（ERROR，技术细节）+ 返回 5xx 用户消息
>      **请直接给出修改后的文件代码或补丁。**

## 步骤 3：定义异常层级

**提示词：**

> **任务**：新建 `common/exceptions.py`：
>
> * 定义 `AppError(Exception)`，含属性：`code,str`、`http_status,int`、`retryable,bool`、`log_level,int`、`user_message_template,str`；提供 `render_user_msg(**ctx)`。
> * `BusinessError(AppError)`：默认 `http_status=400`、`retryable=False`、`log_level=WARN`，`code="BUSINESS_ERROR"`；`user_message_template="{message}"`。
> * `SystemError(AppError)`：默认 `http_status=502`、`retryable` 可选、`log_level=ERROR`，`code="SYSTEM_ERROR"`；`user_message_template="系统繁忙，请稍后重试。"`。
> * 允许传入 `extra` 字段，写日志时合并上下文。

## 步骤 4：包装出站 HTTP（external\_access + 重试 + 透传 trace）

**提示词：**

> **任务**：新建 `common/http.py`：
>
> 1. 提供 `async def request(client: httpx.AsyncClient, method, url, **kw)`：
>
>    * 补齐 headers：`X-Trace-Id/X-Task-Id`；
>    * 记录一条 `external_access` 日志（Tomcat 风格）：`method,url,host,status,bytes,durationMs,retryCount,error,traceId,taskId`；
>    * 对 `429/5xx/超时` 抛出 `SystemError(retryable=True, code="EXTERNAL.HTTP")`，其他非 2xx 抛出 `SystemError(retryable=False)`。
> 2. 提供基于 `tenacity` 的指数退避重试装饰器（最多 3 次，0.2s 起，max 5s），仅对 `e.retryable` 生效。
> 3. 暴露 `async def get/post/put/delete(...)` 便捷方法。
>    **请生成文件内容。**

## 步骤 5：把创建任务的地方生成 `traceId` 并传递

**提示词：**

> **任务**：定位所有“新建用户任务”的入口（APEX 拉取到任务、或用户触发的创建）；在进入业务处理前：
>
> * 生成 `traceId=uuid4()`（若前端未传），`taskId` 取任务主键；
> * 调用 `set_context(traceId, taskId)`；
> * 将 `traceId/taskId` 存入任务上下文，传给后续调用链（包括对各 Service 与外部系统的调用 headers）。
>   **请输出修改点列表和补丁。**

## 步骤 6：事前检查（pre-check）钩子

**提示词：**

> **任务**：新增 `common/precheck.py` 与在关键业务路径调用：
>
> * `check_business(payload, rules)`：字段必填、格式、权限（失败抛 `BusinessError(code="BUSINESS.VALIDATION", message="...")`）
> * `check_internal(service_client)`：中间服务基址/健康探针/超时设置是否齐全（失败抛 `SystemError(code="INTERNAL.UNAVAILABLE")`）
> * `check_external(base_url, auth)`：外部系统（Jira/Confluence）基址、token、超时、代理参数、限流器就绪（失败抛 `SystemError(code="EXTERNAL.MISCONFIG")`）
> * `check_jira_write(idempotency_key)`：写操作强制校验幂等键存在（失败抛 `BusinessError(code="BUSINESS.IDEMPOTENCY", message="缺少幂等键")`）
>   **请生成文件与在两条典型链路（查询、写入）中的调用示例补丁。**

## 步骤 7：运行方式与参数

**提示词：**

> **任务**：为 `main.py` 增加 `argparse`：`--log-level`、`--log-format`；环境变量兜底。示例运行：
>
> ```
> python -m app.main --log-level=INFO --log-format=json
> # 或
> LOG_LEVEL=DEBUG LOG_FORMAT=plain python -m app.main
> ```
>
> **请提供修改后的 `main.py`。**

---

# C. 关键代码骨架（参考实现要点）

> 你可以让助手“按本骨架风格生成”，也可以直接复制改名使用。

### 1) `common/context.py`

```python
from contextvars import ContextVar
trace_id_ctx = ContextVar("trace_id", default="-")
task_id_ctx = ContextVar("task_id", default="-")

def set_context(trace_id: str | None = None, task_id: str | None = None):
    if trace_id: trace_id_ctx.set(trace_id)
    if task_id: task_id_ctx.set(task_id)

def clear_context():
    trace_id_ctx.set("-"); task_id_ctx.set("-")
```

### 2) `common/logging_setup.py`（三类 logger + uvicorn log\_config）

```python
import json, logging, os
from .context import trace_id_ctx, task_id_ctx

class ContextFilter(logging.Filter):
    def filter(self, record):
        record.traceId = trace_id_ctx.get()
        record.taskId = task_id_ctx.get()
        return True

class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "lvl": record.levelname, "logger": record.name,
            "logType": getattr(record, "logType", "business"),
            "traceId": getattr(record, "traceId", "-"),
            "taskId": getattr(record, "taskId", "-"),
            "msg": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)

class PlainFormatter(logging.Formatter):
    def format(self, record):
        return f"{self.formatTime(record,'%H:%M:%S')} [{record.levelname}] {getattr(record,'logType','-')} " \
               f"trace={getattr(record,'traceId','-')} task={getattr(record,'taskId','-')} {record.getMessage()}"

def setup_logging(level: str | None = None, fmt: str | None = None):
    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.getenv("LOG_FORMAT") or "json").lower()
    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else PlainFormatter())

    # 清空并统一根 handler
    root.handlers = [handler]

    # 三类命名 logger
    for name in ("business", "internal_access", "external_access"):
        lg = logging.getLogger(name)
        lg.propagate = True
        lg.setLevel(level)

def make_uvicorn_log_config(level: str, fmt: str):
    formatter = "json" if fmt == "json" else "plain"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"ctx": {"()": f"{__name__}.ContextFilter"}},
        "formatters": {
            "json": {"()": f"{__name__}.JsonFormatter"},
            "plain": {"()": f"{__name__}.PlainFormatter"},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "filters": ["ctx"],
                "formatter": formatter,
            }
        },
        "loggers": {
            # 统一用我们自己的 handler，注入 logType
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": level, "propagate": False},
            # 我们的三类
            "business": {"handlers": ["default"], "level": level, "propagate": False},
            "internal_access": {"handlers": ["default"], "level": level, "propagate": False},
            "external_access": {"handlers": ["default"], "level": level, "propagate": False},
        },
    }
```

> 在发 `internal_access` 日志时，记得设置 `record.logType="internal_access"`（见下一段中间件）；`uvicorn.access` 的输出也会走我们的 formatter/filters，从而带上 traceId/taskId。

### 3) FastAPI 中间件与异常处理（片段）

```python
# app.py 或 api/bootstrap.py
import uuid, logging, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from common.logging_setup import setup_logging, make_uvicorn_log_config
from common.context import set_context, clear_context
from common.exceptions import AppError, BusinessError, SystemError

def create_app():
    app = FastAPI()

    @app.middleware("http")
    async def tracing(request: Request, call_next):
        trace = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        task  = request.headers.get("X-Task-Id") or "-"
        set_context(trace, task)
        try:
            # 入站访问日志（internal_access）
            lg = logging.getLogger("internal_access")
            lg.info("REQUEST %s %s", request.method, request.url, extra={"logType": "internal_access"})
            resp = await call_next(request)
            lg.info("RESPONSE %s %s %s", request.method, request.url.path, resp.status_code,
                    extra={"logType": "internal_access"})
            resp.headers["X-Trace-Id"] = trace
            if task != "-": resp.headers["X-Task-Id"] = task
            return resp
        finally:
            clear_context()

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        log = logging.getLogger("business" if isinstance(exc, BusinessError) else "external_access")
        log.log(exc.log_level, f"{exc.code} {exc.render_user_msg()}", extra={
            "logType": "business" if isinstance(exc, BusinessError) else "external_access"
        })
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.render_user_msg()},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception):
        logging.getLogger("external_access").exception("UNEXPECTED: %s", exc, extra={"logType": "external_access"})
        return JSONResponse(status_code=500, content={"code": "UNEXPECTED", "message": "系统繁忙，请稍后重试。"})

    return app

if __name__ == "__main__":
    import os, argparse
    p = argparse.ArgumentParser()
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL","INFO"))
    p.add_argument("--log-format", default=os.getenv("LOG_FORMAT","json"))
    args = p.parse_args()

    setup_logging(args.log_level, args.log_format)
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000,
                log_config=make_uvicorn_log_config(args.log_level, args.log_format))
```

### 4) `common/exceptions.py`

```python
import logging
from typing import Any, Mapping, Optional

class AppError(Exception):
    code: str = "APP_ERROR"
    http_status: int = 500
    retryable: bool = False
    log_level: int = logging.ERROR
    user_message_template: str = "系统繁忙，请稍后重试。"

    def __init__(self, *, message: str = "", extra: Optional[Mapping[str, Any]] = None, **ctx):
        super().__init__(message)
        self.message = message
        self.extra = dict(extra or {})
        self.ctx = ctx

    def render_user_msg(self) -> str:
        data = {**self.extra, **self.ctx, "message": self.message}
        try:
            return self.user_message_template.format(**data)
        except Exception:
            return self.user_message_template

class BusinessError(AppError):
    code = "BUSINESS_ERROR"
    http_status = 400
    retryable = False
    log_level = logging.WARNING
    user_message_template = "{message}"

class SystemError(AppError):
    code = "SYSTEM_ERROR"
    http_status = 502
    retryable = True   # 可被构造时覆盖
    log_level = logging.ERROR
    user_message_template = "系统繁忙，请稍后重试。"
```

### 5) `common/http.py`（出站访问日志 + 重试）

```python
import time, logging, httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from .context import trace_id_ctx, task_id_ctx
from .exceptions import SystemError

ext_log = logging.getLogger("external_access")

def _retryable(e: Exception) -> bool:
    return isinstance(e, SystemError) and e.retryable

def _mk_headers(h: dict | None) -> dict:
    h = dict(h or {})
    h.setdefault("X-Trace-Id", trace_id_ctx.get())
    if task_id_ctx.get() != "-": h.setdefault("X-Task-Id", task_id_ctx.get())
    return h

@retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=0.2, max=5), reraise=True)
async def request(client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    kw["headers"] = _mk_headers(kw.get("headers"))
    t0 = time.perf_counter()
    try:
        resp = await client.request(method, url, **kw)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        ext_log.warning("EGRESS_FAIL %s %s timeout/connect", method, url, extra={"logType":"external_access"})
        raise SystemError(code="EXTERNAL.HTTP.TIMEOUT", http_status=502, retryable=True, message="外部系统超时") from e

    dur = int((time.perf_counter()-t0)*1000)
    size = int(resp.headers.get("Content-Length") or 0)

    if resp.status_code in (429, 500, 502, 503, 504):
        ext_log.warning("EGRESS %s %s %s %sms bytes=%s RETRYABLE",
                        method, url, resp.status_code, dur, size, extra={"logType":"external_access"})
        raise SystemError(code=f"EXTERNAL.HTTP.{resp.status_code}", http_status=502, retryable=True,
                          message="外部系统暂不可用", extra={"status": resp.status_code})
    if not resp.is_success:
        ext_log.error("EGRESS %s %s %s %sms bytes=%s", method, url, resp.status_code, dur, size,
                      extra={"logType":"external_access"})
        raise SystemError(code=f"EXTERNAL.HTTP.{resp.status_code}", http_status=502, retryable=False,
                          message="外部系统错误", extra={"status": resp.status_code})
    ext_log.info("EGRESS %s %s %s %sms bytes=%s", method, url, resp.status_code, dur, size,
                 extra={"logType":"external_access"})
    return resp

# sugar
async def get(c,*a,**k):   return await request(c,"GET",*a,**k)
async def post(c,*a,**k):  return await request(c,"POST",*a,**k)
async def put(c,*a,**k):   return await request(c,"PUT",*a,**k)
async def delete(c,*a,**k):return await request(c,"DELETE",*a,**k)
```

---

# D. 事前检查（pre-check）清单与示例

**在进入真正调用前做：**

1. **业务校验**

   * 必填字段 / 值域 / 正则 / 互斥/依赖关系
   * 操作权限（项目、IssueType）
   * 写操作的**幂等键存在性**（如 `externalId`）

2. **外部系统（Jira/Confluence）**

   * `base_url` 非空且合法；token 可用（可用 `/myself` 检查一次，缓存 10 分钟）
   * HTTP 客户端：超时、代理、最大并发、重试策略就绪
   * 速率限制器（若有集中限流）对象可用

3. **内部中间服务**

   * 基址、健康探针、auth header
   * 契约字段映射（issueType、cfid）已加载

4. **Jira 专项**

   * JQL 语法预校验（快速 dry-run）
   * 写操作的字段映射完整（缺谁报 `BusinessError("字段映射缺失")`）
   * 幂等键策略确定（创建/更新路径一致）

**示例（伪码）：**

```python
from common.exceptions import BusinessError, SystemError

def precheck_create_issue(payload, cfg):
    if not payload.get("summary"):
        raise BusinessError(code="BUSINESS.VALIDATION", message="缺少摘要 summary")
    if not cfg.jira.base_url or not cfg.jira.token:
        raise SystemError(code="EXTERNAL.MISCONFIG", retryable=False, message="Jira 配置缺失")
    if not payload.get("idempotencyKey"):
        raise BusinessError(code="BUSINESS.IDEMPOTENCY", message="缺少幂等键 idempotencyKey")
```

---

# E. 验收自测（你可以让 Claude/Gemini 生成 pytest）

* **启动参数**：
  `python -m app --log-level=DEBUG --log-format=plain` / `LOG_LEVEL=INFO LOG_FORMAT=json ...`
  期望：日志切换生效；`uvicorn.access` 输出被替换；所有日志带 `traceId/taskId`。

* **入站日志**：打一个简单 GET，检查 `internal_access` 两条（REQUEST/RESPONSE）。

* **出站日志**：Mock 一个 httpbin，触发 200、429、500；检查 `external_access` 包含 method/url/status/duration/bytes 与 `traceId`。

* **错误分层**：

  * 触发 `BusinessError`（参数缺失）→ HTTP 400，`business` 日志 WARN；
  * 触发 `SystemError(retryable=True)`（模拟超时）→ 自动重试 3 次，最终 502，`external_access` WARN/ERROR 组合。

* **trace 贯穿**：人工指定 `X-Trace-Id`，看入站/出站/错误三类都同一个 traceId。

---

# F. 给助手的一句话总提示（可放在最前）

> 这是一个 Python + FastAPI 项目。请按我给定的“目标对照清单”和“分步提示词”，对代码库完成**可参数化的日志初始化**、三类日志（business/internal\_access/external\_access）、traceId 全链路注入、Tomcat 风格的出站访问日志、以及 `BusinessError/SystemError` 的全局异常处理。同时为 Jira/Confluence 等外部调用提供重试与事前检查。所有日志必须包含 `traceId/taskId` 字段，并支持 `json/plain` 两种输出格式。每一步请以**补丁**或**完整文件**形式输出。

---

以上就是把 **Claude Code / Gemini CLI** 用作“代码共创引擎”的完整操作剧本 + 参考实现。你可以直接按步骤贴提示词，让它连续生成补丁；也可以把“代码骨架要点”当成风格约束，要求它输出与之兼容的实现。需要我把这些步骤合成**一键 patch**（统一 diff）也可以，告诉我你的仓库路径结构就行。
