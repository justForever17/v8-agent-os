# Agent Quality Matrix 整改报告

- 生成时间：20260528T191623Z
- 模型配置：mimo
- 矩阵范围：context
- 总体状态：需要整改

## P0 门禁

- 未发现 route → episode → runner → handoff 的 P0 门禁失败。

## 失败矩阵

### [P1] Live case 未观察到期望工具

- Case：aq-context-queue-001
- 现象：未观察到：memory_broker；实际工具：workspace_broker, grep_search, read_native_file。
- 复现：Session agent-quality-live-20260528T191623Z-aq-context-queue-001, run run_0f3bd2504e4e4f2eafdd9a9dccf0b823
- 根因推测：模型未按主链工具面行动，或 runtime events 未正确投影工具调用。
- 涉及模块：api/chat_realtime_routes.py, erc/session_runtime.py, packages/session-realtime
- 推荐修复：核对工具调用事实和投影链；若实际未调用，修 Prompt/tool surface；若已调用未投影，修 runtime event projection。
- 回归测试：agent_quality::context


## 默认 Pytest 结果

- 退出码：0

```text
...                                                                      [100%]
3 passed in 5.46s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools (all owners) | Forbidden Supervisor seen | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-context-queue-001 | context | observed | run_0f3bd2504e4e4f2eafdd9a9dccf0b823 | agent-quality-live-20260528T191623Z-aq-context-queue-001 | 275 | memory_broker | workspace_broker, grep_search, read_native_file | - |  |

## 复现与证据

### aq-context-queue-001

- Prompt：继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。
- 期望工具：memory_broker
- 实际工具（所有 owner）：workspace_broker, grep_search, read_native_file
- 关键事件：message.user.recorded, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, supervisor.graph.diagnostics, extension.route.selected, runtime.reflex.decision, memory.evidence.feedback, context.prepared, supervisor.turn.diagnostics, run.reasoning.delta, extension.execution.completed, tool.started, tool.finished, run.text.delta, run.text_stream.diagnostics
- Supervisor 实际违规工具：-
- Case 禁用清单：-
- Run ID：run_0f3bd2504e4e4f2eafdd9a9dccf0b823
- Session ID：agent-quality-live-20260528T191623Z-aq-context-queue-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260528T191623Z-aq-context-queue-001", "conversationId": "agent-quality-live-20260528T191623Z-aq-context-queue-001", "clientMessageId": "aq-context-queue-001-20260528T191623Z", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "runId": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "userMessage": {"id": "aq-context-queue-001-20260528T191623Z", "session_id": "agent-quality-live-20260528T191623Z-aq-context-queue-001", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-context-queue-001-20260528T191623Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。\", \"timestamp\": 1779995791683}]", "artifacts_json": "[]", "content_text": "继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_0f3bd2504e4e4f2eafdd9a9dccf0b823\", \"runId\": \"run_0f3bd2504e4e4f2eafdd9a9dccf0b823\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779995791683, \"role\": \"user\", \"clientMessageId\": \"aq-context-queue-001-20260528T191623Z\", \"taskShapeHint\": {\"primaryTaskShape\": \"writing\", \"secondaryTaskShapes\": [], \"confidence\": 0.7, \"reason\": \"writing_or_document_terms\", \"suggestedFamilies\": [\"writing\"], \"optionalRuntimeGrants\": [], \"familyScores\": {\"writing\": 0.7}, \"topFamily\": \"writing\", \"scoreMargin\": 0.7, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"writing_action:说明\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-28T19:16:31.685Z", "updated_at": "2026-05-28T19:16:31.685Z", "finalized_at": null, "nodes": [{"id": "aq-context-queue-001-20260528T191623Z:narrative", "kind": "narrative", "role": "user", "content": "继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。", "timestamp": 1779995791683}], "artifacts": [], "metadata": {"run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "runId": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779995791683, "role": "user", "clientMessageId": "aq-context-queue-001-20260528T191623Z", "taskShapeHint": {"primaryTaskShape": "writing", "secondaryTaskShapes": [], "confidence": 0.7, "reason": "writing_or_document_terms", "suggestedFamilies": ["writing"], "optionalRuntimeGrants": [], "familyScores": {"writing": 0.7}, "topFamily": "writing", "scoreMargin": 0.7, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["writing_action:说明"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"durableTimelineFallback": true, "durableEventCount": 490, "observationStage": "runtime_events_observed", "events": [{"seq": 466, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 467, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 468, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 469, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 470, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 471, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 472, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 473, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 474, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 475, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 476, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 477, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 478, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 479, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 480, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 481, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 482, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 483, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 484, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 485, "topic": "extension.execution.completed", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 486, "topic": "run.text.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 487, "topic": "run.text_stream.diagnostics", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 488, "topic": "run.state.changed", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 489, "topic": "run.completed", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 490, "topic": "run.lane.released", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}]}
{"runtimeEventCount": 490, "observationStage": "runtime_events_observed", "observedTopics": ["message.user.recorded", "session.connected", "run.lane.acquired", "safety.preflight.checked", "engineering_lane.trigger.decided", "run.created", "run.state.changed", "agent.started", "supervisor.graph.diagnostics", "extension.route.selected", "runtime.reflex.decision", "memory.evidence.feedback", "context.prepared", "supervisor.turn.diagnostics", "run.reasoning.delta", "extension.execution.completed", "tool.started", "tool.finished", "run.text.delta", "run.text_stream.diagnostics", "run.completed", "run.lane.released"], "actualTools": ["workspace_broker", "grep_search", "read_native_file"], "events": [{"seq": 466, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 467, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 468, "topic": "run.reasoning.delta", "run_id": "run_0f3bd2504e4e4f2eafdd9a9dccf0b823", "summary": "None"}, {"seq": 469, "topic": "run.reasoning.delta"
```
