# Agent Quality Matrix 整改报告

- 生成时间：20260528T192000Z
- 模型配置：mimo
- 矩阵范围：multi_agent
- 总体状态：通过

## P0 门禁

- 未发现 route → episode → runner → handoff 的 P0 门禁失败。

## 失败矩阵

- 默认 fixture/mock 矩阵未发现失败。

## 默认 Pytest 结果

- 退出码：0

```text
...                                                                      [100%]
3 passed in 1.00s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools (all owners) | Forbidden Supervisor seen | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-multi-agent-001 | multi_agent | observed | run_4922bbf601dd437889cdd5ce661a9a17 | agent-quality-live-20260528T192000Z-aq-multi-agent-001 | 293 | runtime_broker, delegation_broker | runtime_broker(auto_episode_route), delegation_broker(auto_episode_route) | - |  |

## 复现与证据

### aq-multi-agent-001

- Prompt：演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。
- 期望工具：runtime_broker, delegation_broker
- 实际工具（所有 owner）：runtime_broker(auto_episode_route), delegation_broker(auto_episode_route)
- 关键事件：message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.plan.created, runtime.episode.queued, planner.auto_dispatch.prequeued, supervisor.graph.diagnostics, runtime.episode.started, runtime.episode.completed, extension.route.selected, extension.mcp.candidate_exposed, context.prepared, extension.execution.completed
- Supervisor 实际违规工具：-
- Case 禁用清单：write_native_file, run_system_command
- Run ID：run_4922bbf601dd437889cdd5ce661a9a17
- Session ID：agent-quality-live-20260528T192000Z-aq-multi-agent-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260528T192000Z-aq-multi-agent-001", "conversationId": "agent-quality-live-20260528T192000Z-aq-multi-agent-001", "clientMessageId": "aq-multi-agent-001-20260528T192000Z", "run_id": "run_4922bbf601dd437889cdd5ce661a9a17", "runId": "run_4922bbf601dd437889cdd5ce661a9a17", "userMessage": {"id": "aq-multi-agent-001-20260528T192000Z", "session_id": "agent-quality-live-20260528T192000Z-aq-multi-agent-001", "run_id": "run_4922bbf601dd437889cdd5ce661a9a17", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-multi-agent-001-20260528T192000Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。\", \"timestamp\": 1779996003299}]", "artifacts_json": "[]", "content_text": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_4922bbf601dd437889cdd5ce661a9a17\", \"runId\": \"run_4922bbf601dd437889cdd5ce661a9a17\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779996003299, \"role\": \"user\", \"clientMessageId\": \"aq-multi-agent-001-20260528T192000Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"delegation_or_parallel\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\", \"research\", \"delegation\"], \"confidence\": 0.9, \"reason\": \"multi_runtime_orchestration_terms\", \"suggestedFamilies\": [\"engineering\", \"research\"], \"optionalRuntimeGrants\": [\"research.core\", \"delegation.recursive\"], \"familyScores\": {\"engineering\": 0.9, \"research\": 0.45, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.45, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": true, \"families\": [\"engineering\"], \"source\": \"task_shape_classifier\", \"reason\": \"high_confidence_single_family\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:工程\", \"delegation_action:调度\", \"delegation_action:主链调度\", \"delegation_action:子 agent\", \"delegation_action:delegation\", \"delegation_action:child delegation\", \"media_output:调研\", \"research_secondary:调研\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"force\", \"engineeringTriggerDecision\": {\"mode\": \"force\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-28T19:20:03.301Z", "updated_at": "2026-05-28T19:20:03.301Z", "finalized_at": null, "nodes": [{"id": "aq-multi-agent-001-20260528T192000Z:narrative", "kind": "narrative", "role": "user", "content": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "timestamp": 1779996003299}], "artifacts": [], "metadata": {"run_id": "run_4922bbf601dd437889cdd5ce661a9a17", "runId": "run_4922bbf601dd437889cdd5ce661a9a17", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779996003299, "role": "user", "clientMessageId": "aq-multi-agent-001-20260528T192000Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["delegation_or_parallel"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media", "research", "delegation"], "confidence": 0.9, "reason": "multi_runtime_orchestration_terms", "suggestedFamilies": ["engineering", "research"], "optionalRuntimeGrants": ["research.core", "delegation.recursive"], "familyScores": {"engineering": 0.9, "research": 0.45, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.45, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": true, "families": ["engineering"], "source": "task_shape_classifier", "reason": "high_confidence_single_family", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:工程", "delegation_action:调度", "delegation_action:主链调度", "delegation_action:子 agent", "delegation_action:delegation", "delegation_action:child delegation", "media_output:调研", "research_secondary:调研"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "force", "engineeringTriggerDecision": {"mode": "force", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engineering", "state": "active"}, {"episodeId": "episode_f3c674f6c99c", "kind": "delegation", "state": "active"}]}
{"delegationSatisfiedBy": "delegation_episode_or_handoff", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "hasExecutedEpisode": true, "hasConfirmedDelegationTopic": false, "hasDelegationFailureDiagnostic": false, "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engineering", "state": "active"}, {"episodeId": "episode_f3c674f6c99c", "kind": "delegation", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engineering", "state": "active"}, {"episodeId": "episode_f3c674f6c99c", "kind": "delegation", "state": "active"}]}
{"delegationSatisfiedBy": "delegation_episode_or_handoff", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "hasExecutedEpisode": true, "hasConfirmedDelegationTopic": false, "hasDelegationFailureDiagnostic": false, "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engineering", "state": "active"}, {"episodeId": "episode_f3c674f6c99c", "kind": "delegation", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engineering", "state": "active"}, {"episodeId": "episode_f3c674f6c99c", "kind": "delegation", "state": "active"}]}
{"delegationSatisfiedBy": "delegation_episode_or_handoff", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "hasExecutedEpisode": true, "hasConfirmedDelegationTopic": false, "hasDelegationFailureDiagnostic": false, "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engineering", "state": "active"}, {"episodeId": "episode_f3c674f6c99c", "kind": "delegation", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["delegation", "engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_476605ca1235", "kind": "engi
```
