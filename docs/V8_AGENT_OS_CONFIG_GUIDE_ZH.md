# V8 Agent OS 配置指南（项目级）

本文描述当前项目级配置真相，而不是历史零散 JSON 的旧口径。

---

## 1. 当前唯一主配置真相

当前主配置真相是：

- `~/.v8-agent-os/config.json`

请优先按“配置域（domain）”理解系统，而不是背旧文件名。

最常用配置域：

- `models`
- `mcp`
- `memory`
- `supervisor`
- `workspace`
- `projects`
- `hooks`
- `cron`
- `automationRuntime`
- `networkSupervisorRuntime`
- `audio`
- `runtimeStability`
- `safety`
- `runtimeRegistry`
- `systemBase`
- `extensions`
- `computerUse`

---

## 2. 仍然独立存在的关键文件

以下文件仍可能是有效真相的一部分：

- `~/.v8-agent-os/users.json`
- `~/.v8-agent-os/V8_AGENT_OS.md`
- `~/.v8-agent-os/state.db`
- `~/.v8-agent-os/checkpoints.db`
- `~/.v8-agent-os/plugin.json`
- `~/.v8-agent-os/computer_use.json`
- `~/.v8-agent-os/network_supervisor_secrets.json`
- `~/.v8-agent-os/network_supervisor_state.json`

理解方式：

- `config.json` 是结构化配置主干
- 上述文件是独立敏感面、数据库面或运行时根级文件

---

## 3. 不应再当真相的内容

下列内容默认不应再被当作配置真相：

- `~/.v8chat/*`
- `backups/*`
- `_legacy_config_backup/*`
- `logs/*`
- `cache/*`
- `extensions_runtime_cache.json`
- `skills_inventory_cache.json`
- 各类 `*.bak`

`~/.v8chat` 现在应视为：

1. 迁移输入
2. 历史残留
3. 排障线索

而不是当前 canonical source。

---

## 4. 配置读取的正确入口

遇到“配置没生效 / 页面显示和运行不一致 / 字段改了但 runtime 没变”时，优先排查：

1. `apps/v8-agent-os-engine/core/storage.py`
2. `apps/v8-agent-os-engine/api/config_registry_routes.py`
3. `apps/v8-agent-os-admin/src/lib/server/bridge-config.ts`
4. 本机 `~/.v8-agent-os/config.json`

不要先根据页面默认值或旧文案推断真实生效源。

---

## 5. workspace / project workspace

当前关于工作区的固定规则：

1. main workspace 与 project workspace 都应进入统一的 scoped resolver
2. `share_workspace_file` 是主动分享资源的推荐主链
3. 裸绝对路径不是远端可预览真相
4. `channel_delivery_stage` 不属于 main/project workspace plane

因此：

- 本地路径可以作为内部审计路径
- 远端 surface 必须消费资源化后的 URL / resourceRef

---

## 6. memory / safety / supervisor

### 6.1 `memory`

当前 memory 配置主要落在：

- `config.json#memory`

包括：

- durable memory 写入门槛
- preference / knowledge / graph 相关阈值
- reranker / embedding / extraction 行为的上层配置

### 6.2 `safety`

治理配置主要落在：

- `config.json#safety`

但最终是否 review / block / allow，仍要回到 engine runtime 主链。

### 6.3 `supervisor`

当前 supervisor 是多源 canonical surface：

1. `V8_AGENT_OS.md`
2. `config.json#supervisor`
3. `config.json#models.roles.supervisor`
4. `config.json#models.roles.default`
5. `config.json#systemBase.identity`

不要把其中某一个片段误认为唯一真相。

---

## 7. Surface 与配置关系

当前项目中：

- `os-phone` 是主远端 surface
- `os-web` 是备用 surface

这意味着：

1. 新配置首先要保证 Engine/ Admin/ shared contract 一致
2. Phone 是主验收面
3. Web 可以共用 contract，但不应再主导配置口径

---

## 8. 推荐操作方式

1. 优先通过 Admin 修改配置
2. 需要代码读取配置时，走统一 registry / storage 映射
3. 不要在页面层直接 hardcode 配置文件路径
4. 不要从历史 alias 文件名反推当前真相

---

## 9. 继续阅读

1. [V8 Agent OS 快速入门](./V8_AGENT_OS_QUICK_START_ZH.md)
2. [V8 Agent OS 开发者指南](./V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md)
3. [V8 Agent OS API 参考](./V8_AGENT_OS_API_REFERENCE_ZH.md)

