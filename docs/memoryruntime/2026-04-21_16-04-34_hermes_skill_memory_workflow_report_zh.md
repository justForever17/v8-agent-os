# Hermes Skill 路线与 V8OS Memory Runtime 行为链报告

- 时间戳：`2026-04-21_16-04-34`
- 范围：不改代码；基于公开 Hermes 文档与当前 V8OS memory/runtime 事实提出 Memory First 方案。
- 结论一句话：V8OS 不应照搬 Hermes 的“一次复杂成功后直接写 skill”作为默认路径，而应先把重复动作沉淀为可验证、可清洗、可渐进注入的 memory workflow；成熟后再可选 promotion 成 skill。

## 1. Hermes 公开路线摘要

基于公开文档，Hermes 的技能系统把 skill 定义为按需加载的知识文档，并采用 progressive disclosure 来控制 token；技能可以存放在 `~/.hermes/skills/`，也支持外部目录扫描。Hermes 还支持 agent-managed skills：当 agent 完成复杂任务、撞到错误后找到工作路径、被用户纠正或发现非平凡 workflow 时，可以通过 `skill_manage` 创建或更新技能。

Hermes 的 persistent memory 则更像小而稳定的随会话注入事实层：`MEMORY.md` 与 `USER.md` 有字符预算，启动时作为冻结快照进入系统 prompt，适合关键偏好、项目事实和长期环境约定。官方文档也明确区分 skills 与 memory：skill 更偏“如何做”，memory 更偏“事实和偏好”。

参考资料：

