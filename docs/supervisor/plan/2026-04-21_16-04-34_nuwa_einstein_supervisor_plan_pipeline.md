# Supervisor Planner / Delegation 约束管线 Dry Run: 女娲 -> 爱因斯坦 Skill

- 时间戳：`2026-04-21_16-04-34`
- 模拟用户发言：`使用女娲技能调研爱因斯坦生成一个爱因斯坦skill`
- 导出口径：Route Dry Run；不调用模型，不执行任务。

## 1. Route Truth

本轮 supervisor route 的输入真相应当是用户原始请求：

```text
使用女娲技能调研爱因斯坦生成一个爱因斯坦skill
```

实际 route 输出显示：

- `huashu-nuwa` 是否进入 selected skills：`False`
- selected skills：`frontend-design, darwin-skill, find-skills, internal-comms, ai-video-generation, ai-avatar-video, docx, pptx, seedance-prompt-en, wechat-account-articles`
- exposed MCP tools：`无`

结论：当前预筛没有把显式“女娲技能 / 造 skill”意图稳定映射到 `huashu-nuwa`，这会导致 supervisor 误以为当前轮次没有女娲技能上下文。

## 2. `fetch_skill_instructions` 精确/模糊命中诊断

```text
[huashu-nuwa]
=== SKILL BLOCKED BY SAFETY GUARDIAN ===
Skill ID: global:67cb9ebfa7543040
Skill Name: huashu-nuwa
Skill Root: C:\Users\sunny\.agents\skills\huashu-nuwa
Source Type: global
Verdict: block
Confidence: 0.98
Skill Trust Score: 0
Audit ID: skillscan_a19095acf645
Reasons:
- 发现 浏览器资料访问（20 个文件）。
- 发现 声明式密钥/环境变量依赖（11 个文件）。
- 发现 大范围破坏性文件系统操作（4 个文件）。
Flagged Files:
- README.md: 浏览器资料访问
- README_EN.md: 浏览器资料访问
- README_ES.md: 浏览器资料访问
- README_JA.md: 浏览器资料访问
- README_KO.md: 浏览器资料访问
- examples/andrej-karpathy-perspective/references/research/01-writings.md: 声明式密钥/环境变量依赖
- examples/andrej-karpathy-perspective/references/research/02-conversations.md: 声明式密钥/环境变量依赖
- examples/feynman-perspective/references/费曼重大决策调研-20260404.md: 浏览器资料访问
- examples/feynman-perspective/references/费曼长对话与即兴思考方式调研-20260404.md: 浏览器资料访问
- examples/ilya-sutskever-perspective/SKILL.md: 声明式密钥/环境变量依赖, 浏览器资料访问
- examples/ilya-sutskever-perspective/references/research/01-writings.md: 声明式密钥/环境变量依赖, 浏览器资料访问
- examples/ilya-sutskever-perspective/references/research/02-conversations.md: 声明式密钥/环境变量依赖, 浏览器资料访问

Safety Guardian 已阻断该 skill 的说明读取。不要继续使用这个 skill，请改用其他 skill、MCP、插件工具或系统工具继续完成当前任务。

[女娲]
=== SKILL BLOCKED BY SAFETY GUARDIAN ===
Skill ID: global:67cb9ebfa7543040
Skill Name: huashu-nuwa
Skill Root: C:\Users\sunny\.agents\skills\huashu-nuwa
Source Type: global
Verdict: block
Confidence: 0.98
Skill Trust Score: 0
Audit ID: skillscan_b11c25feebda
Reasons:
- 发现 浏览器资料访问（20 个文件）。
- 发现 声明式密钥/环境变量依赖（11 个文件）。
- 发现 大范围破坏性文件系统操作（4 个文件）。
Flagged Files:
- README.md: 浏览器资料访问
- README_EN.md: 浏览器资料访问
- README_ES.md: 浏览器资料访问
- README_JA.md: 浏览器资料访问
- README_KO.md: 浏览器资料访问
- examples/andrej-karpathy-perspective/references/research/01-writings.md: 声明式密钥/环境变量依赖
- examples/andrej-karpathy-perspective/references/research/02-conversations.md: 声明式密钥/环境变量依赖
- examples/feynman-perspective/references/费曼重大决策调研-20260404.md: 浏览器资料访问
- examples/feynman-perspective/references/费曼长对话与即兴思考方式调研-20260404.md: 浏览器资料访问
- examples/ilya-sutskever-perspective/SKILL.md: 声明式密钥/环境变量依赖, 浏览器资料访问
- examples/ilya-sutskever-perspective/references/research/01-writings.md: 声明式密钥/环境变量依赖, 浏览器资料访问
- examples/ilya-sutskever-perspective/references/research/02-conversations.md: 声明式密钥/环境变量依赖, 浏览器资料访问

Safety Guardian 已阻断该 skill 的说明读取。不要继续使用这个 skill，请改用其他 skill、MCP、插件工具或系统工具继续完成当前任务。

[造skill]
Error: The requested skill '造skill' was not found in the registry after a freshness check.
Skill inventory revision: f188368e8c4abdcc3d07b795f1ef84a7604b80e7
Visible skill roots:
- C:\Users\sunny\.agents\skills
- C:\Users\sunny\.v8-agent-os\workspace\.agents\skills
Recent skill discovery:
- ai-avatar-video | added | C:\Users\sunny\.agents\skills\ai-avatar-video
- pptx | added | C:\Users\sunny\.agents\skills\pptx
- llm-video | added | C:\Users\sunny\.agents\skills\llm-video
- ai-video-generation | added | C:\Users\sunny\.agents\skills\ai-video-generation
- seedance2-api | added | C:\Users\sunny\.agents\skills\seedance2-api
- vercel-react-best-practices | added | C:\Users\sunny\.agents\skills\vercel-react-best-practices
- algorithmic-art | added | C:\Users\sunny\.agents\skills\algorithmic-art
- canvas-design | added | C:\Users\sunny\.agents\skills\canvas-design
If the skill was just installed, confirm that its SKILL.md lives directly under one of the visible skill roots.

[爱因斯坦 skill]
Error: 找到了多个同名或同引用的 skill，请改用 skillId 或绝对路径精确指定：
- darwin-skill | id=global:c0f140bfdcd7e5cb | source=global | root=C:\Users\sunny\.agents\skills\darwin-skill
- skill-creator | id=global:ea79d371a63649a1 | source=global | root=C:\Users\sunny\.agents\skills\skill-creator
```

