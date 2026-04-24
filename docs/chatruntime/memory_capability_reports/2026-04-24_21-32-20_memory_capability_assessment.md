# 记忆能力双轨评分报告

- 生成时间: `2026-04-24_21-32-20`
- 背景文档: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\参与 agent 记忆能力测评.md`
- 统一运行说明: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\ASSESSMENT_DIAGNOSTICS_RUNBOOK_ZH.md`

## 双轨评分

- 对外 benchmark 映射分: `9.8/10`
- 内部 runtime-first 苛刻治理分: `9.0/10`
- 真实 eval 通过率: `100.0%`
- 真实 eval P0 全通过: `是`
- 硬门槛达成: `是`
- LongMemEval 官方成绩: `未产生`

## 总体判断

- 当前报告采用双层评分：守门评分保留当前结构/配置自检，最终结论优先看 `tests/evals` 的真实可复跑评测。
- LongMemEval 只显示 official harness 接入状态；未运行官方 `evaluate_qa.py` 前，不将任何内部结果表述为官方成绩。
- 同 key 覆写、语义 key 归一、项目隔离、external API 隔离已经进入可执行通过状态。
- 未达门槛时，优先排查真实 eval 失败项，其次排查 durable policy 是否仍停留在旧低阈值模板，以及 workflow learning 是否缺少更多 proof-backed 成功样本。

## 真实 Eval Suite

- eval 目录: `E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\tests\evals`
- caseCount: `6`
- passed: `6`
- failed: `0`
- failedCases: `[]`

## LongMemEval Official Harness

- officialRepo: `https://github.com/xiaowu0162/LongMemEval`
- adapterPath: `E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\tests\evals\longmemeval`
- status: `adapter_ready_not_officially_scored`
- adapterReady: `True`
- smokeTestReady: `True`
- officialScoreAvailable: `False`
- supportedSplits: `oracle, longmemeval_s_cleaned, longmemeval_m_cleaned`
- 发布任何 LongMemEval 分数前，必须记录模型、数据版本、split、评估日期和官方评估日志路径。

## 逐项结果

### 同 key 偏好覆写

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"mergedValue": "耐克", "rawValue": "耐克"}`

### 时间维度偏好覆写推荐题

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"mergedValue": "耐克", "injectionPreview": "- language: zh-CN\n- system_name: V8 Agent OS\n- system_slug: v8-agent-os\n- system_author: justForever17\n- favorite_shoe_brand: 耐克", "example": "1 月喜欢阿迪达斯，4 月改喜欢耐克，7 月推荐鞋时必须引用耐克。"}`

### 同义 key 漂移归一

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"keys": ["favorite_shoe_brand"], "note": "canonical registry 会把明确同义 key 归并到同一偏好键，避免长期注入面并存冲突。"}`

### 项目作用域隔离

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"projectA": {"language": "zh-CN", "system_name": "V8 Agent OS", "system_slug": "v8-agent-os", "system_author": "justForever17", "preferred_framework": "React"}, "projectB": {"language": "zh-CN", "system_name": "V8 Agent OS", "system_slug": "v8-agent-os", "system_author": "justForever17", "preferred_framework": "Vue"}}`

