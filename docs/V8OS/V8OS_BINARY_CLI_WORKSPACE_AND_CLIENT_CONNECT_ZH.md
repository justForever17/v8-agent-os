# V8OS 本地入口、CLI、工作区与客户端连接

更新时间：2026-07-07

产品线总纲见：`docs/V8OS/V8OS_PRODUCTIZATION_MASTERPLAN_ZH.md`。
发布版本基线见：`docs/V8OS/V8OS_RELEASE_VERSIONING_BASELINE_ZH.md`。

## 定位

这篇文档只说明 V8OS 本地产品入口和客户端连接边界。核心原则很简单：

- 本机 Web、Admin、桌宠、CLI 属于本地可信入口，不需要像手机一样扫码配对。
- Phone 是唯一远程交互入口，需要通过 Admin 生成的一次性二维码配对。
- Network Supervisor 的多设备协作是另一件事，不是普通用户打开本机 V8OS 的登录方式。

## 当前已落地

### 本机 CLI 入口

仓库根目录已经提供：

```powershell
.\v8os.cmd --help
.\v8os.ps1 --help
```

可用命令：

```powershell
.\v8os.cmd start
.\v8os.cmd stop
.\v8os.cmd status --json
.\v8os.cmd doctor --json
.\v8os.cmd repair --dry-run
.\v8os.cmd config phone manifest --json
.\v8os.cmd config mcp list
.\v8os.cmd config models roles
```

当前 CLI 仍是源码树产品入口，不是正式安装器，也不是系统服务。它负责统一启动、停止、诊断和查看配置，不直接读取或写入 Engine 数据库。

### 本机服务看护

`v8os start` 默认看护：

- Engine：`9530`
- Admin：`9528`
- Web：`9527`

CyberCore / 桌宠属于可选本地伴随端，不作为基础启动失败的阻塞项。

CLI 会把自己启动的进程记录在：

```text
~/.v8-agent-os/runtime/cli/processes.json
```

如果端口被外部进程占用，CLI 不会擅自杀进程，只会报告占用情况。

### 本地 Web / 桌宠连接

Web 和桌宠是本地可信客户端：

- 不走 Phone 配对票据。
- 不出现在已配对设备列表。
- 不需要用户手动复制连接地址。
- 通过本机 Admin BFF / 本机可信会话进入。

Web 是桌面版本地聊天界面；桌宠是会话运行状态伴随器，负责会话监听、动作反馈、语音发送和播报。二者都不需要用户理解端口或配对链接。

### Phone 配对

Phone 是唯一需要配对的远程交互端。

流程：

1. Admin 顶部“连接手机”生成一次性二维码。
2. Phone 扫码后直接连接。
3. 扫码失败时在 Phone 端显示错误。
4. 备用链接只作为无法扫码时的兜底，不在 Admin 主界面外显。

Phone 登录成功后会保存 server profile。一次网络失败不应删除旧配置，也不应把用户打回空白登录页。

### 配置真相

本地配置源：

```text
~/.v8-agent-os/config.json
```

CLI 的 `config` 命令优先走 Engine/Admin API；离线时只做安全只读或明确的低风险操作。

### 已有验证入口

常用最小验收：

```powershell
node --test apps/v8-agent-os-cli/tests/cli_unit.test.mjs
node apps/v8-agent-os-admin/scripts/verify-instance-pairing.mjs
node apps/v8-agent-os-admin/scripts/verify-device-connect-ui.mjs
```

冷启动验收脚本：

```powershell
node apps/v8-agent-os-cli/tests/scripts/run_v8os_cli_cold_start_smoke.mjs
```

冷启动脚本要求 `9530 / 9528 / 9527` 没有被外部进程占用；如果被占用，它会报告阻塞原因，不会自动清理。

## 近期要做

### CLI 从“骨架入口”补到“日常入口”

近期 CLI 还需要补：

- `v8os chat`：从骨架补到稳定的本机终端对话。
- `v8os sessions`：从骨架补到稳定的会话列出、进入、恢复。
- `v8os inbox`：处理 ask_user / approval。
- `v8os workspace`：从骨架补到稳定的工作区创建、选择、诊断。
- `v8os doctor` 更完整地定位端口、依赖、模型、MCP、Phone 连接问题。
- `v8os repair` 支持更多明确授权后的修复动作。
- `v8os config` 覆盖 MCP 安装/移除、模型角色、Phone 地址候选、常见配置健康检查。

