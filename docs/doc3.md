可以，而且**强烈建议**这样做：在 Python 与 FastAPI 中同时建立**统一的异常体系 + 全局处理器 + 结构化日志模板 + 可重试策略**。下面给你一套可直接落地的做法，含代码骨架与落地步骤，兼容你现有的 `common.logger`、ELK，覆盖 Bot（非Web进程）与 FastAPI 服务。

---

## 一、总体思路（你要得到什么）

1. **统一异常层级**：定义 `AppError` 基类与四大类子类

   * `ExternalAPIError`（Jira/Confluence/Mattermost/LDAP 等外部）
   * `InternalServiceError`（ticket/wiki/mattermost/auth 等内部中间件）
   * `SpecializedServiceError`（htmltag/aicheck 等专用服务）
   * `BusinessError`（权限/校验等业务可预期异常）
     每个异常都内置：`code`、`http_status`、`log_level`、`retryable`、`user_msg_template`、`log_template`。

2. **全局异常处理**

   * **FastAPI**：注册 `@app.exception_handler(AppError)`，统一把异常 -> 结构化响应（含 `traceId/taskId`）、统一按异常类型选用**日志模板**与**消息模板**。
   * **Bot/脚本**：提供 `guarded()` 装饰器或入口 `main()` try/except 统一兜底，行为与 FastAPI 保持一致。

3. **上下文关联（traceId/taskId）**

   * 用 `contextvars` + 日志 `Filter` 把 `traceId`、`taskId` 自动注入每条日志；
   * FastAPI 中间件从请求头（`X-Trace-Id`/`X-Task-Id`）读取或生成，Bot 在拉取 APEX 任务时设置。

4. **结构化日志 + 模板化消息**

   * 统一 JSON/字典结构日志字段：`service`, `event`, `traceId`, `taskId`, `code`, `uri`, `status`, `durationMs`, `detail`…
   * 针对不同异常类型，使用**不同 log 模板**与**用户可见 message 模板**（用于 Mattermost 通知或回写 APEX）。

5. **可重试与幂等**

   * 针对**短暂性 REST 错误**（超时、连接错误、429/5xx），按异常的 `retryable=True` 走**指数退避**重试；
   * 写操作配合**幂等键**（如 externalId、幂等 Header、自维护幂等表），确保重试不产生副作用。

---

## 二、代码骨架（可直接改造成你的 `common` 包）

### 1) context：trace/task 上下文与日志注入

```python
# common/context.py
from contextvars import ContextVar

trace_id_ctx = ContextVar("trace_id", default="-")
task_id_ctx = ContextVar("task_id", default="-")

def set_context(trace_id: str | None = None, task_id: str | None = None):
    if trace_id: trace_id_ctx.set(trace_id)
    if task_id: task_id_ctx.set(task_id)

def clear_context():
    trace_id_ctx.set("-"); task_id_ctx.set("-")
```

```python
# common/logging_setup.py
import logging, json
from .context import trace_id_ctx, task_id_ctx

class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.traceId = trace_id_ctx.get()
        record.taskId = task_id_ctx.get()
        return True

class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "traceId": getattr(record, "traceId", "-"),
            "taskId": getattr(record, "taskId", "-"),
        }
        # 可按需添加更多字段（service、event、uri、status等）
        return json.dumps(payload, ensure_ascii=False)

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    h.addFilter(ContextFilter())
    root.handlers = [h]
```

### 2) 统一异常层级

