# 女娲 / Einstein Supervisor Plan Pipeline 可读版

来源文件：`2026-04-21_23-24-06_nuwa_einstein_supervisor_plan_pipeline.md`

模拟用户请求：

```text
使用女娲技能调研爱因斯坦生成一个爱因斯坦skill
```

本文件是上一份 dry-run pipeline 的中文可读化解释，不替代原始 JSON 证据。原始 JSON 仍作为审计附件保留在来源文件中。

## 1. 总体结论

这次验证说明 supervisor 主链已经走到正确方向：

- Supervisor 的真实 route 能命中 `huashu-nuwa`，且在 supervisor selected skills 中排第 1。
- `fetch_skill_instructions` 对 `女娲`、`造skill`、`蒸馏爱因斯坦` 和完整用户句子都能解析到 `huashu-nuwa`。
- Safety Guardian 对 `huashu-nuwa` 是 `audit`，不是 `block`；说明读取不应被阻断，真正执行脚本、联网、写 skill home 仍应交给运行时安全 gate。
- Subagent 的 route truth 来自 delegated task brief，不是直接复用用户原始消息。
- 最新导出里没有看到 OpenClaw 日志推断工具、旧 web/s3 分散工具、旧后台 trio 或动作级 desktop 工具回潮。

仍发现两个需要修复的点：

- Subagent skill 列表里 `huashu-nuwa` 变成中位，不是因为 prompt 太长冲淡，而是 inherited skills 合并时按全局 inventory 顺序回填，丢失了父 route 的命中顺序。
- Subagent extensions 预筛 query 使用了完整 task brief，`writeSet / acceptanceContract / context` 中的 `documentation / verification / proposal` 等治理词会污染 Stage1。

## 2. Route Truth

| 项目 | 本轮真相 |
| --- | --- |
| 用户原始请求 | `使用女娲技能调研爱因斯坦生成一个爱因斯坦skill` |
| Supervisor route truth | 用户原始请求 |
| Planner strategy | `delegate` |
| Task Brief ID | `nuwa-einstein-skill-research-001` |
| 被选 subagent | `Research Scout` (`research-scout`) |
| Subagent route truth | delegated task brief |
| Supervisor 最终验收权 | 保留在 supervisor，不下放给 subagent |

## 3. Supervisor 侧扩展命中

Supervisor 侧 selected skills 已经符合预期：`huashu-nuwa` 是核心命中项，并且进入前排。

关键含义：

- Supervisor 不需要猜测女娲是否安装。
- Supervisor 下一步应调用 `fetch_skill_instructions("huashu-nuwa")` 或 `fetch_skill_instructions("女娲")`。
- 在读取女娲说明前，不应直接仿写女娲 workflow。

## 4. fetch_skill_instructions 诊断翻译

| 查询 | 是否命中女娲 | 解释 |
| --- | --- | --- |
| `女娲` | 是 | alias/fuzzy 已能解析到 `huashu-nuwa` |
| `造skill` | 是 | 命中女娲的强触发词 |
| `蒸馏爱因斯坦` | 是 | 命中女娲“蒸馏人物/主题”的能力入口 |
| 完整用户句子 | 是 | 即使包含“使用技能/生成”等噪音，也能回到女娲 |

可读结论：resolver 已经足够让 supervisor 正确进入女娲说明读取链，不应再出现“我没有收到女娲技能”的回答。

## 5. Safety Guardian 结果翻译

Safety verdict 是：

```text
audit
```

这表示：

- 允许读取 skill 说明。
- 需要在审计上记录风险。
- 当前发现主要是 `secret_declaration`：skill 或其 examples/references 中提到了 API key、token、环境变量等配置需求。
- 这不是“女娲本身恶意”或“不能用女娲”。

正确执行纪律：

- 读取 `SKILL.md`：可以。
- 运行脚本、联网研究、写入 `~/.agents/skills`：必须经过后续 runtime/safety gate。
- 如果执行级动作需要密钥或外部网络，应明确告知 supervisor 和用户。

## 6. Planner / Delegation 管线翻译

建议的 supervisor 管线应是：

