# V8 Agent OS API 参考

本文说明当前 API 的分层、权威来源和主要路由族。它不是内部函数清单；实际请求/响应字段以当前 OpenAPI 与共享契约为准。

## 1. 地址与调用边界

| 层 | 默认地址 | 面向对象 | 角色 |
| --- | --- | --- | --- |
| Engine | `http://127.0.0.1:9530/v1` | Admin、CLI、受控内部服务 | 会话与 runtime 权威真相 |
| Admin | `http://127.0.0.1:9528/api` | Web、Phone、Shell 内页面 | 认证、代理、资源 URL 与人类可见规范化 |
| Web | `http://127.0.0.1:9527/api` | Web 页面自身 | 同源代理到 Admin，不创造第二套真相 |

固定规则：

1. Engine 是会话、run、runtime event、审批、产物和恢复状态的权威生产者。
2. Web 和 Phone 通过 Admin 的 client-facing API 消费这些真相，不直连数据库，也不依赖 Engine 的本机私有地址。
3. Web、Admin、Phone 的实时与历史投影共用 `packages/session-realtime`。
4. 本地 Web/Shell/桌宠是 trusted clients；Phone 是远程 paired client，认证流程不同。
5. 本地绝对路径、secret、raw provider payload、ledger 和 trace 不能直接投影到普通客户端。

```mermaid
flowchart LR
  Web["Web same-origin API"] --> Admin["Admin broker"]
  Phone["Paired Phone"] --> Admin
  Admin --> Engine["Engine /v1"]
  Engine --> Contract["session-realtime"]
  Contract --> Web
  Contract --> Phone
```

## 2. 会话、消息与实时状态

### 2.1 Engine 权威路由