这些能力做完后，CLI 才能接近 Codex CLI / Claude Code 那种“打开终端就能用”的体验。

### 本地入口免打扰

近期还要继续压实：

- Web 不出现登录页和退出登录语义。
- 桌宠不出现连接页、连接历史、手动地址输入。
- Admin 只保留 Owner 管理和控制台语义，不把 Web/桌宠当远程设备配对。
- Phone 只在确实配对失败时显示错误，不暴露长链接作为默认操作。

### 文档和 UI 词汇收口

用户可见面继续使用产品词：

- 主理人中枢
- 编程模式
- 深度调研
- 多媒体创作
- 桌面操作
- 记忆系统
- 网络连接

`runtime_broker`、`delegation_broker`、`compat_ephemeral` 等 canonical id 只保留在诊断、日志、开发文档中。

### Network Supervisor 边界

Network Supervisor 的多设备远程协作、V8 Relay、可信邻居、第三方应用兼容入口，都不能和普通本地打开 V8OS 的体验混在一起。

近期目标：

- 普通用户只看到“手机连接”和“本地打开”。
- 高级用户在网络连接页面管理多设备协作和三方兼容入口。
- OpenAI / Anthropic compat 默认保持三方应用托管模式，不接管三方应用的上下文和工作区。

## 中长期产品化

### 桌面安装包

目标形态：

- Windows / macOS / Linux 桌面应用。
- 一个产品壳统一启动 Engine、Admin、Web 和可选桌宠。
- 用户无需知道端口号。
- 本地 Web/Admin 只接受产品壳签发的本机 trusted session。
- 普通浏览器直接访问受限或只显示引导页。

### 系统服务与后台看护

中长期可做：

- Engine 作为本机服务运行。
- Admin/Web 作为产品壳内置界面。
- CLI 可控制服务启动、停止、日志、升级、回滚。
- repair 支持明确授权后的依赖修复、端口清理、配置恢复。

### TUI

TUI 放在后期。它应类似本地终端交互界面，不依赖 Admin 端，也不是替代 Web/Phone：

- 适合低配机器。
- CLI/TUI 承担基础配置、鉴权、连接管理、会话、inbox 和工作区操作。
- 支持工具状态、审批、文件产物、终端输出。
- 不包含 `computer_use` 与 `RPA` 两个 runtime。
- 复用 Engine 核心和共享契约，不另造一套历史真相。

### 极简二进制轻量版

极简二进制轻量版是长期线，面向老旧机型和低配边缘设备：

- 砍掉桌面版重依赖和非必要 runtime。
- 只保留基础必需 runtime。
- 额外保留 `network_supervisor`，用于连接、转发、协作或边缘状态同步。
- 不承诺 ESP32 级设备直接运行完整 V8OS。

### Phone 远程体验

Phone 长期保持远程入口定位：

- profile 持久保存。
- Tailscale/LAN/手动地址自动探测。
- 离线可看本地 SQLite 历史。
- 网络恢复后增量同步。
- 扫码只用于添加或更新连接配置，不是每次登录的唯一真相。

### 外部标准入口

ACP、OpenAI-compatible、Anthropic-compatible 都是外部接入面：

- ACP：第三方编辑器 / Agent Client 标准接入适配器。
- OpenAI / Anthropic compat：三方应用托管上下文的无状态模型端点。
- 它们都不替换 V8OS 内部运行时、Spec、Memory、Artifact、Phone/Web 时间线。

## 当前边界

已经能做：

- 本机从源码树根目录启动基础服务。
- Web/桌宠本机可信连接。
- Phone 二维码配对。
- 基础 doctor/status/config/repair。

还不能宣称：

- 已有正式安装器。
- 已是系统级后台服务。
- CLI 已具备完整聊天/TUI/工作区会话能力。
- 桌面版已有 GitHub 自动 release、自动更新或代码签名。
- 所有本地入口已经完全免配置。

这几个边界必须在产品化宣传和交付说明中保持清楚。
