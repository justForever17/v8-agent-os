# V8 Agent OS 配置指南

本文描述当前配置真相、Registry 域和敏感信息边界。优先通过 Admin 或 Config Registry 修改配置，不要把页面默认值、旧 JSON 或缓存当作运行真相。

## 1. 配置根与真相层级

V8OS 的本机状态根：

```text
~/.v8-agent-os/
```

主结构化配置：

```text
~/.v8-agent-os/config.json
```

理解配置时按以下顺序判断：

1. Engine Config Registry 或专用治理 API；
2. `config.json` 中对应存储键；
3. 明确存在的独立文件/数据库；
4. 迁移输入和备份仅作排障证据。

`~/.v8chat` 是历史迁移输入或残留，不是当前 canonical root。

## 2. Config Registry 域

Engine 提供：

- `GET /v1/config-registry`
- `GET /v1/config-registry/{domain}`
- `POST /v1/config-registry/{domain}`

当前 Registry 域：

| Domain | 主要内容 |
| --- | --- |
| `models` | provider endpoint、API 通道、模型、角色绑定与能力 |
| `supervisor` | 主理人策略与身份投影 |
| `agents` | 持久 Agent 配置与头像等展示属性 |
| `memory` | 提取、召回、知识与图谱策略 |
| `extensions` | 普通 Skill/MCP 候选与安全策略 |
| `engineering-lane` | Engineering proof、worktree 与 sandbox 策略 |
| `context` | 上下文预算、压缩与注入策略 |
| `audio` | STT/TTS provider 和语音配置 |
| `hooks` | Hook 定义 |
| `cron` | Cron 定义 |
| `automation-runtime` | 自动化运行策略 |
| `workspace` | 默认工作区与信任相关设置 |
| `runtime-stability` | session lane、durability 等稳定性策略 |
| `safety` | 副作用与审批策略 |
| `computer-use` | 桌面/浏览器观察与输入策略 |
| `rpa` | RPA 配置 |
| `mcp` | MCP server 配置投影 |
| `projects` | 项目与 workspace presentation |
| `desktop-pet` | 桌宠动作、播报和外观 |
| `music` | 音乐库配置 |
| `ui` | 本机产品主题等 UI 偏好 |
| `network-supervisor-runtime` | Network Supervisor 运行策略 |
| `system-base` | 系统身份、服务地址和基础依赖 |

`system-misc` 只是 `system-base` 的兼容 alias。新代码和文档使用 `system-base`。

Registry API 使用 kebab-case domain 名，`config.json` 内部存储键可能使用 camelCase，例如：

| Registry domain | 典型存储键 |
| --- | --- |
| `engineering-lane` | `engineeringLane` |
| `automation-runtime` | `automationRuntime` |
| `runtime-stability` | `runtimeStability` |
| `computer-use` | `computerUse` |
| `desktop-pet` | `desktopPet` |
| `network-supervisor-runtime` | `networkSupervisorRuntime` |
| `system-base` | `systemBase` |

页面和调用方不应自行维护这张映射，应复用 Registry。

## 3. `config.json` 中的非 Registry 根策略

部分根策略由专用服务维护，不作为普通 Registry domain 暴露：

- `pluginManager`：签名目录、安装根、授权范围和刷新策略；
- `storageRetention`：磁盘水位、各类存储预算与保留策略；
- `runtimeRegistry`：安装 profile、平台、已安装 runtime family 与 feature pack；
- 其他只由专用路由维护的运行策略。

不要为这些域发明同名 JSON 文件。尤其不存在当前有效的 `~/.v8-agent-os/plugin.json`。

## 4. 独立文件与数据库

| 路径 | 角色 |
| --- | --- |
| `~/.v8-agent-os/mcp.json` | MCP server 配置真相 |
| `~/.v8-agent-os/users.json` | 用户与本机身份输入 |
| `~/.v8-agent-os/V8_AGENT_OS.md` | 可编辑 Supervisor 系统说明 |
| `~/.v8-agent-os/state.db` | 会话、run、事件、授权、事务等主要状态 |
| `~/.v8-agent-os/checkpoints.db` | 加密 checkpoint 状态 |
| `~/.v8-agent-os/computer_use.json` | Computer Use 独立配置面 |
| `~/.v8-agent-os/network_supervisor_secrets.json` | Network Supervisor 敏感配置 |
| `~/.v8-agent-os/network_supervisor_state.json` | Network Supervisor 本地状态 |

Plugin secret、OAuth token 和其他受保护凭据应进入操作系统安全凭据存储。普通配置、数据库业务字段、日志、API 和 Agent Surface 只保存 opaque `secretRef` 或 configured/missing 状态。

Checkpoint 只使用 strict msgpack 和加密存储；不要恢复 pickle、任意类反序列化或把密钥写回配置文件。