```python
# common/exceptions.py
import logging
from typing import Any, Mapping, Optional

class AppError(Exception):
    code: str = "APP_ERROR"
    http_status: int = 500
    log_level: int = logging.ERROR
    retryable: bool = False
    user_msg_template: str = "系统繁忙，请稍后重试。"
    log_template: str = "{code} reason={reason}"

    def __init__(self, reason: str = "", *, extra: Optional[Mapping[str, Any]] = None, **ctx):
        super().__init__(reason)
        self.reason = reason
        self.extra = dict(extra or {})
        self.ctx = ctx

    def render_log(self) -> str:
        data = {**self.extra, **self.ctx, "code": self.code, "reason": self.reason}
        return self.log_template.format(**{k: data.get(k, "") for k in set(data)})

    def render_user_msg(self) -> str:
        data = {**self.extra, **self.ctx}
        try:
            return self.user_msg_template.format(**data)
        except Exception:
            return self.user_msg_template

class ExternalAPIError(AppError):
    code = "EXTERNAL_API_ERROR"
    http_status = 502
    log_level = logging.ERROR
    user_msg_template = "外部系统不可用，请稍后重试。"
    # 429/5xx等场景可设 retryable=True
    def as_retryable(self): self.retryable = True; return self

class InternalServiceError(AppError):
    code = "INTERNAL_SERVICE_ERROR"
    http_status = 502
    log_level = logging.ERROR
    user_msg_template = "内部服务暂不可用，请稍后重试。"

class SpecializedServiceError(AppError):
    code = "SPECIALIZED_SERVICE_ERROR"
    http_status = 500
    log_level = logging.ERROR
    user_msg_template = "内部专用服务异常，请联系管理员。"

class BusinessError(AppError):
    code = "BUSINESS_ERROR"
    http_status = 400
    log_level = logging.WARNING
    user_msg_template = "{message}"  # 例如：权限不足/数据校验失败等
```

### 3) FastAPI：中间件 + 全局异常处理器

```python
# api/bootstrap.py
import logging, uuid, asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .notifier import Notifier  # 你已有的 mattermost-service 客户端
from common.context import set_context, clear_context
from common.exceptions import AppError
from common.logging_setup import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI()
    notifier = Notifier()

    @app.middleware("http")
    async def tracing_ctx(request: Request, call_next):
        trace = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        task = request.headers.get("X-Task-Id")
        set_context(trace, task)
        try:
            resp = await call_next(request)
            resp.headers["X-Trace-Id"] = trace
            if task: resp.headers["X-Task-Id"] = task
            return resp
        finally:
            clear_context()

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        logging.log(exc.log_level, exc.render_log())
        # 按需触发通知（示例：仅高优先级/非业务可预期）
        if exc.code in ("EXTERNAL_API_ERROR", "INTERNAL_SERVICE_ERROR"):
            asyncio.create_task(notifier.notify(exc))  # 异步，避免阻塞请求线程
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.render_user_msg(),
            },
        )

    # 可选：兜底处理未知异常
    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        logging.exception("UNEXPECTED_ERROR")  # 会带traceId/taskId
        return JSONResponse(status_code=500, content={"code": "UNEXPECTED", "message": "系统繁忙，请稍后重试。"})

    return app
```

### 4) 可重试策略（指数退避 + 幂等）

```python
# common/retry.py
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from common.exceptions import ExternalAPIError

def is_retryable(e: Exception) -> bool:
    return isinstance(e, ExternalAPIError) and e.retryable

# 通用重试装饰器：最多3次，0.2s起指数退避
retryable = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, max=5),
    reraise=True,
    retry=retry_if_exception(is_retryable)
)

@retryable
async def call_jira(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    try:
        resp = await client.request(method, url, **kwargs)
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as e:
        # 连接类超时/中断 -> 可重试
        raise ExternalAPIError("Jira timeout/connection").as_retryable() from e

    # 状态码判定
    if resp.status_code in (429, 500, 502, 503, 504):
        raise ExternalAPIError(f"Jira transient {resp.status_code}", extra={"status": resp.status_code}).as_retryable()
    if not resp.is_success:
        # 非短暂错误，不重试
        raise ExternalAPIError(f"Jira error {resp.status_code}", extra={"status": resp.status_code})
    return resp
```

> 写操作请加入**幂等键**（外部ID/去重Key/请求幂等Header），确保重试不产生重复副作用。

### 5) 日志/消息模板与通知

```python
# api/notifier.py
import logging
from common.context import trace_id_ctx, task_id_ctx
from common.exceptions import AppError

class Notifier:
    async def notify(self, exc: AppError):
        # 这里调用 mattermost-service；示例生成一段简洁的Markdown
        text = f"[{exc.code}] {exc.render_user_msg()} | trace={trace_id_ctx.get()} task={task_id_ctx.get()}"
        logging.getLogger("notice").info(text)
        # TODO: 调用 mattermost-service 发送 text
```

---

## 三、在 Bot（非 FastAPI 进程）里怎么做

* 在**拉取到 APEX 任务**后，用 `set_context(trace_id, task_id)` 注入上下文。
* 入口函数加统一保护，减少散乱 try/except：

