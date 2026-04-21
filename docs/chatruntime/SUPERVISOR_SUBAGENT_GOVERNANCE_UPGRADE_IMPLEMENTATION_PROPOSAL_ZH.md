# Supervisor / Subagent 统御治理升级实施方案书

更新时间：2026-04-21  
适用仓：`E:\Projects\v8chat\v8-agent-os`  
文档性质：实施方案书 / 深技术蓝图 / 已进入 MVP+ 硬化阶段  
主题范围：`supervisor`、`subagent`、`planner lane`、`delegation broker`、`external CLI worker`

## 1. 执行摘要与决策表

### 1.1 这份方案要解决什么

当前 `supervisor / subagent` 主链已经从方案进入 MVP+ 硬化阶段，治理主链已基本收成 `planner_lane + delegation_broker + subagent_swarm`：

- `supervisor` 仍是重提示词、重 direct tool surface、重 runtime registry 的统御节点
- `subagent` 已经有 `explicit / contextual_auto` 两种工具模式，并已引入独立 `capabilitySnapshot`
- `planner_lane` 已经承接结构化 planner pass、quality gate、repair/fallback 与 auto-dispatch diagnostics
- `delegation_broker` 已经成为 supervisor delegation 主链；历史 `delegate_parallel / handoff_to_* / create_agent` 不再作为 supervisor 可见主入口
- `roleLabel` 已明确保留为展示身份，能力路由使用 `capabilitySnapshot`

本方案的目标不是直接扩“并发数”，而是先把统御治理主链收成：

1. `Supervisor Orchestrator` 负责总控和验收
2. `Planner Lane` 负责任务切片、写集隔离、行为域隔离和分工，并切到更窄噪音的 planner prompt / tool surface
3. `Delegation Broker` 负责统一 delegation 主入口
4. `Subagent Lane` 接收结构化 `taskBrief`
5. `Subagent Swarm Runtime Card` 负责承接子代理蜂群中间过程与局部自检
6. 第二阶段再接入 `External CLI Worker Broker`

### 1.2 已锁定决策

| 议题 | 当前真相 | 已锁定决策 | 阶段 |
| --- | --- | --- | --- |
| Planner 优先级 | 已有 `planner_lane` runtime 与结构化 planner pass | 继续硬化 planner quality gate 与 surface inspector | MVP+ |
| Planner 形态 | `Planner Lane` 已嵌入 supervisor 内部 | 不单独拆 planner agent；planner 不直接执行 side effect | MVP+ |
| Planner 触发 | 已支持 `auto / force / off` 与 `taskPlanningMode=true` 兼容映射 | 继续保持显式 override 与安全门 | MVP+ |
| Capability Snapshot 来源 | 已引入独立 `capabilitySnapshot` | 继续采用 `配置 + 派生混合` | MVP+ |
| Capability Snapshot 首版维护面 | Admin 已有 JSON 高级编辑面 | 继续避免复用 `roleLabel` 做能力真相 | MVP+ |
| Delegation 主链 | `delegation_broker` 已是主入口 | 删除旧 supervisor delegation 工具构造与文案残留 | MVP+ |
| 兼容策略 | 历史入口已退出 supervisor 可见主链 | 不再为已清理历史工具调用保留 runtime 兼容壳 | MVP+ |
| 外部 CLI Agent | 当前只有命令会话与 hooks 基础能力 | 纳入第二阶段主线，形态为 `Broker 包装 Worker` | Phase 2 |
| Swarm 并发上限 | `delegation_broker` 承接并发调度；旧 `delegate_parallel` 上限不再代表主链 | 架构不设固定硬上限，治理建议默认 `<=10` 并发 | MVP+ |
| Swarm 展示投影 | subagent 事件仍可能经 message lifecycle 影响同一 assistant 气泡 | 新增独立 `subagent_swarm` runtime lane，subagent 中间输出不混入 supervisor 气泡 | Phase 1 |
| Swarm 状态卡结构 | runtime timeline 当前更偏普通时间线 | 按 `taskBrief / planner task node` 分组，可展开 compact transcript + trace ref | Phase 1 |
| Supervisor 气泡边界 | 中间运行进展与最终答复容易混在同一消息流 | 允许动画 milestone summary，但不承载 subagent 原文；最终采纳与验收仍归 supervisor | Phase 1 |
| 治理/诊断状态卡 | `supervisor.graph.*` 当前归入 chat runtime card | 复用 `context_governance` 卡承接治理、graph timing、planner diagnostics | Phase 1 |
| Runtime timeline 上限 | os-phone/os-web 当前存在 `slice(0, 24)` 展示硬截断 | 解除 24 条语义上限，改用虚拟化完整列表 | Phase 1 |

### 1.3 当前真相与目标状态

#### 当前已实现

- `subagent` 支持 `explicit / contextual_auto`，其中 `contextual_auto` 已按 delegated task route 收窄外部工具面
- `capabilitySnapshot` 已与 `roleLabel` 分层
- `delegation_broker` 已支持本地 subagent 与 external worker descriptor
- `planner_lane` 已成为独立 runtime id，并承接结构化 planner projection
- `subagent_swarm` 已成为独立 runtime id，并承接子代理/外部 worker 中间态
- phone/web runtime panel 已消费 `planner_lane / subagent_swarm`，并按 plan/task 分组 compact feed
- message lifecycle 已显式阻止 `planner_lane / subagent_swarm` 进入 supervisor 气泡

#### 建议方案

- 把“plan before execute”从松散提示偏置升级成 supervisor 内部专用 lane
- 把 agent 元数据拆成 `presentationIdentity` 与 `capabilitySnapshot`
- 把 delegation 入口收成 broker，而不是继续靠多个历史工具并存
- 把 subagent 的外部工具暴露统一收口到 `delegated-task-driven contextual route`
- 把 subagent 蜂群中间过程从 supervisor 气泡中移出，投影到独立 `subagent_swarm` runtime card
- 把治理、上下文压缩和 graph timing 从 ChatRuntime 执行卡中迁到 `context_governance` 卡

#### 后续演进

- 继续硬化 planner quality gate、auto-dispatch safety gate 与 delegation diagnostics
- 继续增强 `planner_lane / subagent_swarm` 的 UI 可读性；当前是 compact feed，不是完整 kanban
- 清理旧 delegation 工具残留，避免兼容壳回潮为主链真相

