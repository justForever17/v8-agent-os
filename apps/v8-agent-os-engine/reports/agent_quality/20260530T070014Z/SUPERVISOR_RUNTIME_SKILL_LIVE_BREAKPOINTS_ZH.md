# Supervisor / Runtime / Skill Live 断点审计报告

- 生成时间：20260530T070014Z
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
| source_write | completed | supervisor-runtime-skill-live-20260530T070014Z-source_write | run_9f6eed5fe7994259aac2268cac4b4250 | 527 ms | web_broker, tool_observation_detail | message.user.recorded, session.connected, run.lane.acquired, safety.preflight.checked, engineering_lane.trigger.decided, run.created, run.state.changed, agent.started, supervisor.graph.diagnostics, extension.route.selected, extension.mcp.candidate_exposed, runtime.gate.decision |

## 失败与整改

未发现 P0/P1/P2 断点。

## 详细回答摘录

### source_write

- 标题：来源型写作应 Research evidence 后成稿
- 最终回答摘录：### V8 Agent OS runtime-first 设计价值短报告
> 来源1：GitHub 官方仓库 <https://github.com/justForever17/v8-agent-os-engine>
> 来源2：V8 Agent OS 官方站点 <https://v8agentos.top>
> 来源3：知乎「AI Agent 运行时」技术解析 <https://zhuanlan.zhihu.com/p/2029139789375357445>

V8 Agent OS 采用的 runtime-first（运行时优先）设计，是区别于传统聊天式Agent框架的核心竞争力：
1. **架构层面**：把多运行时（任务执行引擎、长驻进程管理、运行账本快照、故障自动恢复等）作为系统核心调度层，替代传统单会话无状态的设计，天然支持任务中断续跑、多模块协同（记忆/自动化/插件/桌面操作等），实现真正的业务级持久化运行能力。
2. **成本与安全层面**：解决了开源Agent框架普遍存在的API调用臃肿、会话失忆、第三方插件权限失控的问题，官方数据显示其 runtime 沙箱隔离机制比传统方案降低最多32倍的运行成本，同时内置全局安全管控体系，避免 rogue 插件带来的数据泄露风险。
3. **落地价值**：runtime-first 架构让Agent不再局限于对话窗口，可直接承接真实企业级业务流程，天然支持7×24小时无人值守执行、全生命周期状态同步，是Agent从玩具走向生产可用的核心设计突破。

(੭˙ᗜ˙)੭ 报告整理完成哦主人！没有保存文件，来源都标清楚啦~

<details>
<summary>关键事件</summary>

