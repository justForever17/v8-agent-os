# V8OS TUI / V8OS 终端界面

`@v8-agent-os/v8-agent-os` is the conversation-first terminal client for a
local V8OS Engine. It works in SSH/tmux and on Windows/macOS terminals. The package publishes the
unified `v8os` dispatcher and keeps `v8os-tui` as a direct interactive
compatibility entry. The dispatcher installs the matching portable Engine on
first use, so users do not need a system Python runtime or a second CLI
installation.

## Install / 安装

Requires Node.js 22 or newer. Install the public package and start the unified
Core Base:

```sh
npm install -g @v8-agent-os/v8-agent-os
v8os
```

To install a verified release asset instead:

```sh
npm install -g ./V8OS-TUI-<release-version>.tgz
v8os
```

需要 Node.js 22+。公开 npm 包安装方式：

```sh
npm install -g @v8-agent-os/v8-agent-os
v8os
```

也可以安装 Release 中的已校验 tarball。首次运行 `v8os` 会按 Release
manifest 下载并校验当前平台的便携 Engine；不需要系统 Python，不会再安装
第二套 CLI，也不会在 `postinstall` 阶段启动服务或执行特权操作。离线或国内
网络环境可以设置 `V8OS_ENGINE_MANIFEST_URL=file:///...json` 与 `V8OS_ENGINE_ARCHIVE=/...tar.gz`，或将已验证运行时放到
`V8OS_ENGINE_RUNTIME_DIR`。

`v8os` 也负责 Engine 控制面和非交互命令：

```sh
v8os start
v8os status --json
v8os chat "hello" --json
v8os sessions list --json
```

The npm portable Core Base supports Linux x64 with glibc 2.35+ (tested Ubuntu 22.04/24.04),
Windows x64/arm64 and macOS x64/arm64. Linux ARM64 and the full desktop flow remain
separate release products; do not force an archive for another platform. For a server package
with a manually managed service, use the matching Server release and its
`./install.sh`; that is an alternative deployment path, not a second CLI to
install alongside npm.

For a persistent systemd user service, run `v8os stop` followed by `v8os service install`.
The service command checks the user manager and linger setting before installation.
After updating the npm package, use `v8os restart` for a regular daemon or `v8os service upgrade`
for an installed service; the latter installs the matching Engine before the governed upgrade.
Old runtime directories and user data are preserved. A database schema migration can block
rollback to an older Engine; the service reports this rather than overwriting user data.
Chromium is bundled, but its OS libraries still depend on the host. `v8os doctor` lists
missing libraries and a repair command without silently requesting elevated privileges.

The TUI settings (F3) select a provider, model and workspace. Phone pairing and
Engine identity remain owned by Engine; no Admin page is required for local
chat.

On a fresh Engine, the TUI opens a non-blocking welcome surface. It shows the
Engine, local identity, Supervisor model and workspace readiness separately;
the composer remains available and keeps its draft. Press `F3` or enter
`/setup` for the step-by-step page. Initializing the local owner no longer
opens the Phone page, and the background poller does not repeatedly query
sessions before that identity exists.

当前 npm 便携 Core Base 支持 Linux x64、Windows x64/arm64 和 macOS x64/arm64。
Linux ARM64 与完整桌面流程仍是独立发行物；不要把其他平台 Engine 资产强行复用。
需要手工管理系统服务时，使用匹配版本的 Server Release 和其中的
`./install.sh`，这是另一条部署路径，不是再安装一套 CLI。

进入 TUI 后按 F3 配置 Provider、模型和工作区。Phone 配对和 Engine 身份仍由
Engine 管理，本机对话不要求打开 Admin。

首次连接尚未初始化 owner 时，TUI 会在聊天首屏分别展示 Engine、本机身份、
Supervisor 模型和工作区状态；输入框仍可使用，草稿不会因为打开配置而丢失。
按 `F3` 或输入 `/setup` 进入快速配置页。初始化本机 owner 不再跳转到 Phone
页面，后台轮询也不会在 owner 存在前反复请求会话列表。

## Controls / 操作