### 无显式信号时不自动升级到 global

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"decisions": [{"itemType": "preference", "requestedScope": "global", "finalScope": "project:v8", "scopeDecision": "global_rejected_to_current_scope", "rejectedGlobalReason": "missing_explicit_global_signal"}, {"itemType": "preference", "requestedScope": "global", "finalScope": "global", "scopeDecision": "global_promoted", "globalPromotionReason": "explicit_global_signal"}, {"itemType": "knowledge`

### 外部 API 记忆隔离

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"adapterStatus": "extracted", "resolvedScope": "external_api_thread:thread-memory-eval", "persistScope": "external_api_thread:thread-memory-eval"}`

### durable policy 阈值卫生

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `9/10`
- details: `{"preference_importance_threshold": {"current": 35, "default": 35}, "preference_confidence_threshold": {"current": 0.45, "default": 0.45}, "knowledge_importance_threshold": {"current": 35, "default": 35}, "knowledge_confidence_threshold": {"current": 0.45, "default": 0.45}, "global_knowledge_importance_threshold": {"current": 50, "default": 50}, "global_knowledge_confidence_threshold": {"current":`

### 被动注入链的旧结论污染抵抗力

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `9/10`
- details: `{"mergedPreferences": {"language": "zh-CN", "system_name": "V8 Agent OS", "system_slug": "v8-agent-os", "system_author": "justForever17", "assistant_persona": "专业冷静", "favorite_shoe_brand": "耐克"}, "injectionPreview": "- language: zh-CN\n- system_name: V8 Agent OS\n- system_slug: v8-agent-os\n- system_author: justForever17\n- assistant_persona: 专业冷静\n- favorite_shoe_brand: 耐克", "note": "canonical k`

### 失败验证不会直接进 golden path

- status: `pass`
- evidenceType: `config_governance`
- public: `9/10`
- internal: `6/10`
- details: `{"engineering": {"enabled": true, "extractFromProofLedger": true, "requireEngineeringModeForInjection": true, "requireVerifiedProofForActivation": true, "learnFailedVerificationAsAntiPattern": true, "minVerifiedSuccessCount": 2}}`

### 工程 workflow 激活门槛

- status: `pass`
- evidenceType: `config_governance`
- public: `9/10`
- internal: `6/10`
- details: `{"engineering": {"enabled": true, "extractFromProofLedger": true, "requireEngineeringModeForInjection": true, "requireVerifiedProofForActivation": true, "learnFailedVerificationAsAntiPattern": true, "minVerifiedSuccessCount": 2}, "riskTierActivationPolicy": {"read_only": "auto", "low": "auto", "medium": "approval", "high": "approval", "critical": "quarantine"}}`

## 可提升点

### 配置治理问题

- 如果本机仍保留旧低阈值 durable policy，会持续放大一次性噪音和低置信偏好的进入概率。

### 提取与归一问题

- 主干 canonicalization 已具备，但仍建议继续扩 canonical registry，降低更多长尾 key 漂移。

### scope / policy 问题

- 作用域隔离方向已经收紧，但 external/network 与 global promotion 的边界仍应持续做守门回归。

### workflow learning 资格问题

- Engineering Workflow Memory 的门槛已正确收紧，但内部苛刻分仍需要更多 proof-backed 成功链路样本来支撑。

## 苛刻考题矩阵

### 偏好覆写题

- 题目: 1 月 30 日用户说喜欢阿迪达斯；4 月 30 日又说更喜欢耐克；7 月 30 日让 supervisor 推荐一款鞋。
- 正确答案标准: 必须优先引用最新偏好耐克，并说明旧偏好已被覆盖。
- 当前评估: 同 key 覆写链路可通过，但如果 extractor key 漂移或旧摘要未刷新，就有答错风险。

### 作用域隔离题

- 题目: 项目 A 偏好 React，项目 B 偏好 Vue；默认工作区没有框架偏好。
- 正确答案标准: 项目 A/B 互不串区，默认工作区也不误带项目偏好。
- 当前评估: 当前 scope chain 与 project preference isolation 基本能做对。

### 一次性噪音题

- 题目: 某轮排障出现临时路径、临时 workaround、临时报错说明。
- 正确答案标准: 不应自动沉淀为长期记忆或全局规则。
- 当前评估: durable policy 已收紧到平衡档，但仍应继续用真实排障样本回归，防止一次性 operational 噪音重新混入长期记忆。

### API 隔离题

- 题目: 外部 API 调用里说“以后叫我老板”。随后 phone/web 普通对话中继续打招呼。
- 正确答案标准: 外部 API 偏好不得投影成 phone/web 的人格记忆。
- 当前评估: network supervisor 专用 memory adapter 当前隔离方向是对的。

### 工程绕路清洗题

- 题目: 工程任务先失败验证、后纠偏成功。
- 正确答案标准: golden path 只保留成功链，失败步骤进入 anti-pattern/warning。
- 当前评估: Phase 6 配置门槛方向正确，但仍需要更多 proof-backed 实战数据来完全坐实。

## 可复跑入口

- 运行脚本: `E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\scripts\export_memory_capability_assessment.py`
