# Project Scope 整改计划书

更新时间：2026-04-13
适用仓库：`v8-agent-os`、`v8-agent-os-web`、`v8-agent-os-phone`
状态：待实施

## 1. 结论先行

当前 V8 Agent OS 的“项目级工作区 / project scope”主链存在三类根问题：

1. 语义混乱
   - UI 对外说的是“自动推断项目”，engine 实际做的是“显式锚点优先，失败后再做 scope 启发式推断”。
   - `project`、`workspace`、`scope`、`app:coding`、`app:writing` 被混在同一条链里，导致用户、页面和 runtime 对同一个动作的理解并不一致。
2. 配置面过重
   - Admin 项目注册表当前把 `项目 ID / 描述 / workspaceId / defaultScope / tags` 等内部细节直接暴露给用户，和“项目级工作区绑定”这条主线不匹配。
3. 记忆写入与读取边界不干净
   - `memory` 当前仍可能沿用 `app:*` 与启发式 scope 检测；这与“项目级工作区是明确绑定、可恢复、可审计状态”的目标冲突。

本次整改的总方向固定为：

1. **彻底移除“聊天内容启发式 scope 检测”在项目/工作区绑定主链中的地位**
2. **移除 `app:coding` / `app:writing` 这类语义不清晰的 memory scope**
3. **os-web / os-phone 改为明确的手动工作区绑定，不再提供“自动推断项目”**
4. **项目级工作区的创建与绑定收敛成“名称 + 路径”语义；移动端与 web 只需要输入项目名，路径由 engine 默认生成**
5. **memory 写入和读取的 project scope 只服务于“项目级工作区”**

## 2. 当前问题基线

### 2.1 Admin 项目注册表过度暴露内部字段

当前 Admin `memory?tab=projects` 的项目编辑面板暴露了：

- `项目 ID`
- `项目名称`
- `描述全文`
- `工作区标识`
- `工作区目录`
- `默认范围`
- `Tags`

这会导致两个问题：

1. 把内部治理字段暴露成了普通用户配置项。
2. 用户会误以为 `defaultScope`、`workspaceId` 这类字段需要自己理解并维护。

而从 runtime 主链看，真正对“项目级工作区绑定”有直接价值的核心只有：

- 项目名称
- 工作区路径

其余字段要么应该自动派生，要么应该降为内部元数据，而不是成为主表单。

### 2.2 os-web / os-phone 存在误导性的“自动推断项目”

当前 `os-web` 与 `os-phone` 都存在 `__auto__ / 自动推断项目` 入口。用户看到的是“系统会帮我选项目”，但实际链路是：

1. 前端在未显式选项目时，以 `scopeMode = "mixed"` 调用 scope 解析。
2. engine 先尝试 workflow / channel / workspace 映射。
3. 若没有显式锚点，再落到 `detect_scope(user_query)` 的启发式检测。

这意味着：

- UI 在表达“项目选择”
- engine 很多时候其实只是在做“scope 分类”

这会直接破坏：

- 用户心智
- 会话恢复可解释性
- Web / Phone / Engine 三边语义一致性

### 2.3 engine 仍保留基于消息内容的启发式 scope 推断

当前 `runtimes/memory/scope_resolution.py` 在没有显式项目/工作区锚点时，仍会落到：

- `detect_scope(user_query, ...)`
- 返回 `app:coding` / `app:writing` / `app:chat` / `global`

这条链在今天已经不适合作为项目级工作区主链的一部分，原因是：

1. 它不稳定
2. 它不可预测
3. 它不是项目绑定
4. 它会污染 memory scope 设计

### 2.4 memory scope 仍残留历史语义

当前 memory agent 在会话提取时仍会把 `detect_scope(chat_history_text)` 混进 scope chain。即使实际写入时大多数场景最终还是 `global` 或 `project:*`，这条链依然在制造过时语义。

这会带来两个坏结果：

1. `app:coding` / `app:writing` 明明几乎不再承担真实写入价值，却持续污染代码和事件语义。
2. 项目级工作区的长期记忆边界被“聊天内容分类”干扰。

## 3. 目标状态

整改完成后，Project Scope 主链应满足以下硬约束：

### 3.1 绑定语义

项目级工作区绑定只能来自确定性来源：

