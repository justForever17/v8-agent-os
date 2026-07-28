# V8OS Extensions Runtime：Skills 与 MCP 的候选治理层

V8OS Extensions Runtime 负责 Skills 与 MCP 的编目、刷新、健康、候选筛选和暴露治理。它解决的是一个长期 agent 系统必然会遇到的问题：本地可能安装几十到上千个 skills，也可能接入大量 MCP server 和外部工具，如果把它们的描述、参数和使用说明全量塞进 Supervisor system content，模型上下文会被扩展生态淹没，普通任务也会被无关工具干扰。Extensions Runtime 的目标是让扩展生态足够丰富，但暴露给模型时始终克制、可解释、可降级。

Extensions Runtime 不承担插件安装、授权和凭据生命周期，也不替代 ChatRuntime 编排。它提供的是扩展候选的统一真相源：哪些 skill 存在、属于什么 family、健康状态如何、是否需要热刷新、当前任务命中了哪些候选、哪些 MCP server 可用、哪些工具应该暂缓暴露。ChatRuntime 和 Supervisor 只消费这个候选摘要，而不是直接扫描全部 skill 目录或 MCP 配置。

插件安装的 Skill 与 MCP 仍写入现有 `~/.agents/skills` 和 `~/.v8-agent-os/mcp.json` 等真相面，不建立另一套插件专用资源仓。日常任务仍由 Extensions Runtime 管理候选；只有插件已注册、已安装且当前 run 拥有有效 grant 时，该插件包的精确组件才临时绕过普通预筛进入授权投影。这个特权通道只影响当前插件包，不会关闭或接管其他普通扩展。

## 核心能力

1. **Inventory**
   扫描本地 skills、系统 skills、市场缓存、MCP 配置与运行时快照，形成可治理的扩展目录。

2. **Health / Refresh**
   跟踪扩展目录变化、MCP 连接状态、候选刷新、失败降级和后台 reconcile，避免一次刷新失败让整个扩展面不可用。

3. **Prefilter**
   根据用户请求、任务主题、语言、runtime signals 与 capability hints 选出短候选，而不是把全量 skills/MCP 暴露给 Supervisor。

4. **Candidate Summary**
   将命中的 skills 和 MCP server 压缩成当前任务可用的简短候选说明，包含名称、触发意图、核心能力和必要风险提示。

5. **Safety Mediation**
   记录扩展健康、风险、降级和安全状态；高风险外部工具不会因为存在于 MCP 配置中就自动成为普通 supervisor 默认能力。

## Content 注入原则

- **目录在 runtime，候选进 prompt**：完整 skill/MCP inventory 存在 ExtensionsRuntime；system content 只注入当前任务 shortlist。
- **先筛 family，再给条目**：大量 skills 先按主题、触发语、能力标签和上下文信号缩小范围，再暴露少量具体候选。
- **描述短而可路由**：候选摘要只回答“什么时候用、能做什么、有什么边界”，不粘贴完整 SKILL.md 或 MCP schema。
- **动态但不随机**：候选会随任务变，但要由可观测的 prefilter 和 health evidence 决定，而不是每轮任意抖动。
- **MCP server 不是默认工具洪水**：MCP 工具需要通过候选机制、健康状态、runtime policy 和任务相关性进入上下文。
- **技能执行仍需 activation**：skill 被候选命中不等于把全文常驻；只有任务真的需要时，才读取对应 SKILL.md 的核心工作流。
- **Skill 仍是完整资源包**：通用 `fetch_skill_instructions` 可以按需读取任意可达的 SKILL.md 及其后续引用；插件不额外制造一套 Skill 读取工具，也不为了预筛方便截断合法脚本和资源续读。

## 解决的核心问题

- **Skills 过量侵占上下文**：大量技能只在目录中存在，命中时才短摘要注入；完整技能说明按需读取。
- **MCP server 工具面膨胀**：MCP server 和工具不再天然等于 supervisor 默认工具，先经过健康与候选治理。
- **扩展生态不稳定**：目录刷新、缓存、连接失败和市场变化通过 runtime 统一观测，避免散落在各 loader 中。
- **能力发现与使用分离**：Supervisor 可以知道“有某类扩展能力”，但不会因为知道就立刻背负全部工具参数。
- **跨 runtime 边界清晰**：ExtensionsRuntime 只负责扩展目录与候选暴露；真正的媒体生成、记忆维护、桌面执行仍归各自 runtime。

## 与多媒体 Agent 创作的关系

Creative Media Runtime 是 V8OS 多媒体创作的 canonical runtime；ExtensionsRuntime 可以补充第三方视频、头像、Remotion、提示词、发布或设计类 skills，也可以发现媒体相关 MCP server。但这些扩展只是候选能力，不是 Creative Media 的真相源。复杂创作仍应先走 Creative Media 的 recipe、asset ledger、job、quality 和 artifact 链路，再按需借用 extension 候选完成专门任务。

## 与其他 Runtime 的边界

- ChatRuntime 消费 ExtensionsRuntime 的候选摘要，用于当前 run 的动态 system content。
- MemoryRuntime 可记住用户常用 skill 偏好或成功工作流，但不直接替代 ExtensionsRuntime prefilter。
- 插件管理中心负责目录、安装事务、配置状态和显式授权；ExtensionsRuntime 不替代这条治理链。
- 插件的有效 task grant 会临时投影精确 CLI/MCP/Skill/UI 组件；安装、配置、健康和 grant 缺一不可，知道插件存在不等于已经授权或调用。
- MCP 工具、skills、插件 CLI 与 runtime direct tools 是不同层级：ExtensionsRuntime 管普通候选，Plugin Manager 管插件包授权，具体 runtime 管自己的稳定工具面。

## 当前治理重点

Extensions Runtime 的核心方向是把“扩展多”变成优势而不是上下文负担：保持 inventory 完整、候选短小、暴露有证据、失败可降级，并通过 dry-run / observation report 验证它没有把陈旧模板、无关 skill 或海量 MCP schema 注入到普通 supervisor prompt。
