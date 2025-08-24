
请帮我整理我的系统方案需求。
先帮我梳理我的现状 ASIS
然后帮我梳理应该改善成什么样 TOBE
这里的TOBE 可以分成多个阶段1，阶段2， 阶段3这样。从改善的难易度上。
另外比如改善的时候需要在现在的系统梳理和整理哪些部分的内容，从哪里着手，按照什么技能进行分类进行梳理，梳理成什么养的内容模版。

现在主要讨论整体的方式上是否存在问题。

现状是整套系统，10个业务bot所需要的包含大量的jira操作。这些业务逻辑和对jira基础API的访问都集中在ticket-service。会有问题吧，比如发生bug了，需要修改，会造成degrade。而且资源过于集中，造成瓶颈对吧。

我认为，ticket-service只需要包括对基础jira操作的封装和api提供，比如jira jql search api，get single ticket api，update ticket api等等。每个业务bot具体的业务逻辑不放在ticket-service里。
然后各个业务bot在调用的时候，需要将traceid和包含具体业务信息的taskid和名称，传递给ticket-service以便middle-service可以记录log。此外ticket-service需要有2个独立的log，用于记录各个业务bot访问自己哪些api的access log以及自己访问jira哪些api的access log。这些access log也需要有traceid和traceid和包含具体业务信息的taskid和名称以及URI。
然后这个ticket-service为什么要把基础jira api进行封装，是为了整体控制发送给jira的api的速率。将对jira的操作类型分为GET系，DELETE系，CREATE系和UPDATE系。这些操作类型设置不同的权重，然后整体控制每秒可以发送多少权重的请求出去。GET系单独分类，DELETE系，CREATE系和UPDATE系是需要单独管理的一类。

另外还有wiki-service，mattermost-service，auth-service（会调用mattermost-service，ticket-service和额外的ldap）

再次，请告诉我整体的log方案，以及error-handling方案。
以及业务error log，系统error log。 业务error message（给用户的），系统error message（给用户的）
以及retry的方式和哪些地方需要retry。



如下是比较详细的系统说明
# 架构说明（Bot + Service 分层）

## 1. 背景概述

* **旧系统：** 多个独立 Bot 直接对接外部服务 (APEX/Jira/Confluence/Mattermost/LDAP)。此外还有专用服务 htmltag-service（用于 Indication 导入）和 aicheck-service（用于 Analysis 分析）协助处理业务逻辑，然后将结果写回各系统。
* **新系统：** 由 10 个业务 Bot 和 4 个中间件 Service 组成。每个 Bot 每隔 3 秒轮询 Oracle APEX 的 REST API 拉取待处理任务，并以相同频率回写任务进度和结果；所有对外系统的访问均统一经由中间件 Service 转发处理。

## 2. 名词与术语统一

* **APEX API：** Oracle APEX 暴露的 REST API，用于从任务表获取任务，以及回写任务进度和结果。
* **Bot（业务 Bot）：** 专门用于消费 APEX 任务的独立进程，内部会编排调用各 Service 完成具体业务逻辑。
* **Service（中间件 Service）：** 独立服务层，用于封装对外部系统的调用和统一处理逻辑，包括：

  * **auth-service：** 负责用户鉴权与权限校验。
  * **ticket-service：** 封装 Jira 操作（查询/创建/更新/删除），提供 JQL 组装、字段名映射、限流、幂等等功能。
  * **wiki-service：** 封装 Confluence 操作（页面查询、生成、更新，模板套用，层级管理）。
  * **mattermost-service：** 封装 Mattermost 操作，用于统一发送通知消息。
* **专用 Service：** 针对特定业务 Bot 的辅助服务：

  * **htmltag-service（indication-imp 专用）：** 将从 Confluence 页面表格中读取的内容中的 HTML 标签转换成 Jira 支持的格式（如保持文字样式、表格等），然后再由 ticket-service 将内容写入 Jira。
  * **aicheck-service（analysis-set 专用）：** 根据分析设定页面的规则，接受传入的 Jira Ticket 列表进行 AI 判断，返回需要调查的 Ticket 列表，供 Bot 后续处理（更新结果并通知）。
