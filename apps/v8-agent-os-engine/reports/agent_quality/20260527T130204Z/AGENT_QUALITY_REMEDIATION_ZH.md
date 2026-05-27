# Agent Quality Matrix 整改报告

- 生成时间：20260527T130204Z
- 模型配置：mimo
- 矩阵范围：multi_agent
- 总体状态：需要整改

## P0 门禁

### [P0] Live case 未观察到期望工具

- Case：aq-multi-agent-001
- 现象：未观察到：delegation_broker；实际工具：runtime_broker(auto_episode_route), write_todos。
- 复现：Session agent-quality-live-20260527T130204Z-aq-multi-agent-001, run run_ae85b8b91ffb467c9ac0b4c9907d5bd0
- 根因推测：模型未按主链工具面行动，或 runtime events 未正确投影工具调用。
- 涉及模块：api/chat_realtime_routes.py, erc/session_runtime.py, packages/session-realtime
- 推荐修复：核对工具调用事实和投影链；若实际未调用，修 Prompt/tool surface；若已调用未投影，修 runtime event projection。
- 回归测试：agent_quality::multi_agent


## 失败矩阵


## 默认 Pytest 结果

- 退出码：0

```text
..                                                                       [100%]
2 passed in 1.24s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools | Forbidden tools | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-multi-agent-001 | multi_agent | observed | run_ae85b8b91ffb467c9ac0b4c9907d5bd0 | agent-quality-live-20260527T130204Z-aq-multi-agent-001 | 511 | runtime_broker, delegation_broker | runtime_broker(auto_episode_route), write_todos | write_native_file, run_system_command |  |

## 复现与证据

### aq-multi-agent-001

- Prompt：演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。
- 期望工具：runtime_broker, delegation_broker
- 实际工具：runtime_broker(auto_episode_route), write_todos
- 关键事件：message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.fallback.used, planner.plan.failed, planner.plan.created, supervisor.graph.diagnostics, runtime.episode.queued, runtime.episode.progress, runtime.episode.started, runtime.episode.completed, extension.route.selected, extension.mcp.candidate_exposed
- 禁止工具：write_native_file, run_system_command
- Run ID：run_ae85b8b91ffb467c9ac0b4c9907d5bd0
- Session ID：agent-quality-live-20260527T130204Z-aq-multi-agent-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260527T130204Z-aq-multi-agent-001", "conversationId": "agent-quality-live-20260527T130204Z-aq-multi-agent-001", "clientMessageId": "aq-multi-agent-001-20260527T130204Z", "run_id": "run_ae85b8b91ffb467c9ac0b4c9907d5bd0", "runId": "run_ae85b8b91ffb467c9ac0b4c9907d5bd0", "userMessage": {"id": "aq-multi-agent-001-20260527T130204Z", "session_id": "agent-quality-live-20260527T130204Z-aq-multi-agent-001", "run_id": "run_ae85b8b91ffb467c9ac0b4c9907d5bd0", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-multi-agent-001-20260527T130204Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。\", \"timestamp\": 1779886929051}]", "artifacts_json": "[]", "content_text": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_ae85b8b91ffb467c9ac0b4c9907d5bd0\", \"runId\": \"run_ae85b8b91ffb467c9ac0b4c9907d5bd0\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779886929051, \"role\": \"user\", \"clientMessageId\": \"aq-multi-agent-001-20260527T130204Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"delegation_or_parallel\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\", \"research\"], \"confidence\": 0.74, \"reason\": \"engineering_action_terms\", \"suggestedFamilies\": [\"engineering\", \"research\"], \"optionalRuntimeGrants\": [\"research.core\"], \"familyScores\": {\"engineering\": 0.74, \"research\": 0.45, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.29, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:workspace\", \"media_output:调研\", \"research_secondary:调研\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"force\", \"engineeringTriggerDecision\": {\"mode\": \"force\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-27T13:02:09.054Z", "updated_at": "2026-05-27T13:02:09.054Z", "finalized_at": null, "nodes": [{"id": "aq-multi-agent-001-20260527T130204Z:narrative", "kind": "narrative", "role": "user", "content": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "timestamp": 1779886929051}], "artifacts": [], "metadata": {"run_id": "run_ae85b8b91ffb467c9ac0b4c9907d5bd0", "runId": "run_ae85b8b91ffb467c9ac0b4c9907d5bd0", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779886929051, "role": "user", "clientMessageId": "aq-multi-agent-001-20260527T130204Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["delegation_or_parallel"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media", "research"], "confidence": 0.74, "reason": "engineering_action_terms", "suggestedFamilies": ["engineering", "research"], "optionalRuntimeGrants": ["research.core"], "familyScores": {"engineering": 0.74, "research": 0.45, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.29, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:workspace", "media_output:调研", "research_secondary:调研"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "force", "engineeringTriggerDecision": {"mode": "force", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["completed"], "handoffKinds": ["engineering_patch_bundle", "research_evidence_bundle"]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["completed"], "handoffKi
```