1. 用户在 `os-web / os-phone` 显式选择
2. Admin 项目注册表中的明确项目路径配置
3. workflow / channel 等强绑定来源
4. 已存在会话的已保存 binding

**不允许再由聊天内容推测 project/workspace binding。**

### 3.2 UI 语义

`os-web / os-phone` 不再出现：

- `自动推断项目`
- `Auto-detect project`
- `重新解析 scope`
- 任何把“项目绑定”包装成启发式推断的文案

### 3.3 Memory 语义

memory runtime 对长期记忆只保留：

- `global`
- `project:{project_id}`

`app:coding`、`app:writing`、`app:chat` 等历史 scope 从 memory 写入与读取主链中移除。

### 3.4 路径策略

项目级工作区默认创建路径统一为 **用户 home 下的 runtime-managed project workspace 根**，建议收敛为：

`~/.v8-agent-os/workspace/projects/<project-slug>`

原因：

1. 跨平台稳定
2. 与现有 `WORKSPACE_HOME = ~/.v8-agent-os/workspace` 一致
3. 不再发明新的路径真相

Admin 仍保留“自定义项目路径”能力；但 `os-web / os-phone` 只输入项目名，默认路径由 engine 生成。

## 4. 具体整改方案

## 4.1 语义层整改

### 4.1.1 废除“聊天内容启发式 scope 检测”

处理原则：

1. `detect_scope(user_query)` 退出项目/工作区绑定主链。
2. `scope_mode = "infer" / "mixed"` 不再用于项目绑定语义。
3. Web / Phone 发送新会话和更新 scope 时，只允许：
   - `explicit`
   - 或“无项目绑定，回落主工作区”

整改后：

- 没有显式项目绑定时，不再“猜项目”
- 没有显式项目绑定时，也不再“猜 app scope”
- 统一回到主工作区 + `global`

### 4.1.2 废除 `app:coding` / `app:writing`

处理原则：

1. 从 memory runtime 的写入主链移除。
2. 从 memory runtime 的 recall / session context 注入主链移除。
3. 从对话级 scope 叙事中移除。
4. 对历史数据不做强制迁移，但新逻辑不再生成这些 scope。

整改后：

- memory agent 仅写 `global` 或 `project:{project_id}`
- project 级信息只服务于项目级工作区

## 4.2 Admin 项目注册表整改

### 4.2.1 表单收敛

Admin 项目注册表的主编辑面板改为只展示两项主字段：

1. `项目名称`
2. `工作区路径`

其余字段处理如下：

- `项目 ID`
  - 改为自动生成
  - 默认不对普通用户暴露
  - 仅高级模式可查看或编辑
- `workspaceId`
  - 不再手填
  - 由 engine 从项目 ID 或路径派生
- `defaultScope`
  - 不再手填
  - 统一按 `project:{project_id}` 自动派生
- `description`
  - 不再作为主配置面字段
  - 若保留，降为高级元数据
- `tags`
  - 不再作为主配置面字段
  - 若保留，降为高级元数据

### 4.2.2 数据模型调整

`ProjectDescriptor` 的 canonical 主语义建议收成：

- `id`
- `name`
- `workspacePath`
- `active`

其余字段转为派生或高级元数据：

- `workspaceId`
- `defaultScope`
- `description`
- `tags`
- `channelBindings`
- `workflowBindings`

注：

- `id` 仍然保留，因为它是 runtime、ledger、memory scope 的 canonical key。
- 但它不应继续作为普通用户的主编辑字段。

## 4.3 os-web / os-phone 交互整改

### 4.3.1 新对话创建前先做工作区绑定选择

`os-web / os-phone` 在创建新对话时，不再直接进空白聊天页，而是先唤出**工作区绑定界面**。

该界面只提供三层选择：

1. 主工作区
2. 现有项目级工作区列表
   - 可折叠
   - 可滚动
3. 新建项目级工作区

### 4.3.2 只给一次选择机会

新对话的工作区绑定在创建时完成，之后该会话不再支持通过聊天页继续切换项目。

这意味着：

1. 新建对话时必须先做绑定决定
2. 会话创建后，项目绑定冻结
3. 后续如需切换项目，应新建对话，而不是在原对话里热切换

### 4.3.3 Web / Phone 的“新建项目级工作区”极简化

