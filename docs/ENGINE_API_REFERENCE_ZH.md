# V8 Agent OS - Engine API 参考指南

本指南梳理了 Engine（默认端口 `9530`）对外（Web、Admin 以及网络插件层）提供的核心通讯和调用方式。由于 V8 Agent OS 架构需要应对长执行周期的任务，底层大量依赖事件流、流式拉取（SSE）和 Websocket 连接。

*(注：系统功能高度迭代中，此文档用作快速参考和机制引导。遇到具体的参数结构或路径冲突，请务必以 `apps/v8-agent-os-engine/api/` 目录下的实际实现源码为唯一准则)*

## 0. 当前 API 真相与边界

当前系统已经进入 `Govern` 收口阶段，不能再按“页面自己猜 detail / summary / status”的旧思路理解接口。

固定原则：

1. `engine` 是实时态与历史态的唯一 authoritative producer
2. `admin` 是唯一远端 broker，负责把 engine 数据规范化后提供给 `web/phone`
3. `web/phone` 默认远端，不允许假设与 `engine` 同机，也不允许假设与 `admin` 同源
4. `snapshot` 是当前会话唯一 authoritative realtime state
5. history 统一走 **事件账本 + 物化汇总**

### 0.1 当前会话实时 contract

当前会话 realtime 只允许存在四类 broker 事件：

1. `snapshot`
   - 当前会话唯一 authoritative source
2. `runtime`
   - 标准化的 side-channel
3. `heartbeat`
4. `error`

`snapshot` 至少稳定包含：

1. `session`
2. `currentRun`
3. `latestSeq`
4. `messages`
5. `runtimeTimeline`
6. `approvals`
7. `controls`
8. `recoverable`
9. `artifacts`
10. `todos`
11. `workflowProjection`
12. `summary`
13. `processes`
14. `contextReferences`
15. `contextGovernance`

### 0.2 当前历史记录 contract

历史记录层固定为两层：

1. **History Ledger**
   - 标准化事件账本
2. **History Materialized Summary**
   - 会话列表和历史详情的直接消费视图

物化汇总至少包含：

1. `sessionId`
2. `sourceGroup`
3. `runtimeOwner`
4. `startedAt`
5. `lastActivityAt`
6. `endedAt`
7. `status`
8. `currentRunId / lastRunId`
9. `title`
10. `previewExcerpt`
11. `lastNarrativeExcerpt`
12. `lastRuntimeSummary`
13. `pendingApprovalCount`
14. `recoverable`
15. `scopeTags`
16. `workflowSummary`

账本条目至少包含：

1. `eventId`
2. `seq`
3. `sessionId`
4. `runId`
5. `ts`
6. `runtimeFamily`
7. `eventName`
8. `scope`
9. `visibility`
10. `targets`
11. `messageRef / toolCallId / processRef / resourceRef`
12. `payload`

### 0.3 当前排除项

以下内容不进入当前会话 realtime CDC：

1. `memory`
   - 底层保障 runtime
2. `desktop_live`
   - 手工驱动通讯
3. `plugin_host_channel / channels`
   - 归历史层

## 1. 核心通讯协议划分

### 1.1 HTTP / REST API
主要用于配置的拉取与写入、短效状态获取、运行时实体操作等：
- **`/api/config/*`**: 
  受 `config_registry_routes.py` 驱动。控制整个系统的主干配置平面，涵盖如 `supervisor`、`mcp`、`models` 等各个顶级域（Domain）。**请杜绝在本地独立操作并修改零散 json 的行径**，而是统一由这组接口向 Engine 发起 Registry 请求并落地到 `storage.py`。
- **`/api/runtime/*` / `/api/workflow/*`**: 
  运行时管理接口，如列出当前正在进行的图节点（Graph Node）、检索运行时技能集（Skills Inventory）、下发重试（Retry）与终止操作等。
- **`/api/governance/approve` (或相关恢复态接口)**: 
  将带有 `approval_required` 中止状态（Paused）的任务重新激活（Resume/Retry）释放权限。

### 1.2 SSE (Server-Sent Events)
用于无需客户端频繁且密集强交互，但需要低延迟主动推送机制的场景：
- **`/api/stream/events`**: 
  承载了大部分的大模型文本流推送（Streaming Text Generation）以及动作运行态细颗粒度流推送（Agent execution Progress/Run Records）。

### 1.3 WebSocket
用于全双工实控、高频数据穿透与底层外接设备长连接传输：
- **`/ws/terminal` / `/ws/desktop-live`** (或其他相关WS通道): 
  可以将后端的底层终端进程 IO 或者系统桌面操作流穿透传递至界面与客户端。
  *【开发警示】*：当 Web 端想要调用此类常驻管道时，必须遵循网络层级，通常需要先通过 Admin 端的 Proxy 机制转发连接请求到 Engine（9530），不要试图用 Web 端越级直连。

## 2. 关键运行态机制解析

### 2.1 任务审批接管 (Operations & Runtime Governance)
对于需要拦截的任务（即破坏性系统指令、重要敏感操作、或超出沙箱预期的外部请求），Engine 将不会立刻执行而是阻塞。
- 后端挂起任务节点（图状层暂停）
- 通过流事件或查询接口返回类似 `status: paused`, `reason: approval_required` 数据
- 客户端接收到指示并弹出对应的 ApprovalCard，将决策权归还给人
- 人确定后调用相关 Resume 接口使后端接续执行环境，这体现了不可或缺的**可恢复性设计**。

### 2.2 OpenClaw 生态挂接层 (Plugin Host / Network Supervisor)
为了能接住 OpenClaw 等广域网/开放协议插件群和多渠道协同，Engine 将承担安全的主从隔离与握手。
- **鉴权握手接口**：接收 `v8-bridge` 的外部联络，匹配工具校验许可、验证 Gateway/Channel 令牌与 Handoff Token。
- **Fail-closed 原则**：所有没有在内部 inventory 登记白名单或者校验失败的越界行为会直接干脆地被拒绝以防数据污损。

## 3. 请求和传递风格说明
在编写对 API 的请求体或改动时：
- 对事件保持清晰的分界：不要试图把所有的 Side effect 都打包在同一个粗颗粒度的无状态接口里提交。
- Artifacts 与 Workflow Ledgers 的变更需严格依附于系统本身的消息事件与 Action Executor 生命周期同步。