## 2. 当前实现真相审计

### 2.1 Supervisor 当前仍是重统御节点

#### 当前已实现

- `supervisor` system content 仍会注入：
  - runtime 能力卡片
  - direct tool registry
  - specialist agent registry
  - memory / workspace / todos / safety 等规则
- `graph/supervisor_context.py` 当前教学 `delegation_broker`：
  - 复杂专业任务通过 `delegation_broker` 委派
  - planner task briefs 是本地 subagent 与 external worker 的 canonical delegation contract

这说明当前 `supervisor` 不是一个极窄的纯编排器，而是：

- 总控调度器
- 直接执行入口集合
- runtime registry 注入中心

事实源：

- `apps/v8-agent-os-engine/graph/supervisor_context.py`

### 2.2 Subagent 当前已有两种工具模式，但治理语义仍不完整

#### 当前已实现

- `core/agents.py` 目前持久化的 agent 元数据主要包括：
  - `name`
  - `description`
  - `avatar`
  - `icon`
  - `roleLabel`
  - `model`
  - `tools`
  - `tool_mode`
  - `reflection_enabled`
  - `max_reflections`
- `graph/agent_factories.py` 中 `_resolved_tool_mode(...)` 已明确两种模式：
  - `explicit`
  - `contextual_auto`

#### 当前已实现但仍是半成品的地方

- `contextual_auto` 并不是全量裸暴露
- 它会在 subagent node 内重新执行：
  - `_extract_delegated_query(...)`
  - `extensions_runtime_service.build_contextual_route(...)`

这意味着 subagent 其实已经具备“按委派任务再做 route”的主链雏形。

#### 风险

- 当前没有独立的 agent capability truth
- `explicit` 模式仍保留较强的人工选择器历史路径
- `contextual_auto` 是 route 雏形，但尚未与 planner / capability snapshot / delegation contract 收成一套完整治理面

事实源：

- `apps/v8-agent-os-engine/core/agents.py`
- `apps/v8-agent-os-engine/graph/agent_factories.py`

### 2.3 Specialist agent registry 仍偏薄

#### 当前已实现

`supervisor_context.py` 当前向 supervisor 注入的 specialist agent 信息仍偏薄，主要只有：

- `id`
- `name`
- `description`
- `tools count`

#### 风险

这对于未来做以下动作明显不够：

- planner 选型
- 事件驱动暴露
- agent 候选筛选
- 工具面压缩
- 并发编排与写集隔离

换句话说，当前 supervisor 看见的 subagent 更像“薄名片”，而不是“可治理能力单元”。

### 2.4 `task_planning_mode` 当前只是 todos 偏置，不是 planner lane

#### 当前已实现

- `runtimes/chat/runtime.py` 已存在：
  - `chat.task_planning_mode.enabled`
  - `chat.task_planning_mode.decided`
- `core/native_tools.py` 已存在：
  - `write_todos`
  - `update_todo`

#### 当前真相

这套能力目前更像：

- 是否鼓励进入 todos 工作法
- 是否把任务拆成可见步骤

而不是：

- 专用 planner prompt
- 专用 planner diagnostics
- 专用 task graph / write-set / behavior-scope contract

#### 风险

如果继续把 planner 问题理解成“多写 todo”，会错过真正的主问题：

- 任务切片质量
- 写集隔离
- 行为域隔离
- agent 分工边界
- 汇聚与验收契约

事实源：

- `apps/v8-agent-os-engine/runtimes/chat/runtime.py`
- `apps/v8-agent-os-engine/core/native_tools.py`

### 2.5 旧 `delegate_parallel` 上限 2，是历史工具合同，不是架构结论

#### 历史状态

旧 `delegate_parallel` 工具曾把并发委派限定为：

- `min_length=1`
- `max_length=2`

运行时也会显式报错：

- `delegate_parallel supports at most 2 concurrent subtasks`

#### 当前真相

这只代表旧工具合同，不能代表当前架构主链。当前并发委派主链已经迁到 `delegation_broker`：

- `delegation_broker` 接收 planner task briefs
- 本地 subagent 分支仍复用 `parallel_delegate_task / parallel_delegate_join` 执行节点
- 架构不设固定并发硬上限，治理建议默认控制在 `<=10`

旧 `delegate_parallel` 不再作为 supervisor 可见工具或 prompt 教学真相。

#### 风险

如果把 swarm 问题误收缩成“把 2 改成 6 或 10”，会掩盖更根本的问题：

- planner 缺位
- 写集重叠
- 行为域冲突
- 汇聚与验收不足
- 失败恢复与中断治理不足

事实源：

- `apps/v8-agent-os-engine/graph/parallel_support.py`

### 2.6 `roleLabel` 当前属于展示身份，不是能力分类真相

#### 当前已实现

- `core/agents.py` 把 `roleLabel` 作为 agent 配置字段持久化
- `chat runtime` 会把 `roleLabel` 放入 agent start / message metadata
- `os-phone` 的消息气泡会消费 `agentRoleLabel`
- `admin` 的 subagent 配置页也把 `roleLabel` 当作前端可编辑字段

#### 当前真相

`roleLabel` 当前本质上属于：

- 前端消息气泡展示身份
- UI 可读角色称谓
- 会话中 agent 的可视化身份标签

它不是：

- 稳定能力分类
- route 真相
- planner 选型标签
- 工具暴露真相

#### 风险

如果直接把 `roleLabel` 升级成能力字段，会立刻造成：

1. 展示语义与治理语义缠绕
2. 前端改气泡文案会误伤 runtime 治理
3. 自由文本字段被误当成稳定枚举

事实源：

- `apps/v8-agent-os-engine/core/agents.py`
- `apps/v8-agent-os-engine/runtimes/chat/runtime.py`
- `apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx`
- `apps/v8-agent-os-admin/src/app/admin/(dashboard)/subagents/page.tsx`

## 3. 核心问题分层

### 3.1 Planner 缺位是当前第一问题

当前最大缺口不是并发槽位，而是：

- 没有稳定的 planner lane
- 没有 task graph contract
- 没有 write-set / behavior-scope isolation contract
- 没有把“拆分质量”从 supervisor 自由发挥中抽出来

