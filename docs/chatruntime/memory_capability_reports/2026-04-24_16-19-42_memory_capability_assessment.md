# 记忆能力双轨评分报告

- 生成时间: `2026-04-24_16-19-42`
- 背景文档: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\参与 agent 记忆能力测评.md`
- 统一运行说明: `E:\Projects\v8chat\v8-agent-os\docs\chatruntime\ASSESSMENT_DIAGNOSTICS_RUNBOOK_ZH.md`

## 双轨评分

- 对外 benchmark 映射分: `6.8/10`
- 内部 runtime-first 苛刻治理分: `5.6/10`

## 总体判断

- 当前系统不是“没有记忆”，而是“基础能力有了，但对冲突更新、语义归一、噪音阈值治理还不够稳”。
- 同 key 覆写、项目隔离、external API 隔离这几条已经具备不错基础。
- 真正拖分的点主要是 durable policy 阈值过低、同义 key 漂移、旧摘要/旧结论可能在时间跨度题里压过新事实。

## 逐项结果

### 同 key 偏好覆写

- status: `pass`
- evidenceType: `executed`
- public: `10/10`
- internal: `10/10`
- details: `{"mergedValue": "耐克", "rawValue": "耐克"}`

### 时间维度偏好覆写推荐题

- status: `partial`
- evidenceType: `analysis`
- public: `6/10`
- internal: `4/10`
- details: `{"note": "同 key 覆写本身可行，但最终能否在数月后稳定答对，仍依赖 extractor key 稳定、summary 不残留旧偏好、以及新事件成功进入 durable memory。", "example": "1 月喜欢阿迪达斯，4 月改喜欢耐克，7 月问推荐鞋时必须引用最新偏好。"}`

### 同义 key 漂移归一

- status: `pass`
- evidenceType: `executed`
- public: `0/10`
- internal: `0/10`
- details: `{"keys": ["favorite_shoe_brand"], "note": "当前系统不会自动把语义相近的不同 key 归并成同一偏好。"}`

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

- status: `fail`
- evidenceType: `executed`
- public: `2/10`
- internal: `0/10`
- details: `{"preference_importance_threshold": {"current": 18, "default": 35}, "preference_confidence_threshold": {"current": 0.18, "default": 0.45}, "knowledge_importance_threshold": {"current": 20, "default": 35}, "knowledge_confidence_threshold": {"current": 0.2, "default": 0.45}, "global_knowledge_importance_threshold": {"current": 20, "default": 50}, "global_knowledge_confidence_threshold": {"current": `

### 摘要污染抵抗力

- status: `partial`
- evidenceType: `analysis`
- public: `5/10`
- internal: `2/10`
- details: `{"memoryConfig": {"extraction_temperature": 0.2, "recall_strategy": "balanced", "recall_top_k": 3, "retrieval_threshold": 0.2, "passive_injection_enabled": true, "passive_context_profile": "balanced", "passive_summary_enabled": true, "passive_memory_map_enabled": true, "passive_recent_activity_teaser_enabled": true, "passive_recent_activity_teaser_limit": 2, "passive_memory_map_node_limit": 4, "ma`

### 失败验证不会直接进 golden path

- status: `pass`
- evidenceType: `config_governance`
- public: `8/10`
- internal: `6/10`
- details: `{"engineering": {"enabled": true, "extractFromProofLedger": true, "requireEngineeringModeForInjection": true, "requireVerifiedProofForActivation": true, "learnFailedVerificationAsAntiPattern": true, "minVerifiedSuccessCount": 2}}`

### 工程 workflow 激活门槛

- status: `pass`
- evidenceType: `config_governance`
- public: `7/10`
- internal: `4/10`
- details: `{"engineering": {"enabled": true, "extractFromProofLedger": true, "requireEngineeringModeForInjection": true, "requireVerifiedProofForActivation": true, "learnFailedVerificationAsAntiPattern": true, "minVerifiedSuccessCount": 2}, "riskTierActivationPolicy": {"read_only": "auto", "low": "auto", "medium": "approval", "high": "approval", "critical": "quarantine"}}`

## 可提升点

### 配置治理问题

- 当前 durable policy 阈值显著低于默认推荐值，容易让一次性噪音或低置信偏好进入长期记忆。

### 提取与归一问题

- `favorite_shoe_brand` 与 `shoe_brand_preference` 这类语义同义 key 目前不会自动归一，是时间跨度题的真实风险点。

### scope / policy 问题

- 作用域隔离方向基本正确，但任何把 global 当默认写入的 extractor 漂移，都会显著放大串区风险。

### workflow learning 资格问题

- Engineering Workflow Memory 的激活门槛方向正确，但要拿高分还需要更多 proof-backed 成功链路样本来证明不会误学失败绕路。

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
- 当前评估: durable 阈值当前偏低，这类题是现阶段最容易翻车的点之一。

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
