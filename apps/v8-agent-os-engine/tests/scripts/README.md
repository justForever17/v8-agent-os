# Internal Diagnostic Scripts

这里存放 Engine 内部开发/评测用脚本。它们不是 Admin 普通页面的功能入口，也不应被公开站点直接引用。

## 当前脚本

- `export_context_management_assessment.py`：导出超长上下文管理评估报告。
- `export_memory_capability_assessment.py`：导出记忆能力双轨评分与评测报告。
- `probe_memory_durable_thresholds.py`：探测 durable memory 阈值与写入行为。
- `replay_memory_session.py`：按 fixture 回放 memory session。
- `seed_phone_file_preview_session.py`：为 phone 文件预览链路准备内部 smoke session。
- `smoke_openai_compat.ps1`：OpenAI compat smoke 诊断脚本。

## 与 `engine/scripts` 的区别

- 如果脚本服务 runtime cron、Admin 按钮、外部兼容命令或部署流程，应放在 `apps/v8-agent-os-engine/scripts/`。
- 如果脚本只用于开发者复验、内部评估、临时诊断或非公开 benchmark，应放在本目录。

移动脚本时要同步更新：

- 脚本里的 `ENGINE_ROOT` 计算。
- `docs/chatruntime/*RUNBOOK*.md` 中的复跑命令。
- 脚本输出报告里的自引用命令。
