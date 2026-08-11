# V8OS 分层产品化开发总纲

更新时间：2026-08-10

## 目标

V8OS 的开发重心从继续堆 runtime 功能，转为把现有能力分层产品化。产品线按使用场景拆开，核心运行真相仍归 Engine 和共享契约管理，不让各端各自发明一套连接、历史、配置或发布流程。

## 当前已落地

### 桌面版预览线

当前进度最高。源码树内已有：

- `v8os preview`：检查并启动 Engine/Admin/Web，打开本地 Shell。
- Shell：本地产品壳预览，统一承载 Web/Admin 入口和全局托盘。
- Web：本地 trusted chat surface。
- Admin：控制台和治理配置面。
- Desktop Pet：受控桌宠伴随器，由 Shell 管理启动/退出。

当前边界：

- `release-manifest.json` schema 2 统一维护版本、通道、tag 与产品目标矩阵；Desktop 六个目标和 Android 当前为 required，iOS 因缺少非交互签名凭据明确 disabled/skipped。
- 已有 Windows x64/ARM64、macOS Intel/Apple Silicon、Linux x64/arm64 unsigned preview 的 reusable 构建链；根工作流负责单一 fan-in GitHub Release，真实 GUI 主机验收仍按平台单独执行。
- 构建 job 只上传安装包与诊断工件；运行时探针和包布局 JSON 只保留在 Actions artifact，最终公开 Release 只展示安装包和统一校验和。
- Linux 发布门禁会把 DEB 安装到 root-owned `/opt`，再以普通用户在隔离的 D-Bus、Secret Service 与 Xvfb 会话中启动 Engine/Admin/Web；真实 Wayland/X11、托盘和窗口交互仍由 Ubuntu 实机验收。
- 仍不是 stable 正式安装包。
- 已有统一 Preview Release 的启动后自动探测与手动检查入口；没有自动下载、静默安装和代码签名。
- 小改 topbar、登录态、生产构建、Shell IPC、桌宠 managed mode 都可能破坏预览壳，必须跑预览验收。

### Phone 远程端

Phone 是唯一远程交互入口：

- 通过 Admin BFF 配对。
- 保存 server profile。
- 支持远程访问、SQLite 本地历史、墓碑同步和恢复。

当前发布架构以 `v8-os-vYYYY.MM.DD.N` 统一 tag 为主：Android 与 Desktop 共同进入 required 门禁；iOS 在完成签名凭据、注册设备和真实安装验收前保持 disabled/skipped。`v8-os-phone-v*` 与 `v8-os-desktop-v*` 只保留两个成功统一发布周期的兼容窗口，旧 `phone-v*` / `desktop-v*` 仅作历史 tag。schema dry-run 或工作流落地不等于首个统一 Preview 已真实成功。

### CLI 源码树入口

CLI 已具备 `start/stop/status/doctor/config/repair/preview/chat/sessions/inbox/workspace` 等骨架。当前定位是源码树产品入口和诊断入口，不是正式安装器或系统服务。

近期要把 CLI 从“能启动和诊断”补到“日常可用”：

- `v8os chat` 能稳定与主理人中枢对话。
- `v8os sessions` 能列出、进入、恢复会话。
- `v8os workspace` 能创建、选择、诊断工作区。
- `v8os inbox` 能处理 ask_user / approval。
- `doctor/repair/config` 覆盖更多真实故障。

## 近期 P0/P1

### P0：桌面预览壳变稳

- `v8os preview` 必须稳定进入生产构建的 Admin/Web。
- Shell 启动页、登录态切换、标题栏、托盘、桌宠受控启动要有 smoke。
- Next standalone 资源只能在构建期预置，安装目录运行期只读；核心服务早退必须显示失败服务和日志入口，并允许重试。
- Windows/Linux/macOS 凭据分别落入 Credential Manager、Secret Service 与 Keychain，不允许明文文件 fallback。
- 退出 V8OS 能清理 CLI 管理的 Engine/Admin/Web/Shell/桌宠进程。
- 预览壳不应出现 Turbopack/HMR 或 `npm run dev` 体验。

