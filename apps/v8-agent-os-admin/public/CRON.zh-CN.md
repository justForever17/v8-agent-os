# 定时任务使用说明

Cron 属于 **Automation Runtime** 的正式能力，用来让系统在后台按固定计划执行任务。

## 适合什么场景

- 每天固定时间做巡检
- 每周自动整理项目状态
- 定时触发 Automation Runtime 工作流
- 在无人值守时维持系统级后台维护

## 当前推荐做法

### 1. 优先使用表单计划

如果你只需要“每天”“工作日”“每周”“每月”或“每隔几小时”运行，优先使用页面里的计划表单，不需要手写 Cron 表达式。

只有在这些预设不够用时，才切换到自定义表达式。

### 2. 把长流程交给 Automation Runtime

如果任务本质上是一个运行时流程，而不是单条 shell 命令，优先选择：

- **AutomationRuntime 任务**

适合：

- 任务编排
- 代理工作流
- 需要保留 run/session 语义的处理

### 3. Memory Maintenance 已经是内建能力

页面顶部的 **Memory Maintenance** 是系统内建任务，不再是教学示例。

它负责：

- daily log 整理
- week / month / year 摘要补齐
- durable preference / knowledge / graph 的维护性批处理

这张卡：

- 可以启用 / 停用
- 可以调整执行时间
- 不可删除
- 不可修改目标、类型和核心参数

不需要再配置旧的 nightly memory Python 脚本。

## 执行方式怎么选

### AutomationRuntime 任务

适合完整运行时流程。

示例：

```text
supervisor
```

### 系统命令

适合已经能独立运行的命令。

示例：

```text
node dist/index.js
```

### Python 模块

适合已经在 Engine Python 环境里的模块入口。

系统会调用模块中的 `run(...)`。

示例：

```text
agents.runners.memory_maintenance_job
```

## 什么时候需要 targetBinding / recoveryAnchor

如果你希望任务以显式 wake 方式进入某个目标，而不是普通 nudge，就需要设置：

- `targetBinding`
- `recoveryAnchor`

如果不提供，系统会把很多触发降级成普通 nudge。

## 使用建议

- 高风险任务先降低频率，再观察日志
- 长耗时任务不要设置得过于频繁
- 需要结构化参数时，用 JSON 填写附加参数
- 先验证一次“立即运行”，再长期启用
- 优先让任务进入统一 runtime 主链，而不是散落在临时脚本里

## 排查方向

- 计划时间是否正确
- 执行目标是否能独立运行
- 参数 JSON 是否合法
- 日志里是否有超时、解析失败或治理拒绝
- 如果是 Memory Maintenance，优先去记忆页查看：
  - 摘要缺失数
  - 最近一次补齐结果
  - extractor failure / policy filtered 区分状态
