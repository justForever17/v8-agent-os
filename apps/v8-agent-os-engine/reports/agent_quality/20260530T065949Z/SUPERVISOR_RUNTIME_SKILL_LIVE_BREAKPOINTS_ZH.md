# Supervisor / Runtime / Skill Live 断点审计报告

- 生成时间：20260530T065949Z
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
| ambiguous_doc | completed | supervisor-runtime-skill-live-20260530T065949Z-ambiguous_doc | run_92de4a403bbd4e25a3c4eff521e35316 | 883 ms |  | message.user.recorded, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, writing.clarification.required, run.text.delta, run.text_stream.diagnostics, run.completed |

## 失败与整改

未发现 P0/P1/P2 断点。

## 详细回答摘录

### ambiguous_doc

- 标题：模糊写文档应先澄清
- 最终回答摘录：这篇文档我先确认一下交付方式，避免写偏：
1. 只在聊天里直接写正文
2. 先调研并保留来源，再写成稿
3. 保存为文件或仓库文档（请告诉我路径、格式或文件名）

<details>
<summary>关键事件</summary>

```text
{"submitResponse": {"accepted": true, "session_id": "supervisor-runtime-skill-live-20260530T065949Z-ambiguous_doc", "conversationId": "supervisor-runtime-skill-live-20260530T065949Z-ambiguous_doc", "clientMessageId": "ambiguous_doc-20260530T065949Z", "run_id": "run_92de4a403bbd4e25a3c4eff521e35316", "runId": "run_92de4a403bbd4e25a3c4eff521e35316", "userMessage": {"id": "ambiguous_doc-20260530T065949Z", "session_id": "supervisor-runtime-skill-live-20260530T065949Z-ambiguous_doc", "run_id": "run_92de4a403bbd4e25a3c4eff521e35316", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"ambiguous_doc-20260530T065949Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"帮我写一篇文档。\", \"timestamp\": 1780124390143}]", "artifacts_json": "[]", "content_text": "帮我写一篇文档。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_92de4a403bbd4e25a3c4eff521e35316\", \"runId\": \"run_92de4a403bbd4e25a3c4eff521e35316\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1780124390143, \"role\": \"user\", \"clientMessageId\": \"ambiguous_doc-20260530T065949Z\", \"taskShapeHint\": {\"primaryTaskShape\": \"writing\", \"secondaryTaskShapes\": [], \"confidence\": 0.7, \"reason\": \"writing_or_document_terms\", \"suggestedFamilies\": [\"writing\"], \"optionalRuntimeGrants\": [], \"familyScores\": {\"writing\": 0.7}, \"topFamily\": \"writing\", \"scoreMargin\": 0.7, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"writing_action:文档\"], \"writingRoute\": {\"present\": true, \"mode\": \"ask_user_clarify\", \"reason\": \"ambiguous_writing_deliverable_needs_body_research_or_file_choice\", \"needsClarification\": true, \"clarificationOptions\": [\"direct_body\", \"research_backed\", \"save_as_file\"], \"requiresResearch\": false, \"requiresArtifact\": false, \"requiresSkillExecution\": false, \"recommendedFamily\": \"\", \"preferredAgentId\": \"\", \"skillName\": \"\", \"firstActionTool\": \"\", \"allowCreateSubagentOnMismatch\": false}, \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-30T06:59:50.144Z", "updated_at": "2026-05-30T06:59:50.144Z", "finalized_at": null, "nodes": [{"id": "ambiguous_doc-20260530T065949Z:narrative", "kind": "narrative", "role": "user", "content": "帮我写一篇文档。", "timestamp": 1780124390143}], "artifacts": [], "metadata": {"run_id": "run_92de4a403bbd4e25a3c4eff521e35316", "runId": "run_92de4a403bbd4e25a3c4eff521e35316", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1780124390143, "role": "user", "clientMessageId": "ambiguous_doc-20260530T065949Z", "taskShapeHint": {"primaryTaskShape": "writing", "secondaryTaskShapes": [], "confidence": 0.7, "reason": "writing_or_document_terms", "suggestedFamilies": ["writing"], "optionalRuntimeGrants": [], "familyScores": {"writing": 0.7}, "topFamily": "writing", "scoreMargin": 0.7, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["writing_action:文档"], "writingRoute": {"present": true, "mode": "ask_user_clarify", "reason": "ambiguous_writing_deliverable_needs_body_research_or_file_choice", "needsClarification": true, "clarificationOptions": ["direct_body", "research_backed", "save_as_file"], "requiresResearch": false, "requiresArtifact": false, "requiresSkillExecution": false, "recommendedFamily": "", "preferredAgentId": "", "skillName": "", "firstActionTool": "", "allowCreateSubagentOnMismatch": false}, "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"topic": "agent.started", "payload": {"type": "agent_start", "message_id": "7ec729b0-1d2d-4ade-a918-d46fc584e573", "agent": {"id": "supervisor", "name": "智能主管", "avatar": "http://127.0.0.1:9528/brand-mark.png", "roleLabel": "主理人"}, "node_id": "7ec729b0-1d2d-4ade-a918-d46fc584e573:agent_start:supervisor", "transcript_version": 2}}
{"terminalFacts": {"runStatus": "completed", "runFinishedAt": "2026-05-30T06:59:54.764Z", "runError": null, "activeEpisodes": []}}
```

</details>
