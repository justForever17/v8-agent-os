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
| daily_chat | supervisor | group moderation / execution hints | 1008 |
| project_coding | supervisor | group moderation / execution hints | 951 |
| daily_chat | supervisor | extensions runtime route block | 922 |
| daily_chat | subagent | extensions runtime route block | 922 |
| project_coding | subagent | extensions runtime route block | 898 |
| project_coding | supervisor | extensions runtime route block | 866 |
| network_api | supervisor | direct tool registry | 775 |
| daily_chat | supervisor | direct tool registry | 757 |
| project_coding | supervisor | direct tool registry | 757 |
| network_api | supervisor | group moderation / execution hints | 740 |
| daily_chat | supervisor | base prompt / system persona | 682 |
| project_coding | supervisor | base prompt / system persona | 682 |
| network_api | supervisor | base prompt / system persona | 682 |
| network_api | supervisor | extensions runtime route block | 654 |
| network_api | subagent | extensions runtime route block | 654 |
| network_api | supervisor | runtime registry / capability registry | 588 |
| daily_chat | supervisor | runtime registry / capability registry | 586 |

## 可疑毒点清单
- 未发现明确的模块串味或异常膨胀。

## 现状事实摘要
- 本轮直接复用真实 builder/route/agent 选择链，不调用模型，不执行工具。
- 为避免污染 state.db，workflow hint 的 guide state / hint event 写入与 active todos 落盘在导出进程内被临时熔断为 no-op。
- `system_content` 快照使用当前仓内真实配置与本机真实项目/workspace 真相。