### 3.2 Delegation 主入口分裂

当前与 delegation 相关的主链能力分散在：

- `handoff_to_*`
- `delegate_parallel`
- `create_agent`

这三者分别承担：

- 单 agent 委派
- 小规模并行委派
- 临时补建 agent

但它们没有共享统一的：

- task brief contract
- acceptance contract
- aggregation contract
- migration story

### 3.3 身份字段与能力字段缠绕

当前只有 `roleLabel` 这类展示字段，没有独立 `capability snapshot`，导致未来要做：

- subagent 候选筛选
- 事件驱动暴露
- 默认工具面压缩
- planner 选型

都会缺少稳定的能力真相层。

### 3.4 Subagent 外部工具暴露语义仍不完整

虽然 `contextual_auto` 已经具备 delegated query route 雏形，但仍有断层：

- 没有显式 task brief contract
- 没有 capability snapshot 参与 route
- 没有统一 delegation broker 负责工具暴露主链
- 仍存在历史 `explicit` / 手工选择器路径

### 3.5 Swarm 问题被误收缩成“并发数问题”

真实瓶颈通常不在“2 不够”本身，而在：

- planner 产出的任务边界不清
- 多 agent 写集交叉
- 多 runtime 行为相互干扰
- 汇聚与验收没有结构化 contract

因此正确顺序不是：

- 先把并发数拉高

而是：

1. 先补 planner lane
2. 再补 capability snapshot
3. 再统一 delegation broker
4. 最后再安全扩 swarm 并发

## 4. 目标架构蓝图

### 4.1 总体蓝图

建议将 `supervisor / subagent` 主链升级为下面这套分层：

1. `Supervisor Orchestrator`
   - 保持重统御
   - 负责总目标、风险控制、最终验收、运行时治理

2. `Planner Lane`
   - 嵌入 supervisor 内部
   - 只负责规划，不负责重执行

3. `Delegation Broker`
   - 统一所有委派入口
   - 接受单任务或任务数组
   - 负责编排、分配、聚合与兼容转发

4. `Executor / Subagent Lane`
   - 只吃 `taskBrief`
   - 走 delegated-task-driven contextual route
   - 不再直接吃用户原始请求

5. `Runtime Planes`
   - `chat / extensions / computer_use / rpa / plugin_host / memory / automation`
   - 继续由运行时平面承接真实执行

6. `External CLI Worker Broker`
   - 第二阶段接入
   - 基于 `command_session_broker + hooks`
   - 不先单独新起 runtime

### 4.2 真相分层

方案中的三层 truth 必须显式区分：

1. 用户原始请求
   - `supervisor route truth`

2. `delegatedTask / taskBrief`
   - `subagent route truth`

3. runtime / event context
   - `gate truth`

不能再把这三者混成一段 prompt 文本自由继承。

### 4.3 治理原则

目标架构必须遵守下面几条治理纪律：

1. Planner 先于并发扩张
2. 结构化 task brief 先于自由文字委派
3. `presentation identity` 与 `capability snapshot` 分层
4. subagent 工具暴露继续复用 extensions 稳定预筛主链
5. 兼容入口可保留，但必须薄壳转发到新主链
6. swarm 不设架构级固定上限，但默认治理建议 `<=10`

## 5. 第一阶段：Planner Lane 设计

### 5.1 目标

第一阶段优先把 `task_planning_mode` 升级为 supervisor 内置专用 `Planner Lane`。

目标不是“让 supervisor 更爱写 todo”，而是把下列职责显式拉成一个稳定 lane：

- 任务切片
- 写集隔离
- 行为域隔离
- 子代理选择
- 并发编排
- 验收契约生成
- planner 专用 prompt / 更窄噪音工具面

### 5.2 触发规则

#### 建议方案

引入：

- `plannerMode: auto | force | off`

语义如下：

- `auto`
  - 对 `plan / task / todos / 拆解 / 分工 / 大任务执行 / 多阶段实施` 这类意图自动切换
- `force`
  - 即使用户表述不明显，也强制先走 planner lane
- `off`
  - 明确跳过 planner lane，直接由 supervisor 正常执行

#### 触发来源

- 意图自动判断
- 显式用户覆盖
- 后续也可接入更细粒度 runtime policy，但第一版不依赖复杂策略中心

### 5.3 Planner Lane 的职责边界

#### Planner 负责

- 识别任务是否需要拆分
- 产出 `taskGraph / todos`
- 给每个任务分配：
  - `writeSet`
  - `behaviorScope`
  - `requiredCapabilities`
  - `acceptanceContract`
  - `parallelGroup / dependency`

#### Planner 不负责

- 大量真实工具调用
- 大量写文件
- 直接执行 computer/rpa/desktop 主链
- 代替 subagent 做细节工作

如果 planner 自己变成重执行者，lane 就会重新坍塌回“普通 supervisor 多写一点字”。

#### Planner lane 的提示词与工具面建议

建议 planner lane 在进入时同步切换到：

- 更专注拆分质量的 planner system prompt
- 更小的 direct tool surface
- 更明确的 write-set / behavior-scope / acceptance 约束语言

不建议继续让 planner lane 背完整 supervisor 噪音工具面，否则“切 lane”只会停留在语义层，不会形成真正的编排质量提升。

### 5.4 Planner 输出契约

建议 planner 输出至少包含：

- `taskGraph`
- `todos`
- `taskBriefs[]`
- `writeSet`
- `behaviorScope`
- `requiredCapabilities`
- `acceptanceContract`
- `parallelGroups`
- `dependencyEdges`

推荐最小语义：

- `writeSet`
  - 哪些文件/目录/资源允许修改
- `behaviorScope`
  - 哪类行为允许发生，例如：
    - 只读调研
    - 写代码
    - 文档修改
    - runtime 操作
    - 外部网络搜索
- `acceptanceContract`
  - 预期交付与验收标准

### 5.5 Planner diagnostics

建议同时引入：

- `plannerIntentDiagnostics`

至少记录：

- 为什么切入 planner lane
- 当前切分策略
- 是否判定存在并行价值
- 是否检测到写集重叠风险
- 是否检测到行为域冲突

这既是可观测性，也是后续排障抓手。

