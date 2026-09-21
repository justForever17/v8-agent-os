# V8OS TUI / V8OS 终端界面

`@v8-agent-os/v8-agent-os` is the conversation-first terminal client for a
local V8OS Engine. It is intended for Linux servers, SSH and tmux. The TUI
does not start Engine, own credentials, or stop the server when the terminal
closes.

`@v8-agent-os/v8-agent-os` publishes the `v8os-tui` command only. The
administrative/automation CLI remains the separate `@v8/agent-os-cli` command
(`v8os`). Keeping these commands separate prevents a global TUI install from
silently replacing `v8os chat`, `v8os service` or JSON automation commands.

## Install / 安装

Requires Node.js 22 or newer. Install the public package:

```sh
npm install --global @v8-agent-os/v8-agent-os
v8os-tui
```

To install a verified release asset instead:

```sh
npm install --global ./V8OS-TUI-<release-version>.tgz
v8os-tui
```

需要 Node.js 22+。公开 npm 包安装方式：

```sh
npm install -g @v8-agent-os/v8-agent-os
v8os-tui
```

也可以安装 Release 中的已校验 tarball。`v8os-tui` 只连接已有 Engine，
不会在 `postinstall` 下载、启动 Engine 或执行特权操作。普通运维和非交互
命令继续使用 server 包随附的 `v8os` CLI：

```sh
v8os service start
v8os chat "hello" --json
v8os sessions list --json
```

First install the matching V8OS server package on the host. Extract it, run
`./install.sh`, initialize the server credentials, and start Engine with
`v8os service install` / `v8os service start`. TUI settings (F3) then select a
provider, model and workspace. Phone pairing and Engine identity remain owned
by Engine; no Admin page is required for local chat.

先在服务器安装匹配版本的 V8OS server 包，解压后运行 `./install.sh`，再按
server README 初始化凭据并执行 `v8os service install` / `v8os service start`。
进入 TUI 后按 F3 配置 Provider、模型和工作区。Phone 配对和 Engine 身份仍由
Engine 管理，本机对话不要求打开 Admin。

## Controls / 操作

| Key | Action | 按键 | 操作 |
| --- | --- | --- | --- |
| Enter / F9 | Send | Enter / F9 | 发送 |
| F8 / Alt+Enter | Toggle or insert multiline mode | F8 / Alt+Enter | 切换或插入多行 |
| Ctrl+P or `/` at empty input | Search commands | Ctrl+P 或空输入 `/` | 搜索操作 |
| Ctrl+B / Ctrl+T / Ctrl+N | Sessions / task details / new session | Ctrl+B / Ctrl+T / Ctrl+N | 会话 / 任务详情 / 新会话 |
| F2 / F3 / F4 / F1 | Inbox / settings / connections / help | F2 / F3 / F4 / F1 | 待处理 / 设置 / 连接 / 帮助 |
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
`TERM=dumb`. `--no-color` or `NO_COLOR=1` disables styling. Non-TTY calls belong
to the server CLI; `v8os-tui --json` only reports the clear `tty_required`
diagnostic and never waits for hidden input.

`--screen-reader` 使用线性编号输出并兼容 `TERM=dumb`；`--no-color` 或
`NO_COLOR=1` 禁用颜色。非 TTY 命令应使用 server 包的 `v8os` CLI；
`v8os-tui --json` 只返回明确的 `tty_required` 诊断，不会隐藏等待输入。

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