- [Hermes Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [Hermes Working with Skills](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills/)
- [Hermes Persistent Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/)
- [Hermes Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills/)
- [Hermes Built-in Tools Reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference/)

## 2. 对 V8OS 的启发与不照搬点

Hermes 最值得借鉴的是“程序性知识可以被 agent 自己沉淀”，不是“每次完成复杂任务就立刻生成 skill”。V8OS 已经有更重的 memory runtime、RAG、知识图谱、cron 夜间整理、workflow ledger、runtime events 和 tool surface 治理，所以更适合走 Memory First：先把动作链作为 memory workflow 候选沉淀，再在成熟后升级为 skill。

不建议直接照搬的原因：

- V8OS 的 skill 生态有 Safety Guardian、Extensions 预筛、SkillLoader 增量发现和多 runtime gate；自动写 skill 的副作用比 Hermes CLI 单体更大。
- V8OS 的 memory runtime 已经能记录长期事实与 operational workflow，直接生成 skill 会绕过 memory 的去重、衰减、冲突标记和夜间清洗。
- V8OS 的用户场景包含 os-phone/os-web/admin、多 agent、external worker、OpenClaw/pluginhost；错误动作被沉淀成 skill 的代价更高。

## 3. 当前 V8OS 可用雏形

当前 memory extraction prompt 已包含 `operational_workflow` 类目，会要求提取可复用的 Computer Use、tool、browser、runtime、approval、media/upload 等流程，并排除瞬时噪音。memory maintenance cron 也已经承担夜间整理角色，适合扩展为 workflow candidate 聚类与清洗层。

这意味着 V8OS 不需要先从零做 skill learning，而可以在 memory runtime 中增加三层：

1. `workflow episode`：单轮/单任务执行轨迹摘要。
2. `workflow candidate`：跨 session 聚类后的候选动作链。
3. `guided chain hint`：面向未来运行时的渐进式下一步提示。

## 4. Memory First 三层数据模型建议

### 4.1 Workflow Episode

每个 episode 不是完整 transcript，而是 compact execution trace：

- `episodeId`
- `taskFamily`
- `initialUserIntent`
- `firstActionSignature`
- `runtimeLane`
- `toolsOrSkillsUsed`
- `orderedActionSummary`
- `failureMarkers`
- `userCorrectionPoints`
- `finalSuccessEvidence`
- `userVerdict`
- `sideEffectScope`
- `privacyScope`

Episode 的用途是“给夜间整理看的原材料”，不直接注入 supervisor。

### 4.2 Workflow Candidate

多个 episode 聚类后形成 candidate：

- `candidateId`
- `taskFamilySignature`
- `canonicalTriggerPatterns`
- `firstActionTriggers`
- `goldenPathSteps`
- `antiPatterns`
- `verificationSteps`
- `successCount`
- `correctionCount`
- `negativeFeedbackCount`
- `lastSeenAt`
- `maturityScore`
- `dedupeSignature`
- `status: candidate | active_hint | quarantine | promoted_skill_candidate`

### 4.3 Guided Chain Hint

Guided hint 是真正运行时消费的层：

- `whenMatched`
- `nextStepHint`
- `toolBias`
- `skillBias`
- `doNotDo`
- `confidence`
- `sourceCandidateId`

它只提供“下一步”或“下一小段”提示，不一次性把完整 workflow 灌进 prompt。

## 5. 动作链成熟门槛

建议满足以下条件才从 episode 晋升为 active workflow candidate：

- 至少跨 session 或跨任务族重复成功 2-3 次。
- 有明确验收证据，例如文件存在、测试通过、用户确认、runtime verification contract 成功。
- 低负反馈：没有被用户明确否定，或否定已被清洗成 caveat。
- task family 稳定：不是一次性路径、临时文件、一次性环境变量造成的偶发成功。
- dedupe signature 一致：动作链结构相似，而不是仅文本相似。
- side effect 可控：不会把高风险 destructive 操作变成默认建议。

## 6. Errorful-Success Distillation：多次错但最终成功的清洗归档

新增重点：当本轮对话中 supervisor 多次错误操作、误用工具、绕路或被用户纠偏，但最终执行成功时，memory runtime 不应原样沉淀失败轨迹。

建议流程：

1. 标记 episode 为 `success_after_corrections`。
2. 抽取错误类型：例如错误工具、错误 route、错误写集、错误路径、错误 runtime lane、误判 skill 可用性。
3. 抽取用户纠偏点：用户说“不是这个”“应该先 route”“别用底层工具”等。
4. 抽取最终成功路径：只保留必要步骤、验证方式和正确前置条件。
5. 生成 compact workflow：`应做 / 不应做 / 验证方式`。
6. 把失败步骤归入 `antiPatterns` 或 `caveats`，而不是未来默认动作。
7. 只有最终成功有明确验收证据时，才允许进入候选归档；否则进入 quarantine。

示例归档形态：

```yaml
status: active_hint
episodeClass: success_after_corrections
taskFamily: extensions_skill_route_debugging
do:
  - 先检查 route truth 与 candidate_summary
  - 再调用 fetch_skill_instructions 做精确解析
  - 若 resolver 命中但 Safety Guardian block，向用户报告 block 而不是假装 skill 可用
doNotDo:
  - 不要仅凭 Extensions Runtime 未显示就断言 skill 未安装
  - 不要绕过 Safety Guardian 执行 blocked skill
verification:
  - route summary 中出现 selected/blocked/not-selected 的可解释状态
```

## 7. 渐进式 next-step hint 注入

动作链不应在用户发言后一次性全部注入。推荐按“意图或首动作触发”渐进式推进：

- 用户意图阶段：只给 route/skill/tool 选择偏置。
- 首动作完成后：根据第一步结果给第二步 hint。
- 遇到历史 anti-pattern：插入短 caveat，例如“历史上这里常误用 X，优先检查 Y”。
- 验收阶段：给 verification hint，而不是重放完整 workflow。

这会提升 agent 后续动作被正确选中的概率，同时避免 prompt 变成流程大全。

## 8. 夜间 Cron / Memory Agent 维护职责

夜间 memory maintenance 应增加以下任务：

- episode 聚类与 dedupe。
- 成熟度评分与衰减。
- success_after_corrections 清洗成 golden path。
- 错误链 quarantine。
- 与已有 knowledge graph 条目冲突检测。
- 将高成熟 workflow 输出为 `active_hint`。
- 仅在用户确认或高成熟度条件满足时，标记 `promoted_skill_candidate`，但不默认写 skill。

## 9. Skill Promotion：未来可选，不是默认

当 workflow candidate 达到高成熟度后，可以提供三种升级路径：

1. 继续作为 memory guided hint，适合短流程和运行时路由偏置。
2. 生成人类可审阅的 playbook 文档，适合中等复杂流程。
3. 用户确认后生成/更新 skill，适合长流程、有模板/脚本/引用资源的程序性知识。

默认不自动生成 skill，尤其不能把一次成功或一次 errorful-success 直接变成 skill。

## 10. 建议验收样例

- 用户多次要求“新装 skill 后立即使用”：memory 应提示先做 delta reload / fetch exact / safety 状态检查。
- 用户多次纠正“不要直接调用底层工具”：memory 应在类似任务下一步提示 route-first / broker-first。
- 某流程第一次成功但无用户验收：只能 episode 记录，不进入 active hint。
- 某流程先错三次后成功：进入 success_after_corrections，夜间清洗后只保留 golden path 与 anti-pattern。
- 新 workflow 与旧 workflow 冲突：进入 conflict review，不自动注入。

## 11. 推荐实施顺序

1. 扩展 memory episode schema，先记录 compact workflow trace。
2. 夜间 maintenance 增加 workflow candidate 聚类与 errorful-success distillation。
3. supervisor prompt 注入增加极轻量 `guided_chain_hint`，只给下一步。
4. runtime 事件里记录 hint 是否被采纳、是否导致成功或负反馈。
5. 成熟后再评估 skill promotion UI/流程。

## 12. 结论

Hermes 的亮点是把 agent 的经验沉淀成可复用 procedural memory；V8OS 更合适的路径是先把这件事纳入 memory runtime 的治理体系。也就是说：先让 memory runtime 学会“识别动作链、清洗错误绕路、渐进式提示下一步”，再决定哪些候选值得变成 skill。这样比“一次复杂任务后自动写 skill”更稳，也更符合 V8OS runtime-first、可恢复、可观测的主线。
