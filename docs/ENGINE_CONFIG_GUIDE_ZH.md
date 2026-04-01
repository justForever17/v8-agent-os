# V8 Agent OS - 详细配置指南

V8 Agent OS 的配置系统可能和传统的配置模型有些不同。抛弃寻找零散 JSON 文件的旧思维，这里是当前的唯一配置事实规范。

## 1. 唯一配置真相来源

在 V8 Agent OS 架构下，针对各种分散的子功能选项（从大模型指定再到工作流状态），遵循以下统一的数据链路约束协议：
- **核心数据与持久化目录**：统一驻留在计算机用户的 `~/.v8-agent-os/` 主干下。
- **主要配置的聚合文件**：所有的结构化核心配置信息最终都集中交接到 `~/.v8-agent-os/config.json` 这个单一入口，并且按照不同的“配置域（Domain）”进行组织。

所有的生效参数设定只以此处产生的值为权威结果。

## 2. 核心配置域 (Config Domains)

通过 Admin 面板修改或直接编辑 `config.json`（若重启可直接重载），你会接触以下高频域：

- **`models`**: 
  定义所有对话层和推理层调用的具体 LLM，包含代理使用的角色分配 (`models.roles.supervisor`, `models.roles.default`) 等。千万别忘记配置 Reranker。
- **`mcp`**:
  决定加载哪些 Model Context Protocol (MCP) 服务。配置各类技能或扩展服务的节点与环境变量。
- **`workspace`**:
  当前工程树、白名单允许编辑的目录等。为了安全，不要在工作区允许配置直接指向根系统物理根目录。
- **`supervisor` / `networkSupervisorRuntime`**:
  包含智能体元设定、默认提示词策略池（通常和 `V8_AGENT_OS.md` 内容互为参照映射）。
- **`memory`**:
  长期记忆配置，影响长期存储与上下文读取的能力设定与范围。
- **`music` / `audio`**:
  这里映射相关的音乐和音频参数设置，不要再去修改或读取曾经的独立文件。
- **其他关键域**：`hooks`、`cron`、`automationRuntime`、`runtimeStability`、`safety`、`projects`、`systemBase` 等都在该树下统一收口。

## 3. 独立且重要的周边配置文件

除了 `config.json`，以下由于其敏感性或特定的机器硬件强绑定性，依然暂存于 `~/.v8-agent-os/` 根目录下的独立文件中（非必需不修改）：

- **`network_supervisor_secrets.json`** / **`network_supervisor_state.json`**:
  用来存放高敏感的网络通信令牌、跨端认证 Secret 和 Supervisor 跨端连接状态。
- **`computer_use.json`**:
  记录 Computer Use 模式在操作桌面时的相关参数。
- **`plugin.json`**:
  记录第三方或本地直接安装宿主插件的基础启动信息清单。
- **各类 DB 存储**:
  `state.db`、`checkpoints.db` 保存长对话、图状流树和运行时断点数据。
- **`V8_AGENT_OS.md`**:
  默认的外部引导级 System Prompt / 规范约束说明性模板源。

## 4. 废弃的或临时缓存性质内容 (排障了解即可)

作为开发者和运维者，必须正确区分什么是配置真相、什么仅仅是临时文件的产物：
- `extensions_runtime_cache.json`、`skills_inventory_cache.json` 都是作为计算或同步节点生成的**临时缓存文件**，它们可能面临随时被销毁或自我重置的情况，严格意义上不能作为系统基础设定的依赖源头。
- 绝大部分底层参数均已经被迁移收口进入了 `config.json` 统一管理。我们提倡在代码中若涉及读取配置，一定要分清其落地的实际所属域结构，避免将外部缓存当作内部配置事实读取。

## 5. 最佳实践
**永远优先使用 Admin 控制台(9528)去变更配置**。它会帮你处理格式对齐、旧字段剥离与向 Engine 下发热加载信号。
如在代码层需要读取设定，务必使用 `config_registry_routes.py` 或底层的统一配置读取接口，拒绝 Hardcode 文件路径或直接 `JSON.parse`。
