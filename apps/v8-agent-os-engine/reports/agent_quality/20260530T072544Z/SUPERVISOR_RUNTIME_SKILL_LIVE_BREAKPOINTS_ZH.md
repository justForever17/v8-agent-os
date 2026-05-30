# Supervisor / Runtime / Skill Live 断点审计报告

- 生成时间：20260530T072544Z
- 模型标签：`doubao-seed-2-0-pro`
- Engine：`http://127.0.0.1:9530`（报告内敏感路径已脱敏）

## 结论概览

| 等级 | 数量 |
| --- | --- |
| P0 | 1 |
| P1 | 0 |
| P2 | 0 |

## Case 结果

| Case | 状态 | Session | Run | 延迟 | 工具 | Runtime topics |
| --- | --- | --- | --- | --- | --- | --- |
| huashu_plan | completed | supervisor-runtime-skill-live-20260530T072544Z-huashu_plan | run_b9f67daa32e649aeb48863df6559cc3c | 324 ms |  | message.user.recorded, chat.planner_mode.enabled, chat.task_planning_mode.enabled, chat.skill_references.applied, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, planner.plan.created |

## 失败与整改


### P0

#### huashu_plan - huashu-nuwa Skill 应先读取 skill instructions

- 摘要：缺少必须工具调用：fetch_skill_instructions
- 涉及模块：runtimes/chat/runtime.py, graph/supervisor_context.py
- 建议修复：Skill 驱动任务必须把 skillReferences/contextMentions 注入模型可见上下文，并把 fetch_skill_instructions 作为首个可验证动作。
- 回归测试：`tests/agent_quality/test_skill_writing_routing.py`

<details>
<summary>证据</summary>

```text
{"tools": [], "topics": ["message.user.recorded", "chat.planner_mode.enabled", "chat.task_planning_mode.enabled", "chat.skill_references.applied", "session.connected", "run.lane.acquired", "safety.preflight.checked", "engineering_lane.trigger.decided", "run.created", "run.state.changed", "agent.started", "planner.plan.created", "runtime.episode.queued", "planner.auto_dispatch.prequeued", "runtime.episode.started", "supervisor.graph.diagnostics", "extension.route.selected", "context.prepared", "extension.execution.completed", "extension.skill.loaded", "runtime.episode.completed", "extension.mcp.candidate_exposed", "supervisor.turn.diagnostics", "run.reasoning.delta", "run.text.delta", "run.text_stream.diagnostics", "planner.plan.projected", "chat.planner_mode.decided", "chat.task_planning_mode.decided", "run.completed", "run.lane.released"], "finalText": "主人~ 已按照huashu-nuwa技能要求完成「测试人物视角」蒸馏的女娲造人执行计划啦 (/≧▽≦)/\n✅ 核心执行计划要点：\n1. 前置对齐：先拉取技能内置的人物视角蒸馏规范，明确测试视角的最小有效特征边界\n2. 分层筛选：从原始人物素材中3轮过滤，仅保留和测试场景强关联的属性、行为、交互逻辑特征\n3. 一致性校验：预设5个边界测试用例，校验输出视角的稳定性无自相矛盾\n4. 全程无本地文件写入、无新skill创建动作，仅输出纯文本计划结果\n📦 需提前准备的资料清单：测试人物原始设定文档、过往测试交互记录、目标测试场景的范围说明\n如果需要调整计划细节随时告诉我哦~"}
```

</details>


## 详细回答摘录

### huashu_plan

- 标题：huashu-nuwa Skill 应先读取 skill instructions
- 最终回答摘录：主人~ 已按照huashu-nuwa技能要求完成「测试人物视角」蒸馏的女娲造人执行计划啦 (/≧▽≦)/
✅ 核心执行计划要点：
1. 前置对齐：先拉取技能内置的人物视角蒸馏规范，明确测试视角的最小有效特征边界
2. 分层筛选：从原始人物素材中3轮过滤，仅保留和测试场景强关联的属性、行为、交互逻辑特征
3. 一致性校验：预设5个边界测试用例，校验输出视角的稳定性无自相矛盾
4. 全程无本地文件写入、无新skill创建动作，仅输出纯文本计划结果
📦 需提前准备的资料清单：测试人物原始设定文档、过往测试交互记录、目标测试场景的范围说明
如果需要调整计划细节随时告诉我哦~

<details>
<summary>关键事件</summary>

