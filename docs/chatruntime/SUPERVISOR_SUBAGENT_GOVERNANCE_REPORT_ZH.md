# Chat Runtime：Supervisor / Subagent 统御治理报告

更新时间：2026-04-20  
适用仓：`E:\Projects\v8chat\v8-agent-os`  
主题范围：`supervisor`、`subagents`、`planner lane`、`swarm orchestration`

## 1. 目的

本文不是直接实现方案，而是基于当前代码真相形成的一份治理报告。目标有三点：

1. 解释 supervisor 与 subagent 当前到底各自承担什么职责
2. 识别现存的语义断层、工具断层和编排断层
3. 给出后续更稳的演进顺序，避免一上来只盯着“并发数”

本文所有“建议方案”都不应被误读为“当前已实现”。

## 2. 当前真相

### 2.1 supervisor 当前定位

当前已实现：

- supervisor 仍是重提示词、重 runtime registry、重 direct tool surface 的统御节点
- supervisor system content 中会同时注入：
  - runtime 能力上下文
  - specialist agent registry
  - direct tool registry
  - todos / memory / workspace rules 等上下文

这决定了 supervisor 现在仍然偏“总控调度器 + 执行入口集合”，而不是一个极窄的纯编排节点。

相关事实源：

- [supervisor_context.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/graph/supervisor_context.py)

### 2.2 subagent 当前定位

当前已实现：

- subagent 当前不是纯静态 prompt 壳
- 它已有两种工具模式：
  - `explicit`
  - `contextual_auto`

其中：

- `explicit` 会使用显式选择器拼出可用工具面
- `contextual_auto` 会在 agent node 内再次调用 `extensions_runtime_service.build_contextual_route(...)`

这意味着 subagent 当前已经具备“按上下文重做 route”的雏形，但治理语义还不够完整。

相关事实源：

- [agent_factories.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/graph/agent_factories.py)
- [agents.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/core/agents.py)
- [subagents page](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-admin/src/app/admin/(dashboard)/subagents/page.tsx)

### 2.3 specialist agent registry 当前信息仍偏薄

当前已实现：

- supervisor 可见的 specialist agent registry 目前主要只有：
  - `name`
  - `id`
  - `description`
  - `tools count`

这对于“挑哪个 agent 去做哪类任务”远远不够，尤其在未来要做事件驱动暴露和 planner lane 时，会缺少可治理的 agent profile。

### 2.4 `task_planning_mode` 当前语义

当前已实现：

- chat runtime 已有 `task_planning_mode`
- native tools 已有 `write_todos / update_todo`

但当前真相仍然是：

- `task_planning_mode` 更像“是否鼓励进入 todos 链”
- 还不是独立的 `planner lane`
- 还没有自动切换到更窄噪音、更专注拆分质量的 planner prompt / planner pipeline

相关事实源：

- [runtime.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/chat/runtime.py)
- [native_tools.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/core/native_tools.py)

### 2.5 `delegate_parallel` 当前并发真相

当前已实现：

- `delegate_parallel` 当前工具合同明确只支持最多 `2` 个并发 subtask
- 这个限制直接写在输入 schema 与运行时检查里

但这里要严格区分两件事：

- 当前实现：工具合同上限是 `2`
- 架构判断：这**不等于** V8OS 永久不支持更大的 swarm

相关事实源：

- [parallel_support.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/graph/parallel_support.py)

## 3. 身份标签与能力标签快照必须拆分

### 3.1 `roleLabel` 当前本质

当前已实现：

- `roleLabel` 当前主要进入 agent runtime profile
- 它会被 phone / web / runtime projection / plugin-host 相关链路消费
- 它本质上属于**前端交互消息气泡展示身份**

也就是说，`roleLabel` 目前更像：

- 展示身份
- UI 可读称谓
- 会话侧角色气泡文案

而不是一个稳定的能力分类真相。

相关事实源：

- [storage.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/core/storage.py)
- [chat runtime](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/chat/runtime.py)
- [MessageBubble.tsx](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx)

### 3.2 为什么不能直接拿 `roleLabel` 做能力分类

风险有三类：

1. 展示语义和治理语义会纠缠
2. 前端想改气泡称谓时，会误伤 route / 暴露 / planner 逻辑
3. 能力分类通常需要稳定枚举，而 `roleLabel` 天然是自由文本

因此不建议把 `roleLabel` 直接升级成 agent capability 分类真相。

### 3.3 推荐拆分模型

建议方案：

- `presentation identity`
  - 负责：
    - 气泡身份展示
    - 头像语义
    - 前端可读角色名称
  - 可继续沿用 `name / avatar / roleLabel`

- `capability snapshot`
  - 负责：
    - supervisor 候选筛选
    - 事件驱动暴露
    - 默认工具面压缩
    - planner 选型
    - swarm 分工决策

建议后续把 subagent metadata 拆成这两层，而不是让同一个字段同时承载 UI 身份和能力治理。

## 4. subagent 的外部工具暴露建议

