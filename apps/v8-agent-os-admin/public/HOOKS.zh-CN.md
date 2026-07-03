# 动作钩子使用说明

动作钩子用于在系统关键时机自动执行命令、脚本或自动化任务。

## 适合什么场景

- 在任务开始前先做检查
- 在任务结束后整理记录
- 在固定事件发生时自动执行脚本
- 把一段固定处理流程接到系统运行中

## 常见工作方式

### 等待完成再继续

先执行钩子，执行完成后主流程才继续。

适合：

- 风险检查
- 代码格式检查
- 需要先确认再放行的流程

### 放到后台执行

不会阻塞当前任务，系统会把它放到后台继续处理。

适合：

- 聊天结束后的整理工作
- 写日志、发通知、生成摘要
- 不需要用户等待的后续任务

## 什么时候会触发

### 会话相关

- `on_supervisor_start`：主流程开始时
- `on_supervisor_thinking_start`：Supervisor 开始产生可识别思考流时
- `on_supervisor_thinking_end`：Supervisor 本轮思考流结束时
- `on_supervisor_end`：主流程结束时
- `on_chat_end`：一轮对话全部完成后

### 智能体相关

- `on_agent_start`：某个智能体开始处理时
- `on_agent_end`：某个智能体处理完成时
- `on_reviewer_start`：审查步骤开始时
- `on_reviewer_end`：审查步骤结束时

### 工具相关

- `on_tool_execute_start`：工具开始执行时
- `on_tool_execute_end`：工具执行结束时

如果想监听多个事件，可以用英文逗号分隔；如果想全部监听，可以填写 `*`。

注意：思考流事件只表示 Supervisor 的模型 reasoning 阶段进入或结束，不代表整轮对话已经完成；如果要做收尾整理，应优先使用 `on_chat_end`。运行中的 Supervisor / 工具事件默认携带来源 `parent_session_id` / `parent_run_id`，不直接抢占当前会话 lane，避免同步 hook 和正在执行的对话互相等待。工具事件围绕实际工具执行，适合审计、阻断或轻量记录，不适合长时间阻塞。

## 断点验收矩阵

配置或排查 Hooks 时，至少确认这些断点：

| 断点 | 期望结果 |
| --- | --- |
| 事件命中 | 事件名拼写正确，`*` 只用于明确需要监听全部事件的场景 |
| Supervisor 开始/结束 | `on_supervisor_start` 和 `on_supervisor_end` 不被后台 hook 误触发成普通聊天 |
| 思考流 | 一次模型 reasoning 流只触发一次 start 和一次 end，不按每个 chunk 重复执行 |
| 工具调用 | `on_tool_execute_start/end` 能拿到工具名，失败或超时不会让 run 静默丢失 |
| 会话来源 | 运行中 hook 保留 `parent_session_id` / `parent_run_id` 作为来源；终态后的 `on_chat_end` 可安全附着原 session |
| 故障处理 | hook 报错应写日志或 runtime 事件，不应污染普通聊天正文 |

## 触发类型怎么选

### 命令行脚本

适合已经能直接在命令行运行的命令。

示例：

```text
python scripts/check.py
```

### Python 脚本

适合已经在 Engine Python 环境里的模块。

系统会调用模块中的 `run(event_name, **kwargs)`。

示例：

```text
core.hooks.my_python_hook
```

### 自动化任务

适合把事件交给一个更完整的自动处理流程。

示例：

```text
agents.memory_agent
```

## 目标怎么填

- 命令行脚本：填可直接执行的命令
- Python 脚本：填模块路径
- 自动化任务：填系统中可导入的自动化入口

不要填写本机绝对文件路径，除非该命令本身就需要。

## 常见建议

- 耗时很长的任务尽量放后台执行
- 风险检查类钩子尽量放在开始前触发
- 一个钩子只做一件事，便于排查
- 先在测试环境验证，再放到长期运行环境

## 出问题时先看什么

- 目标是否填写正确
- 事件名是否拼写正确
- 触发类型是否选对
- 命令或脚本是否能独立运行
- 相关日志里是否有报错信息