```text
{"submitResponse": {"accepted": true, "session_id": "supervisor-runtime-skill-live-20260530T072544Z-huashu_plan", "conversationId": "supervisor-runtime-skill-live-20260530T072544Z-huashu_plan", "clientMessageId": "huashu_plan-20260530T072544Z", "run_id": "run_b9f67daa32e649aeb48863df6559cc3c", "runId": "run_b9f67daa32e649aeb48863df6559cc3c", "userMessage": {"id": "huashu_plan-20260530T072544Z", "session_id": "supervisor-runtime-skill-live-20260530T072544Z-huashu_plan", "run_id": "run_b9f67daa32e649aeb48863df6559cc3c", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"huashu_plan-20260530T072544Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"使用已选择的 huashu-nuwa skill，给我做一次女娲造人的执行计划：目标是蒸馏一个“测试人物视角”，只输出计划和需要的资料，不写文件、不创建 skill。\", \"timestamp\": 1780125944527}]", "artifacts_json": "[]", "content_text": "使用已选择的 huashu-nuwa skill，给我做一次女娲造人的执行计划：目标是蒸馏一个“测试人物视角”，只输出计划和需要的资料，不写文件、不创建 skill。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_b9f67daa32e649aeb48863df6559cc3c\", \"runId\": \"run_b9f67daa32e649aeb48863df6559cc3c\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1780125944527, \"role\": \"user\", \"clientMessageId\": \"huashu_plan-20260530T072544Z\", \"plannerMode\": \"force\", \"plannerDispatchMode\": \"auto\", \"plannerIntentDiagnostics\": {\"matched\": true, \"signals\": [\"explicit_planning\", \"large_implementation\"], \"reason\": \"signals_matched\"}, \"taskPlanningMode\": true, \"taskShapeHint\": {\"primaryTaskShape\": \"writing\", \"secondaryTaskShapes\": [\"creative_media\", \"delegation\"], \"confidence\": 0.78, \"reason\": \"selected_skill_requires_writing_subagent_execution\", \"suggestedFamilies\": [\"writing\", \"engineering\"], \"optionalRuntimeGrants\": [\"delegation.recursive\"], \"familyScores\": {\"engineering\": 0.74, \"creative_media\": 0.38}, \"topFamily\": \"writing\", \"scoreMargin\": 0.36, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"code_action:测试\", \"media_output:测试\", \"writing_route:skill_subagent\"], \"writingRoute\": {\"present\": true, \"mode\": \"skill_subagent\", \"reason\": \"selected_skill_reference_must_be_executed_by_writing_subagent\", \"needsClarification\": false, \"requiresResearch\": false, \"requiresArtifact\": false, \"requiresSkillExecution\": true, \"recommendedFamily\": \"writing\", \"preferredAgentId\": \"\", \"skillName\": \"huashu-nuwa\", \"firstActionTool\": \"fetch_skill_instructions\", \"allowCreateSubagentOnMismatch\": true}, \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}, \"skillReferences\": [{\"id\": \"huashu-nuwa\", \"name\": \"huashu-nuwa\", \"description\": \"女娲造人：深度调研并生成可运行的人物/主题视角 Skill。\", \"path\": \"C:\\\\Users\\\\sunny\\\\.agents\\\\skills\\\\huashu-nuwa\", \"sourceType\": \"local\", \"workspacePath\": \"\", \"workspaceId\": \"\", \"projectId\": \"\"}], \"contextMentions\": [{\"kind\": \"skill\", \"id\": \"huashu-nuwa\", \"name\": \"huashu-nuwa\", \"label\": \"huashu-nuwa\", \"description\": \"女娲造人：深度调研并生成可运行的人物/主题视角 Skill。\", \"path\": \"C:\\\\Users\\\\sunny\\\\.agents\\\\skills\\\\huashu-nuwa\", \"familyId\": \"\", \"sourceType\": \"local\"}]}", "version": 1, "created_at": "2026-05-30T07:25:44.529Z", "updated_at": "2026-05-30T07:25:44.529Z", "finalized_at": null, "nodes": [{"id": "huashu_plan-20260530T072544Z:narrative", "kind": "narrative", "role": "user", "content": "使用已选择的 huashu-nuwa skill，给我做一次女娲造人的执行计划：目标是蒸馏一个“测试人物视角”，只输出计划和需要的资料，不写文件、不创建 skill。", "timestamp": 1780125944527}], "artifacts": [], "metadata": {"run_id": "run_b9f67daa32e649aeb48863df6559cc3c", "runId": "run_b9f67daa32e649aeb48863df6559cc3c", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1780125944527, "role": "user", "clientMessageId": "huashu_plan-20260530T072544Z", "plannerMode": "force", "plannerDispatchMode": "auto", "plannerIntentDiagnostics": {"matched": true, "signals": ["explicit_planning", "large_implementation"], "reason": "signals_matched"}, "taskPlanningMode": true, "taskShapeHint": {"primaryTaskShape": "writing", "secondaryTaskShapes": ["creative_media", "delegation"], "confidence": 0.78, "reason": "selected_skill_requires_writing_subagent_execution", "suggestedFamilies": ["writing", "engineering"], "optionalRuntimeGrants": ["delegation.recursive"], "familyScores": {"engineering": 0.74, "creative_media": 0.38}, "topFamily": "writing", "scoreMargin": 0.36, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["code_action:测试", "media_output:测试", "writing_route:skill_subagent"], "writingRoute": {"present": true, "mode": "skill_subagent", "reason": "selected_skill_reference_must_be_executed_by_writing_subagent", "needsClarification": false, "requiresResearch": false, "requiresArtifact": false, "requiresSkillExecution": true, "recommendedFamily": "writing", "preferredAgentId": "", "skillName": "huashu-nuwa", "firstActionTool": "fetch_skill_instructions", "allowCreateSubagentOnMismatch": true}, "lexiconSignature": "lexicon:54434ba2b829f9f3|ta
```

</details>
