# V8OS CLI 命令参考

本文面向通过终端启动、检查和操作 V8 Agent OS 的用户与开发者，内容以当前公开 Preview 和 `main` 分支为准。

## 1. 调用方式

完成安装并把 CLI 加入 `PATH` 后，使用：

```powershell
v8os --help
```

在 Windows 源码仓根目录中，使用仓库自带入口：

```powershell
.\v8os.cmd --help
```

下文统一写作 `v8os`。在源码仓执行时，将它替换为 `.\v8os.cmd` 即可。

## 2. 通用约定

- 带空格的消息和路径需要加引号。
- 支持 `--json` 的命令会输出适合脚本处理的结构化结果；未指定时优先输出人类可读摘要。
- `chat`、`sessions`、`inbox` 和部分 `config` 命令需要本机 Admin 与 Engine 已启动。
- CLI 是本机可信入口，不使用 Phone 配对票据，也不会创建远程设备记录。
- 会话 ID、审批 ID 和配置域名称用于精确操作。普通诊断优先使用人类可读输出，需要自动化时再使用 `--json`。
- 不要把 API Key、访问令牌或 Cookie 写进命令行。需要凭据时，请使用 Web/Phone 中的安全凭据卡或 Admin 配置页。

查看当前版本实际支持的命令：

```powershell
v8os --help
```

## 3. 启动与停止

| 命令 | 用途 |
| --- | --- |
| `v8os` / `v8os start` | 启动 Engine、Admin 和 Web；默认使用开发模式。 |
| `v8os start --mode start` | 使用已有生产构建启动默认服务。 |
| `v8os start --only engine,admin` | 只启动指定组件。 |
| `v8os start --with shell` | 在默认服务之外启动指定可选组件。 |
| `v8os start --all` | 启动源码树内全部已注册组件，主要用于开发验收。 |
| `v8os preview --rebuild` | 停止当前源码树拥有的旧预览进程，重建并启动完整桌面预览。 |
| `v8os preview --no-build` | 使用已经存在的生产构建启动桌面预览。 |
| `v8os stop` | 停止当前源码树管理的服务。 |
| `v8os stop --only engine,admin` | 只停止指定组件。 |
| `v8os restart` | 停止后重新启动默认服务。 |
| `v8os status` | 查看组件、进程和端口状态。 |
| `v8os status --json` | 输出结构化状态。 |

完整桌面验收优先使用：

```powershell
v8os preview --rebuild
```

`preview` 是源码树预览入口，不是稳定版安装器、系统服务或自动更新命令。

## 4. 对话与会话

### 4.1 发送消息

在指定工作区创建或继续任务：

```powershell
v8os chat "检查这个项目并修复构建错误" --workspace "E:\Projects\my-project"
```

向已有会话发送消息：

```powershell
v8os chat "继续完成并运行验证" --session <sessionId>
```

进入交互模式：

```powershell
v8os chat --session <sessionId> --interactive
```

交互模式中输入 `/exit` 或 `/quit` 退出。

常用选项：

| 选项 | 说明 |
| --- | --- |
| `--session <id>` | 使用已有会话。 |
| `--workspace <path>` | 为新会话绑定并信任实际工作区路径。 |
| `--workspace-id <id>` | 使用已有工作区标识。 |
| `--project <id>` | 指定项目标识。 |
| `--interactive` / `-i` | 持续输入消息。 |
| `--timeout <seconds>` | 设置等待完成的超时时间，默认 120 秒。 |
| `--no-wait` | 提交后不等待本轮完成。 |
| `--spec` | 以 Spec 模式发起新任务。 |
| `--safety-approval manual\|reduced\|minimal` | 设置本轮支持的安全确认姿态。 |

### 4.2 查询和恢复会话

```powershell
v8os sessions list --limit 20
v8os sessions show <sessionId>
v8os sessions turns <sessionId> --limit 3
v8os sessions open <sessionId>
v8os sessions resume <sessionId>
v8os sessions resume <sessionId> "继续处理剩余任务"
```

| 子命令 | 用途 |
| --- | --- |
| `list` | 列出最近会话，支持 `--limit` 和 `--json`。 |
| `show` | 查看一个会话的摘要。 |
| `turns` | 查看最近若干轮投影。 |
| `open` | 在 Web 中打开会话。 |
| `resume` | 不带消息时打开会话；带消息时继续执行。 |

## 5. 处理待办确认

查看等待用户处理的问题和审批：

