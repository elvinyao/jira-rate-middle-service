下面给你一套“**一处出错 → 三类产物**”的做法：
同一处异常只产生**一份标准异常对象**（包含错误码/可重试/上下文），由全局处理器**一次性**输出三样东西：

1. **系统错误日志**（给工程/运维看）
2. **业务错误日志**（给业务审计/运营看）
3. **用户可见错误消息**（给前端/APEX/Mattermost）

三者共享同一 **traceId / taskId / errorCode** 做关联，但**信息粒度不同**。

---

# 1) 三者的关系与边界

* **系统错误日志（System Error Log）**

  * 目的：定位故障与根因分析。
  * 内容：完整技术细节（异常类、堆栈、下游HTTP状态、requestId、重试次数、超时/限流指标、入参与出参“摘要/哈希”）。
  * 级别：ERROR（或最终失败时ERROR，中间重试WARN/INFO）。
  * **永不**记录敏感字段明文（token、密码、PII）；用脱敏/哈希替代。

* **业务错误日志（Business Error Log）**

  * 目的：业务可读、可审计，解释“这次任务为什么没完成/部分完成”。
  * 内容：错误码、简要业务原因、影响范围（项目/IssueType/条数）、处理结果（失败/部分成功/已回滚）、**用户可采取动作**。
  * 级别：WARN（业务可预期）、ERROR（导致业务中断且不可恢复）。
  * 不含堆栈/内部URI/返回体等技术细节。

* **用户可见错误消息（User Message）**

  * 目的：给用户**简短、可行动**的提示，不暴露内部实现。
  * 内容：一行主消息 + 简要建议 + **errorCode** +（可选）supportId（=traceId）。
  * 语气统一、可国际化；**不含内部细节**（类名/堆栈/内部URL）。

> 关系：**一份异常对象 → 渲染三种模板**（系统日志模板、业务日志模板、用户消息模板），用相同的 `errorCode/traceId/taskId` 互相关联。

---

# 2) 字段与模板（示例）

## 2.1 统一异常对象（AppError）关键字段

* `errorCode`：分层命名（如 `TICKET.EXTERNAL.429`、`WIKI.INTERNAL.TIMEOUT`、`BUSINESS.PERMISSION.DENIED`）
* `httpStatus`：对外HTTP状态（FastAPI 返回）
* `retryable`：是否可重试（决定重试与日志级别策略）
* `category`：`EXTERNAL_API / INTERNAL_MIDDLE / INTERNAL_SPECIAL / BUSINESS`
* `userTemplate` / `bizLogTemplate` / `sysLogTemplate`：三套模板
* `context`：`traceId`、`taskId`、`service`、`operation`、`uri`、`durationMs`、`projectKey`、`issueType`、`count` 等

## 2.2 系统错误日志（JSON模板）

```json
{
  "ts":"2025-08-14T02:34:56.123Z",
  "lvl":"ERROR",
  "logger":"ticket-service",
  "event":"EXTERNAL_CALL_FAIL",
  "errorCode":"TICKET.EXTERNAL.429",
  "traceId":"abc123",
  "taskId":"T-100",
  "service":"ticket-service",
  "operation":"jiraSearch",
  "uri":"/rest/api/2/search",
  "httpStatus":429,
  "retryable":true,
  "retryCount":3,
  "durationMs":850,
  "stackHash":"c7a1e5",
  "detail":"rate limited; will backoff",
  "extra":{"jqlHash":"f9a0b1"}
}
```

## 2.3 业务错误日志（JSON模板）

```json
{
  "ts":"2025-08-14T02:34:56.130Z",
  "lvl":"WARN",
  "logger":"notice-bot",
  "event":"BUSINESS_FAIL",
  "errorCode":"TICKET.EXTERNAL.429",
  "traceId":"abc123",
  "taskId":"T-100",
  "projectKey":"ABC",
  "effect":"skipped",
  "hint":"Jira限流，已自动重试3次仍失败，稍后系统将重试或请手动重试"
}
```

## 2.4 用户可见错误消息（返回给前端/通知）

```json
{
  "code":"TICKET.EXTERNAL.429",
  "message":"外部系统暂时不可用，请稍后重试（已自动重试3次）。如需协助请提供支持码：abc123。"
}
```