### P0：本地 trusted client 边界

- Web/Shell/桌宠不走 Phone 配对。
- Phone 是唯一远程配对入口。
- Network Supervisor 的多设备协作和第三方兼容入口不得混入普通本机使用流程。

### P1：CLI 补成日常入口

- 本地终端聊天、会话、工作区、inbox 可用。
- 配置命令覆盖 MCP、模型角色、Phone 地址候选和健康检查。
- doctor 能定位端口、依赖、模型、MCP、Phone 连接和 Electron 安装问题。
- repair 在明确授权后修复常见问题，并写修复报告。

### P1：发布基线

- schema 2 manifest 是唯一发布身份真相，统一 tag 同批驱动 Desktop 与 Android。
- Desktop/Phone reusable workflows 只产出工件，根 fan-in job 在全部 required 项成功后创建一个 Release；preview 必须标记为 prerelease。
- PR 只跑始终可见的 `CI Gate`，不打全平台安装包、不运行 EAS 或真实 provider。
- EAS/签名 job 绑定受保护的 `release` Environment；密钥只在这些 job 注入。仓库外部 secret 的迁移状态必须另行验收，不能由 workflow 声明代替。
- 桌面版先做 preview channel，再做 stable channel。

## 后续版本线

### 桌面版

目标形态：

- Windows / macOS / Linux 桌面应用。
- 一个 Shell 窗口和一个系统托盘。
- Engine/Admin/Web/桌宠由产品壳统一看护。
- 本机 Web/Admin 只接受产品壳或本地 trusted session。
- 用户无需知道端口、auth secret 或内部服务。

后续基础建设：

- 受签名保护的下载、校验与用户确认安装；Linux DEB 等需要系统包管理器授权的格式不得宣传为静默更新。
- 代码签名。
- 崩溃日志和 doctor 报告。
- stable 安装包与跨平台 release workflow。

### TUI版

TUI 是未来线，不是 Web 的复刻，也不依赖 Admin 端。

边界：

- 由 CLI/TUI 承担基础配置、鉴权、连接管理和会话交互。
- 复用 Engine 核心能力，但不需要 Admin 页面。
- 不包含 `computer_use` 与 `RPA`。
- 适合终端用户、服务器环境和轻量交互。

必须补齐：

- 配置管理与 Admin 等价的关键能力。
- 会话列表、恢复、inbox、审批、附件/产物的终端表达。
- 明确的低副作用工具边界。

### 极简二进制轻量版

这是长期线，面向老旧机型、低配设备和边缘连接场景。

边界：

- 不承诺 ESP32 直接运行完整 V8OS。
- 砍掉重型非必要依赖。
- 仅保留基础必要 runtime。
- 额外保留 `network_supervisor`，用于连接、转发、远程协作或边缘状态同步。

第一阶段目标应是定义 profile、依赖裁剪清单、最小启动和诊断能力，而不是直接复制桌面版。

## 跨线不变量

- Engine 是运行真相，客户端不直连 DB。
- `packages/session-realtime` 是事件和 UI 投影契约。
- `~/.v8-agent-os/config.json` 是本地配置真相。
- Phone 远程、本地 trusted client、多设备协作、三方兼容入口四件事必须分开。
- 人类可见面用产品词；agent 可见面用干净 Markdown；runtime 可见面保留完整 JSON。

## 近期验收清单

- `v8os preview` 冷启动可用。
- schema 2 dry-run 能稳定解析统一版本、required/disabled 目标和兼容 tag。
- 首个 `v8-os-v*` Preview 由单一 fan-in job 发布 Desktop 与 Android，iOS skipped，且 Release 元数据为 prerelease。
- Release 公开资产不包含诊断 JSON；校验和覆盖全部公开下载文件。
- PR 最终 `CI Gate` 始终可见且不读取发布 secret。
- Web/Shell/桌宠不出现配对或手动地址输入。
- Admin 不把 Network Supervisor 远程协作当普通手机连接。
- CLI `chat/sessions/workspace/inbox` 有最小 smoke。
- 文档能清楚说明：当前能做什么、不能宣称什么、下一步补什么。