`os-web / os-phone` 创建项目级工作区时，只输入：

1. 项目名称

engine 负责：

1. 生成 `project_id`
2. 生成默认 `workspacePath`
3. 写入项目注册表
4. 立即把当前对话绑定到新项目

### 4.3.4 会话创建后的按钮行为

工作区按钮在对话创建后不再承担“切换 / 推测 / 重新解析”能力。

它只显示：

1. 当前对话名称
2. 当前项目路径

允许的增强行为可以是：

- 只读查看
- 跳转 Admin 查看项目详情
- 打开本地路径

但不再允许：

- 自动推断项目
- 重新解析 scope
- 在原会话中切换到另一个项目

## 4.4 Engine Scope Resolution 整改

### 4.4.1 `ScopeResolutionService` 收敛

新的解析顺序固定为：

1. 显式项目 / 工作区绑定
2. workflow 绑定
3. channel 绑定
4. 已存在 session binding
5. 回落主工作区

明确移除：

- `workspace_inferred` 中基于聊天内容的解释性叙事
- `heuristic_detected`
- 任何 `detect_scope(user_query)` 驱动的项目/工作区决策

### 4.4.2 `scope_mode` 收敛

对于 chat surfaces：

- `explicit`：保留
- `mixed`：废弃
- `infer`：废弃

在过渡期可以保留 API 兼容字段，但 engine 应将其视为：

- 未显式绑定时直接回落主工作区
- 不再触发启发式推断

## 4.5 Memory Runtime 整改

### 4.5.1 写入规则

memory agent 在 session extraction 时：

1. 若 session binding 存在 `project_id`，写入 `project:{project_id}`
2. 否则写入 `global`

不再把 `detect_scope(chat_history_text)` 混入写入链。

### 4.5.2 读取规则

session context / recall / prior memory summary 只在以下两层之间工作：

1. `global`
2. `project:{project_id}`

项目级工作区的对话：

- 应优先命中 `project:{project_id}`
- 再补 `global`

非项目级工作区对话：

- 只走 `global`

### 4.5.3 历史数据策略

历史 `app:*` scope 不做强制迁移，但要做两件事：

1. 新逻辑不再写入新的 `app:*`
2. 读取链逐步不再依赖 `app:*`

## 5. 可能打架的功能与先决约束

本计划若直接推进，真正可能与其他主线打架的不是“页面字段删多删少”，而是下面这些 runtime 交叉面。

### 5.1 `project_id` 不能被 UI 极简化误删

Admin 项目表单可以简化成“名称 + 路径”，但 `project_id` 仍必须作为内部 canonical key 保留。原因是以下主线已经在稳定消费它：

1. memory scope 的 `project:{project_id}`
2. model budget / project budget ledger
3. extensions runtime 的 workspace-scoped skills
4. computer_use / network_supervisor / plugin_host / automation 等 runtime 的显式项目元数据

所以本次整改的正确做法是：

1. **隐藏 `project_id`，不再让普通用户手填**
2. **自动生成并长期稳定保留**

而不是把项目模型真的简化成只剩 `name + workspacePath` 两个内部字段。

### 5.2 `app:*` 退出 memory 主链会牵动 recall / context / audit

`app:coding`、`app:writing` 不是只在一个 resolver 文件里残留，它们当前仍影响：

1. `memory_agent` 的 extraction scope 对齐
2. `memory_store` 的偏好读取、MEMORY.md 模板、recall 优先级链
3. `knowledge_routes` 的 scope 过滤
4. `context_orchestrator` 的 `resolved_scope / scope_chain` 审计语义

因此如果我们决定废除 `app:*`，就必须把它当成 **memory/runtime scope 语义整治** 来做，而不能只删 `detect_scope()` 的调用点。

### 5.3 Safety Guardian 仍可能消费 `resolved_scope`

Safety Guardian 当前的 runtime preflight 仍允许按 `auditScopePatterns / reviewScopePatterns / blockedScopePatterns` 对 scope 做治理。

这意味着：

1. 若配置里还残留对 `app:coding` / `app:writing` 的匹配规则，就会和新主链打架
2. 本次整改必须同步审计 `safety` 的 runtime scope patterns

但这里要注意：

1. **移除 `app:*` 不等于移除 safety 的 scope plane**
2. **保留 `global` 与 `project:{id}` 才是正确收敛**