### 5.6 与当前 `task_planning_mode` 的关系

#### 当前已实现

`task_planning_mode` 已存在事件和 UI 感知能力。

#### 建议方案

第一阶段不推翻现有命名，而是扩语义：

- 保留现有 `task_planning_mode` 事件面
- 让它从“todos 偏置”提升为“是否进入 planner lane 的控制面”

这样可以最大化复用：

- 当前 chat runtime 事件链
- phone / web / admin 已存在的任务模式感知

## 6. 第一阶段：Subagent Capability Snapshot 设计

### 6.1 目标

把 agent 元数据从“展示身份 + 少量执行配置”的状态，升级为：

- `presentationIdentity`
- `capabilitySnapshot`

两层显式分离。

### 6.2 `presentationIdentity`

#### 当前已实现

这一层实际上已经存在，只是字段分散在现有 agent metadata 里。

建议语义固定为：

- `name`
- `avatar`
- `icon`
- `roleLabel`

职责只包含：

- 前端消息气泡展示
- 头像语义
- UI 可读身份

不得承担：

- agent 分类真相
- planner 选型
- route 暴露
- swarm 治理

### 6.3 `capabilitySnapshot`

建议新增独立真相层，至少包含下面这些字段组：

- `agentClass`
- `domainTags`
- `artifactCapabilities`
- `operationCapabilities`
- `runtimeAffinities`
- `toolExposurePolicy`
- `plannerSuitability`
- `externalWorkerSuitability`
- `confidence`
- `source`

#### 字段语义建议

- `agentClass`
  - 例如：
    - `coding_executor`
    - `researcher`
    - `reviewer`
    - `writer`
    - `ops_or_runtime`
    - `generalist`
- `domainTags`
  - 例如：
    - `frontend`
    - `python`
    - `docs`
    - `growth`
    - `product_strategy`
- `artifactCapabilities`
  - 例如：
    - `code`
    - `doc`
    - `pptx`
    - `report`
    - `analysis_note`
- `operationCapabilities`
  - 例如：
    - `read`
    - `write`
    - `review`
    - `refactor`
    - `research`
    - `plan`
- `runtimeAffinities`
  - 例如：
    - `chat`
    - `extensions`
    - `computer_use`
    - `rpa`
    - `plugin_host`
- `toolExposurePolicy`
  - 例如：
    - `contextual_only`
    - `explicit_only`
    - `hybrid`
- `plannerSuitability`
  - 例如：
    - 是否适合被 planner 选为：
      - 主执行者
      - 审核者
      - 验证者
      - 汇聚者
- `externalWorkerSuitability`
  - 例如：
    - 是否适合被替换为外部 CLI worker

### 6.4 Snapshot 来源：配置 + 派生混合

已锁定决策：

- 第一版采用 `配置 + 派生混合`

#### Admin 可编辑基础层

建议在 admin 里让人类维护基础真相：

- `agentClass`
- `domainTags`
- `artifactCapabilities`
- `operationCapabilities`
- `runtimeAffinities`
- `toolExposurePolicy`

#### 运行时派生补全

建议由系统从下面这些信号做派生补全：

- `system_prompt`
- 当前工具集
- 实际 runtime 使用轨迹
- 历史执行成功类型
- 反射/验收轨迹
- delegated task 完成历史

#### 为什么要混合

只靠配置会陈旧，只靠派生会漂移。混合模式的意义是：

- 用配置给出稳定基座
- 用派生补足真实运行行为
- 用 `confidence / source` 保留可观测性

### 6.5 第一版为何必须 Admin 可编辑

已锁定决策：

- `capability snapshot` 第一版需要 `Admin 可编辑`

理由有三点：

1. 当前 subagent 生态仍在快速变化，完全自动推导会过度漂移
2. 人工可编辑基础标签可以形成治理真相
3. 这比拿 `roleLabel` 直接硬改能力分类安全得多

## 7. 第一阶段：Delegation 与工具暴露主链

### 7.1 总原则

未来 delegation 主链应统一收成：

- 单一 `Delegation Broker`

它负责：

- 接收单任务或多任务
- 路由到合适 subagent
- 绑定 `taskBrief`
- 管理并发和依赖
- 汇聚结果
- 管理兼容入口转发

### 7.2 Subagent 的 route truth

已锁定决策：

- `supervisor` 的 route truth = 用户原始请求
- `subagent` 的 route truth = `delegatedTask / taskBrief`
- runtime / event context = gate truth

#### 当前已实现的好消息

`agent_factories.py` 当前已经通过 `_extract_delegated_query(...)` 为 subagent 提供 delegated query route 雏形。

#### 建议方案

下一步不是另起一套 route 系统，而是把这条现有雏形升级为正式主链：

- 输入从自由文本升级为结构化 `taskBrief`
- route 继续复用稳定的 `extensions` 预筛机制
- 但 query truth 固定为 `delegatedTask`

### 7.3 Subagent 外部工具暴露策略

建议 future design 明确保持：

- subagent 的外部工具暴露继续复用当前稳定的 `extensions` 预筛主链

不建议回到：

- 全量 MCP / PluginHost / Skills 裸暴露
- 手工指定全部可用工具作为长期主链

推荐语义是：

- `delegated-task-driven contextual route`

这样才能同时兼顾：

- 工具面降噪
- route 稳定性
- 与 supervisor 主链的一致性

### 7.4 `Delegation Broker` 建议语义

建议新增统一 delegation broker，承担未来主入口。推荐语义：

- 支持单任务和任务数组
- 能接受 planner 产出的 `taskBrief[]`
- 能描述依赖关系与并发分组
- 能结构化汇聚结果
- 能把兼容工具转发进来

#### 它应该替代什么

- `handoff_to_*`
  - 已退出 supervisor 可见主链；后续不再保留 runtime 兼容壳
- `delegate_parallel`
  - 已退出 supervisor 可见主链；并行委派由 `delegation_broker` 直接接收任务数组
- `create_agent`
  - 已退出 supervisor 编排入口；agent 创建/编辑保留在 Admin/API 管理面

### 7.5 兼容迁移策略

当前执行状态：