关键诊断：

- `huashu-nuwa` / `女娲` 能被 resolver 找到，但被 Safety Guardian 阻断。
- `造skill` 没有通过 alias/fuzzy 命中。
- `爱因斯坦 skill` 被判为 `darwin-skill` 与 `skill-creator` 的歧义候选，而不是女娲。

这说明问题不是单点：既有预筛漏召回，也有 skill 安全拦截，以及 fuzzy/alias 对“造 skill”语义支持不足。

## 3. 建议 Planner 约束管线

### Planner 输入

- 用户目标：使用女娲技能调研爱因斯坦，并生成一个爱因斯坦 skill。
- 运行边界：先确认女娲 skill 可用；若被 Safety Guardian 阻断，不应绕过安全链，而应报告阻断原因并请求用户选择。
- 产物边界：目标产物应是一个可运行的 `SKILL.md`，并可能包含 references/scripts/examples 等辅助资源。

### Broker-ready Task Brief 建议

```json
{
  "taskBriefId": "nuwa-einstein-skill-research",
  "goal": "使用女娲技能调研爱因斯坦生成一个爱因斯坦skill",
  "context": "Route dry run for validating supervisor/subagent prompt composition. The intended workflow is to use the huashu-nuwa skill to research Albert Einstein and generate an Einstein perspective skill. Do not call external models or execute the task during this export.",
  "writeSet": [
    "C:/Users/sunny/.agents/skills/einstein-perspective/SKILL.md",
    "C:/Users/sunny/.agents/skills/einstein-perspective/references/*"
  ],
  "behaviorScope": [
    "research",
    "skill-authoring",
    "synthesis",
    "verification"
  ],
  "requiredCapabilities": [
    "research",
    "skill-creation",
    "skill-authoring",
    "perspective-distillation",
    "huashu-nuwa"
  ],
  "acceptanceContract": "A generated Einstein skill should include clear trigger conditions, research-backed mental models, usage boundaries, verification notes, and should avoid fabricating sources.",
  "dependency": [],
  "parallelGroup": "nuwa-einstein",
  "executionLaneHint": "subagent",
  "preferredAgentId": "",
  "preferredWorkerType": ""
}
```

### 必须执行的前置检查

1. 使用 `fetch_skill_instructions("huashu-nuwa")` 或等价精确名获取女娲说明。
2. 如果返回 Safety Guardian block，停止“假装已加载女娲”，转为向用户解释阻断，并给出可选路径：
   - 暂不使用女娲，改用通用 skill-creator / research flow；
   - 用户调整 skill 安全配置后重新 route；
   - 只读取安全允许的描述级信息，不执行其脚本/ destructive 路径。
3. 若女娲加载成功，planner 再切任务：资料调研、框架提炼、skill 草稿、验证与去重。

### 验收约束

- 不能生成伪造来源或不可验证的爱因斯坦观点。
- 必须包含触发词、使用边界、禁用边界、核心心智模型、验证方式。
- 若写入 skill 文件，应明确写集并避免覆盖现有同名 skill。
- 若使用 subagent，应通过 `delegation_broker` 传结构化 task brief，而不是把用户原始请求整段塞给 subagent。

## 4. 本轮暴露出的遗留问题

- Extensions Stage1/Stage2 对显式 `女娲 / 造 skill` 语义仍有漏召回。
- `fetch_skill_instructions` 可精确定位 `huashu-nuwa`，但 Safety Guardian block 结果没有被 route 阶段提前显式化。
- 若 supervisor 只看 Extensions Runtime 候选，会误判“没有女娲”。更理想的 route 诊断应区分：`not selected`、`selected but blocked`、`resolver can find but safety blocked`。
