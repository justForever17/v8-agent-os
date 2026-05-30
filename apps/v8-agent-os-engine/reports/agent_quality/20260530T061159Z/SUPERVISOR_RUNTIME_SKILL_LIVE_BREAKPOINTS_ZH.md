# Supervisor / Runtime / Skill Live 断点审计报告

- 生成时间：20260530T061159Z
- 模型标签：`doubao-seed-2-0-pro`
- Engine：`http://127.0.0.1:9530`（报告内敏感路径已脱敏）

## 结论概览

| 等级 | 数量 |
| --- | --- |
| P0 | 0 |
| P1 | 0 |
| P2 | 0 |

## Case 结果

| Case | 状态 | Session | Run | 延迟 | 工具 | Runtime topics |
| --- | --- | --- | --- | --- | --- | --- |
| simple_doc | completed | supervisor-runtime-skill-live-20260530T061159Z-simple_doc | run_b2f7741feeb3440db21ad04fed49a5c1 | 547 ms |  | message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.plan.created, runtime.episode.queued |

## 失败与整改

未发现 P0/P1/P2 断点。

## 详细回答摘录

### simple_doc

- 标题：简单短文应由 Supervisor 直接完成
- 最终回答摘录：V8 Agent OS是面向复杂多模态任务的AI操作系统哦w(≧▽≦)w
它以运行时编排为核心，内置调研、工程、创意媒体、桌面控制、RPA、记忆管理等独立执行平面，能自动拆解跨域任务、路由到对应能力模块执行，全程保障工作可恢复、可观测、进度可追溯，避免零散工具调用的混乱问题。
不管是代码项目开发、多源信息调研、音视频内容生成，还是自动化流程搭建，它都能稳定承接，还支持跨节点协作、自定义技能插件扩展，灵活适配各类个性化需求哒～

<details>
<summary>关键事件</summary>

```text
{"submitResponse": {"accepted": true, "session_id": "supervisor-runtime-skill-live-20260530T061159Z-simple_doc", "conversationId": "supervisor-runtime-skill-live-20260530T061159Z-simple_doc", "clientMessageId": "simple_doc-20260530T061159Z", "run_id": "run_b2f7741feeb3440db21ad04fed49a5c1", "runId": "run_b2f7741feeb3440db21ad04fed49a5c1", "userMessage": {"id": "simple_doc-20260530T061159Z", "session_id": "supervisor-runtime-skill-live-20260530T061159Z-simple_doc", "run_id": "run_b2f7741feeb3440db21ad04fed49a5c1", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"simple_doc-20260530T061159Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。\", \"timestamp\": 1780121520006}]", "artifacts_json": "[]", "content_text": "只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_b2f7741feeb3440db21ad04fed49a5c1\", \"runId\": \"run_b2f7741feeb3440db21ad04fed49a5c1\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1780121520006, \"role\": \"user\", \"clientMessageId\": \"simple_doc-20260530T061159Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"explicit_engineering_runtime\"], \"reason\": \"explicit_engineering_runtime_requested\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"project_coding\", \"secondaryTaskShapes\": [\"creative_media\", \"research\"], \"confidence\": 0.74, \"reason\": \"engineering_action_terms\", \"suggestedFamilies\": [\"engineering\", \"research\"], \"optionalRuntimeGrants\": [\"research.core\"], \"familyScores\": {\"engineering\": 0.74, \"research\": 0.45, \"creative_media\": 0.38}, \"topFamily\": \"engineering\", \"scoreMargin\": 0.29, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:工程\", \"code_action:保存文件\", \"media_output:调研\", \"research_secondary:调研\"], \"writingRoute\": {\"present\": true, \"mode\": \"artifact_runtime\", \"reason\": \"writing_requires_file_or_repository_side_effect\", \"needsClarification\": false, \"requiresResearch\": true, \"requiresArtifact\": true, \"requiresSkillExecution\": false, \"recommendedFamily\": \"engineering\", \"preferredAgentId\": \"\", \"skillName\": \"\", \"firstActionTool\": \"\", \"allowCreateSubagentOnMismatch\": false}, \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"force\", \"engineeringTriggerDecision\": {\"mode\": \"force\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-30T06:12:00.012Z", "updated_at": "2026-05-30T06:12:00.012Z", "finalized_at": null, "nodes": [{"id": "simple_doc-20260530T061159Z:narrative", "kind": "narrative", "role": "user", "content": "只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。", "timestamp": 1780121520006}], "artifacts": [], "metadata": {"run_id": "run_b2f7741feeb3440db21ad04fed49a5c1", "runId": "run_b2f7741feeb3440db21ad04fed49a5c1", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1780121520006, "role": "user", "clientMessageId": "simple_doc-20260530T061159Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["explicit_engineering_runtime"], "reason": "explicit_engineering_runtime_requested"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "project_coding", "secondaryTaskShapes": ["creative_media", "research"], "confidence": 0.74, "reason": "engineering_action_terms", "suggestedFamilies": ["engineering", "research"], "optionalRuntimeGrants": ["research.core"], "familyScores": {"engineering": 0.74, "research": 0.45, "creative_media": 0.38}, "topFamily": "engineering", "scoreMargin": 0.29, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:工程", "code_action:保存文件", "media_output:调研", "research_secondary:调研"], "writingRoute": {"present": true, "mode": "artifact_runtime", "reason": "writing_requires_file_or_repository_side_effect", "needsClarification": false, "requiresResearch": true, "requiresArtifact": true, "requiresSkillExecution": false, "recommendedFamily": "engineering", "preferredAgentId": "", "skillName": "", "firstActionTool": "", "allowCreateSubagentOnMismatch": false}, "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "force", "engineeringTriggerDecision": {"mode": "force", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"topic": "agent.started", "payload": {"type": "agent_start", "message_id": "27499115-e1b9-41d9-a415-0beaa4f9d6df", "agent": {"id": "supervisor", "name": "智能主管", "avatar": "http://127.0.0.1:9528/brand-mark.png", "roleLabel": "主理人"}, "node_id": "27499115-e1b9-41d9-a415-0beaa4f9d6df:agent_start:supervisor", "transcript_version": 2}}
{"topic": "runtime.episode.queued", "payload": {"episode": {"needId": "episode_22ba7d638127", "episodeId": "episode_22ba7d638127", "kind": 
```

</details>