- `delegation_broker` 已成为 supervisor delegation 主入口
- `delegate_parallel / handoff_to_* / create_agent` 不再需要面向 supervisor 的运行兼容壳
- 历史工具调用记录已清理，不再为这些旧 tool call 保留 replay 兼容

清理路线：

1. 主链硬删
   - 删除旧工具构造、传参、bundle 字段和测试桩
   - 保留 `parallel_delegate_task / parallel_delegate_join`，因为它们服务 `delegation_broker` 的本地 subagent branch

2. 文案清毒
   - supervisor prompt / docs / diagnostics 不再把旧入口描述成长期真相
   - Admin 只展示 `delegation_broker` 主链与 `contextual_auto / explicit` 模式边界

3. 回归防线
   - supervisor toolset 和 prompt snapshot 持续断言旧工具不可见
   - CDC 测试持续断言 planner/subagent 事件不污染 supervisor 气泡

### 7.6 第一阶段补充：Swarm Surface Projection 与状态卡治理

这一节补齐 `os-phone / os-web` 对 subagent 蜂群作业的展示治理。它不是视觉细节，而是 runtime contract 的一部分：如果不先写死投影边界，后续实现很容易把 subagent 输出重新混进 supervisor 消息气泡，导致统御权、验收权和排障面再次缠绕。

#### 当前已实现

shared session 目前已经有三道关键投影关口：

- `runtimeTimeline`
  - 位于 shared session contract，负责把运行时事件投影成 runtime panel 可消费的活动。
- `event-taxonomy`
  - 决定事件归属哪个 runtime、是否可见、投向 `message / runtime_card / hud / process` 哪些 surface。
- `message-lifecycle`
  - 决定某个 runtime event 是否会进入 assistant message、生成 narrative node 或影响当前 agent identity。

当前风险点是：

- `agent.started` 当前归入 `chat` runtime，并会影响当前 assistant message 的 agent identity。
- `run.text.delta` 默认投向 `message`。
- `tool.started / tool.finished` 默认同时投向 `message / runtime_card / process`。
- `supervisor.graph.*` 当前归入 `chat` runtime card，容易污染 ChatRuntime 执行卡。
- `os-phone / os-web` runtime panel 当前仍存在 `slice(0, 24)` 类硬截断。

事实源：

- `packages/session-realtime/src/contract.ts`
- `packages/session-realtime/src/event-taxonomy.ts`
- `packages/session-realtime/src/message-lifecycle.ts`
- `apps/v8-agent-os-phone/src/components/chat/RuntimeTimelinePanel.tsx`
- `apps/v8-agent-os-web/src/components/chat/RuntimeTimelinePanel.tsx`

#### 建议方案：新增 `subagent_swarm` runtime lane

第一阶段建议新增独立 runtime lane：

- 推荐名：`subagent_swarm`
- 备选名：`agents`
- 本方案固定推荐：`subagent_swarm`

语义边界：

- `subagent_swarm` 承接 subagent 蜂群中间过程。
- `chat` 承接 supervisor 的统御叙述、用户决策请求、最终汇总和最终验收。
- subagent 的原始文本流、工具事件、局部失败、局部自检默认不得进入 `message` target。
- subagent 的 compact transcript、tool summary、artifact summary、trace ref 默认进入 `runtime_card`。
- 长任务或外部 worker 对应的 process link 仍可进入 `process / hud`，但必须保持同一 canonical task/run 关联。

#### Subagent Swarm Runtime Card

`os-phone / os-web` 都应展示独立的 swarm runtime card，而不是把 subagent 事件塞回 ChatRuntime 卡。

卡内主分组固定为：

- `taskBrief / planner task node`

不建议按纯时间线或纯 subagent 分组作为主结构。原因是 swarm 的治理重点是任务边界、写集隔离、行为域隔离和验收，而不是“谁说了多少话”。

每个 task group 至少展示：

- `taskGoal`
- `assignedSubagent`
- `status`
- `localSelfCheck`
- `compactTranscript`
- `toolSummary`
- `artifactSummary`
- `traceRef`
- `writeSet / behaviorScope hint`

展开策略：

- 默认显示 compact transcript。
- 允许展开看该 task group 的关键转录与工具摘要。
- 完整原始流不进入 session snapshot，只通过 `traceRef / debugRef` 按需读取。

#### Supervisor Bubble Boundary

Supervisor 气泡只能承载统御视角内容：

- plan / task outline
- animated milestone summary
- 用户决策请求
- 最终 synthesis
- supervisor acceptance

允许的 milestone summary 示例：

- “正在协调 6 个子任务”
- “已完成 4 个，2 个等待合并”
- “检测到 1 个写集冲突，已暂停对应子任务”

这些 milestone 可以在 UI 上用动画状态表达，但不得包含 subagent 原文、长工具日志或局部转录。

Subagent 的中间输出统一进入 `subagent_swarm` runtime card：

- subagent 原始文本
- 子任务工具调用摘要
- 子任务局部失败
- 子任务 local self-check
- 子任务产物草稿

最终进入 supervisor 气泡的，只能是 supervisor 采纳后的 curated refs、汇总判断与验收结论。

#### Local self-check 与 Supervisor final acceptance

Subagent 可以声明局部自检完成，但这不是最终验收。

语义固定为：

- `localSelfCheck`
  - subagent 对自己负责的子任务给出的完成/风险/置信声明。
- `supervisorAcceptance`
  - supervisor 对子任务结果是否采纳、是否需要重试、是否进入最终汇总的判断。
- `finalAcceptance`
  - supervisor 对用户总任务的完成判断。

禁止把 subagent 的 `localSelfCheck=passed` 投影成用户任务完成。它只能作为 supervisor 决策输入。

#### Artifact projection

Subagent 产物与证据采用两阶段投影：

1. 运行中
   - artifact draft / evidence / intermediate file 先挂到 `subagent_swarm` runtime card。
   - 每个 artifact ref 必须能回到对应 `taskBriefId` 和 `subagentId`。

2. 汇总后
   - supervisor 最终气泡只引用采纳后的关键产物。
   - 未采纳或仅供排障的产物留在 runtime card，不挤占主消息流。

这样可以同时满足：

- 用户能复查 subagent 做了什么
- supervisor 气泡保持干净
- 最终答案仍有明确证据链

#### 治理/诊断卡分流