```python
# bots/runner.py
import logging
from common.context import set_context, clear_context
from common.exceptions import AppError, BusinessError

def guarded(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AppError as e:
            logging.log(e.log_level, e.render_log())
            # 非关键错误可记录并继续；关键错误可抛出或上报
        except Exception:
            logging.exception("UNEXPECTED_ERROR")
        finally:
            clear_context()
    return wrapper

@guarded
def process_task(task):
    set_context(trace_id=task.trace_id, task_id=task.id)
    # ... 业务逻辑
    # 发现权限问题：
    # raise BusinessError("no permission", extra={"project": "ABC"}, message="权限不足：无权操作项目 ABC")
```

> 注意：`BusinessError.user_msg_template` 可使用 `message` 占位符，抛出时传入 `message="..."`。

---

## 四、日志规范（建议字段与分类）

* **统一字段**：`ts/lvl/logger/service/event/traceId/taskId/code/httpStatus/uri/method/status/durationMs/retryCount` + 任意 `extra`。
* **分类 logger**：

  * `app`（业务事件/处理结果）
  * `external`（对外 HTTP 调用；请求/响应/耗时）
  * `access`（对内接口访问日志）
  * `notice`（告警/通知消息）

ELK 中建仪表板：错误率、外部依赖失败率、429/5xx 命中、重试次数、Top 慢接口、按 `traceId` 串联链路。

---

## 五、从现有代码着手的实施步骤（低风险改造）

**阶段A（1\~2天／服务）**

1. 落地 `common/context.py`、`common/logging_setup.py`，全服务启动时调用 `setup_logging()`。
2. 为 FastAPI 服务加**上下文中间件**，为 Bot 在任务开始时 `set_context()`。
3. 新建 `common/exceptions.py`，在一两个最活跃的调用点（如 ticket-service 调 Jira）开始**抛出统一异常**。
4. FastAPI 注册 `@app.exception_handler(AppError)`；Bot 用 `guarded()` 包裹入口。

**阶段B（并行推进）**
5\) 梳理 HTTP 客户端，统一封装（如 httpx/requests），在封装层：

* 记录 `external` 日志（URI、方法、状态、耗时、traceId/taskId）；
* 识别短暂错误 -> `ExternalAPIError(...).as_retryable()`；
* 写操作传入**幂等键**；
* 用 `tenacity` 装饰写/读操作（读可更积极重试）。

6. 为重要错误添加**消息通知**（通过 `Notifier`），避免噪音：做**采样/节流**（相同错误1分钟只发一次）。

**阶段C（逐步替换散落 try/except）**
7\) 把零散 `try/except` 改成：

* 捕获就地转换为上述异常类型并 `raise`；
* 或直接让底层封装抛异常，上层只关注业务流程。

8. 统一**错误响应格式**（FastAPI 的 JSON）和**业务可见消息**（APEX/Mattermost）。

---

## 六、异常到 HTTP/消息的映射建议

| 异常类型                    | 典型场景                |    HTTP | 日志等级  | retryable | 用户消息示例            |
| ----------------------- | ------------------- | ------: | ----- | --------- | ----------------- |
| BusinessError           | 权限不足/校验失败           | 400/403 | WARN  | 否         | “权限不足：无权操作项目 X”   |
| ExternalAPIError        | Jira 429/504        |     502 | ERROR | 是         | “外部系统不可用，请稍后重试”   |
| InternalServiceError    | ticket-service 超时   |     502 | ERROR | 是         | “内部服务暂不可用，请稍后重试”  |
| SpecializedServiceError | htmltag-service 500 |     500 | ERROR | 视情况       | “内部专用服务异常，请联系管理员” |
| 未知异常                    | bug                 |     500 | ERROR | 否         | “系统繁忙，请稍后重试”      |

---

## 七、你关心的问题，一句话回答

* **能否全局异常处理、按类型套不同日志/消息模板？**
  能。FastAPI 用全局异常处理器，Bot 用统一入口/装饰器；异常类自带模板与级别，处理器里统一渲染并输出。

* **能否对特定 REST 异常做重试？**
  能。把“短暂性错误”归为 `retryable=True`，用 `tenacity` 做指数退避；写操作用幂等键保证安全。

---

需要的话，我可以把以上骨架打包成一个小“脚手架”（包含示例 FastAPI 服务、Bot Runner 与单元测试），你直接套用到 `ticket-service` / `wiki-service` / 各 Bot 上。