* **Issue Type：** Jira 中 `issueType` 的名称。不允许在代码中硬编码，在配置中可调整或被任务覆盖。
* **Field Name：** Jira 字段名称，不允许硬编码，可通过配置指定映射到不同环境的字段或自定义字段 ID（cfid）。
* **Excel 模板/文件：** 对于 Import 类型任务，用户在 APEX 上传 Excel 文件供 Bot 处理；对于 Export 类型任务，Bot 根据筛选条件生成 Excel 文件并上传回 APEX。
* **轮询与回写：** Bot 轮询任务的间隔为 3 秒；任务进度回写间隔为 3 秒（可配置）。

## 3. 总体架构与数据流

1. **任务拉取：** 各业务 Bot 定时（每 3 秒）通过 APEX API 拉取自己对应类型的待处理任务。
2. **用户鉴权：** Bot 调用 auth-service 对任务执行者的身份和权限进行校验，并获取所需的项目上下文等信息。
3. **业务处理：** Bot 根据任务类型，调用适当的 ticket-service、wiki-service 和/或 mattermost-service 等，完成具体业务逻辑（如创建更新 Jira 工单、读取/写入 Confluence 页面、发送通知等）。
4. **结果回写：** Bot 将任务处理进度及最终结果回写到 APEX（通过相应 API 更新数据库记录）。对于 Export 任务，会将生成的 Excel 文件一并上传回 APEX 供用户下载。

上述流程由各 Bot 独立运行，互不影响。所有对 Jira、Confluence、Mattermost 等外部系统的访问均统一经过相应的中间件 Service 实现。

## 4. 中间件 Service 职责边界

* **auth-service：**

  * **输入：** 登录用户身份、目标资源或操作。
  * **输出：** 是否具备访问权限，以及用户的项目权限上下文信息（如所属项目、权限范围）。
* **ticket-service：**

  * **输入：** 语义化的 Jira 操作请求（例如按条件查询、批量创建/更新/删除工单等）。
  * **输出：** 对应的 Jira 数据对象或操作结果。
  * **能力：** 自动组装 JQL、字段名⇄自定义字段ID (cfid) 映射、IssueType 配置替换；对外请求统一限流；幂等写入（保证重复请求不重复创建）。
* **wiki-service：**

  * **输入：** Confluence 页面模板、内容数据、目标空间及层级位置信息。
  * **输出：** Confluence 页面创建、更新或查询的结果。
* **mattermost-service：**

  * **输入：** 目标频道或用户、消息内容。
  * **输出：** 发送结果（带自动重试及降级处理机制）。

## 5. 业务 Bot 分类与职责

业务 Bot 根据任务类型可分为以下几类，每类 Bot 处理对应的业务场景：

* **批量导入/导出类：** 包括 `task-imp`、`task-exp`、`case-imp`、`case-exp`、`indication-imp`。这类任务由用户在 APEX 页面即时触发（点击操作按钮后记录任务），Bot 在 3 秒轮询中获取到任务即立即执行，并将数据库中该任务记录的状态更新为“执行中”。
* **通知类：** 包括 `notice-set`、`analysis-set`、`update-set`。此类任务既可以由用户即时触发执行，也可以由用户预先设定为定时任务（如每日、每周特定时间执行）。Bot 轮询到任务后，对于定时任务仅在设定的时间窗内执行，错过时间则等待下一个计划窗口。
* **状况汇总类：** 包括 `pr-set`、`qe-set`。这些任务同样支持即时触发和定时执行两种模式。Bot 会根据用户预先配置的 Confluence 页面模板来统计汇总 Jira 数据，并更新或生成相应的 Confluence 报告页面。

## 6. 调用关系矩阵

下表概括了各业务 Bot 与外部系统/服务的交互调用情况（✅ 表示有调用，— 表示无）：

