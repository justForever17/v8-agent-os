# 评估诊断复跑说明

## 目标

这份说明用于复跑两类治理报告：

- 超长上下文管理评估
- 记忆能力双轨评分与苛刻考题矩阵

它们都只读取当前主链代码、本机 `~/.v8-agent-os` 配置与现有 runtime truth：

- 不调用真实模型进行任务执行
- 不执行外部工具
- 不写 durable memory
- 不生成 workflow 学习结果

## 运行命令

### 1. 超长上下文管理评估

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe `
  E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\scripts\export_context_management_assessment.py
```

输出目录：

- `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\context_management_reports\`

### 2. 记忆能力双轨评分

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe `
  E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\scripts\export_memory_capability_assessment.py
```

输出目录：

- `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\memory_capability_reports\`

## 建议复验顺序

1. 先跑 `pytest` 守门规则
2. 再跑两份报告脚本
3. 最后检查 Markdown 与 JSON 是否同步

## 配套守门测试

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest `
  E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\tests\test_prompt_budget_governance.py `
  E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\tests\test_context_orchestrator_governance.py `
  E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\tests\test_memory_runtime_durable_policy.py -q
```

## 说明

- 报告里的“对外 benchmark 映射分”不是官方 leaderboard 分数，而是基于当前主链事实给出的映射评分。
- 报告里的“内部 runtime-first 苛刻治理分”更偏保守，用来暴露真实毒点与治理缺口。
- 如果本机模型窗口、memory 阈值或 projects/workspace 绑定发生变化，报告结果也会跟着变化；这是预期行为。
