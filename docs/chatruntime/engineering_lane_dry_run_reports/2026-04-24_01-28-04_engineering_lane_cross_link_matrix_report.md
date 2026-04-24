# Engineering Lane Cross-Link Dry-run Matrix Report (2026-04-24_01-28-04)

## 摘要
- Payloads: 11
- Scenarios: total=132, pass=127, warning=5, fail=0
- Findings: P0=0, P1=0, P2=5
- Synthetic prefix: `eng-matrix-2026-04-24_01-28-04-a1e64da7`
- JSON: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\engineering_lane_dry_run_reports\2026-04-24_01-28-04_engineering_lane_cross_link_matrix_results.json`

## Durable Memory 污染检查
- `memory_workflow_candidates`: pre=10, post_before_cleanup=10, post_after_cleanup=10
- `memory_workflow_episodes`: pre=30, post_before_cleanup=30, post_after_cleanup=30
- `memory_workflow_hint_events`: pre=4, post_before_cleanup=4, post_after_cleanup=4
- 结论: PASS，dry-run 未新增 workflow durable memory。

## Synthetic Observation 回收检查
- `engineering_workset_observations`: pre=0, post_before_cleanup=154, post_after_cleanup=0, deleted=154
- `engineering_proof_entries`: pre=0, post_before_cleanup=0, post_after_cleanup=0, deleted=0
- 结论: PASS，synthetic dry-run 记录已完整回收。

## 分组统计
| Group | Total | Pass | Warning | Fail |
|---|---:|---:|---:|---:|
| broker | 22 | 22 | 0 | 0 |
| memory | 22 | 20 | 2 | 0 |
| phase6_learning | 11 | 11 | 0 | 0 |
| planner | 11 | 10 | 1 | 0 |
| proof | 22 | 22 | 0 | 0 |
| runtime_lane | 11 | 11 | 0 | 0 |
| trigger | 22 | 22 | 0 | 0 |
| workspace | 11 | 9 | 2 | 0 |

## Payload 结果
| Payload | Trigger active | Reason | Repo | Matrix pass/warn/fail | Persisted observations | Workspace |
|---|---:|---|---:|---|---:|---|
| code_fix_auto_repo | True | engineering_signals_and_repo | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| test_debug_auto_repo | True | engineering_signals_and_repo | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| refactor_auto_repo | True | engineering_signals_and_repo | True | 11/1/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| docs_only_repo | True | engineering_signals_and_repo | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| review_only_repo | True | engineering_signals_and_repo | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| ordinary_chat_auto_repo | False | no_engineering_signal_or_repo | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| media_request_auto_repo | False | no_engineering_signal_or_repo | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| force_repo | True | request_override_force | True | 11/1/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| off_repo | False | request_override_off | True | 12/0/0 | 28 | E:\Projects\v8chat\v8-agent-os |
| project_test1_force | True | request_override_force | False | 11/1/0 | 28 | E:\Projects\test1 |
| no_repo_force | True | request_override_force | False | 10/2/0 | 28 | E:\temp\v8-eng-matrix-no-repo-lwjoc6jr |

## 发现列表
| Priority | Status | Payload | Group | Scenario | Summary |
|---|---|---|---|---|---|
| P2 | warning | refactor_auto_repo | memory | workflow_hint_eligibility | No ranked workflow path is available; this is acceptable but weakens Phase 6 validation coverage. |
| P2 | warning | force_repo | memory | workflow_hint_eligibility | No ranked workflow path is available; this is acceptable but weakens Phase 6 validation coverage. |
| P2 | warning | project_test1_force | workspace | workspace_scope_truth | Active engineering mode should normally have a detected repo; no-repo is allowed but diagnostic. |
| P2 | warning | no_repo_force | workspace | workspace_scope_truth | Active engineering mode should normally have a detected repo; no-repo is allowed but diagnostic. |
| P2 | warning | no_repo_force | planner | coding_planner_contract | writeSet is missing or empty; broker auto-dispatch should be conservative. |

## 排毒判定
- 未发现 P0/P1。剩余 warning 为预期或低风险诊断信号。

## 原始 JSON
详见同目录 results JSON。