| 业务 Bot           | 拉取任务<br>(APEX) | 权限校验<br>(auth) | Jira 操作<br>(ticket) | Confluence<br>(wiki) | 通知<br>(mattermost) | 进度/结果回写<br>(APEX) |       获取用户信息<br>(LDAP)       |           HTMLTag 转换<br>Service           | AI 分析<br>CheckService |
| ---------------- | :------------: | :------------: | :-----------------: | :------------------: | :----------------: | :---------------: | :--------------------------: | :---------------------------------------: | :-------------------: |
| `task-imp`       |        ✅       |        ✅       |       ✅（批量增改删）      |           —          |          —         |         ✅         | ✅ *（通过 auth-service 获取用户列表）* |                     —                     |           —           |
| `task-exp`       |        ✅       |        ✅       |        ✅（查询）        |           —          |          —         |  ✅ *（含 Excel 上传）* | ✅ *（通过 auth-service 获取用户列表）* |                     —                     |           —           |
| `case-imp`       |        ✅       |        ✅       |       ✅（批量增改删）      |           —          |          —         |         ✅         | ✅ *（通过 auth-service 获取用户列表）* |                     —                     |           —           |
| `case-exp`       |        ✅       |        ✅       |        ✅（查询）        |           —          |          —         |  ✅ *（含 Excel 上传）* | ✅ *（通过 auth-service 获取用户列表）* |                     —                     |           —           |
| `indication-imp` |        ✅       |        ✅       |       ✅（批量增改删）      |      ✅ *（解析/回写）*     |          —         |         ✅         | ✅ *（通过 auth-service 获取用户列表）* | ✅ *（调用 htmltag-service 转换内容后再交由 Jira 操作）* |           —           |
| `notice-set`     |        ✅       |        —       |        ✅（查询）        |           —          |          ✅         |         ✅         |               —              |                     —                     |           —           |
| `analysis-set`   |        ✅       |        —       |        ✅（查询）        | ✅ *（解析/回写，用于 AI 检查）* |          ✅         |         ✅         |               —              |                     —                     |           ✅           |
| `update-set`     |        ✅       |        ✅       |       ✅（批量更新）       |           —          |          ✅         |         ✅         |               —              |                     —                     |           —           |
| `pr-set`         |        ✅       |        —       |        ✅（查询）        |      ✅ *（解析/回写）*     |          —         |         ✅         |               —              |                     —                     |           —           |
| `qe-set`         |        ✅       |        —       |        ✅（查询）        |      ✅ *（解析/回写）*     |          —         |         ✅         |               —              |                     —                     |           —           |

**说明：**

* “获取用户信息 (LDAP)”一列指需要获取用户邮箱或ID的场景，实际通过 auth-service 内部集成 Jira/Mattermost/LDAP 来间接获取（见下文 auth-service 处理逻辑）。
* Indication 类和 Analysis 类任务涉及从 Confluence 页面读取配置信息或内容，因此需要调用 wiki-service。Indication 导入任务在将内容写入 Jira 前，会借助 htmltag-service 转换 HTML 标签格式。
* Analysis-set 任务调用专用的 AI 检查服务 (aicheck-service) 对 Jira 数据进行智能分析判断。

## 7. Service ↔ 外部系统映射

中间件 Service 对应的外部系统及其关键功能、策略如下：

| Service                | 对接外部系统及处理逻辑                                                                                                                                                                      | 主要能力                           | 关键策略                                  |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------------------------- |
| **auth-service**       | **Jira / Mattermost / LDAP：** 处理逻辑为首先使用 Jira 验证登录用户名对目标项目的权限；如果权限不足，则通过 Mattermost API 根据用户名查询 email，再使用该 email 重试 Jira 权限校验；若仍无权限，再通过 LDAP 查询用户 email，并再次用该 email 进行 Jira 权限校验。 | 用户鉴权、解析用户权限范围                  | 失败快速返回；操作全程可审计                        |
| **ticket-service**     | **Jira：** 通过 Jira REST API 进行查询、批量创建/更新/删除，支持 JQL 拼装、字段与 IssueType 名称到实际 ID 的映射。                                                                                                 | 批量查询与写入；JQL 组装；字段/IssueType 映射 | 幂等（利用外部关联键防止重复）；统一限流（错误时减速、恢复平稳后逐步提速） |
| **wiki-service**       | **Confluence：** 通过 Confluence API 查询或创建/更新页面，支持模板渲染和页面层级结构管理。                                                                                                                    | 页面内容获取与写入；模板套用                 | 模板化更新（通过页面标识或标签实现幂等更新）                |
| **mattermost-service** | **Mattermost：** 通过 Mattermost API 发送频道或用户消息通知。                                                                                                                                   | 统一消息通知发送                       | 自动重试；失败降级处理（记录失败不阻塞主流程）               |

