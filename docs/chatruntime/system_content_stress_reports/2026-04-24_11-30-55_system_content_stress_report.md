# Supervisor/Subagent SYSTEM_CONTENT 压力空运行总报告

## 前后计数快照（确认无学习污染）
```json
{
  "before": {
    "memory_workflow_episodes": 30,
    "memory_workflow_candidates": 10,
    "memory_workflow_hint_events": 4,
    "memory_workflow_guide_states": 4,
    "engineering_proof_entries": 0,
    "engineering_workset_observations": 0
  },
  "after": {
    "memory_workflow_episodes": 30,
    "memory_workflow_candidates": 10,
    "memory_workflow_hint_events": 4,
    "memory_workflow_guide_states": 4,
    "engineering_proof_entries": 0,
    "engineering_workset_observations": 0
  },
  "delta": {
    "memory_workflow_episodes": 0,
    "memory_workflow_candidates": 0,
    "memory_workflow_hint_events": 0,
    "memory_workflow_guide_states": 0,
    "engineering_proof_entries": 0,
    "engineering_workset_observations": 0
  }
}
```

## 三场景 Supervisor 对比
| 场景 | 总 Tokens | 工程块 | Planner | Network Context | External App Instr | Workflow Hints | Workspace Rules | 最大模块 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 通用日常聊天 | 4839 | N | N | N | N | N | Y | specialist agent registry (1019) |
| 项目编程 | 5485 | Y | Y | N | N | N | Y | specialist agent registry (1019) |
| Network API | 5173 | N | N | Y | N | N | Y | specialist agent registry (1019) |

## 三场景 Subagent 对比
| 场景 | 选中 Subagent | 总 Tokens | Delegated Plan | Active Plan | Network Context | Extensions Route | 最大模块 |
|---|---|---:|---:|---:|---:|---:|---|
| 通用日常聊天 | Docs Delivery Writer | 1865 | Y | N | N | Y | extensions runtime route block (922) |
| 项目编程 | Implementation Engineer | 2364 | Y | Y | N | Y | extensions runtime route block (898) |
| Network API | Research Synthesizer | 1620 | Y | N | N | Y | extensions runtime route block (654) |

## 各模块 Estimated Token 排名（Top 20）
| 场景 | 角色 | 模块 | Tokens |
|---|---|---|---:|
| daily_chat | supervisor | specialist agent registry | 1019 |
| project_coding | supervisor | specialist agent registry | 1019 |
| network_api | supervisor | specialist agent registry | 1019 |
| daily_chat | supervisor | extensions runtime route block | 922 |
| daily_chat | subagent | extensions runtime route block | 922 |
| project_coding | subagent | extensions runtime route block | 898 |
| project_coding | supervisor | extensions runtime route block | 866 |
| network_api | supervisor | direct tool registry | 775 |
| daily_chat | supervisor | direct tool registry | 757 |
| project_coding | supervisor | direct tool registry | 757 |
| daily_chat | supervisor | base prompt / system persona | 682 |
| project_coding | supervisor | base prompt / system persona | 682 |
| network_api | supervisor | base prompt / system persona | 682 |
| network_api | supervisor | extensions runtime route block | 654 |
| network_api | subagent | extensions runtime route block | 654 |
| network_api | supervisor | runtime registry / capability registry | 588 |
| daily_chat | supervisor | runtime registry / capability registry | 586 |
| project_coding | supervisor | runtime registry / capability registry | 586 |
| project_coding | subagent | engineering context | 553 |
| project_coding | subagent | planner context / delegated task plan | 548 |

## 可疑毒点清单
- [fact] 外部系统指令停留在输入消息而非 system_content：当前 network api 主链会把 [EXTERNAL APP INSTRUCTIONS] 保留在标准化输入消息中，而不是放入 supervisor system_content。
- [warning] workspace-less Network API 仍带入本机 memory / workspace 模块：当前 network api workspace-less 场景仍注入了 workspace rules 与 memory summary/map，说明 compat 支线还没有完全从本机默认工作区语义中摘离。

## 现状事实摘要
- 本轮直接复用真实 builder/route/agent 选择链，不调用模型，不执行工具。
- 为避免污染 state.db，workflow hint 的 guide state / hint event 写入与 active todos 落盘在导出进程内被临时熔断为 no-op。
- `system_content` 快照使用当前仓内真实配置与本机真实项目/workspace 真相。