### 4.1 不建议回到全量外部工具裸暴露

建议方案：

- subagent 的外部工具暴露应继续复用当前稳定的 extensions 预筛主链
- 不建议让 subagent 默认全量拿到 MCP / PluginHost / Skills
- 也不建议继续把“手工指定全部可用工具”当未来主链

原因很直接：

- 全量暴露会放大噪音
- 会扩大错误工具选择面
- 会让 delegated task 的局部语义被无关工具污染

### 4.2 subagent 的 query truth 必须独立

建议方案：

- supervisor 预筛的 query truth：`用户原始请求`
- subagent 预筛的 query truth：`delegated task / task brief`
- runtime / event context：继续作为 gate truth

三层 truth 不应混用。

如果把 supervisor 的原始用户消息直接透传给 subagent 作为 route 输入，最容易出现：

- subagent 继承了与自己无关的上游目标
- 工具暴露过宽
- 局部执行与全局目标纠缠
- rerank 被上游大上下文污染

### 4.3 推荐的未来方向

建议方案：

- 让 subagent 继续走 `contextual_auto` 的稳定主链思路
- 但 route 输入改为 delegated task truth
- 再叠加 capability snapshot 和 runtime/event gate

换句话说，未来主链不应是：

- “全量暴露”  
或
- “纯手工点工具”

而应是：

- `delegated-task-driven contextual route`

## 5. planner lane 与任务编排建议

### 5.1 当前缺口不只是并发数

当前最关键的问题不是“2 不够，想改成 6 或 10”，而是：

- 任务切片质量不稳定
- 写集边界不清
- 行为域隔离不足
- 子代理分工容易重叠
- 汇聚和验收纪律不足

如果这些问题不先解决，单纯扩并发只会把冲突和噪音放大。

### 5.2 当前没有独立 planner lane

当前已实现：

- 有 `task_planning_mode`
- 有 todos 工具

当前未实现：

- 没有独立 planner prompt
- 没有 planner 专属 route / pipeline
- 没有稳定的“拆解后再执行”的 lane 分工

### 5.3 推荐的 future planner lane

建议方案：

- 把“计划 / 任务拆解 / 分工 / 写集隔离”从普通 supervisor 自由发挥中抽出来
- future planner lane 只负责：
  - 任务切片
  - 写集隔离
  - 行为域隔离
  - 子代理选择
  - 并发编排
- executor lane 再负责具体执行

建议把这个 planner lane 理解成“更窄、更安静、更偏治理”的提示与执行通道，而不是又加一层泛化 prompt。

## 6. 关于并发与蜂群作业的结论

### 6.1 当前实现结论

当前已实现：

- `delegate_parallel` 当前只支持 `2` 并发 subtask

这是工具合同真相，应如实表达，不能淡化。

### 6.2 架构建议结论

建议方案：

- future swarm 设计**不设架构级固定硬上限**
- 但治理建议默认控制在 `10` 并发以下

这里的“10 以下”是治理建议，不是硬编码结论。

### 6.3 为什么不建议只改并发上限

即便未来要支持像 `huashu-nuwa` 这种多采样、多搜索、多写作分工的场景，真正的瓶颈通常仍然在：

- planner 质量
- overlap guard
- 结果汇聚
- 验收与复核

所以不建议把“2 改成 6 / 10”当成主修。更稳的顺序应是：

1. 先统一 agent capability snapshot
2. 再统一 delegated-task-driven route
3. 再做 planner lane
4. 最后扩 swarm 并发规模

## 7. 建议路线

基于当前代码真相，推荐顺序如下：

1. **先拆分展示身份与能力标签快照**
   - 保留 `roleLabel` 继续做展示身份
   - 新增独立 capability snapshot 负责治理

2. **再把 subagent 的外部工具暴露收口到 delegated-task-driven contextual route**
   - 不回到全量暴露
   - 不继续以手工工具表作为唯一真相

3. **再引入 planner lane**
   - 专门处理 plan/task/todos 语义
   - 先解决任务切片质量和冲突控制

4. **最后再扩 swarm 并发规模**
   - 不设架构级固定硬上限
   - 默认治理建议 `<= 10`

## 8. 非目标

以下方向不建议作为主线：

- 把 `roleLabel` 直接作为 subagent 分类真相
- 把所有 supervisor 工具原样平移给 subagent
- 继续依赖全量 MCP / PluginHost / Skills 暴露
- 把“并发数不够”当成唯一问题
- 在没有 planner lane 的情况下直接放大 swarm 并发

## 9. 事实源

本文以当前代码真相为准，主要事实源如下：

- [agent_factories.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/graph/agent_factories.py)
- [parallel_support.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/graph/parallel_support.py)
- [supervisor_context.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/graph/supervisor_context.py)
- [runtime.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/chat/runtime.py)
- [agents.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/core/agents.py)
- [subagents page](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-admin/src/app/admin/(dashboard)/subagents/page.tsx)
- [storage.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/core/storage.py)
- [MessageBubble.tsx](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx)
