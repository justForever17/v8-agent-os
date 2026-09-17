# V8OS TUI

以对话为主的终端界面，连接本机 V8OS Engine。默认单栏；会话与任务详情按需展开。

## 安装

需要 Node.js 22+。下载 Release 中的 npm 包，在任意目录安装：

```sh
npm install -g ./v8-agent-os-tui-0.1.0.tgz
v8os-tui
```

公开 npm 包名登记后再启用 registry 安装；当前先分发 tarball。本包不会在
postinstall 下载、启动 Engine 或执行特权操作。普通 `v8os` CLI 仍支持 Node 20；
已装 CLI 时可用 `v8os tui` 惰性启动独立终端包。

首次使用需要安装 server 发行包：下载对应 Linux x64 server 包、解压，执行包内
`./install.sh`，再按其 README 初始化凭据并运行 `./v8os service install`。
已有服务用 `v8os service start` 启动。TUI 的 F3 设置可连接 Provider/模型并确认
已有工作区目录。身份、授权、配置和后台任务均由 Engine 管理，无需 Admin 页面。

## 操作

| 按键 | 操作 |
| --- | --- |
| Enter / F9 | 发送；多行模式用 F9；粘贴后显式 F9 发送 |
| F8 / Alt+Enter | 多行模式开关 / 插入换行 |
| Ctrl+P / `/` | 可搜索操作菜单，键被终端占用时可用菜单 |
| Ctrl+B / Ctrl+T / Ctrl+N | 会话列表 / 任务详情 / 新会话 |
| F2 / F3 / F4 / F1 | 待处理 / 设置 / 连接 / 帮助 |
| PageUp / PageDown | 滚动并暂停自动跟随；菜单可回到底部、载入更早历史 |
| Tab / 上下键 / Esc | 切换字段、选择动作 / 返回 |
| Ctrl+C / Ctrl+Z | 关闭页面或清空输入 / 撤销清空 |
| Ctrl+D | 空草稿退出终端，后台服务继续运行 |

停止任务使用菜单“停止当前任务”；Engine 确认之前显示请求中。审批默认选择
“返回”，只有用户选中批准才提交。断线恢复只读取状态，不会自动重发消息、
审批或配置修改。未能核对的发送会保留“结果待确认”和草稿。

附件通过 Engine 上传登记为来源，生成产物显示位置，均不会自动执行或打开。
Phone 配对票据只在添加页面显示，关闭页面撤销未消费票据。

`--no-color` / `NO_COLOR=1` 禁用颜色。`--screen-reader` 使用线性输出和编号菜单，
兼容 `TERM=dumb`。重绘模式需要 stdin/stdout 均为 TTY；非交互场景使用
`v8os chat`、`v8os sessions list --json` 或 `v8os inbox list --json`。

视图和未发送草稿保存在状态目录 `runtime/tui/view-<instance-hash>.json`，按 Engine
实例隔离。旧的未绑定 `view.json` 保留原处，不会自动导入另一实例。
模型密钥只通过隐藏表单提交 Engine，不进入草稿文件。Unix 文件权限为 0600。

审批模式默认沿用 Engine / 当前会话的设置；操作菜单可以显式选择逐项审批、减少
审批或免审。累计预算、上下文窗口和模型单次输出长度在设置中分别编辑；模型输出
参数会影响使用同一模型的全部角色。

## 开发与验证

```sh
npm ci
npm run typecheck
npm test
npm run build
npm pack
# Linux：对实际安装的 bin 运行真实 PTY
python3 tests/terminal_pty.py --node /path/to/node --bin /prefix/lib/node_modules/@v8/agent-os-tui/bin/v8os-tui.mjs
# 显式隔离状态目录、已配置真实 provider
V8_AGENT_OS_HOME=/path/to/isolated-state npm run test:live
```

共享投影与 Engine 客户端在构建时从同一仓库打包，不依赖 `file:../../` 或源码目录。
Qwen 研究版本和采用边界见 NOTICE.md。PTY 字符测试不等于真实中文 IME、读屏软件
或移动设备验收；各发布版本分别报告实际覆盖平台。