## 8. 典型业务流程

下面详述各类任务的处理流程，包含主要的步骤和所涉及的服务调用：

### Import（`task-imp` / `case-imp` / `indication-imp`）

| 步骤 | 动作   | 说明                                                              | 主要调用                                 |
| -- | ---- | --------------------------------------------------------------- | ------------------------------------ |
| 1  | 拉取任务 | Bot 每 3 秒轮询获取待导入任务                                              | APEX API                             |
| 2  | 权限校验 | 校验用户对目标项目是否有创建/编辑权限                                             | auth-service                         |
| 3  | 解析输入 | 读取并解析上传的 Excel 文件；对于 Indication 类型，从指定的 Confluence 页面表格中读取配置和内容 | 本地解析（Excel）；wiki-service（Indication） |
| 4  | 业务写入 | 批量创建或更新 Jira 工单（包含字段填充等）                                        | ticket-service                       |
| 5  | 进度回写 | 持续更新导入进度，完成后记录结果状态                                              | APEX API                             |

### Export（`task-exp` / `case-exp`）

| 步骤 | 动作       | 说明                       | 主要调用           |
| -- | -------- | ------------------------ | -------------- |
| 1  | 拉取任务     | Bot 每 3 秒轮询获取待导出任务       | APEX API       |
| 2  | 权限校验     | 校验用户对目标项目是否有查询/浏览权限      | auth-service   |
| 3  | 数据查询     | 根据任务附带的筛选条件查询 Jira 工单数据  | ticket-service |
| 4  | 生成 Excel | 按预定义模板将查询结果渲染成 Excel 文件  | 本地处理           |
| 5  | 回传结果     | 上传生成的 Excel 文件，并更新任务完成状态 | APEX API       |

### Notice（`notice-set` 通知设定）

| 步骤 | 动作   | 说明                                           | 主要调用               |
| -- | ---- | -------------------------------------------- | ------------------ |
| 1  | 拉取任务 | Bot 每 3 秒轮询获取待执行的通知任务；若为定时任务，则检查当前时间是否在执行窗口内 | APEX API           |
| 2  | 数据查询 | 根据用户在任务中配置的 JQL 条件查询 Jira 工单数据               | ticket-service     |
| 3  | 数据分析 | 按预先设定的业务规则分析查询结果（例如筛选出需要通知的事项）               | 本地处理               |
| 4  | 发送通知 | 将分析/筛选后的结果整理为消息，通过 Mattermost 发送给指定频道或用户     | mattermost-service |
| 5  | 结果回写 | 更新任务状态为完成（可记录发送的通知概要）                        | APEX API           |

### Analysis（`analysis-set` 分析设定，含 AI 检查）

| 步骤 | 动作    | 说明                                                     | 主要调用               |
| -- | ----- | ------------------------------------------------------ | ------------------ |
| 1  | 拉取任务  | Bot 每 3 秒轮询获取待执行的分析任务；若为定时任务则检查是否到设定时间                 | APEX API           |
| 2  | 解析配置  | 获取用户设定的 Confluence 配置页面，并解析其中的检查规则和对应 JQL              | wiki-service       |
| 3  | 数据查询  | 根据解析得到的 JQL 查询 Jira 工单数据                               | ticket-service     |
| 4  | AI 检查 | 调用 AI Check 服务，对查询出的 Jira 数据逐项进行智能规则判断                 | aicheck-service    |
| 5  | 更新页面  | 将 AI 检查识别出的需关注的结果更新到 Confluence 页面（如在预先约定的结果表格中填入每条结果） | wiki-service       |
| 6  | 发送通知  | 将分析/检查结果整理为消息，通过 Mattermost 通知相关人员（**AI 检查的通知格式略有不同**） | mattermost-service |
| 7  | 结果回写  | 更新任务状态为完成（记录结果已同步到页面并通知）                               | APEX API           |

### Update（`update-set` 更新设定）

