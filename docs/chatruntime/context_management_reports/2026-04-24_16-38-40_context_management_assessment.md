# 超长上下文管理评估报告

- 生成时间: `2026-04-24_16-38-40`
- 当前 supervisor 模型: `doubao-seed-2.0-pro`
- 当前 summary 模型: `deepseek-chat`
- 当前 supervisor 窗口: `1000000`
- 当前 summary 窗口: `1000000`
- stress report 参考: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\system_content_stress_reports\2026-04-24_11-30-55_system_content_stress_report.md`

## 结论摘要

- 主上下文压缩器只压旧的非 system 消息，不压 system persona、workspace rules、extensions route block 这类系统块。
- `[MEMORY SUMMARY] / [MEMORY MAP] / workflow hints` 不是主压缩器的对象，它们通过 `build_session_context(...)` 先被预算裁剪，再作为系统注入进入上下文。
- 当前机器上的 supervisor / summary 模型都报告为超大窗口，因此在真实模型窗口下，极长上下文也可能几乎不触发压缩。
- 这说明当前逻辑方向是合理的，但不能宣称已经完美胜任大型项目超长流程：真正最肥的系统块仍主要靠注入预算治理，而不是主压缩器治理。

## 底层逻辑

### 主上下文压缩器

- 触发依据是 `soft_trigger_ratio / hard_trigger_ratio * resolved model context window`。
- 只压缩旧非 system 消息；保留所有 system messages、最后一条 human、adapter blocks。
- soft 预算默认走 `rule_summary`，hard 预算且 `use_llm_summary=true` 时走 `llm_summary`。

### 被动注入裁剪层

- 入口是 `memory_runtime.build_session_context(...)`。
- 通过 `max_context_tokens` 统一裁剪 `[USER PROFILE] / [MEMORY SUMMARY] / [WORKFLOW HINTS] / [MEMORY MAP] / [RECENT ACTIVITY TEASER]`。
- 工程模式下可以单独 suppress `daily memory / memory map`，但不影响主压缩器的 system-message 保留规则。

## 三场景结果

### 通用日常聊天

- scope: `workspace:main`
- runtimeKind: `chat`
- workspaceRules present: `True`
- workspaceRules tokens: `79`
- passiveContext tokens: `423`
- extensionsRoute tokens: `306`

被动注入模块：
- `USER PROFILE`: `113` tokens
- `MEMORY MAP`: `243` tokens

压缩变体：
- actual: trigger=`within_budget`, applied=`False`, method=`none`
- fallback soft: trigger=`within_budget`, applied=`False`, method=`none`
- fallback hard: trigger=`baseline_refreshed`, applied=`True`, method=`llm_summary`
- fallback hard reuse: trigger=`baseline_reused`, applied=`True`, method=`llm_summary`

### 项目编程

- scope: `project:test1`
- runtimeKind: `chat`
- workspaceRules present: `True`
- workspaceRules tokens: `78`
- passiveContext tokens: `400`
- extensionsRoute tokens: `617`

被动注入模块：
- `USER PROFILE`: `112` tokens
- `WORKFLOW HINTS`: `220` tokens

压缩变体：
- actual: trigger=`within_budget`, applied=`False`, method=`none`
- fallback soft: trigger=`within_budget`, applied=`False`, method=`none`
- fallback hard: trigger=`baseline_refreshed`, applied=`True`, method=`llm_summary`
- fallback hard reuse: trigger=`baseline_reused`, applied=`True`, method=`llm_summary`

### Network API

- scope: `external_api_thread:context-eval`
- runtimeKind: `network_supervisor_openai`
- workspaceRules present: `False`
- workspaceRules tokens: `0`
- passiveContext tokens: `657`
- extensionsRoute tokens: `626`

被动注入模块：
- `USER PROFILE`: `122` tokens
- `WORKFLOW HINTS`: `220` tokens
- `MEMORY MAP`: `243` tokens

压缩变体：
- actual: trigger=`within_budget`, applied=`False`, method=`none`
- fallback soft: trigger=`within_budget`, applied=`False`, method=`none`
- fallback hard: trigger=`baseline_refreshed`, applied=`True`, method=`llm_summary`
- fallback hard reuse: trigger=`baseline_reused`, applied=`True`, method=`llm_summary`

## 合理性判断

- 通用日常聊天：合理。历史聊天在 fallback 小窗口下能被压缩，偏好/总结块则靠被动注入预算控制。
- 项目编程：部分合理。daily/map suppression 能减轻噪音，但真正大型项目里的 runtime registry、tool registry、extensions route block 仍不会被主压缩器处理。
- Network API：合理性提升。workspace-less 场景已经不再误吃默认工作区 rules，但 memory summary/map 仍保留，符合你刚确认的口径。

## 稳定保真结论

- 主压缩器对 system 消息、最后一条用户消息和 adapter block 的保留规则比较稳定。
- 真正的风险不在“压缩错了历史聊天”，而在“系统级大块根本不参与主压缩”，它们只能靠各自的预算治理。
- 因为当前模型窗口极大，真实运行里压缩触发频率偏低，所以不能把这套机制描述成已经完美覆盖大型项目超长流程。

## 可复跑入口

- 运行脚本: `E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\tests\scripts\export_context_management_assessment.py`
- 统一运行说明: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\ASSESSMENT_DIAGNOSTICS_RUNBOOK_ZH.md`