本方案不建议新增额外诊断 tab，而是复用现有 `context_governance` 卡，并允许 UI 文案升级为“上下文/治理”。

迁入 `context_governance` 卡的内容包括：

- `context.prepared`
- context governance digest / history
- `supervisor.graph.*`
- graph timing
- planner diagnostics
- runtime governance hints
- 写集冲突、行为域冲突等治理提示

ChatRuntime 卡应回归：

- 当前 chat execution
- supervisor orchestration
- 用户可感知的运行进度

这不是删除诊断信息，而是把诊断从聊天执行卡迁到治理/上下文卡，避免 ChatRuntime 卡成为所有低层事件的垃圾桶。

#### 解除 24 条硬上限

`os-phone / os-web` 的 runtime panel 不应再用固定 `slice(0, 24)` 作为语义上限。

建议实现纪律：

- phone 继续利用 `FlatList` 或等价虚拟列表承载完整 activity list。
- web 使用 virtualization / windowing 或等价滚动容器。
- BroadcastRail / dock badge 可以保留小数量预览。
- 小数量预览不能替代完整 timeline。
- 不允许因为单轮活动超过 24 条就让后续 subagent 事件不可见。

#### 防漂移规则

后续实现必须把下面几条写入 event taxonomy / projection 层，而不是只靠 UI 约定：

- subagent swarm text event 默认不投向 `message`。
- subagent swarm tool event 默认不生成 supervisor message tool node。
- subagent swarm artifact 默认先投向 runtime card，再由 supervisor curated refs 进入最终气泡。
- `supervisor.graph.*` 默认归入 `context_governance`，不再归入 ChatRuntime 执行卡。
- supervisor milestone summary 可以进入气泡，但必须是 supervisor 自己的统御摘要，不得直接转发 subagent 原文。

## 8. 第二阶段：External CLI Worker Broker

### 8.1 为什么纳入第二阶段主线

已锁定决策：

- 外部 CLI agent 列入第二阶段主线
- 形态为 `Broker 包装 Worker`

原因不是“好玩”，而是当前 V8 已经具备很强的前置基础：

- `command_session_broker`
- PTY / session attach / observe / input / terminate
- hooks
- process surface
- `ProcessesHUD`
- runtime events

也就是说，V8 距离“外部 CLI worker 编排层”并不远，真正缺的是：

- worker contract
- 调度 contract
- 汇聚 contract
- 治理 contract

### 8.2 为什么不先单独做 external-agent runtime

建议方案：

- 第二阶段优先沿用 `command_session_broker + hooks`
- 让 broker 包装 worker，而不是先发明一个全新 runtime

这样做的优点：

1. 复用现有命令会话、PTY 与进程面
2. 复用当前 process HUD / tool card / interrupt 纪律
3. 降低新 runtime 边界带来的恢复语义分裂

### 8.3 第二阶段 worker contract

建议在第二阶段定义统一的 external worker contract，至少包括：

- `launch contract`
  - 如何启动 worker
  - 使用哪个 CLI / profile / env
- `session attach / observe contract`
  - 如何附着并继续观察
- `result summary contract`
  - 如何把 worker 结果汇总成结构化结果
- `interrupt / timeout / cleanup contract`
  - 如何超时、中断、清理副作用

### 8.4 Phase 2 的边界

第二阶段要解决的是：

- 外部 worker 如何纳入 V8 编排治理

而不是：

- 把外部 CLI 当作另一个自由聊天面

external worker 必须受下面这些治理约束：

- 允许的 side effects
- 允许运行的命令 profile
- session 生命周期
- 结果结构化
- 汇聚与验收

## 9. 接口与类型建议

本节不是逐条 schema 代码，而是把实现边界提前钉死，避免后续实现者二次拍板。

### 9.1 Agent metadata 分层

```ts
type PresentationIdentity = {
  name: string
  avatar?: string
  icon?: string
  roleLabel?: string
}

type CapabilitySnapshot = {
  agentClass: string
  domainTags: string[]
  artifactCapabilities: string[]
  operationCapabilities: string[]
  runtimeAffinities: string[]
  toolExposurePolicy: "contextual_only" | "explicit_only" | "hybrid"
  plannerSuitability?: {
    canLead?: boolean
    canExecute?: boolean
    canReview?: boolean
    canValidate?: boolean
    canSynthesize?: boolean
  }
  externalWorkerSuitability?: {
    replaceableByCliWorker?: boolean
    preferredWorkerType?: string
  }
  confidence?: number
  source?: "admin" | "derived" | "mixed"
}
```

#### 语义边界

- `roleLabel` 保留，但只归入 `presentationIdentity`
- `capabilitySnapshot` 成为未来治理真相层

### 9.2 Planner 控制面

```ts
type PlannerMode = "auto" | "force" | "off"

type PlannerIntentDiagnostics = {
  plannerMode: PlannerMode
  triggeredBy: string[]
  reason: string
  parallelismSuggested: boolean
  writeSetRisk?: "low" | "medium" | "high"
  behaviorConflictRisk?: "low" | "medium" | "high"
}
```

#### 语义边界

- `auto` 是默认模式
- `force` 用于强制 planner lane
- `off` 用于明确跳过 planner lane

### 9.3 Task brief / task contract

```ts
type TaskBrief = {
  goal: string
  context: string
  writeSet: string[]
  behaviorScope: string[]
  requiredCapabilities: string[]
  acceptanceContract: string[]
  parallelGroup?: string
  dependency?: string[]
}
```

#### 语义边界

- `goal`
  - 该任务真正要达成什么
- `context`
  - subagent 需要的最小上下文
- `writeSet`
  - 允许修改的文件/目录/资源
- `behaviorScope`
  - 允许运行的行为域
- `requiredCapabilities`
  - planner 选 agent / worker 的依据
- `acceptanceContract`
  - 如何判断任务完成

### 9.4 Delegation 主入口

```ts
type DelegationBrokerRequest = {
  tasks: TaskBrief[]
  plannerMode?: "auto" | "force" | "off"
  aggregationMode?: "summary" | "judge" | "synthesis"
  maxConcurrency?: number
}
```

#### 语义边界