1. 确认 Extensions Runtime 已暴露 `huashu-nuwa`。
2. 调用 `fetch_skill_instructions("huashu-nuwa")` 或 `fetch_skill_instructions("女娲")`。
3. 按女娲说明生成 bounded task brief。
4. 通过 `delegation_broker` 把任务交给最合适的 subagent。
5. Subagent 只执行自己的 task brief，不重新解释用户的全局请求。
6. Supervisor 汇总、验收、决定是否采纳产物或继续执行写入。

## 7. Task Brief 可读版

| 字段 | 内容 |
| --- | --- |
| Goal | 使用 `huashu-nuwa` 的方法调研 Einstein，并整理 Einstein 人物 Skill 的候选内容与验收清单。 |
| Context | 用户明确要求使用女娲技能；本 dry-run 不执行真实模型、不联网研究、不写入 skill，只验证 planner -> broker -> subagent prompt 与 route truth。 |
| Required Capabilities | `skill_authoring`、`research`、`synthesis`、`documentation` |
| Behavior Scope | `fetch_skill_instructions`、`research_planning`、`skill_authoring_outline`、`verification_contract` |
| Write Set | `~/.agents/skills/einstein-perspective`，但 dry-run 阶段只做 proposal，不写入 |
| Acceptance Contract | 返回紧凑研究/skill-authoring 计划，明确必须先读取女娲说明，最终验收留给 supervisor |

这里要注意：完整 task brief 适合放进 subagent prompt，但不适合原样喂给 extensions Stage1。`documentation / verification_contract / writeSet` 会把预筛带向文档类或验收类 skill。

## 8. Subagent Selection 可读版

本轮选择的是：

```text
Research Scout (research-scout)
```

可解释原因：

- 任务需要 research / synthesis。
- 任务暂不执行真实写入，更偏研究和技能草案准备。
- Supervisor 仍保留最终写入和验收权。

这类选择是合理的。但 subagent 收到的 skill 暴露顺序仍需要修复：父 route 已把 `huashu-nuwa` 放在第一，subagent 不应因全局 inventory 顺序把它挤到中位。

## 9. 本轮意外发现

### 9.1 `[Interactive CLI Rule]` 过长

Subagent Full SYSTEM_CONTENT 中的 `[Interactive CLI Rule]` 仍承担了太多工具说明职责。

建议修法：

- 系统提示只保留 3-5 行硬纪律。
- 复杂细节下沉到 `command_session_broker` 工具 description。
- Subagent prompt 只需要知道：短命令用 `run_system_command`，长任务/交互式 CLI 用 `command_session_broker`，观察 broker JSON，不确定就如实报告。

### 9.2 inherited skill 顺序丢失

父 route 的 selected skills 里 `huashu-nuwa` 是第一，但 subagent prompt 里变成中位。

根因不是 prompt 太长，而是合并 inherited skills 时按全局 skill inventory 扫描，丢失了 `selectedSkillIds / selectedSkillNames` 的原始顺序。

建议修法：

- inherited skills 先按父 route 传入顺序构造 pinned entries。
- 再合并 subagent 自己 route 选出的 skills。
- 总数仍受用户配置上限约束，例如当前 `stage1TopK=10`，不能因为 inherited/pinned 就暴露 16 个。

### 9.3 delegated route query 过宽

Subagent route query 当前包含完整 task brief，导致治理词进入 Stage1。

建议修法：

- Prompt 中继续注入完整 `[DELEGATED TASK PLAN]`。
- Extensions 预筛单独使用窄 query：
  - 优先 `taskBrief.routeQuery`
  - 否则 `goal + 高价值 requiredCapabilities / behaviorScope`
  - 不包含完整 `writeSet / acceptanceContract / long context`

## 10. 验收标准

修复后，下一轮 dry-run 应满足：

- Supervisor selected skills 仍包含并靠前显示 `huashu-nuwa`。
- Subagent selected skills 数量不超过用户配置上限。
- Subagent selected skills 中 `huashu-nuwa` 保持父 route 前排顺序。
- Subagent Full SYSTEM_CONTENT 的 `[Interactive CLI Rule]` 明显变短。
- `command_session_broker` 工具 description 承接原来长规则里的关键观测语义。
- Diagnostics 能区分完整 delegated task query 与 extensions route query。

