# Supervisor / Runtime / Skill Live 断点审计报告

- 生成时间：20260530T065804Z
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
| simple_doc | completed | supervisor-runtime-skill-live-20260530T065804Z-simple_doc | run_d81e080a0d3b47d581cd729d47adb1cc | 1027 ms |  | message.user.recorded, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, supervisor.graph.diagnostics, extension.route.selected, extension.mcp.candidate_exposed, runtime.gate.decision |

## 失败与整改

未发现 P0/P1/P2 断点。

## 详细回答摘录

### simple_doc

- 标题：简单短文应由 Supervisor 直接完成
- 最终回答摘录：(๑•̀ㅂ•́)و✧ 主人好呀～V8 Agent OS是由开发者justForever17独立打造的多运行时协同AI操作系统哦！

它采用分层运行时架构，把科研调研、代码开发、创意媒体生成、桌面自动化操作等不同类型的任务，拆分到对应的专属运行时执行，既能最大化发挥各模块能力，也能严格管控权限边界避免风险。

系统支持自定义技能扩展、MCP工具对接，还内置了完整的任务编排、状态回溯、断点续跑能力，哪怕是复杂的跨域任务也能稳定交付结果哒～(≧▽≦)

<details>
<summary>关键事件</summary>

```text
{"submitResponse": {"accepted": true, "session_id": "supervisor-runtime-skill-live-20260530T065804Z-simple_doc", "conversationId": "supervisor-runtime-skill-live-20260530T065804Z-simple_doc", "clientMessageId": "simple_doc-20260530T065804Z", "run_id": "run_d81e080a0d3b47d581cd729d47adb1cc", "runId": "run_d81e080a0d3b47d581cd729d47adb1cc", "userMessage": {"id": "simple_doc-20260530T065804Z", "session_id": "supervisor-runtime-skill-live-20260530T065804Z-simple_doc", "run_id": "run_d81e080a0d3b47d581cd729d47adb1cc", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"simple_doc-20260530T065804Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。\", \"timestamp\": 1780124285719}]", "artifacts_json": "[]", "content_text": "只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_d81e080a0d3b47d581cd729d47adb1cc\", \"runId\": \"run_d81e080a0d3b47d581cd729d47adb1cc\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1780124285719, \"role\": \"user\", \"clientMessageId\": \"simple_doc-20260530T065804Z\", \"taskShapeHint\": {\"primaryTaskShape\": \"writing\", \"secondaryTaskShapes\": [], \"confidence\": 0.7, \"reason\": \"writing_or_document_terms\", \"suggestedFamilies\": [\"writing\"], \"optionalRuntimeGrants\": [], \"familyScores\": {\"writing\": 0.7}, \"topFamily\": \"writing\", \"scoreMargin\": 0.7, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"writing_action:说明\"], \"writingRoute\": {\"present\": true, \"mode\": \"direct_supervisor\", \"reason\": \"simple_bounded_text_generation\", \"needsClarification\": false, \"requiresResearch\": false, \"requiresArtifact\": false, \"requiresSkillExecution\": false, \"recommendedFamily\": \"\", \"preferredAgentId\": \"\", \"skillName\": \"\", \"firstActionTool\": \"\", \"allowCreateSubagentOnMismatch\": false}, \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-30T06:58:05.722Z", "updated_at": "2026-05-30T06:58:05.722Z", "finalized_at": null, "nodes": [{"id": "simple_doc-20260530T065804Z:narrative", "kind": "narrative", "role": "user", "content": "只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。", "timestamp": 1780124285719}], "artifacts": [], "metadata": {"run_id": "run_d81e080a0d3b47d581cd729d47adb1cc", "runId": "run_d81e080a0d3b47d581cd729d47adb1cc", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1780124285719, "role": "user", "clientMessageId": "simple_doc-20260530T065804Z", "taskShapeHint": {"primaryTaskShape": "writing", "secondaryTaskShapes": [], "confidence": 0.7, "reason": "writing_or_document_terms", "suggestedFamilies": ["writing"], "optionalRuntimeGrants": [], "familyScores": {"writing": 0.7}, "topFamily": "writing", "scoreMargin": 0.7, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["writing_action:说明"], "writingRoute": {"present": true, "mode": "direct_supervisor", "reason": "simple_bounded_text_generation", "needsClarification": false, "requiresResearch": false, "requiresArtifact": false, "requiresSkillExecution": false, "recommendedFamily": "", "preferredAgentId": "", "skillName": "", "firstActionTool": "", "allowCreateSubagentOnMismatch": false}, "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"topic": "agent.started", "payload": {"type": "agent_start", "message_id": "95f89974-1492-46c7-83e2-9565b360c35d", "agent": {"id": "supervisor", "name": "智能主管", "avatar": "http://127.0.0.1:9528/brand-mark.png", "roleLabel": "主理人"}, "node_id": "95f89974-1492-46c7-83e2-9565b360c35d:agent_start:supervisor", "transcript_version": 2}}
{"terminalFacts": {"runStatus": "failed", "runFinishedAt": "2026-05-30T06:58:50.616Z", "runError": "database is locked", "activeEpisodes": []}}
```

</details>