- 新主入口建议接受单任务和任务数组
- 未来不再让 `delegate_parallel` 独自承担并发委派真相
- `maxConcurrency` 不是架构上限，只是单次治理参数

### 9.5 External CLI Worker descriptor

```ts
type ExternalWorkerDescriptor = {
  workerType: string
  launchProfile: string
  sessionMode: "interactive" | "batch" | "daemon"
  allowedSideEffects: string[]
  resultSchema: string
}
```

#### 语义边界

- `workerType`
  - 例如：`claude_cli`、`hermes_cli`、`custom_script`
- `launchProfile`
  - 决定如何通过 broker 启动
- `sessionMode`
  - 决定 attach / observe / interrupt 语义
- `allowedSideEffects`
  - 治理与安全约束
- `resultSchema`
  - 规定结果必须如何汇总

### 9.6 Swarm surface projection contract

第一阶段建议补充一组只服务 session projection 的类型语义。它们不要求一次性落成完整代码 schema，但实现时必须保持这些边界。

```ts
type SubagentSwarmTimelineEntry = {
  runtimeId: "subagent_swarm"
  taskBriefId: string
  subagentId: string
  kind: "handoff" | "progress" | "tool" | "artifact" | "self_check" | "result"
  summary: string
  status: "pending" | "running" | "blocked" | "self_checked" | "accepted" | "rejected" | "failed"
  projectionTargets: Array<"runtime_card" | "hud" | "process" | "artifact_ref">
  traceRef?: string
}

type SwarmTaskProjection = {
  taskBriefId: string
  taskGoal: string
  subagentId: string
  subagentLabel?: string
  status: "pending" | "running" | "blocked" | "self_checked" | "accepted" | "rejected" | "failed"
  localSelfCheck?: {
    status: "passed" | "risk" | "failed"
    summary: string
    confidence?: number
  }
  compactTranscript: Array<{
    role: "subagent" | "tool" | "system"
    summary: string
    timestamp?: string
  }>
  traceRef?: string
  artifactRefs?: SwarmArtifactRef[]
  acceptanceHint?: string
  supervisorAcceptance?: {
    status: "pending" | "accepted" | "retry_requested" | "rejected"
    reason?: string
  }
}

type SwarmArtifactRef = {
  artifactId: string
  taskBriefId: string
  subagentId: string
  kind: string
  title?: string
  uri?: string
  adoptedBySupervisor?: boolean
}
```

#### 语义边界

- `localSelfCheck` 只代表子任务自检，不代表用户任务完成。
- `supervisorAcceptance` 才代表 supervisor 是否采纳该子任务结果。
- `projectionTargets` 不应包含 `message`，除非事件是 supervisor 自己生成的 milestone summary。
- `compactTranscript` 是状态卡可读摘要，不是完整原始 token 流。
- `traceRef` 是完整排障链路入口，不应默认展开进 snapshot。
- `adoptedBySupervisor=true` 的 artifact 才能进入最终 supervisor 气泡的 curated refs。

## 10. 验收与回归要求

### 10.1 Planner Lane

后续实现必须验证：

- `plan / task / todos / 拆解 / 分工 / 大任务执行` 类请求会自动切入 planner lane
- 显式 override 能强制开 / 关 planner lane
- planner 输出包含：
  - `taskBrief`
  - `writeSet`
  - `behaviorScope`
  - `acceptanceContract`

### 10.2 Capability Snapshot

后续实现必须验证：

- `roleLabel` 仍只影响前端身份展示
- `capabilitySnapshot` 用于 subagent 筛选与工具暴露
- Admin 可以编辑基础 capability 字段
- 运行时会基于 prompt / tools / events / execution history 做派生补全

### 10.3 Subagent routing

后续实现必须验证：

- subagent 的 extensions 预筛使用 `delegatedTask / taskBrief`
- 不再直接把用户原始消息透传为 subagent route 输入
- `contextual_auto` 被收口为 delegated-task-driven route 的长期主链

### 10.4 Delegation migration

后续实现必须验证：

- supervisor 工具面只保留 `delegation_broker` 作为 delegation 主链
- `delegate_parallel / handoff_to_* / create_agent` 不再出现在 supervisor toolset、prompt snapshot 或主链文案中
- 删除的是 supervisor 编排旧入口，不是 Admin/API 的 subagent CRUD
- prompt / docs / diagnostics 不再把旧入口描述成长期真相

### 10.5 Swarm governance

后续实现必须验证：

- 蓝图不设架构级固定上限
- 默认治理建议 `<=10` 并发
- 在 planner + write-set isolation 到位前，不扩大默认并发规模

### 10.6 Phase 2 external workers

后续实现必须验证：

- `command_session_broker + hooks` 能承接外部 CLI worker
- worker 具备明确的：
  - launch
  - attach / observe
  - interrupt
  - timeout
  - cleanup
  - result aggregation

### 10.7 Swarm surface projection

后续实现必须验证：

- subagent text events 不会生成或污染 supervisor assistant message narrative nodes。
- subagent tool events 不会在 supervisor 气泡里生成对应 tool node，除非 supervisor 显式把结果采纳成 milestone summary。
- `subagent_swarm` runtime card 在 os-phone/os-web 均可见。
- swarm runtime card 按 `taskBrief / planner task node` 分组，而不是只按纯时间线展示。
- 每个 task group 能展开查看 compact transcript、tool summary、artifact summary 与 trace ref。
- `localSelfCheck` 只显示为子任务局部自检，不会被 UI 显示成总任务完成。
- supervisor 气泡可以显示动画 milestone summary，但不得混入 subagent 原文。
- subagent artifacts 先进入 swarm runtime card，最终气泡只引用 supervisor 采纳后的 curated refs。
- `context_governance` 卡能看到 context governance 与 `supervisor.graph.*` 诊断信息。
- ChatRuntime 卡不再承接 graph timing、上下文压缩、治理提示这类非执行主线噪音。
- os-phone/os-web 长 swarm 超过 24 条 activity 时仍能完整查看，不丢事件。
- BroadcastRail / dock badge 的小数量预览不会成为完整 timeline 的替代品。

## 附录 A：Claude 源码启发，不照搬

本附录仅总结可借鉴原则，不等于建议直接移植 `Claude` 的实现。

### A.1 `teammate.ts`：身份与上下文隔离

