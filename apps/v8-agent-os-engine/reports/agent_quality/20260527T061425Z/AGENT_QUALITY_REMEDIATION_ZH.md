# Agent Quality Matrix 整改报告

- 生成时间：20260527T061425Z
- 模型配置：mimo
- 矩阵范围：tool
- 总体状态：通过

## P0 门禁

- 未发现 route → episode → runner → handoff 的 P0 门禁失败。

## 失败矩阵

- 默认 fixture/mock 矩阵未发现失败。

## 默认 Pytest 结果

- 退出码：0

```text
....                                                                     [100%]
4 passed in 4.47s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools | Forbidden tools | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-tool-route-001 | tool | observed | run_148529d2e9444a2e9d541dd0bf668e4e | agent-quality-live-20260527T061425Z-aq-tool-route-001 | 320 | runtime_broker | runtime_broker(auto_episode_route) | write_native_file, run_system_command |  |

## 复现与证据

### aq-tool-route-001

- Prompt：在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。
- 期望工具：runtime_broker
- 实际工具：runtime_broker(auto_episode_route)
- 关键事件：message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.fallback.used, planner.plan.failed, planner.plan.created, supervisor.graph.diagnostics, runtime.episode.queued, runtime.episode.progress, runtime.episode.started
- 禁止工具：write_native_file, run_system_command
- Run ID：run_148529d2e9444a2e9d541dd0bf668e4e
- Session ID：agent-quality-live-20260527T061425Z-aq-tool-route-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260527T061425Z-aq-tool-route-001", "conversationId": "agent-quality-live-20260527T061425Z-aq-tool-route-001", "clientMessageId": "aq-tool-route-001-20260527T061425Z", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "runId": "run_148529d2e9444a2e9d541dd0bf668e4e", "userMessage": {"id": "aq-tool-route-001-20260527T061425Z", "session_id": "agent-quality-live-20260527T061425Z-aq-tool-route-001", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-tool-route-001-20260527T061425Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。\", \"timestamp\": 1779862472322}]", "artifacts_json": "[]", "content_text": "在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_148529d2e9444a2e9d541dd0bf668e4e\", \"runId\": \"run_148529d2e9444a2e9d541dd0bf668e4e\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779862472322, \"role\": \"user\", \"clientMessageId\": \"aq-tool-route-001-20260527T061425Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"large_implementation\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\"], \"confidence\": 0.74, \"reason\": \"engineering_action_terms\", \"suggestedFamilies\": [\"engineering\"], \"optionalRuntimeGrants\": [], \"familyScores\": {\"engineering\": 0.74, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.36, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:工作区\", \"code_action:workspace\", \"media_output:创建\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-27T06:14:32.323Z", "updated_at": "2026-05-27T06:14:32.323Z", "finalized_at": null, "nodes": [{"id": "aq-tool-route-001-20260527T061425Z:narrative", "kind": "narrative", "role": "user", "content": "在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。", "timestamp": 1779862472322}], "artifacts": [], "metadata": {"run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "runId": "run_148529d2e9444a2e9d541dd0bf668e4e", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779862472322, "role": "user", "clientMessageId": "aq-tool-route-001-20260527T061425Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["large_implementation"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media"], "confidence": 0.74, "reason": "engineering_action_terms", "suggestedFamilies": ["engineering"], "optionalRuntimeGrants": [], "familyScores": {"engineering": 0.74, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.36, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:工作区", "code_action:workspace", "media_output:创建"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["delegation"], "episodeStates": ["active"], "handoffKinds": []}
{"runtimeEventCount": 17, "observationStage": "episode_observed", "observedTopics": ["message.user.recorded", "chat.planner_mode.enabled", "chat.task_planning_mode.enabled", "session.connected", "run.lane.acquired", "safety.preflight.checked", "engineering_lane.trigger.decided", "run.created", "run.state.changed", "agent.started", "planner.fallback.used", "planner.plan.failed", "planner.plan.created", "supervisor.graph.diagnostics", "runtime.episode.queued", "runtime.episode.progress", "runtime.episode.started"], "actualTools": ["runtime_broker(auto_episode_route)"], "events": [{"seq": 1, "topic": "message.user.recorded", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 2, "topic": "chat.planner_mode.enabled", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 3, "topic": "chat.task_planning_mode.enabled", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 4, "topic": "session.connected", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 5, "topic": "run.lane.acquired", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 6, "topic": "safety.preflight.checked", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 7, "topic": "engineering_lane.trigger.decided", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 8, "topic": "run.created", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 9, "topic": "run.state.changed", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 10, "topic": "agent.started", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 11, "topic": "planner.fallback.used", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "Planner lane used deterministic fallback."}, {"seq": 12, "topic": "planner.plan.failed", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "Planner lane failed over to deterministic fallback."}, {"seq": 13, "topic": "planner.plan.created", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 14, "topic": "supervisor.graph.diagnostics", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 15, "topic": "runtime.episode.queued", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 16, "topic": "runtime.episode.progress", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}, {"seq": 17, "topic": "runtime.episode.started", "run_id": "run_148529d2e9444a2e9d541dd0bf668e4e", "summary": "None"}]}
```
