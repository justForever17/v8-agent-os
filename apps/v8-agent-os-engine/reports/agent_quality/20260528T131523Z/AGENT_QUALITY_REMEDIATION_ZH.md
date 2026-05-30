# Agent Quality Matrix 整改报告

- 生成时间：20260528T131523Z
- 模型配置：mimo
- 矩阵范围：multi_agent
- 总体状态：需要整改

## P0 门禁

### [P0] Live case 未观察到期望工具

- Case：aq-multi-agent-001
- 现象：未观察到：delegation_broker；实际工具：workspace_broker, runtime_broker, write_todos, update_todo, research_broker。
- 复现：Session agent-quality-live-20260528T131523Z-aq-multi-agent-001, run run_4de68af2b4d94a36b1d63f537e4b5be5
- 根因推测：模型未按主链工具面行动，或 runtime events 未正确投影工具调用。
- 涉及模块：api/chat_realtime_routes.py, erc/session_runtime.py, packages/session-realtime
- 推荐修复：核对工具调用事实和投影链；若实际未调用，修 Prompt/tool surface；若已调用未投影，修 runtime event projection。
- 回归测试：agent_quality::multi_agent


## 失败矩阵


## 默认 Pytest 结果

- 退出码：0

```text
...                                                                      [100%]
3 passed in 1.08s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools (all owners) | Forbidden Supervisor seen | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-multi-agent-001 | multi_agent | observed | run_4de68af2b4d94a36b1d63f537e4b5be5 | agent-quality-live-20260528T131523Z-aq-multi-agent-001 | 485 | runtime_broker, delegation_broker | workspace_broker, runtime_broker, write_todos, update_todo, research_broker | - |  |

## 复现与证据

### aq-multi-agent-001

- Prompt：演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。
- 期望工具：runtime_broker, delegation_broker
- 实际工具（所有 owner）：workspace_broker, runtime_broker, write_todos, update_todo, research_broker
- 关键事件：message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.fallback.used, planner.plan.failed, planner.plan.created, supervisor.graph.diagnostics, extension.route.selected, extension.mcp.candidate_exposed, context.prepared, supervisor.turn.diagnostics, run.reasoning.delta, extension.execution.completed
- Supervisor 实际违规工具：-
- Case 禁用清单：write_native_file, run_system_command
- Run ID：run_4de68af2b4d94a36b1d63f537e4b5be5
- Session ID：agent-quality-live-20260528T131523Z-aq-multi-agent-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260528T131523Z-aq-multi-agent-001", "conversationId": "agent-quality-live-20260528T131523Z-aq-multi-agent-001", "clientMessageId": "aq-multi-agent-001-20260528T131523Z", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "runId": "run_4de68af2b4d94a36b1d63f537e4b5be5", "userMessage": {"id": "aq-multi-agent-001-20260528T131523Z", "session_id": "agent-quality-live-20260528T131523Z-aq-multi-agent-001", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-multi-agent-001-20260528T131523Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。\", \"timestamp\": 1779974126482}]", "artifacts_json": "[]", "content_text": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_4de68af2b4d94a36b1d63f537e4b5be5\", \"runId\": \"run_4de68af2b4d94a36b1d63f537e4b5be5\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779974126482, \"role\": \"user\", \"clientMessageId\": \"aq-multi-agent-001-20260528T131523Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"delegation_or_parallel\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\", \"research\", \"delegation\"], \"confidence\": 0.9, \"reason\": \"multi_runtime_orchestration_terms\", \"suggestedFamilies\": [\"engineering\", \"research\"], \"optionalRuntimeGrants\": [\"research.core\", \"delegation.recursive\"], \"familyScores\": {\"engineering\": 0.9, \"research\": 0.45, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.45, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": true, \"families\": [\"engineering\"], \"source\": \"task_shape_classifier\", \"reason\": \"high_confidence_single_family\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:工程\", \"delegation_action:调度\", \"delegation_action:主链调度\", \"delegation_action:子 agent\", \"delegation_action:delegation\", \"delegation_action:child delegation\", \"media_output:调研\", \"research_secondary:调研\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"force\", \"engineeringTriggerDecision\": {\"mode\": \"force\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-28T13:15:26.486Z", "updated_at": "2026-05-28T13:15:26.486Z", "finalized_at": null, "nodes": [{"id": "aq-multi-agent-001-20260528T131523Z:narrative", "kind": "narrative", "role": "user", "content": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "timestamp": 1779974126482}], "artifacts": [], "metadata": {"run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "runId": "run_4de68af2b4d94a36b1d63f537e4b5be5", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779974126482, "role": "user", "clientMessageId": "aq-multi-agent-001-20260528T131523Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["delegation_or_parallel"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media", "research", "delegation"], "confidence": 0.9, "reason": "multi_runtime_orchestration_terms", "suggestedFamilies": ["engineering", "research"], "optionalRuntimeGrants": ["research.core", "delegation.recursive"], "familyScores": {"engineering": 0.9, "research": 0.45, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.45, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": true, "families": ["engineering"], "source": "task_shape_classifier", "reason": "high_confidence_single_family", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:工程", "delegation_action:调度", "delegation_action:主链调度", "delegation_action:子 agent", "delegation_action:delegation", "delegation_action:child delegation", "media_output:调研", "research_secondary:调研"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "force", "engineeringTriggerDecision": {"mode": "force", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"durableTimelineFallback": true, "durableEventCount": 460, "observationStage": "runtime_events_observed", "events": [{"seq": 436, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 437, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 438, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 439, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 440, "topic": "extension.execution.completed", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 441, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 442, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 443, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 444, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 445, "topic": "run.reasoning.delta", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 446, "topic": "research.tool.started", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 447, "topic": "research.tool.finished", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 448, "topic": "extension.route.selected", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 449, "topic": "extension.mcp.candidate_exposed", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 450, "topic": "context.prepared", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 451, "topic": "supervisor.turn.diagnostics", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 452, "topic": "run.watchdog.stream_idle_timeout", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 453, "topic": "run.liveness.stalled", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 454, "topic": "run.state.changed", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 455, "topic": "run.failed", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 456, "topic": "run.lane.released", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "None"}, {"seq": 457, "topic": "engineering.proof.collected", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summary": "工程证明已收集 · failed_due_to_dispatch_error"}, {"seq": 458, "topic": "memory.workflow.episode_extracted", "run_id": "run_4de68af2b4d94a36b1d63f537e4b5be5", "summa
```
