# Engine 开发者指南

这份指南写给当前要维护 Engine 的开发者。

## 先记住这一句

Engine 是执行平面。

如果你不确定某个改动该放在哪里，就用这个规则：

- runtime 逻辑放 Engine
- UI 放 Web 或 Admin

## 当前目录怎么理解

请把 `ENGINE_CORE_DIRECTORY_GUIDE.md` 当成当前 canonical 目录导览。

旧的平铺 import 只算兼容壳，不再是新增代码的优先位置。

## 改 runtime 主链前先问自己

如果你改到这些区域：

- `erc/*`
- `runtimes/*`
- `graph/*`
- `core/action_executor.py`
- `core/plugin_host/*`

先回答这 4 个问题：

1. 这段行为属于哪个 runtime？
2. 它有没有继续走统一 runtime 主链？
3. event、ledger、snapshot、approval 会不会漂？
4. 中断后还能不能恢复？

## 当前配置真相源

优先看：

- `~/.v8-agent-os/config.json`
- `~/.v8-agent-os/V8_AGENT_OS.md`
- `~/.v8-agent-os/plugin.json`
- `~/.v8-agent-os/computer_use.json`
- `~/.v8-agent-os/users.json`（仅 Admin 用户与认证数据）

`~/.v8chat/` 只应视为迁移来源或历史归档，不再是当前真相源。

多数结构化配置已归一到 `config.json` 的域中，例如：

- `config.json#models`
- `config.json#supervisor`
- `config.json#music`
- `config.json#runtimeStability`
- `config.json#systemBase`

不要因为看到 `settings.json`、`models.json`、`music.json`、`*_config.json` 这类旧名字，就把它们当成当前运行时事实。

## 稳定本地验证

如果任务涉及长任务、恢复或审批：

- 不要优先用 `--reload`
- 用本地 `.venv`
- 尽量用 prod-like 启动

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\runtime\run_engine_prod_like.ps1
```

## 建议继续读

- [仓库 README](../README-ZH.md)
- [Engine API 参考](./ENGINE_API_REFERENCE.md)
- [Engine Core 目录导览](./ENGINE_CORE_DIRECTORY_GUIDE.md)

## 文档规则

更新这个仓库里的文档时，请保持：

- 面向读者
- 只讲当前事实
- 少写不会帮助决策的历史绕路