### 5.4 `extensions runtime` 依赖稳定的项目/工作区绑定

我们刚做完 `workspace/.agents/skills` 动态支持，`extensions runtime` 已经把 `projectId / workspaceId / workspacePath` 作为 scoped inventory 的一部分。

这意味着：

1. “禁止聊天内容推测项目”是正确方向
2. 但不能误伤“显式项目/工作区绑定”这条 metadata plane

换句话说：

- 该删的是 `mixed / infer / heuristic`
- 该保的是 `explicit project_id / workspace_id / workspace_path`

### 5.5 Admin 目前已有两个项目/工作区控制面，会互相打架

当前 Admin 不只 `memory?tab=projects` 一个入口，`/admin/projects-workspaces` 也在表达“主工作区 + 项目级工作区”的治理语义。

如果本次只改 `memory?tab=projects`，不同时收口 `/admin/projects-workspaces`，会出现：

1. 一个页面说“项目只要名称 + 路径”
2. 另一个页面还在沿用旧的项目/工作区叙事

所以本次整改必须明确：

1. `projects-workspaces` 是工作区治理入口
2. `memory?tab=projects` 是项目注册表与项目级记忆辅助入口

两者不能继续平行定义同一套心智。

### 5.6 “新对话只给一次绑定机会”是正确方向，但有一个 UX 风险

这个方向整体上是好的，但要明确它的代价：

1. 用户如果选错项目，不能在原对话里热切换
2. 恢复方式只能是“新建一个绑定正确项目的新对话”

这个代价我认为是可以接受的，因为它换来的是：

1. 会话可恢复性
2. 路由可解释性
3. memory / artifact / workspace 输出路径一致性

因此本计划继续坚持“绑定冻结”，但要把这个选择明确写进产品语义，而不是假装系统还能无损切换。

## 6. 迁移策略

本次整改**不考虑兼容旧对话记录**，但仍保留以下两条硬约束：

1. 不重写历史对话明细，只从新会话开始执行新纪律
2. 不破坏仍然必要的内部 canonical key 与 runtime metadata plane

### 6.1 Admin 配置迁移

对已有项目注册表记录：

1. 保留历史 `id`
2. 保留历史 `workspacePath`
3. `defaultScope` 改为派生值，不再作为主配置项
4. `workspaceId` 改为派生值，不再作为主配置项
5. `description`、`tags` 若保留，迁入高级元数据区

### 6.2 Web / Phone 会话迁移

对已有会话：

1. 不做旧会话兼容优化
2. 新会话开始执行“创建前绑定”纪律
3. 新会话不再允许 `__auto__` / `scopeMode=mixed` / `scopeMode=infer`

### 6.3 Scope 与 Memory 迁移

对已有 `scope_source`、`scope_mode`、`app:*` 历史记录：

1. 历史记录保留
2. 新事件不再写入 `heuristic_detected`
3. 新逻辑不再生成 `app:coding` / `app:writing` / `app:chat`
4. 新 recall / session context 主链只保留 `global + project:{id}`

## 7. 两阶段实施方案

原 4 阶段拆得太细，会把“语义层收口”和“交互层收口”人为拆散，反而增加返工概率。  
在**不兼容旧对话记录**这个前提下，我有信心压成 2 个阶段完成。

### 阶段 1：Engine + Admin 语义一次收正

目标：

1. 从 engine 主链移除 `mixed / infer / heuristic_detected`
2. 从 memory 主链移除 `app:*`
3. 固定 project scope 只服务于项目级工作区
4. Admin 项目注册表收缩成“名称 + 路径”为主
5. 收口 `/admin/projects-workspaces` 与 `/admin/memory?tab=projects` 的职责分工

涉及：

- `apps/v8-agent-os-engine/runtimes/memory/scope_resolution.py`
- `apps/v8-agent-os-engine/agents/memory_agent.py`
- `apps/v8-agent-os-engine/core/memory_store.py`
- `apps/v8-agent-os-engine/core/scope_detector.py`
- `apps/v8-agent-os-engine/api/session_workflow_routes.py`
- `apps/v8-agent-os-engine/api/models.py`
- `apps/v8-agent-os-engine/api/knowledge_routes.py`
- `apps/v8-agent-os-engine/erc/safety_guardian.py`
- `apps/v8-agent-os-admin/src/components/memory/ProjectRegistryPanel.tsx`
- `apps/v8-agent-os-admin/src/app/admin/(dashboard)/projects-workspaces/page.tsx`
- `apps/v8-agent-os-admin/src/lib/admin-copy.ts`

