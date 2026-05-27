# Agent Quality Matrix 整改报告

- 生成时间：20260527T055847Z
- 模型配置：mimo
- 矩阵范围：tool
- 总体状态：需要整改

## P0 门禁

### [P0] Live case 未观察到期望工具

- Case：aq-tool-route-001
- 现象：未观察到：runtime_broker；实际工具：-。
- 复现：Session agent-quality-live-20260527T055847Z-aq-tool-route-001, run run_a7f0bcfe002a4228acdf19302d38c953
- 根因推测：模型未按主链工具面行动，或 runtime events 未正确投影工具调用。
- 涉及模块：api/chat_realtime_routes.py, erc/session_runtime.py, packages/session-realtime
- 推荐修复：核对工具调用事实和投影链；若实际未调用，修 Prompt/tool surface；若已调用未投影，修 runtime event projection。
- 回归测试：agent_quality::tool


## 失败矩阵


## 默认 Pytest 结果

- 退出码：0

```text
....                                                                     [100%]
4 passed in 6.17s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools | Forbidden tools | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-tool-route-001 | tool | observed | run_a7f0bcfe002a4228acdf19302d38c953 | agent-quality-live-20260527T055847Z-aq-tool-route-001 | 325 | runtime_broker | - | write_native_file, run_system_command |  |

## 复现与证据

### aq-tool-route-001

- Prompt：在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。
- 期望工具：runtime_broker
- 实际工具：-
- 关键事件：message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.fallback.used, planner.plan.failed, planner.plan.created, supervisor.graph.diagnostics, runtime.episode.queued, runtime.episode.failed, runtime.episode.started, extension.route.selected, extension.execution.completed, runtime.episode.completed
- 禁止工具：write_native_file, run_system_command
- Run ID：run_a7f0bcfe002a4228acdf19302d38c953
- Session ID：agent-quality-live-20260527T055847Z-aq-tool-route-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260527T055847Z-aq-tool-route-001", "conversationId": "agent-quality-live-20260527T055847Z-aq-tool-route-001", "clientMessageId": "aq-tool-route-001-20260527T055847Z", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "runId": "run_a7f0bcfe002a4228acdf19302d38c953", "userMessage": {"id": "aq-tool-route-001-20260527T055847Z", "session_id": "agent-quality-live-20260527T055847Z-aq-tool-route-001", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-tool-route-001-20260527T055847Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。\", \"timestamp\": 1779861537299}]", "artifacts_json": "[]", "content_text": "在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_a7f0bcfe002a4228acdf19302d38c953\", \"runId\": \"run_a7f0bcfe002a4228acdf19302d38c953\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779861537299, \"role\": \"user\", \"clientMessageId\": \"aq-tool-route-001-20260527T055847Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"large_implementation\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\"], \"confidence\": 0.74, \"reason\": \"engineering_action_terms\", \"suggestedFamilies\": [\"engineering\"], \"optionalRuntimeGrants\": [], \"familyScores\": {\"engineering\": 0.74, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.36, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:工作区\", \"code_action:workspace\", \"media_output:创建\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-27T05:58:57.301Z", "updated_at": "2026-05-27T05:58:57.301Z", "finalized_at": null, "nodes": [{"id": "aq-tool-route-001-20260527T055847Z:narrative", "kind": "narrative", "role": "user", "content": "在当前工作区创建一个极小的演示文件，但必须通过 runtime 路由，不要直接写文件。", "timestamp": 1779861537299}], "artifacts": [], "metadata": {"run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "runId": "run_a7f0bcfe002a4228acdf19302d38c953", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779861537299, "role": "user", "clientMessageId": "aq-tool-route-001-20260527T055847Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["large_implementation"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media"], "confidence": 0.74, "reason": "engineering_action_terms", "suggestedFamilies": ["engineering"], "optionalRuntimeGrants": [], "familyScores": {"engineering": 0.74, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.36, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:工作区", "code_action:workspace", "media_output:创建"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"durableTimelineFallback": true, "durableEventCount": 30, "observationStage": "episode_observed", "events": [{"seq": 6, "topic": "safety.preflight.checked", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 7, "topic": "engineering_lane.trigger.decided", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 8, "topic": "run.created", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 9, "topic": "run.state.changed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 10, "topic": "agent.started", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 11, "topic": "planner.fallback.used", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "Planner lane used deterministic fallback."}, {"seq": 12, "topic": "planner.plan.failed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "Planner lane failed over to deterministic fallback."}, {"seq": 13, "topic": "planner.plan.created", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 14, "topic": "supervisor.graph.diagnostics", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 15, "topic": "runtime.episode.queued", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 16, "topic": "runtime.episode.failed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 17, "topic": "runtime.episode.started", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 18, "topic": "extension.route.selected", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 19, "topic": "extension.execution.completed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 20, "topic": "extension.route.selected", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 21, "topic": "extension.execution.completed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 22, "topic": "extension.route.selected", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 23, "topic": "extension.execution.completed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 24, "topic": "extension.route.selected", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 25, "topic": "extension.execution.completed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 26, "topic": "runtime.episode.completed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 27, "topic": "extension.route.selected", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 28, "topic": "extension.mcp.candidate_exposed", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 29, "topic": "context.prepared", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}, {"seq": 30, "topic": "supervisor.turn.diagnostics", "run_id": "run_a7f0bcfe002a4228acdf19302d38c953", "summary": "None"}]}
{"runtimeEventCount": 30, "observationStage": "episode_observed", "observedTopics": ["message.user.recorded", "chat.planner_mode.enabled", "chat.task_planning_mode.enabled", "session.connected", "run.lane.acquired", "safety.preflight.checked", "engineering_lane.trigger.decided", "run.created", "run.state.changed", "agent.starte
```