```text
{"submitResponse": {"accepted": true, "session_id": "supervisor-runtime-skill-live-20260530T070014Z-source_write", "conversationId": "supervisor-runtime-skill-live-20260530T070014Z-source_write", "clientMessageId": "source_write-20260530T070014Z", "run_id": "run_9f6eed5fe7994259aac2268cac4b4250", "runId": "run_9f6eed5fe7994259aac2268cac4b4250", "userMessage": {"id": "source_write-20260530T070014Z", "session_id": "supervisor-runtime-skill-live-20260530T070014Z-source_write", "run_id": "run_9f6eed5fe7994259aac2268cac4b4250", "ordinal": 1, "role": "user", "state": "completed", "nodes_json": "[{\"id\": \"source_write-20260530T070014Z:narrative\", \"kind\": \"narrative\", \"role\": \"user\", \"content\": \"联网查找 2-3 个来源，写一段关于 V8OS runtime-first 设计价值的短报告，必须保留来源，不保存文件。\", \"timestamp\": 1780124414619}]", "artifacts_json": "[]", "content_text": "联网查找 2-3 个来源，写一段关于 V8OS runtime-first 设计价值的短报告，必须保留来源，不保存文件。", "reasoning_text": null, "metadata_json": "{\"run_id\": \"run_9f6eed5fe7994259aac2268cac4b4250\", \"runId\": \"run_9f6eed5fe7994259aac2268cac4b4250\", \"transport\": \"submit\", \"workspace_path\": \"E:\\\\Projects\\\\v8chat\", \"resolved_scope\": \"workspace:main\", \"scope_source\": \"request_explicit\", \"scope_chain\": [\"global\", \"workspace:main\"], \"timestamp\": 1780124414619, \"role\": \"user\", \"clientMessageId\": \"source_write-20260530T070014Z\", \"taskShapeHint\": {\"primaryTaskShape\": \"writing\", \"secondaryTaskShapes\": [\"research\"], \"confidence\": 0.7, \"reason\": \"writing_or_document_terms\", \"suggestedFamilies\": [\"writing\", \"research\"], \"optionalRuntimeGrants\": [\"research.core\"], \"familyScores\": {\"writing\": 0.7, \"research\": 0.48}, \"topFamily\": \"writing\", \"scoreMargin\": 0.22, \"ambiguityFlags\": [], \"autoRevealRecommendation\": {\"eligible\": false, \"families\": [], \"source\": \"task_shape_classifier\", \"reason\": \"below_threshold_or_ambiguous\", \"minConfidence\": 0.9, \"minScoreMargin\": 0.15, \"requireNoAmbiguity\": true}, \"signals\": [\"writing_action:报告\", \"research_secondary:来源\", \"research_secondary:联网\"], \"writingRoute\": {\"present\": true, \"mode\": \"research_then_write\", \"reason\": \"source_backed_writing_requires_research_evidence_first\", \"needsClarification\": false, \"requiresResearch\": true, \"requiresArtifact\": false, \"requiresSkillExecution\": false, \"recommendedFamily\": \"writing\", \"preferredAgentId\": \"\", \"skillName\": \"\", \"firstActionTool\": \"\", \"allowCreateSubagentOnMismatch\": false}, \"lexiconSignature\": \"lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a\", \"policy\": \"hint_only_conservative_auto_reveal_recommendation_no_grant\"}, \"engineeringMode\": \"auto\", \"engineeringTriggerDecision\": {\"mode\": \"auto\", \"active\": false, \"matched\": false, \"deferred\": true, \"reason\": \"deferred_until_background_run_execution\"}}", "version": 1, "created_at": "2026-05-30T07:00:14.622Z", "updated_at": "2026-05-30T07:00:14.622Z", "finalized_at": null, "nodes": [{"id": "source_write-20260530T070014Z:narrative", "kind": "narrative", "role": "user", "content": "联网查找 2-3 个来源，写一段关于 V8OS runtime-first 设计价值的短报告，必须保留来源，不保存文件。", "timestamp": 1780124414619}], "artifacts": [], "metadata": {"run_id": "run_9f6eed5fe7994259aac2268cac4b4250", "runId": "run_9f6eed5fe7994259aac2268cac4b4250", "transport": "submit", "workspace_path": "<REPO_ROOT>", "resolved_scope": "workspace:main", "scope_source": "request_explicit", "scope_chain": ["global", "workspace:main"], "timestamp": 1780124414619, "role": "user", "clientMessageId": "source_write-20260530T070014Z", "taskShapeHint": {"primaryTaskShape": "writing", "secondaryTaskShapes": ["research"], "confidence": 0.7, "reason": "writing_or_document_terms", "suggestedFamilies": ["writing", "research"], "optionalRuntimeGrants": ["research.core"], "familyScores": {"writing": 0.7, "research": 0.48}, "topFamily": "writing", "scoreMargin": 0.22, "ambiguityFlags": [], "autoRevealRecommendation": {"eligible": false, "families": [], "source": "task_shape_classifier", "reason": "below_threshold_or_ambiguous", "minConfidence": 0.9, "minScoreMargin": 0.15, "requireNoAmbiguity": true}, "signals": ["writing_action:报告", "research_secondary:来源", "research_secondary:联网"], "writingRoute": {"present": true, "mode": "research_then_write", "reason": "source_backed_writing_requires_research_evidence_first", "needsClarification": false, "requiresResearch": true, "requiresArtifact": false, "requiresSkillExecution": false, "recommendedFamily": "writing", "preferredAgentId": "", "skillName": "", "firstActionTool": "", "allowCreateSubagentOnMismatch": false}, "lexiconSignature": "lexicon:54434ba2b829f9f3|task-shape:dc2bef528eb9a67a", "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant"}, "engineeringMode": "auto", "engineeringTriggerDecision": {"mode": "auto", "active": false, "matched": false, "deferred": true, "reason": "deferred_until_background_run_execution"}}}}}
{"topic": "agent.started", "payload": {"type": "agent_start", "message_id": "39785b02-588d-49d9-960a-9af015de2ad8", "agent": {"id": "supervisor", "name": "智能主管", "avatar": "http://127.0.0.1:9528/brand-mark.png", "roleLabel": "主理人"}, "node_id": "39785b02-588d-49d9-960a-9af015de2ad8:agent_start:supervisor", "transcript_version": 2}}
{"terminalFacts": {"runStatus": "completed", "runFinishedAt": "2026-05-30T07:01:28.834Z", "runError": null, "activeEpisodes": []}}
```

</details>
