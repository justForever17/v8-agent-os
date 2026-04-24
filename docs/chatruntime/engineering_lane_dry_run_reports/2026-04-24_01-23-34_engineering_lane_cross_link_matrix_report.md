# Engineering Lane Cross-Link Dry-run Matrix Report (2026-04-24_01-23-34)

## 摘要
- Payloads: 11
- Scenarios: total=132, pass=127, warning=5, fail=0
- Findings: P0=0, P1=1, P2=4
- Synthetic prefix: `eng-matrix-2026-04-24_01-23-34-5a57a56e`
- JSON: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\engineering_lane_dry_run_reports\2026-04-24_01-23-34_engineering_lane_cross_link_matrix_results.json`

## Durable Memory 污染检查
- `memory_workflow_candidates`: pre=10, post_before_cleanup=10, post_after_cleanup=10
- `memory_workflow_episodes`: pre=25, post_before_cleanup=25, post_after_cleanup=25
- `memory_workflow_hint_events`: pre=4, post_before_cleanup=4, post_after_cleanup=4
- 结论: PASS，dry-run 未新增 workflow durable memory。

## Synthetic Observation 清理
- `engineering_workset_observations` deleted=0
- `engineering_proof_entries` deleted=0
- `runtime_events` deleted=0
- `run_records` deleted=0
- `messages` deleted=0
- `sessions` deleted=-1

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
| Payload | Trigger active | Reason | Repo | Matrix pass/warn/fail | Workspace |
|---|---:|---|---:|---|---|
| code_fix_auto_repo | True | engineering_signals_and_repo | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| test_debug_auto_repo | True | engineering_signals_and_repo | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| refactor_auto_repo | True | engineering_signals_and_repo | True | 11/1/0 | E:\Projects\v8chat\v8-agent-os |
| docs_only_repo | True | engineering_signals_and_repo | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| review_only_repo | True | engineering_signals_and_repo | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| ordinary_chat_auto_repo | False | no_engineering_signal_or_repo | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| media_request_auto_repo | False | no_engineering_signal_or_repo | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| force_repo | True | request_override_force | True | 11/1/0 | E:\Projects\v8chat\v8-agent-os |
| off_repo | False | request_override_off | True | 12/0/0 | E:\Projects\v8chat\v8-agent-os |
| project_test1_force | True | request_override_force | False | 11/1/0 | E:\Projects\test1 |
| no_repo_force | True | request_override_force | False | 10/2/0 | E:\temp\v8-eng-matrix-no-repo-glhtyydh |

## 发现列表
| Priority | Status | Payload | Group | Scenario | Summary |
|---|---|---|---|---|---|
| P2 | warning | refactor_auto_repo | memory | workflow_hint_eligibility | No ranked workflow path is available; this is acceptable but weakens Phase 6 validation coverage. |
| P2 | warning | force_repo | memory | workflow_hint_eligibility | No ranked workflow path is available; this is acceptable but weakens Phase 6 validation coverage. |
| P2 | warning | project_test1_force | workspace | workspace_scope_truth | Active engineering mode should normally have a detected repo; no-repo is allowed but diagnostic. |
| P2 | warning | no_repo_force | workspace | workspace_scope_truth | Active engineering mode should normally have a detected repo; no-repo is allowed but diagnostic. |
| P1 | warning | no_repo_force | planner | coding_planner_contract | writeSet is missing or empty; broker auto-dispatch should be conservative. |

## 排毒判定
- 存在 P0/P1，需要进入最小修复与复跑。

## 原始 JSON
详见同目录 results JSON。