| 步骤 | 动作   | 说明                                              | 主要调用               |
| -- | ---- | ----------------------------------------------- | ------------------ |
| 1  | 拉取任务 | Bot 每 3 秒轮询获取待执行的更新任务；若为定时任务则检查是否到设定时间          | APEX API           |
| 2  | 权限校验 | 校验用户对目标项目是否有批量编辑权限                              | auth-service       |
| 3  | 解析规则 | 读取任务中配置的更新规则（包括目标 Jira Ticket 或 JQL、以及要修改的字段和值） | 本地处理               |
| 4  | 执行更新 | 按规则对目标 Jira 工单批量更新指定字段值                         | ticket-service     |
| 5  | 发送通知 | 将更新结果整理为消息，通过 Mattermost 通知相关人员                 | mattermost-service |
| 6  | 结果回写 | 更新任务状态为完成（记录更新执行结果）                             | APEX API           |

### 状况汇总（`pr-set` / `qe-set`）

| 步骤 | 动作   | 说明                                          | 主要调用           |
| -- | ---- | ------------------------------------------- | -------------- |
| 1  | 拉取任务 | Bot 每 3 秒轮询获取待执行的汇总任务；若为定时任务则检查是否到设定时间      | APEX API       |
| 2  | 获取模板 | 根据任务配置，读取用户准备好的 Confluence 雏形页面内容（作为统计模板）   | wiki-service   |
| 3  | 解析设置 | 解析模板页中的“状况统计设定”表格，获取其中定义的各项统计视角和对应 JQL、字段等  | 本地处理 (解析)      |
| 4  | 数据查询 | 遍历设定的各项统计项，调用 Jira 接口查询相应数据并汇总结果            | ticket-service |
| 5  | 输出结果 | 根据汇总结果更新原 Confluence 页面或在指定位置创建新的页面，以呈现统计报表 | wiki-service   |
| 6  | 结果回写 | 更新任务状态为完成，并在 APEX 中记录生成的报表页面信息              | APEX API       |

## 9. 配置与可变项

各项可配置内容及默认/覆盖来源如下：

| 配置项                               | 默认值来源  | 任务级覆盖来源                                           | 备注                 |
| --------------------------------- | ------ | ------------------------------------------------- | ------------------ |
| IssueType（如 Task、Case、Indication） | 程序内配置  | Case：Excel 文件的「设置」Sheet；Indication：Confluence 设置表 | 禁止硬编码              |
| 字段名映射（含自定义字段 cfid）                | 程序内配置  | 可按部署环境覆盖配置                                        | 用于适配不同环境的字段差异      |
| Export Excel 模板                   | 外部配置文件 | —                                                 | 导出任务使用的 Excel 模板   |
| Confluence 页面模板                   | 外部配置文件 | —                                                 | wiki-service 创建页面用 |
| 轮询/回写间隔                           | 3 秒    | 可通过配置调整                                           | 全局统一控制             |

## 10. 非功能与运维约束

* **幂等性：** 外部写操作采用唯一键、防重复机制，确保由于重试导致的重复请求不会产生重复数据。
* **统一节流：** 所有对 Jira 的调用经由 ticket-service 实现集中限流。当出现错误时自动降低调用频率，并在系统恢复后逐步恢复速率，保证外部系统稳定性。
* **高可用：** 各 Bot 相互独立部署，单个 Bot 发布或停机不会影响其他 Bot 的任务执行。通过无状态设计和滚动发布，实现升级过程中不中断服务。
* **可观测性：** 提供统一的日志记录和监控指标，包括任务成功率、调用 QPS、错误分类、限流触发次数、重试次数等，便于运维定位问题和优化性能。

## 11. 设计补充（JQL 与字段处理）

* **JQL 组装：** 由 ticket-service 统一负责根据参数模板拼装生成，Bot 层无需关注具体 JQL 拼接细节，可通过配置调整查询逻辑。
* **字段与 cfid 映射：** 在 ticket-service 内维护 Jira 字段名与自定义字段 ID 的映射表。Bot 传递业务上识别的字段名，由 ticket-service 转换为对应环境下的字段 ID，再进行调用，避免在 Bot 层硬编码任何字段信息。

## 12. 发布约束

为了避免发布更新对任务处理造成影响，新系统要求单个 Bot 服务的发布/停机不影响其他 Bot 的运行。通过将各 Bot 独立部署并采用灰度/滚动发布方式，在发布过程中保证其余 Bot 正常工作，不中断整体服务。