```powershell
v8os inbox list
v8os inbox list --limit 50 --json
```

处理审批：

```powershell
v8os inbox approve <approvalId> --reason "已核对影响范围"
v8os inbox reject <approvalId> --reason "请先缩小写入范围"
```

回答 Agent 问题：

```powershell
v8os inbox answer <askUserId> --answer "采用方案 A"
```

这些命令只处理已经存在的待办项，不会绕过会话自己的权限、Spec 或副作用治理。

## 6. 工作区

```powershell
v8os workspace show
v8os workspace doctor "E:\Projects\my-project"
v8os workspace create "E:\Projects\new-project" --select
v8os workspace select "E:\Projects\my-project"
v8os workspace open
v8os workspace open "E:\Projects\my-project"
```

| 子命令 | 用途 |
| --- | --- |
| `show` | 查看当前选择的工作区。 |
| `doctor [path]` | 检查路径、项目状态和使用条件。 |
| `create <path>` | 创建基本工作区结构；`--select` 同时设为当前工作区。 |
| `select <path>` | 选择已有工作区。 |
| `open [path]` | 在系统资源管理器中打开工作区。 |

工作区显示名称不改变底层路径。路径被物理删除后，V8OS 不会把它伪装成仍然存在，也不会自动重建用户项目。

## 7. 配置

### 7.1 配置域

```powershell
v8os config list
v8os config get system-base
v8os config get engineering-lane
```

`config list` 列出可读取的配置域；`config get <domain>` 返回指定域的当前投影。配置仍以 `~/.v8-agent-os/config.json` 和各独立真相面为准，不建议绕过 Registry 直接批量改文件。

### 7.2 MCP

```powershell
v8os config mcp list
v8os config mcp status
v8os config mcp install my-server --type stdio --command npx --arg -y --arg @example/mcp-server
v8os config mcp install remote-server --type http --url "https://example.com/mcp"
v8os config mcp remove my-server
```

安装参数：

- `--type stdio|http|sse`：默认 `stdio`。
- `stdio` 需要 `--command`，可重复使用 `--arg`。
- `http` / `sse` 需要 `--url`。
- `--disabled`：保存配置但暂不启用。

CLI 会拒绝把疑似密钥直接写入 `--env` 或 `--header`。这不是缺失能力，而是避免密钥进入终端历史、日志和进程参数。

### 7.3 模型

```powershell
v8os config models doctor
v8os config models list --category text --limit 20
v8os config models list --query "vision"
v8os config models roles
v8os config models recommend supervisor --limit 5
v8os config models set-role supervisor <modelRef>
```

| 子命令 | 用途 |
| --- | --- |
| `doctor` | 检查模型接入、能力元数据和近期健康状态。 |
| `list` | 按类别或关键词查看已接入模型。 |
| `roles` | 查看需要模型的角色/功能当前绑定。 |
| `recommend <role>` | 根据当前接入和健康信息给出候选模型。 |
| `set-role <role> <modelRef>` | 更新角色或功能的模型绑定。 |

内置模型目录只用于快捷填写和建议，不会覆盖用户已经保存的真实 endpoint、模型 ID 或能力配置。

### 7.4 Phone

```powershell
v8os config phone show
v8os config phone manifest
```

`show` 查看当前 Phone 配置投影；`manifest` 查看配对所需的公开连接信息。敏感 token 不会通过这些命令回显。

## 8. 诊断、修复和入口

```powershell
v8os doctor
v8os doctor --json
v8os repair --dry-run
v8os repair --yes
v8os logs
v8os open web
v8os open admin
```

- `doctor`：检查安装、配置、端口和关键依赖。
- `repair`：默认只给出修复计划；只有 `--yes` 才应用受支持的修复。
- `logs`：打开或显示 CLI 管理的本地日志位置。
- `open web|admin`：打开本机聊天面或控制台。

## 9. 自动化与故障排查

脚本调用建议：

1. 优先选择支持 `--json` 的查询命令。
2. 检查进程退出码；失败时 CLI 使用非零退出码。
3. 不解析彩色的人类可读输出，也不要依赖内部运行 ID 的显示格式。
4. 不把 `repair --yes` 放进无人工观察的启动脚本。

最短排障顺序：

```powershell
v8os status --json
v8os doctor --json
v8os logs
```

若命令仍失败，请记录所执行的命令、退出码和已脱敏的错误摘要，再到项目 Issue 页面反馈。不要提交 API Key、Phone token、Cookie、完整本地路径清单或私有会话内容。
