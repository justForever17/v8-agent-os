# Engine Tests 目录说明

`tests/` 是 Engine 的内部验证与诊断目录，默认不作为 Admin 普通用户面或公开文档入口。

## 边界规则

- `tests/`：放 pytest、fixtures、内部 eval、一次性诊断与可复跑评估脚本。
- `tests/scripts/`：放开发者手动执行的内部诊断脚本，例如上下文评估、记忆能力评估、session replay、smoke probe。
- `scripts/`：只保留运行时或控制面需要直接调用的脚本，例如 cron 入口、Admin 明确提供的测试入口或外部兼容 wrapper。
- 如果一个脚本开始被 Admin 页面调用，需要从 `tests/scripts/` 移回 `scripts/`，并补清楚调用契约。

## 常用命令

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests
```

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\export_memory_capability_assessment.py
```

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\export_context_management_assessment.py
```

## 维护纪律

- 内部评估脚本不得写入真实用户 durable memory，除非脚本名、README 和运行参数明确说明。
- 新增 eval 请优先放到 `tests/evals/`，并在该目录 README 中补索引。
- 新增 fixture 请放到 `tests/fixtures/`，不要依赖本机私有路径作为唯一真相。
- 迁移脚本后必须检查 `ENGINE_ROOT`、复跑命令、runbook 与报告模板里的路径。
