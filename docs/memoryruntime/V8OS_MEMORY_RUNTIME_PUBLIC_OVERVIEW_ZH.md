# V8OS Memory Runtime：九层记忆体系

V8OS Memory Runtime 是一套面向长期智能体运行的九层记忆体系：它不把“记忆”简化为聊天摘要或向量检索，而是将上下文治理、偏好画像、时序日志、行为链记忆、知识库、Artifact Explorer、知识图谱、Code Engineering Memory 与 RPA/ComputerUse 肌肉记忆统一纳入 runtime 可观测链路。系统以 scope 隔离、证据回流和低噪音被动注入为核心原则，让 supervisor、subagent、Network API 与工程治理面在不同任务中按需获得最小充分记忆，而不是把历史信息无差别塞进 prompt。通过 canonicalization、summary contamination audit、workflow proof gating、knowledge graph summary 与 persistent context baseline，V8OS 追求的是一种强大但克制的记忆能力：既能长期保留偏好、事实、流程和执行经验，又能防止旧偏好、失败绕路、跨项目污染和一次性噪音变成错误的长期人格或操作习惯。

## 九层结构

1. **上下文治理与 persistent baseline**  
   面向超长任务的上下文水位管理层。原始会话历史保留给 UI 与审计，模型消费侧使用压缩基线层与最近 raw 对话组合，减少反复重压和长流程细节漂移。

2. **偏好画像**  
   维护用户长期偏好、沟通习惯、语言、工具与工作风格。通过 canonicalization 与 overwrite 规则处理“旧偏好被新偏好覆盖”的时间反转问题。

3. **日志清单**  
   保存按日期、周、月、年组织的时序记忆，支撑长期复盘、跨会话追溯和精确日志读取。

4. **行为链记忆**  
   将重复动作链沉淀为可渐进注入的 workflow hints。它不是脚本替代物，而是低噪音 checklist/bias，并通过 outcome 记录、风险门控和 proof-backed 资格治理避免学坏。

5. **知识库**  
   保存长期事实、项目约定、业务知识与运行时知识条目，支持 scope 隔离、全文检索与被动召回。

6. **Artifact Explorer**  
   管理用户与 agent 生成的文档、报告、图片、代码产物和多模态 artifact，让可复用产物成为长期协作上下文的一部分。

7. **知识图谱**  
   将知识条目之间的关系转为可按需摘要注入的图谱上下文。图谱不做全量 dump，只在 query seed 明确命中时提供小预算事实补充。

8. **Code / Engineering Workflow Memory**  
   面向大项目工程任务，从 Proof Ledger、workset observation、verification evidence 中学习工程链路。只有 verified、proof-backed 且风险允许的链路才有资格进入 active hint。

9. **RPA / ComputerUse 肌肉记忆**  
   记录可复用的 GUI 操作骨架、RPA 路径和 ComputerUse 执行经验，让重复性的外部系统操作从一次性动作变成可治理的 runtime 记忆。

## 设计原则

- **证据优先**：长期记忆不只来自聊天总结，还来自 runtime events、proof ledger、workflow outcomes、graph evidence 与 scope 诊断。
- **按需注入**：supervisor、subagent、Network API 和工程链路只获得当前任务需要的最小充分记忆。
- **作用域隔离**：默认工作区、项目工作区、external API thread 与 global 记忆有明确边界，避免跨项目、跨 surface 串味。
- **强大但克制**：高风险 workflow、失败验证、manual override 和 outside write-set 不会直接晋升为 golden path。
- **可复跑评测**：内部 eval 负责 runtime-first 守门，LongMemEval Official Harness 负责外部可比评测，二者分开呈现，不把自评成绩包装成官方成绩。

## 当前外部评测路线

V8OS 已具备内部 Memory Runtime eval suite，用于验证图谱摘要注入、摘要污染审计、canonical registry、external API 隔离与 workflow learning 资格。下一步是接入 [LongMemEval 官方评测](https://github.com/xiaowu0162/LongMemEval)，将 timestamped history 输入 V8OS isolated memory session，生成官方要求的 `question_id / hypothesis` JSONL，再交由官方 `evaluate_qa.py` 计算分数。

在官方 harness 完整跑通前，V8OS 不声明 LongMemEval 官方成绩；公开材料只应描述架构能力、内部评测覆盖和官方 harness 接入状态。
