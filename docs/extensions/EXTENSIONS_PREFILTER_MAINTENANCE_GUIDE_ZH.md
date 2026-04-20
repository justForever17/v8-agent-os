# Extensions 预筛维护指南

更新时间：2026-04-20  
适用仓：`E:\Projects\v8chat\v8-agent-os`  
适用范围：`apps/v8-agent-os-engine/runtimes/extensions/*`

## 1. 目的

本文不是阶段性复盘，而是后续持续维护 `Extensions Runtime` 预筛主链的操作手册。目标有三点：

1. 让维护者先按分层诊断定位问题，而不是直接“补一个词”
2. 让 `Skills / MCP / PluginHost` 的预筛策略保持同代，但不混成一套实现
3. 为后续多语言扩展、subagent 复用和异常回归留出稳定演进面

本文只描述当前代码真相、常见断层和建议维护方法，不在这里发明新的 runtime 契约。

## 2. 当前预筛主链真相

### 2.1 暴露单位

当前三条链的暴露单位并不相同：

- Skills：按 `skill` 暴露
- MCP：按 `server` 暴露，命中后展开整 server 工具树
- PluginHost：按 `插件族/工具族` 暴露，命中后展开整族

这意味着三条链虽然共享 query faceting 和 rerank 思想，但不能简单共享“最终候选”的统计口径。

### 2.2 画像缓存形态

当前画像缓存分两类：

- Skills：使用磁盘缓存画像，核心来源在 [loader.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py)
- MCP / PluginHost：使用 runtime 内存级轻量画像缓存，核心来源在 [runtime.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/extensions/runtime.py)

当前应把 Skills 视为“静态资源索引”，把 MCP / PluginHost 视为“动态 inventory 画像”。

### 2.3 Query faceting 真相

当前预筛主链已经不是单纯 `name + description` 文本打分。核心 query 槽位包括：

- `artifactIntent`
- `documentSubIntent`
- `operationIntent`
- `primaryThemeIntents`
- `secondaryThemeHints`

其中：

- `artifactIntent` 决定明确产物型请求的主排序链
- `documentSubIntent` 用于拆开 `office_document` 与 `documentation`
- `operationIntent` 用于区分 `create / edit / analyze / guide / advise`
- `primaryThemeIntents` 主要服务顾问类、方法论类、知识协作类请求

### 2.4 `fetch_skill_instructions` 当前语义

`fetch_skill_instructions` 当前已经不是全文搜索器，而是受控匹配器，匹配顺序为：

1. `exact`
2. `alias / hint`
3. `受控 fuzzy`

它当前的职责是“稳定命中已知 skill 并返回说明”，不是把整个 skills registry 当语义搜索引擎来用。相关实现位于 [loader.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py) 的 `resolve_skill_matches(...)` 与 `fetch_skill_instructions(...)`。

## 3. 维护分层法

### 3.1 先判断问题属于哪一层

当前异常优先按下面 5 类分层：

1. `Stage1 召回缺失`
2. `画像漂移`
3. `documentSubIntent 误判`
4. `Stage2 精排错误`
5. `fetch_skill_instructions 命中失败`

不要在还没分层之前就开始补词表或改排序权重。

### 3.2 推荐排查顺序

统一按下面顺序排查：

1. 先看 query truth 是否正确
2. 再看 faceting 是否正确切槽
3. 再看画像是否失真
4. 最后才看 rerank / alias / fuzzy

如果顺序反过来，最常见的结果就是：

- 用 Stage2 补 Stage1 的洞
- 用 alias/fuzzy 掩盖画像错误
- 用 query 表面词修复其实是 ontology 缺失的问题

### 3.3 各层典型症状

#### `Stage1 召回缺失`

常见表现：

- 明确产物型请求根本没把对应家族拉进 shortlist
- 顾问型请求被无关生成型 skill 淹没
- 零命中后只剩空结果或错误 fallback

优先检查：

- query 传入的真相文本是否正确
- faceting 是否抽出了正确的 `artifactIntent / documentSubIntent / primaryThemeIntents`
- Stage1 的规则词典和画像是否包含该概念簇

#### `画像漂移`

常见表现：

- 某个 skill 看起来“什么都能做”
- `presentation / video / document` 同时挂在大量无关 skill 上
- 顾问类 skill 被误塞进文件产物竞争里

优先检查：

- 主画像字段是否过宽
- 弱证据是否被错误提升为主画像
- cache 是否仍在吃旧 schema 生成的脏数据

#### `documentSubIntent 误判`

常见表现：

- `word / docx / word文档` 命中 docs/spec 协作文档技能
- `docs / spec / RFC / PRD` 却把 office document 技能抬到前面

优先检查：

- `documentSubIntent` 是否已从 query 中稳定抽出
- 对应 family/skill 的画像里是否有正确的 `documentSubIntentHints`

#### `Stage2 精排错误`

常见表现：

- Stage1 shortlist 看起来已经正确，但最终暴露顺序错误
- LLM rerank 把正确候选从前几名挤掉

优先检查：

- 送给 rerank 的 payload 是否过厚或信息失真
- family/profile 摘要是否缺失关键 artifact/theme 信号
- timeout/fallback 是否把系统带回了 Stage1-only 结果

#### `fetch_skill_instructions 命中失败`

常见表现：

- 明明已有 skill，却 only-not-found
- 输入近义名称时命中多个歧义结果
- 表面看像“语义不够强”，实际是 match 阈值或 query variants 不够

优先检查：