阶段 1 完成后应达到：

1. engine 不再存在“自动推断项目”的有效执行链
2. Admin 已不再暴露 `defaultScope/workspaceId` 为主表单字段
3. memory 新写入与新读取只剩 `global/project:{id}`

### 阶段 2：Web / Phone 新建对话绑定重做 + 全链回归

目标：

1. `os-web / os-phone` 新对话前先选工作区
2. 删除 `自动推断项目 / Auto-detect project`
3. 删除原会话内切换项目与 re-resolve scope 主链
4. 把工作区按钮改为只读展示
5. 做跨 runtime 回归：memory、extensions、artifacts、computer_use、network_supervisor 的项目/工作区元数据不退化
6. 阶段 2 的整改注意让侧边栏历史记录按钮的层级关系处于更高层，防止用户打开app/交互面板想直接进某个项目级对话时被新会话工作区选择窗口卡住的现象。

涉及：

- `apps/v8-agent-os-web/src/app/chat/ChatClient.tsx`
- `apps/v8-agent-os-web/src/app/api/chat/route.ts`
- `apps/v8-agent-os-web/src/app/api/sessions/[id]/scope/re-resolve/route.ts`
- `apps/v8-agent-os-phone/src/screens/ChatScreen.tsx`
- `apps/v8-agent-os-phone/src/lib/phone-api.ts`
- `apps/v8-agent-os-phone/src/types/admin.ts`
- 必要的 session/history 投影与 route metadata 校验代码

阶段 2 完成后应达到：

1. 用户侧不再感知任何“自动推断项目”语义
2. 项目级工作区绑定在新会话创建时一次完成并冻结
3. 多 runtime 对 `project_id/workspace_id/workspace_path` 的显式消费保持稳定

## 8. 验收标准

### 8.1 Admin

1. 项目注册表主表单只显示：
   - 项目名称
   - 工作区路径
2. 不再暴露：
   - 默认范围
   - 工作区标识
   - tags
   - 描述全文
   作为主配置项

### 8.2 os-web / os-phone

1. 不再出现“自动推断项目 / Auto-detect project”
2. 新对话前先弹出工作区绑定页
3. 创建后项目绑定冻结
4. 工作区按钮只显示当前对话信息和项目路径

### 8.3 engine

1. `ScopeResolutionService` 不再使用 `detect_scope(user_query)` 推断项目/工作区
2. `scope_mode = mixed / infer` 不再驱动项目绑定
3. 未显式绑定时直接回落主工作区

### 8.4 memory

1. memory agent 新写入只允许：
   - `global`
   - `project:{project_id}`
2. 新逻辑不再写入 `app:coding` / `app:writing`
3. 项目级工作区对话优先命中 `project:{project_id}`

## 9. 风险与注意事项

### 8.1 这是一次语义收紧，不是普通 UI 微调

它会影响：

- 会话创建流程
- scope binding 规则
- memory 写入语义
- web / phone 用户心智

所以必须按 runtime 主链治理来做，不能只在页面上删几个控件。

### 8.2 不建议保留“自动推断项目”作为隐藏 fallback

因为这会导致：

1. 用户以为行为已收紧，实际系统仍在偷偷猜
2. 恢复链变得不可解释
3. Web / Phone / Engine 语义再次分裂

如果要彻底整改，就应该把它**真正请出历史舞台**，而不是把按钮藏起来。

## 10. 最终裁决

本计划的核心裁决如下：

1. **项目绑定必须是显式、可恢复、可审计的状态**
2. **聊天内容启发式 scope 检测退出项目级工作区主链**
3. **`app:coding / app:writing` 退出 memory 主链**
4. **项目级工作区只为 `project:{id}` 服务**
5. **os-web / os-phone 以手动选择替代推测**
6. **Admin 项目注册表从“内部字段堆叠面板”收缩为“名称 + 路径”的清晰配置面**

这不是“把推断调弱一点”，而是**把不成熟且误导人的语义彻底退役**。