| Key | Action | 按键 | 操作 |
| --- | --- | --- | --- |
| Enter / F9 | Send | Enter / F9 | 发送 |
| F8 / Alt+Enter | Toggle or insert multiline mode | F8 / Alt+Enter | 切换或插入多行 |
| Ctrl+P or `/` at empty input | Search commands | Ctrl+P 或空输入 `/` | 搜索操作 |
| Ctrl+B / Ctrl+T / Ctrl+N | Sessions / task details / new session | Ctrl+B / Ctrl+T / Ctrl+N | 会话 / 任务详情 / 新会话 |
| F2 / F3 / F4 / F1 | Inbox / quick setup or settings / connections / help | F2 / F3 / F4 / F1 | 待处理 / 快速配置或设置 / 连接 / 帮助 |
| PageUp / PageDown | Pause or resume transcript follow | PageUp / PageDown | 暂停或恢复消息跟随 |
| Tab / arrows / Esc | Move focus, select, return | Tab / 方向键 / Esc | 切换焦点、选择、返回 |
| Ctrl+C / Ctrl+Z | Close page or clear / undo draft | Ctrl+C / Ctrl+Z | 关闭页面或清空 / 撤销草稿 |
| Ctrl+U / Ctrl+K / Ctrl+W | Delete to line start / end / previous word | Ctrl+U / Ctrl+K / Ctrl+W | 删除至行首 / 行尾 / 前一个词 |
| Ctrl+X | Open the current input in `$VISUAL` or `$EDITOR` | Ctrl+X | 使用 `$VISUAL` 或 `$EDITOR` 编辑当前输入 |
| Ctrl+D | Detach when the draft is empty | Ctrl+D | 草稿为空时退出 TUI |

Enter never approves a pending action by itself. Use the explicit action in the
inbox or operation menu. Closing the TUI detaches the terminal; Engine, Phone
and network peers continue running. A lost submission stays marked as
“result pending confirmation” and is never silently retried.

Enter 不会单独批准待处理动作，必须在待处理页或操作菜单中显式选择。关闭 TUI
只退出终端，Engine、Phone 和组网连接继续运行。发送结果无法核对时会保留“结果待
确认”，不会静默重试。

`--screen-reader` selects the linear, numbered reader surface and works with
`TERM=dumb`. `--no-color` or `NO_COLOR=1` disables styling. Non-TTY calls use
the unified `v8os` dispatcher (`v8os chat`, `v8os sessions`, and other Engine
commands); `v8os-tui --json` only reports the clear `tty_required` diagnostic and
never waits for hidden input.

`--screen-reader` 使用线性编号输出并兼容 `TERM=dumb`；`--no-color` 或
`NO_COLOR=1` 禁用颜色。非 TTY 命令使用统一的 `v8os` 入口（例如
`v8os chat`、`v8os sessions`）；`v8os-tui --json` 只返回明确的
`tty_required` 诊断，不会隐藏等待输入。

`--lang en` 或 `V8OS_LANG=en` 选择英文界面，也可从 Ctrl+P → language 持久化到
本机 TUI 视图；该设置不会修改 Engine、Provider 或其他客户端。未知的 Engine
业务内容保留原文，避免把模型输出误当成 UI 翻译。

## State and safety / 状态与安全

Only the view, scroll anchor and unsent draft are stored locally, under an
Engine-instance-specific TUI state file. Provider secrets are submitted through
Engine hidden fields and are never written to draft history. Attachments are
registered as sources by Engine and generated artifacts are displayed without
being executed.

本地只保存视图、滚动锚点和未发送草稿，并按 Engine 实例隔离。Provider 密钥通过
Engine 隐藏字段提交，不写入草稿历史。附件由 Engine 登记为来源，产物只展示位置，
不会自动执行。

## Development / 开发验证

```sh
npm ci
npm run typecheck
npm test
npm run build
npm pack
```

Run the installed tarball in a real PTY on Linux. The PTY tests cover grapheme
editing, bracketed paste, command suggestions and reader output; they do not
replace real Chinese IME, screen-reader, SSH or tmux validation.

在 Linux 的真实 PTY 中运行安装后的 tarball。PTY 测试覆盖 grapheme 编辑、括号
粘贴、命令候选和读屏输出，但不能替代真实中文输入法、读屏软件、SSH 或 tmux
验收。

Qwen Code interaction notes and licensing boundaries are recorded in
`NOTICE.md` and the V8OS TUI skill references. V8OS adopts the useful terminal
contracts while keeping Engine authorization, message state and runtime facts
in their existing owners.