- exact / alias / hint 是否已经能覆盖
- fuzzy 候选是否合理但被 ambiguity gap 拦下
- 该问题是否应该修在 registry 别名层，而不是 query rerank 层

## 4. 异常补充方法

### 4.1 什么时候补 query 同义词

适合补 query 同义词的场景：

- 用户表达稳定，但 faceting 没能提取出既有 ontology 概念
- 同一概念存在稳定跨语言表述，如 `ppt / slides / presentation deck`
- 某个词属于 query 输入层的常见说法，而不是 skill 自身画像缺失

不适合靠补 query 同义词解决的场景：

- skill 本身主画像漂移
- 只对某一个个体 skill 生效的私有别名
- 本质上是主题 ontology 还没定义

### 4.2 什么时候补 profile 规则

适合补画像规则的场景：

- 一类 skill 长期被同一种概念误判
- 规则层明明能稳定提取的信息没有进入画像
- `artifact / operation / theme / documentSubIntent` 中某一维缺失

不推荐的做法：

- 为单个 skill 写 if/else 特判
- 直接把弱证据提升为主画像
- 只看正文里的泛词就扩大主画像

### 4.3 什么时候走 refresh-time LLM 辅助画像

适合启用 refresh-time LLM 辅助的场景：

- 规则置信度不足
- 多个主类/主主题冲突
- query 时频繁暴露同一类画像缺口，但规则很难稳定覆盖

不适合的场景：

- 每轮 query 动态补画像
- 用 LLM 替代 ontology 设计
- 用 LLM 掩盖 cache 污染

### 4.4 明确不推荐的修法

以下方式应视为最后手段，默认不进入主链：

- 个体级 skill 特判
- 只修中文表面词，不补概念簇
- 只在 Stage2 rerank 上补排序，不修 Stage1 召回
- 把 `fetch_skill_instructions` 强行扩成全文语义搜索

## 5. 多语言扩展面

### 5.1 这套设计不是“仅中文增强”

当前代码里的 query 同义词、artifact 规则、theme ontology 已经是中英混合。后续扩语言时，不应把它理解成“中文版先修，其他语言以后再说”，而应理解成“概念层已经统一，语言层是别名映射”。

### 5.2 多语言扩展建议按四层维护

#### query 同义词簇

用于把不同语言的表述映射到同一 query intent：

- `ppt / slides / presentation deck / 演示稿`
- `word / docx / word文档`
- `赚钱 / wealth / monetization`

#### ontology 映射

用于保证不同语言最终落到同一概念层：

- `artifactIntent`
- `documentSubIntent`
- `primaryThemeIntents`

#### profile inference 规则词典

用于让 skill/server/family 的画像也能吃到多语言信号，而不是只会匹配某一种语言。

#### regression 样本

每加一种高频语言表达，都应配对应的 query 回归样本，而不是只改词典不补验证面。

### 5.3 维护原则

推荐顺序：

1. 先补概念层
2. 再补语言别名
3. 最后补个体命名差异

不要一上来就把问题归因成“中文词不够多”或“英文没覆盖”，先看是不是概念层根本没定义。

## 6. 关于 supervisor 与 subagent 的 query truth

这节是给后续 chatruntime 治理对齐使用，本轮不实现代码变更。

### 6.1 supervisor 预筛的 query truth

supervisor 侧预筛应继续以**用户原始请求**为真相，而不是 task-planning 包装文本、delegate 包装文本或其他中间提示词残片。

### 6.2 subagent 预筛的 query truth

如果 subagent 未来复用同一套 extensions 预筛机制，它的 query truth 应改为 **delegated task / task brief**，而不是用户原始请求。

理由很简单：

- supervisor 负责理解用户总体目标
- subagent 负责执行 supervisor 切下来的局部任务
- 两者的可见任务边界本来就不同

### 6.3 明确禁止的做法

不建议把 supervisor 收到的原始用户消息直接透传给 subagent 当预筛输入。否则最常见的问题会是：

- subagent 继承了和自己无关的上层语义
- 工具暴露过宽
- 局部任务和总体任务混淆
- rerank 被上游大目标污染

## 7. 建议保留的回归样本

后续每次调整 Stage1 / Stage2 / 画像推导时，建议至少回归这几组样本：

- 明确产物型
  - `帮我生成ppt`
  - `slides`
  - `presentation deck`
  - `帮我生成视频`

- 文档双支型
  - `word`
  - `docx`
  - `docs`
  - `design doc`
  - `RFC`
  - `技术文档`

- 顾问/方法论型
  - `我想赚钱`
  - `我想提升决策质量`
  - `怎么提高组织效率`
  - `怎么写得更有说服力`

- skill 指令命中型
  - `女娲`
  - `思维顾问`
  - `ppt`
  - `elon perspective`

## 8. 维护 checklist

当后续出现新的预筛异常时，建议按这份 checklist 处理：

1. 先确认问题发生在 Skills、MCP 还是 PluginHost
2. 记录原始 query 与当前暴露结果
3. 判断属于 Stage1、画像、documentSubIntent、Stage2，还是 fetch_skill_instructions
4. 先修概念层，再修语言别名
5. 避免个体级 skill 特判
6. 如改动会影响画像结构，确认 cache schema 是否需要升级
7. 补至少一个正例和一个相邻混淆例的回归样本

## 9. 事实源

本文以当前代码真相为准，主要事实源如下：

- [runtime.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/extensions/runtime.py)
- [loader.py](E:/Projects/v8chat/v8-agent-os/apps/v8-agent-os-engine/runtimes/extensions/skills/loader.py)

