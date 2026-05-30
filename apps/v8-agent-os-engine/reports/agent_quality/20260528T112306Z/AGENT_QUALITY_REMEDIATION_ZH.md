# Agent Quality Matrix 整改报告

- 生成时间：20260528T112306Z
- 模型配置：mimo
- 矩阵范围：multi_agent
- 总体状态：需要整改

## P0 门禁

### [P0] Live case 未观察到期望工具

- Case：aq-multi-agent-001
- 现象：未观察到：delegation_broker；实际工具：write_todos, update_todo, fetch_skill_instructions, runtime_broker, research_broker。
- 复现：Session agent-quality-live-20260528T112306Z-aq-multi-agent-001, run run_a9f76704ffbf4cbcab0c73fbaad5108e
- 根因推测：模型未按主链工具面行动，或 runtime events 未正确投影工具调用。
- 涉及模块：api/chat_realtime_routes.py, erc/session_runtime.py, packages/session-realtime
- 推荐修复：核对工具调用事实和投影链；若实际未调用，修 Prompt/tool surface；若已调用未投影，修 runtime event projection。
- 回归测试：agent_quality::multi_agent


## 失败矩阵


## 默认 Pytest 结果

- 退出码：0

```text
...                                                                      [100%]
3 passed in 0.97s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools (all owners) | Forbidden Supervisor seen | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-multi-agent-001 | multi_agent | observed | run_a9f76704ffbf4cbcab0c73fbaad5108e | agent-quality-live-20260528T112306Z-aq-multi-agent-001 | 291 | runtime_broker, delegation_broker | write_todos, update_todo, fetch_skill_instructions, runtime_broker, research_broker | - |  |

## 复现与证据

### aq-multi-agent-001

- Prompt：演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。
- 期望工具：runtime_broker, delegation_broker
- 实际工具（所有 owner）：write_todos, update_todo, fetch_skill_instructions, runtime_broker, research_broker
- 关键事件：message.user.recorded, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, supervisor.graph.diagnostics, extension.route.selected, extension.mcp.candidate_exposed, runtime.gate.decision, memory.evidence.feedback, context.prepared, supervisor.turn.diagnostics, run.reasoning.delta, extension.execution.completed, run.text.delta, tool.started, tool.finished
- Supervisor 实际违规工具：-
- Case 禁用清单：write_native_file, run_system_command
- Run ID：run_a9f76704ffbf4cbcab0c73fbaad5108e
- Session ID：agent-quality-live-20260528T112306Z-aq-multi-agent-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260528T112306Z-aq-multi-agent-001", "conversationId": "agent-quality-live-20260528T112306Z-aq-multi-agent-001", "clientMessageId": "aq-multi-agent-001-20260528T112306Z", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "runId": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "userMessage": {"id": "aq-multi-agent-001-20260528T112306Z", "session_id": "agent-quality-live-20260528T112306Z-aq-multi-agent-001", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-multi-agent-001-20260528T112306Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。\", \"timestamp\": 1779967389473}]", "artifacts_json": "[]", "content_text": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_a9f76704ffbf4cbcab0c73fbaad5108e\", \"runId\": \"run_a9f76704ffbf4cbcab0c73fbaad5108e\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779967389473, \"role\": \"user\", \"clientMessageId\": \"aq-multi-agent-001-20260528T112306Z\", \"taskShapeHint\": {\"primaryTaskShape\": \"research\", \"secondaryTaskShapes\": [], \"confidence\": 0.91, \"reason\": \"research_or_current_source_terms\", \"suggestedFamilies\": [\"research\"], \"optionalRuntimeGrants\": [\"research.core\"], \"familyScores\": {\"research\": 0.91}, \"topFamily\": \"research\", \"scoreMargin\": 0.91, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": true, \"families\": [\"research\"], \"source\": \"task_shape_classifier\", \"reason\": \"high_confidence_single_family\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"research_action:调研\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-28T11:23:09.474Z", "updated_at": "2026-05-28T11:23:09.474Z", "finalized_at": null, "nodes": [{"id": "aq-multi-agent-001-20260528T112306Z:narrative", "kind": "narrative", "role": "user", "content": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "timestamp": 1779967389473}], "artifacts": [], "metadata": {"run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "runId": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779967389473, "role": "user", "clientMessageId": "aq-multi-agent-001-20260528T112306Z", "taskShapeHint": {"primaryTaskShape": "research", "secondaryTaskShapes": [], "confidence": 0.91, "reason": "research_or_current_source_terms", "suggestedFamilies": ["research"], "optionalRuntimeGrants": ["research.core"], "familyScores": {"research": 0.91}, "topFamily": "research", "scoreMargin": 0.91, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": true, "families": ["research"], "source": "task_shape_classifier", "reason": "high_confidence_single_family", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["research_action:调研"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"durableTimelineFallback": true, "durableEventCount": 328, "observationStage": "runtime_events_observed", "events": [{"seq": 304, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 305, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 306, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 307, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 308, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 309, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 310, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 311, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 312, "topic": "run.reasoning.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 313, "topic": "extension.execution.completed", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 314, "topic": "run.text.delta", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 315, "topic": "research.tool.started", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 316, "topic": "research.tool.finished", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 317, "topic": "extension.route.selected", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 318, "topic": "extension.mcp.candidate_exposed", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 319, "topic": "runtime.gate.decision", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 320, "topic": "memory.evidence.feedback", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 321, "topic": "context.prepared", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 322, "topic": "supervisor.turn.diagnostics", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 323, "topic": "run.watchdog.stream_idle_timeout", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 324, "topic": "run.liveness.stalled", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 325, "topic": "run.state.changed", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 326, "topic": "run.failed", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 327, "topic": "run.lane.released", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}, {"seq": 328, "topic": "extension.execution.completed", "run_id": "run_a9f76704ffbf4cbcab0c73fbaad5108e", "summary": "None"}]}
{"runtimeEventCount": 328, "observationStage": "runtime_events_observed", "observedTopics": ["message.user.recorded", "session.connected", "run.lane.acquired", "safety.preflight.checked", "engineering_lane.trigger.decided", "run.created", "run.state.changed", "agent.started", "supervisor.graph.diagnostics", "extension.route.selected", "extension.mcp.candidate_exposed", "runtime.gate.decision", "memory.evidence.feedback", "context.prepared", "supervisor.turn.diagnostics", "run.reasoning.delta", "extension.execution.completed", "run.text.delta", "tool.started", "tool.finished", "extension.skill.blocked", "safety.skill_blocked", "research.tool.started", "research.tool.finished", "run.watchdog.stream_idle_timeout", "run.liveness.stalled", "run.failed", "run.lane.released"], "actualTools": ["write_todos", "update_todo", "fetch_skill_instructions", "runtime_broker", "research_broker"], "eve
```
