# V8OS ACP 标准接入仿真测试矩阵

更新时间：2026-07-03

## 官方事实边界

本轮只采用官方或一手来源作为依据：

- ACP 官方介绍：[Agent Client Protocol Introduction](https://agentclientprotocol.com/get-started/introduction)
- ACP 官方协议概览：[Protocol Overview](https://agentclientprotocol.com/protocol/v1/overview)
- ACP 官方传输页：[Transports](https://agentclientprotocol.com/protocol/v1/transports)
- ACP 官方 Schema：[Protocol Schema](https://agentclientprotocol.com/protocol/v1/schema)
- ACP 官方客户端页：[Clients](https://agentclientprotocol.com/get-started/clients)
- ACP 官方 Agent 清单：[Agents](https://agentclientprotocol.com/get-started/agents)
- ACP 官方仓库：[zed-industries/agent-client-protocol](https://github.com/zed-industries/agent-client-protocol)
- Zed 官方 ACP 页面：[Zed Agent Client Protocol](https://zed.dev/acp)
- Zed 官方文章：[Bring Your Own Agent to Zed](https://zed.dev/blog/bring-your-own-agent-to-zed)
- JetBrains 官方 ACP 页面：[JetBrains Agent Client Protocol](https://www.jetbrains.com/acp/)
- JetBrains IDE 帮助页：[Agent Client Protocol in JetBrains AI Assistant](https://www.jetbrains.com/help/ai-assistant/acp.html)

结论：

1. ACP 是第三方编辑器 / Agent Client 和 Agent 之间的外部标准入口，不是 V8OS 内部运行协议。
2. V8OS 内部仍保持 Supervisor、runtime episode、Phone/Web 时间线、Spec、Memory、Artifact、Approval 体系。
3. V8OS ACP bridge 的验收重点是 stdio JSON-RPC framing、session 生命周期、workspace 边界、permission / ask_user / Spec approval 分离、客户端可读输出面和取消/终端映射。
4. 未能用官方页面确认的“已支持 ACP”平台不进入本轮支持方验收清单，只列为后续人工核验项。

传输细节需要分开表述：

- ACP v1 官方 stdio 传输是 newline-delimited JSON-RPC。
- V8OS 同时保留 `Content-Length` framing 作为兼容扩展，用于兼容 LSP 风格或历史 smoke 客户端；它不是 V8OS 内部协议，也不应在用户可见页面当作 ACP 官方必需项宣传。

## 支持方核验清单

| 平台 / 来源 | 本轮状态 | 进入矩阵 | 说明 |
| --- | --- | --- | --- |
| ACP 官方协议站点与仓库 | 已核验 | 是 | 作为协议行为、Schema 和术语来源 |
| Zed / Zed Industries | 已核验官方 ACP 页面和 Zed 文章 | 是 | 用于定义“第三方编辑器连接 V8OS”的产品边界 |
| JetBrains | 已核验官方 ACP 页面和 IDE 帮助页 | 是 | 用于确认 ACP 不只是单一编辑器私有协议 |
| ACP 官方 Agents 清单中的 Agent | 已核验官方清单存在 | 部分 | 作为后续真实客户端/Agent 适配候选，不等于 V8OS 已端到端兼容每个 Agent |
| GitHub issue、社区文章、Marketplace、Reddit 中声称支持 ACP 的项目 | 非官方或非一手实现声明 | 否 | 不写入通过项，后续单独核验 |

## 不影响主链路的测试策略

本轮新增测试只使用 `MatrixBackend` / fake backend：

- 不访问 Admin BFF。
- 不访问 Engine DB。
- 不创建真实 Phone/Web/CyberCore 会话。
- 不消费模型额度。
- 不触发 runtime episode。
- 不修改 workspace 文件。

因此它只能证明 ACP bridge 的协议适配和投影边界，不代表真实 Zed / JetBrains 客户端已经端到端联通。

## 仿真矩阵

| 用例 | 维度 | 风险 | 验收方式 |
| --- | --- | --- | --- |
| `transport.content_length.multi_frame` | transport | 部分编辑器桥接会使用 LSP 风格 framed stdio；解析错误会让兼容客户端卡死 | 连续两个 `Content-Length` 请求能返回两个 framed 响应 |
| `transport.content_length.parse_error` | transport | malformed framed input 不应崩溃或泄漏栈 | invalid JSON 返回 framed JSON-RPC parse error |
| `session.lifecycle.external_scope` | session | ACP session 不应污染普通 V8OS 会话真相 | `session/new -> prompt -> cancel` 全程带 `acp_bridge` 外部标记 |
| `workspace.absolute_boundary` | workspace | 第三方客户端不能绕过 workspace trust | 相对路径在 backend 前被拒绝 |
| `permission.separation` | permission | 安全授权、用户补充问题、Spec 审批不能混成一种事件 | 只有 safety/file/command permission 映射成 ACP permission |
| `surface.raw_suppression` | surface | 外部客户端不应看到 provider raw JSON、ledger、fingerprint | summary 为 raw JSON 时退回紧凑 topic，并保留 `detailRef` |
| `terminal.escape_sequences` | terminal | Ctrl+C、Esc、方向键如果被清洗，终端不可用 | 控制字符和 ANSI escape sequence 原样传给 terminal broker |
| `errors.unknown_method` | error | 未支持方法不应产生副作用 | 返回 `-32601`，backend 无 create/prompt/cancel |

## 当前已覆盖文件

- `apps/v8-agent-os-engine/tests/acp_bridge/test_acp_bridge.py`
- `apps/v8-agent-os-engine/tests/acp_bridge/test_acp_simulation_matrix.py`

运行：

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\acp_bridge -q
```

## 仍需真实客户端验收的部分

1. `v8os acp` 是否在安装包 / PATH / token 注入上形成普通用户可用的一键链路。
2. Zed / JetBrains 等真实 ACP 客户端启动、握手、prompt、cancel 的端到端行为。
3. 官方 ACP Schema 后续变更导致的方法名、ContentBlock、permission 结构漂移。
4. 长会话 streaming 性能与 backpressure。
5. 文件 diff、artifact preview、diagnostic projection 在真实编辑器 UI 中的可用性。

这些不应由当前 fake backend 测试冒充通过。