常用入口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/v1/chat/submit` | 提交一轮聊天请求 |
| `POST` | `/v1/chat/upload` | 登记聊天上传来源 |
| `GET` | `/v1/sessions` | 会话列表 |
| `POST` | `/v1/sessions` | 创建会话 |
| `PATCH` | `/v1/sessions/{sessionId}` | 更新标题、展示元数据等 |
| `DELETE` | `/v1/sessions/{sessionId}` | 删除会话并触发关联治理 |
| `GET` | `/v1/sessions/{sessionId}/snapshot` | 当前紧凑快照 |
| `GET` | `/v1/sessions/{sessionId}/runtime-events` | runtime 事件源 |
| `GET` | `/v1/sessions/{sessionId}/history` | 历史投影 |
| `GET` | `/v1/sessions/{sessionId}/turn-index` | 稳定 canonical turn 索引 |
| `GET` | `/v1/sessions/{sessionId}/turns` | 按 turn 拉取内容 |
| `GET` | `/v1/sessions/{sessionId}/timeline/sync` | 增量时间线同步 |

`turn-index` 是导航真相；客户端缓存可以加速首屏，但不能自行重排 canonical turn。reasoning、tool、approval、ask_user、session coordination 等节点有各自结构化类型，不能伪装成用户消息。

### 2.2 Admin 客户端路由

客户端常用入口：

- `POST /api/client/chat-submit`
- `POST /api/client/upload`
- `GET|POST /api/client/conversations`
- `GET /api/client/conversations/{id}`
- `GET /api/client/conversations/{id}/turn-index`
- `GET /api/client/conversations/{id}/turns`
- `GET /api/realtime/sessions/{id}/snapshot`
- `GET /api/realtime/sessions/{id}/stream`

Web 的 `/api/*` 再代理到这些 Admin 路由。不要在 Web 页面里拼 Engine URL，也不要为 Phone 复制一套不同字段名。

### 2.3 排序与运行态

- `historySortAt`：历史列表排序真相。
- `lastActivityAt`：综合活动时间，不用于随意重排历史。
- 稳定 run/turn 状态来自 Engine；客户端 focus、点击或本地时钟不能把已运行任务改判为空闲。

## 3. 审批、询问与运行控制

Engine 控制入口包括：

- `GET /v1/approvals`
- `POST /v1/approvals/{approvalId}/approve`
- `POST /v1/approvals/{approvalId}/reject`
- `POST /v1/ask-user/{interactionId}/respond`
- `GET /v1/runs`
- `GET /v1/runs/{runId}/ledger`
- `POST /v1/runs/{runId}/commands/{command}`
- `GET /v1/runtime-episodes/overview`

Spec 阶段同意、ask_user 回答和安全副作用审批是不同语义，客户端不得合并成一种“确认”。Admin 可以展示治理详情，Web/Phone 只展示用户需要理解和操作的内容。

## 4. 工作区、来源与产物

### 4.1 工作区资源

常用 Engine 路由：

- `GET /v1/workspace/resource`
- `POST /v1/sessions/{sessionId}/workbench/files/resolve`
- `GET /v1/sessions/{sessionId}/workbench/files/read`
- `GET|PUT /v1/sessions/{sessionId}/scope`
- `POST /v1/sessions/{sessionId}/scope/re-resolve`

客户端应消费 Admin 规范化后的资源引用，而不是裸 `C:\...`、`file://` 或 Engine 私网 URL。工作区显示名不改变底层路径和信任边界。

### 4.2 source 与 artifact

| 类型 | 真相 | 是否出现在会话产物概览 |
| --- | --- | --- |
| 用户上传 | source ledger，绑定 session/message | 否，已在用户消息中展示 |
| Agent 写入/下载/Spec/媒体输出 | artifact ledger，绑定 session/run/tool | 是 |
| 工作区已有或手工复制文件 | 普通工作区文件 | 否，除非显式采用 |

相关路由：

- `GET /v1/sources`
- `GET /v1/sessions/{sessionId}/artifacts`
- `GET /v1/artifacts`
- `GET /v1/artifacts/{artifactId}`
- `GET /v1/artifacts/{artifactId}/content`
- `POST /v1/artifacts/adopt-workspace-file`

产物查询必须按会话 lineage 收敛，不能把同一工作区中其他会话或整个目录树混进当前看板。

## 5. Engineering 与 UI Patch

### 5.1 Engineering 控制面

- `POST /v1/engineering-lane/dry-run`
- `GET /v1/engineering-lane/proof-ledger`
- `GET /v1/engineering-lane/workset-observations`
- `GET /v1/projects/{projectId}/engineering-workspace`
- `POST /v1/projects/{projectId}/engineering-workspace/adopt`

项目工作区路由受安装 profile/knowledge service 影响。采用现有非 Git 工作区必须是显式操作；托管 worktree、sandbox lease 和候选 change set 是执行控制面，不应作为普通文件 API 暴露。

### 5.2 UI Patch Workbench

首版 UI Patch 只面向 Web 的本地 HTML/CSS 与可映射源码的开发页面：

- `POST /v1/sessions/{sessionId}/ui-patch/previews`
- `GET|DELETE /v1/sessions/{sessionId}/ui-patch/previews/{patchSessionId}`
- `POST /v1/sessions/{sessionId}/ui-patch/previews/{patchSessionId}/selections`
- `POST /v1/sessions/{sessionId}/ui-patch/previews/{patchSessionId}/commits`
- `POST /v1/sessions/{sessionId}/ui-patch/transactions/{transactionId}/verification`
- `POST /v1/sessions/{sessionId}/ui-patch/transactions/{transactionId}/undo`

一次 commit 必须能映射源码、产生 diff 并进入验证；不能只改浏览器内联样式后声称已经写回项目。

## 6. 配置、模型与插件

### 6.1 Config Registry

- `GET /v1/config-registry`
- `GET /v1/config-registry/{domain}`
- `POST /v1/config-registry/{domain}`

Registry domain 使用 kebab-case API 名。页面不应直接修改 `~/.v8-agent-os/config.json`。

### 6.2 模型

主要 Engine 路由族：

- `/v1/models/public`
- `/v1/models/catalog`
- `/v1/models/providers/{providerId}`
- `/v1/models/bindings`
- `/v1/models/control-plane`
- `/v1/models/role-doctor`
- `/v1/models/test-connection`
- `/v1/model-cache/*`

模型显示名不是路由真相。调用需要保留 provider endpoint、API channel、model ID 和 capability；供应商原生 system/tool/reasoning 合同优先，provider-hosted tools 仍受当前工具面约束。

### 6.3 Plugin Manager

客户端使用 Admin 的 `/api/plugins/*`。Engine 内部对应路由当前位于 `/v1/api/plugins/*`，包括 catalog、installed、readiness、configuration requirements、OAuth、install jobs、Doctor、uninstall 和 grants。

关键授权规则：

1. `@插件` 是强提示，不是唯一入口。
2. Supervisor 的轻量目录提示只包含已安装插件的能力与状态，不加载 Skill 正文、MCP schema 或 CLI action。
3. `plugin_broker` 只能为当前 run 创建已安装、已配置且健康组件的最小 task grant；安装、补配置和读取 secret 不属于该工具。
4. `plugin_cli` 不在默认工具面，只有有效 grant 投影出受审 profile 后才动态加入。
5. 直接子 Agent 只能获得父级明确组件子集；它可向一层孙 Agent 继续传递更小子集，孙 Agent 不能再传播。
6. 每次执行前重新校验 owner、session/run、delegation identity、manifest digest、组件和健康状态。
7. 上机发现只读：可识别已安装 CLI/官方 Skill，但不接管或修改普通 Extensions MCP 配置；冲突必须显式显示。
8. CLI 只接受 manifest 定义的 `actionId + typed parameters`，不接受任意 shell argv。

凭据只通过 opaque `secretRef` 投影；明文不得进入 API、数据库普通字段、日志或 Agent Surface。

## 7. Checkpoint 与存储治理

### 7.1 Checkpoint Governance

- `POST /v1/checkpoint-governance/plan`
- `GET /v1/checkpoint-governance/operations/{operationId}`
- `POST /v1/checkpoint-governance/operations/{operationId}/execute`

plan/fork/replay 需要治理审批。跨用户、跨权限 patch、源状态漂移和插件 grant 继承会被拒绝或失效。checkpoint 使用 strict msgpack 与加密存储，不接受 pickle 或宽泛反序列化兼容。

### 7.2 Storage Retention

- `GET /v1/storage-retention/stats`
- `POST /v1/storage-retention/dry-run`
- `POST /v1/storage-retention/prune`
- `POST /v1/storage-retention/compact`
- `POST /v1/storage-retention/registry/refresh`
- `POST /v1/storage-retention/config`

有副作用的清理应先 dry-run。自动压力处理只针对可丢弃存储类；用户可见转录、未接受 worktree 与恢复证据不能被普通 LRU 当缓存删除。

## 8. 错误与可见面纪律

- Human Surface：状态、结果、阻塞、风险、下一步和可打开产物。
- Agent Surface：紧凑 Markdown 与必要 evidence/detail reference。
- Runtime Surface：完整结构化 ledger、trace、rawRef、metrics 与恢复元数据。

API 出错时保留稳定错误码和可行动摘要；不要把栈、SQL、provider raw JSON 或内部 `run_*` ID 直接扔给普通用户。内部 ID 可在诊断面保留并配合人类可读标签。

## 9. 验证入口

- Engine OpenAPI：启动后查看 `/docs` 或 `/openapi.json`。
- 共享契约：`packages/session-realtime` 的构建与测试。
- Engine 测试地图：[apps/v8-agent-os-engine/tests/README.md](../apps/v8-agent-os-engine/tests/README.md)。
- 桌面真实烟测：`.\v8os.cmd preview --rebuild`。

修改 API 时至少同步检查 Engine 源头、Admin 代理、共享契约、Web 与 Phone 消费方；只让其中一个页面“能跑”不算契约闭环。
