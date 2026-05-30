# Agent Quality Matrix 整改报告

- 生成时间：20260527T173100Z
- 模型配置：mimo
- 矩阵范围：multi_agent
- 总体状态：需要整改

## P0 门禁

### [P0] Live case 未达到终态闭环

- Case：aq-multi-agent-001
- 现象：失败阶段：run_not_terminal。
- 复现：Session agent-quality-live-20260527T173100Z-aq-multi-agent-001, run run_b7724174628d43428c995a0658e2a334
- 根因推测：runtime episode、EpisodeRunner、handoff 回流或 Supervisor 恢复链路仍有未闭合状态。
- 涉及模块：core/runtime_episode_runner.py, graph/workflow_assembly.py, core/native_tools.py
- 推荐修复：回查 liveRunFacts/activeEpisodes，把该 run 固化为 fixture，确保 route→episode→runner→handoff→Supervisor 终态闭环。
- 回归测试：agent_quality::multi_agent

### [P0] Live case 未观察到期望工具

- Case：aq-multi-agent-001
- 现象：未观察到：runtime_broker, delegation_broker；实际工具：runtime_broker(auto_episode_route), delegation_broker(auto_episode_route), delegation_broker, run_system_command, write_native_file。
- 复现：Session agent-quality-live-20260527T173100Z-aq-multi-agent-001, run run_b7724174628d43428c995a0658e2a334
- 根因推测：模型未按主链工具面行动，或 runtime events 未正确投影工具调用。
- 涉及模块：api/chat_realtime_routes.py, erc/session_runtime.py, packages/session-realtime
- 推荐修复：核对工具调用事实和投影链；若实际未调用，修 Prompt/tool surface；若已调用未投影，修 runtime event projection。
- 回归测试：agent_quality::multi_agent


## 失败矩阵


## 默认 Pytest 结果

- 退出码：0

```text
..                                                                       [100%]
2 passed in 1.17s
```

## Live 审计记录

| Case | Matrix | Status | Run | Session | Latency | Expected tools | Actual tools | Forbidden tools | Failure |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| aq-multi-agent-001 | multi_agent | observed | run_b7724174628d43428c995a0658e2a334 | agent-quality-live-20260527T173100Z-aq-multi-agent-001 | 333 | runtime_broker, delegation_broker | runtime_broker(auto_episode_route), delegation_broker(auto_episode_route), delegation_broker, run_system_command, write_native_file | write_native_file, run_system_command | run_not_terminal |

## 复现与证据

### aq-multi-agent-001

- Prompt：演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。
- 期望工具：runtime_broker, delegation_broker
- 实际工具：runtime_broker(auto_episode_route), delegation_broker(auto_episode_route), delegation_broker, run_system_command, write_native_file
- 关键事件：message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.fallback.used, planner.plan.failed, planner.plan.created, supervisor.graph.diagnostics, runtime.episode.queued, runtime.episode.progress, runtime.episode.started, runtime.episode.completed, extension.route.selected, extension.mcp.candidate_exposed
- 禁止工具：write_native_file, run_system_command
- Run ID：run_b7724174628d43428c995a0658e2a334
- Session ID：agent-quality-live-20260527T173100Z-aq-multi-agent-001

```json
{"response": {"accepted": true, "session_id": "agent-quality-live-20260527T173100Z-aq-multi-agent-001", "conversationId": "agent-quality-live-20260527T173100Z-aq-multi-agent-001", "clientMessageId": "aq-multi-agent-001-20260527T173100Z", "run_id": "run_b7724174628d43428c995a0658e2a334", "runId": "run_b7724174628d43428c995a0658e2a334", "userMessage": {"id": "aq-multi-agent-001-20260527T173100Z", "session_id": "agent-quality-live-20260527T173100Z-aq-multi-agent-001", "run_id": "run_b7724174628d43428c995a0658e2a334", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"aq-multi-agent-001-20260527T173100Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。\", \"timestamp\": 1779903064192}]", "artifacts_json": "[]", "content_text": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_b7724174628d43428c995a0658e2a334\", \"runId\": \"run_b7724174628d43428c995a0658e2a334\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1779903064192, \"role\": \"user\", \"clientMessageId\": \"aq-multi-agent-001-20260527T173100Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"delegation_or_parallel\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\", \"research\"], \"confidence\": 0.74, \"reason\": \"engineering_action_terms\", \"suggestedFamilies\": [\"engineering\", \"research\"], \"optionalRuntimeGrants\": [\"research.core\"], \"familyScores\": {\"engineering\": 0.74, \"research\": 0.45, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.29, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:workspace\", \"media_output:调研\", \"research_secondary:调研\"], \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"force\", \"engineeringTriggerDecision\": {\"mode\": \"force\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-27T17:31:04.194Z", "updated_at": "2026-05-27T17:31:04.194Z", "finalized_at": null, "nodes": [{"id": "aq-multi-agent-001-20260527T173100Z:narrative", "kind": "narrative", "role": "user", "content": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度，不要由 Supervisor 直接硬干。", "timestamp": 1779903064192}], "artifacts": [], "metadata": {"run_id": "run_b7724174628d43428c995a0658e2a334", "runId": "run_b7724174628d43428c995a0658e2a334", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1779903064192, "role": "user", "clientMessageId": "aq-multi-agent-001-20260527T173100Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["delegation_or_parallel"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media", "research"], "confidence": 0.74, "reason": "engineering_action_terms", "suggestedFamilies": ["engineering", "research"], "optionalRuntimeGrants": ["research.core"], "familyScores": {"engineering": 0.74, "research": 0.45, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.29, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:workspace", "media_output:调研", "research_secondary:调研"], "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "force", "engineeringTriggerDecision": {"mode": "force", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["queued"], "handoffKinds": [], "activeEpisodes": [{"episodeId": "episode_110f4dff8c2d", "kind": "research", "state": "queued"}, {"episodeId": "episode_20263d008641", "kind": "engineering", "state": "queued"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": "episode_20263d008641", "kind": "engineering", "state": "active"}]}
{"routeSatisfiedBy": "runtime_episode", "episodeKinds": ["engineering", "research"], "episodeStates": ["active", "completed"], "handoffKinds": ["research_evidence_bundle"], "activeEpisodes": [{"episodeId": 
```