`E:\Projects\v8chat\Claude\src\utils\teammate.ts` 显示 Claude 在 swarm 协调里显式区分：

- 主会话
- in-process teammate
- tmux / CLI teammate

关键启发：

- teammate 身份不是 prompt 文本拼出来的，而是运行时上下文显式携带
- AsyncLocalStorage 可用于隔离 in-process teammate context
- 父会话 / 子会话的 identity 和 lifecycle 不应靠字符串约定维持

### A.2 `teammateMailbox.ts`：邮箱式异步消息回收

`E:\Projects\v8chat\Claude\src\utils\teammateMailbox.ts` 说明 Claude 对 swarm 协调大量使用 mailbox 语义：

- leader / worker 消息回收
- 权限请求回传
- shutdown / mode set / task assignment

关键启发：

- swarm 内部回收不应只靠“LLM 记住前文”
- 结构化消息回收层可以显著减轻 parent-child prompt 纠缠
- 这对 V8 的 process HUD / runtime events / hooks 设计很有参考价值

### A.3 `plans.ts`：主会话与子代理独立 plan persistence

`E:\Projects\v8chat\Claude\src\utils\plans.ts` 显示 Claude 已经把：

- 主会话 plan 文件
- subagent plan 文件

分开持久化。

关键启发：

- plan 不应只是一段临时上下文
- 主会话与子代理的 plan 应具备独立 persistence
- 这和 V8 未来的 planner lane / task brief 非常契合

### A.4 `planModeV2.ts`：plan mode gating

`E:\Projects\v8chat\Claude\src\utils\planModeV2.ts` 展示了：

- 计划模式 gating
- agent count 读取
- 计划模式实验与配置开关

关键启发：

- plan-before-implement 应该有明确 gating
- 并发与 planning 不应完全耦死在单一工具上
- 但 V8 不需要照搬 Claude 的实验门控体系

## 附录 B：Hermes Agent 启发，不照搬

### B.1 官方能力启发

根据官方站点与公开设计讨论，Hermes 在 multi-agent 方向的几个关键启发包括：

- child agents 使用隔离上下文
- orchestrator 汇聚结构化结果，而不是让 worker 互相总结摘要
- 支持按任务裁剪工具集
- 可配置并发
- 资源感知调度
- 把 workflow DAG / 结果传递 / checkpointing / stuck detection 作为演进方向

参考：

- [Hermes Multi-Agent 功能页](https://hermes-agent.ai/features/multi-agent)
- [Hermes Multi-Agent 博客说明](https://hermes-agent.ai/blog/hermes-agent-multi-agent)
- [NousResearch/hermes-agent Issue #344](https://github.com/NousResearch/hermes-agent/issues/344)

### B.2 对 V8 最有价值的借鉴点

不是“每个点都搬过来”，而是优先吸收这几条原则：

1. 子代理只吃 task brief，不吃父上下文全文
2. 子代理工具集必须按任务裁剪
3. 汇聚必须结构化，而不是靠自然语言拼接
4. 并发控制是资源治理问题，不只是一个数字参数
5. DAG / dependency / result passing 比“多开几个 agent”更重要

### B.3 为什么不直接照搬 Hermes

V8OS 与 Hermes 的运行时约束不同，至少有这些关键差异：

- V8 有自己的 memory 体系
- V8 有 hooks 与 process surface
- V8 有 `phone` 主交互面和 `ProcessesHUD`
- V8 有多 runtime planes，而不是单一 agent shell
- V8 的 external tools 还要受 `extensions`、`plugin_host`、`computer_use` 等多条主链约束

因此 V8 的正确路线是：

- 借鉴 Hermes 的原则
- 但将其重新落到 V8 自己的 runtime / plane / HUD / hook / memory 体系里

## 附录 C：建议实施顺序

推荐的总体顺序如下：

1. 先把 `roleLabel` 与 `capabilitySnapshot` 分层
2. 再把 `task_planning_mode` 提升为内部 `Planner Lane`
3. 再把 subagent 工具暴露收口到 `delegated-task-driven contextual route`
4. 再引入统一 `Delegation Broker`
5. 最后接入 `External CLI Worker Broker`

不建议的顺序包括：

- 先把 `delegate_parallel` 从 2 改成 6 或 10
- 先把所有 supervisor 工具直接平移给 subagent
- 先把 `roleLabel` 直接当能力标签
- 先新起一个 external-agent runtime

## 参考事实源

### V8 当前代码真相

- `apps/v8-agent-os-engine/graph/supervisor_context.py`
- `apps/v8-agent-os-engine/graph/agent_factories.py`
- `apps/v8-agent-os-engine/graph/parallel_support.py`
- `apps/v8-agent-os-engine/graph/supervisor_support.py`
- `apps/v8-agent-os-engine/core/agents.py`
- `apps/v8-agent-os-engine/runtimes/chat/runtime.py`
- `apps/v8-agent-os-engine/core/native_tools.py`
- `packages/session-realtime/src/contract.ts`
- `packages/session-realtime/src/event-taxonomy.ts`
- `packages/session-realtime/src/message-lifecycle.ts`
- `apps/v8-agent-os-admin/src/app/admin/(dashboard)/subagents/page.tsx`
- `apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx`
- `apps/v8-agent-os-phone/src/components/chat/RuntimeTimelinePanel.tsx`
- `apps/v8-agent-os-phone/src/lib/runtime-stage.ts`
- `apps/v8-agent-os-web/src/components/chat/RuntimeTimelinePanel.tsx`
- `apps/v8-agent-os-web/src/lib/runtime-stage.ts`

### 外部启发

- `E:\Projects\v8chat\Claude\src\utils\teammate.ts`
- `E:\Projects\v8chat\Claude\src\utils\teammateMailbox.ts`
- `E:\Projects\v8chat\Claude\src\utils\plans.ts`
- `E:\Projects\v8chat\Claude\src\utils\planModeV2.ts`
- [Hermes Multi-Agent 功能页](https://hermes-agent.ai/features/multi-agent)
- [Hermes Multi-Agent 博客说明](https://hermes-agent.ai/blog/hermes-agent-multi-agent)
- [NousResearch/hermes-agent Issue #344](https://github.com/NousResearch/hermes-agent/issues/344)
