# Agent Quality Matrix 整改报告

- 生成时间：20260528T193453Z
- 模型配置：mimo
- 矩阵范围：context
- 总体状态：通过

## P0 门禁

- 未发现 route → episode → runner → handoff 的 P0 门禁失败。

## 失败矩阵

- 默认 fixture/mock 矩阵未发现失败。

## 默认 Pytest 结果

- 退出码：0

```text
.....                                                                    [100%]
5 passed in 5.73s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools (all owners) | Forbidden Supervisor seen | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-context-queue-001 | context | observed | run_8d29bba379d44c5694f7fc037f9c2947 | agent-quality-live-20260528T193453Z-aq-context-queue-001 | 277 | memory_broker | memory_broker, tool_observation_detail, workspace_broker | - |  |

## 复现与证据

### aq-context-queue-001

- Prompt：继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。
- 期望工具：memory_broker
- 实际工具（所有 owner）：memory_broker, tool_observation_detail, workspace_broker
- 关键事件：message.user.recorded, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, supervisor.graph.diagnostics, extension.route.selected, runtime.reflex.decision, memory.evidence.feedback, context.prepared, supervisor.turn.diagnostics, run.reasoning.delta, extension.execution.completed, tool.started, tool.finished, ask_user.tool_result.unmatched, run.text.delta
- Supervisor 实际违规工具：-
- Case 禁用清单：-
- Run ID：run_8d29bba379d44c5694f7fc037f9c2947
- Session ID：agent-quality-live-20260528T193453Z-aq-context-queue-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260528T193453Z-aq-context-queue-001", "conversationId": "agent-quality-live-20260528T193453Z-aq-context-queue-001", "clientMessageId": "aq-context-queue-001-20260528T193453Z", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "runId": "run_8d29bba379d44c5694f7fc037f9c2947", "userMessage": {"id": "aq-context-queue-001-20260528T193453Z", "session_id": "agent-quality-live-20260528T193453Z-aq-context-queue-001", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-context-queue-001-20260528T193453Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。\", \"timestamp\": 1779996902426}]", "artifacts_json": "[]", "content_text": "继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_8d29bba379d44c5694f7fc037f9c2947\", \"runId\": \"run_8d29bba379d44c5694f7fc037f9c2947\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779996902426, \"role\": \"user\", \"clientMessageId\": \"aq-context-queue-001-20260528T193453Z\", \"taskShapeHint\": {\"primaryTaskShape\": \"writing\", \"secondaryTaskShapes\": [], \"confidence\": 0.7, \"reason\": \"writing_or_document_terms\", \"suggestedFamilies\": [\"writing\"], \"optionalRuntimeGrants\": [], \"familyScores\": {\"writing\": 0.7}, \"topFamily\": \"writing\", \"scoreMargin\": 0.7, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"writing_action:说明\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-28T19:35:02.429Z", "updated_at": "2026-05-28T19:35:02.429Z", "finalized_at": null, "nodes": [{"id": "aq-context-queue-001-20260528T193453Z:narrative", "kind": "narrative", "role": "user", "content": "继续上一轮上下文：说明当前工作区和队列消息应该如何保持在同一个 session。", "timestamp": 1779996902426}], "artifacts": [], "metadata": {"run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "runId": "run_8d29bba379d44c5694f7fc037f9c2947", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779996902426, "role": "user", "clientMessageId": "aq-context-queue-001-20260528T193453Z", "taskShapeHint": {"primaryTaskShape": "writing", "secondaryTaskShapes": [], "confidence": 0.7, "reason": "writing_or_document_terms", "suggestedFamilies": ["writing"], "optionalRuntimeGrants": [], "familyScores": {"writing": 0.7}, "topFamily": "writing", "scoreMargin": 0.7, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["writing_action:说明"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"runtimeEventCount": 633, "observationStage": "runtime_events_observed", "observedTopics": ["message.user.recorded", "session.connected", "run.lane.acquired", "safety.preflight.checked", "engineering_lane.trigger.decided", "run.created", "run.state.changed", "agent.started", "supervisor.graph.diagnostics", "extension.route.selected", "runtime.reflex.decision", "memory.evidence.feedback", "context.prepared", "supervisor.turn.diagnostics", "run.reasoning.delta", "extension.execution.completed", "tool.started", "tool.finished", "ask_user.tool_result.unmatched", "run.text.delta", "run.text_stream.diagnostics", "run.completed", "run.lane.released"], "actualTools": ["memory_broker", "tool_observation_detail", "workspace_broker"], "events": [{"seq": 609, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 610, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 611, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 612, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 613, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 614, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 615, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 616, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 617, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 618, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 619, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 620, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 621, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 622, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 623, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 624, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 625, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 626, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 627, "topic": "run.reasoning.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 628, "topic": "extension.execution.completed", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 629, "topic": "run.text.delta", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 630, "topic": "run.text_stream.diagnostics", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 631, "topic": "run.state.changed", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 632, "topic": "run.completed", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}, {"seq": 633, "topic": "run.lane.released", "run_id": "run_8d29bba379d44c5694f7fc037f9c2947", "summary": "None"}]}
{"liveRunTerminal": true, "liveRunFacts": {"run": {"runId": "run_8d29bba379d44c5694f7fc037f9c2947", "status": "completed", "finishedAt": "2026-05-28T19:37:29.725Z", "errorMessage": null}, "activeEpisodes": [], "episodeStates": []}}
```