---

# 3) 什么时候记“业务log”vs“系统log”？

| 场景          | 分类               | 系统错误日志                    | 业务错误日志              | 用户消息                     |
| ----------- | ---------------- | ------------------------- | ------------------- | ------------------------ |
| 用户权限不足      | BUSINESS         | WARN（可选，含上下文）             | **WARN**（必须，便于审计）   | “权限不足：无权操作项目X（code…）”    |
| 参数校验失败      | BUSINESS         | 可省略或WARN                  | **WARN**            | “输入校验失败：第3行格式不正确（code…）” |
| Jira 429/超时 | EXTERNAL         | **ERROR/WARN**（含重试次数、URI） | **WARN**（说明影响与后续动作） | “外部系统繁忙，请稍后重试（code…）”    |
| 内部service超时 | INTERNAL\_MIDDLE | **ERROR**                 | **WARN**            | “内部服务暂不可用，请稍后重试（code…）”  |
| Bug/空指针     | SYSTEM           | **ERROR**（含堆栈）            | 可选（若影响业务）           | “系统繁忙，请稍后重试（code…）”      |

> 原则：**业务可预期问题**主记“业务log”，**技术故障**主记“系统log”；返回给用户的**消息始终简洁且可行动**。

---

# 4) 展示与记录如何“连起来”

* **同一错误**：

  * 系统日志用 `sysLogTemplate` 记录技术细节；
  * 业务日志用 `bizLogTemplate` 记录业务影响与建议；
  * 前端返回 `userTemplate` 渲染的消息；
  * 三者共享 `errorCode + traceId + taskId`，便于在 ELK/界面互相定位。

* **去重与降噪**：

  * 同一 `errorCode+uri+项目+1分钟窗口` 的系统ERROR做**采样/聚合**，避免告警风暴。
  * 业务日志按“任务粒度”只记一次失败记录。

---

# 5) 与重试/幂等的配合

* `retryable=true` 时：

  * 中间尝试：系统日志记 **WARN/INFO**（包含 attempt 序号、退避时间），不出业务日志/不提示用户；
  * 最终仍失败：系统日志记 **ERROR**，输出业务日志一条，向用户返回失败消息。
* 写操作务必带**幂等键**（externalId/请求Id/去重表）；用户消息不要承诺“已写入”，统一说“请求已受理/正在重试/稍后可重试”。

---

# 6) 快速落地清单

1. **异常基类**携带 `errorCode/category/retryable` 与三套模板。
2. **全局异常处理器**（FastAPI & Bot Runner）：

   * 渲染并输出 **系统日志** + **业务日志** + **用户消息**；
   * 自动把 `traceId/taskId` 注入三者。
3. **日志分类 Logger**：`system` / `business` / `access`。
4. **字段标准**：三者都带 `errorCode/traceId/taskId`；系统日志带技术细节，业务日志带影响与建议，用户消息短句+建议+支持码。
5. **重试装饰器**：只对 `retryable` 异常，指数退避；最终失败再落三产物。
6. **脱敏/合规**：参数与响应体做“摘要/哈希/截断”，避免泄露敏感信息。

---

## 两个落地示例

**A. Jira 限流（429）**

* 抛出 `ExternalAPIError(..., retryable=True, context={uri,...})`
* 重试3次→仍失败：

  * 系统日志（ERROR）：记录429、URI、重试次数、耗时
  * 业务日志（WARN）：记录“本次任务跳过/稍后重试”
  * 用户消息：外部系统繁忙 + 支持码

**B. 权限不足（403）**

* 抛出 `BusinessError(message="权限不足：无权操作项目 ABC")`
* 不重试：

  * 系统日志：可无或WARN（无堆栈）
  * **业务日志（WARN）**：记录任务与项目、谁执行
  * 用户消息：同上简短可行动

---

> 这套分层与模板，让“记录什么”和“给谁看”完全清晰：**同源异常，三套视图**，彼此用同一个 `errorCode/traceId/taskId` 关联。以后你在 ELK 里能从用户报错的支持码（traceId）直达系统日志，再回看业务日志评估影响范围。