## 5. 核心配置关系

### 5.1 Models

一个可调用模型至少由以下事实组成：

- provider/endpoint identity；
- API channel/protocol；
- provider-native model ID；
- capability（文本、视觉、图片/视频/语音/音乐/3D 等）；
- role binding（Supervisor、subagent、media、embedding 等）。

快捷目录只是填写便利，不覆盖用户已保存的 endpoint 或真实模型。Provider 原生 system/tool/reasoning 合同应尽量保留，不能为兼容 UI 强行扁平化成同一种消息。

### 5.2 Supervisor 与 Agents

Supervisor 的有效上下文来自多源组合：

1. `V8_AGENT_OS.md`；
2. `config.json#supervisor`；
3. 模型 role binding；
4. 当前会话工作模式；
5. Engineering Kernel、插件可用性等运行时动态上下文。

持久 Agent 的创建/校验通过 `agent_broker` 或 Admin 的受治理入口完成。显示名、头像与角色配置不等于工具权限；实际工具面仍按 actor、delegation depth、Capsule 和 grant 计算。

### 5.3 Workspace 与 Projects

工作区绑定包含真实规范化路径和信任状态。项目自定义名称与置顶只属于跨 Web/Phone/桌宠的 presentation，不改变底层路径。

Engineering Kernel 会把绑定工作区和命令环境注入协作角色；不再依赖 `workspace_broker` 重复发现。现有非 Git 工作区如需托管并行写入，必须显式采用；自定义 worktree root 必须与原仓库位于同一卷。

物理路径丢失时，产品面不得假装工作区仍存在或自动重建。历史会话可以保留，但重新进入应明确提示工作区缺失并由用户决定是否删除会话记录。

### 5.4 Engineering Lane

关键策略包括：

- worktree 放置与生命周期；
- sandbox lease 和 network mode；
- proof ledger；
- write set、文件大小和交付验证。

当前 sandbox capability 是 `partial`：有进程树、资源限制、环境 allowlist、路径预检、不可变写集与 Git diff 验证，但没有硬文件系统 namespace 或硬离线网络 namespace。配置和 UI 不得把它升级宣传成 `enforced`。

### 5.5 Plugin Manager 与 Extensions

两者是不同平面：

- Extensions：普通 Skill/MCP 的发现、安全审查和候选预筛；
- Plugin Manager：签名 catalog、组件安装事务、配置需求、授权与精确能力投影。

Plugin Manager 上机发现只读。它可以识别外部 CLI 和官方 Skill，并显示 adopt/install/conflict，但不会接管用户维护的普通 MCP 配置。受审 CLI 执行器只在有效 task grant 投影后出现。

`@插件` 是强提示，不是强制唯一入口。Supervisor 可以为当前 run 创建最小 task grant；持续 session grant 仍由用户显式控制。子 Agent 授权必须绑定精确 delegation identity，组件范围只能缩小，最多传播到一层孙 Agent。

### 5.6 Memory

Memory 是证据层，不是当前指令的替代者。自动提取、手动会话提取、召回、知识图谱与周期维护共享同一长期记忆真相，但候选内容只有在策略通过并真实持久化后才能增长知识/关系计数。

### 5.7 Storage Retention

磁盘治理通过专用 API 修改：

- `/v1/storage-retention/stats`
- `/v1/storage-retention/dry-run`
- `/v1/storage-retention/prune`
- `/v1/storage-retention/compact`
- `/v1/storage-retention/config`

先 dry-run，再执行有副作用的清理。用户可见转录、未接受 worktree、恢复点和用户管理文件不属于普通可丢弃缓存。

## 6. 正确的修改方式

1. 优先在 Admin 中修改；
2. 程序化修改走 Config Registry 或专用治理 API；
3. 修改前保留原子备份，失败时暴露 degraded/rollback 状态；
4. 不在页面里硬编码本机路径；
5. 不把 secret 写进 JSON、日志或测试 fixture；
6. 不从 `*.bak`、cache 或旧 alias 文件反推当前生效值。

排查“保存成功但运行没变”时按顺序检查：

1. API 返回与 Engine 日志；
2. `api/config_registry_routes.py` 或对应专用 route；
3. `core/storage.py` 的规范化/原子写入；
4. 当前运行服务是否重新加载；
5. `~/.v8-agent-os` 中的 canonical source。

## 7. 继续阅读

- [快速入门](./V8_AGENT_OS_QUICK_START_ZH.md)
- [API 参考](./V8_AGENT_OS_API_REFERENCE_ZH.md)
- [开发者指南](./V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md)
- [Managed Engineering Execution](../apps/v8-agent-os-engine/core/engineering_sandbox/README.md)
