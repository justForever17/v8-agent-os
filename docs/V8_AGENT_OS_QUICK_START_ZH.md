# V8 Agent OS 快速入门

V8OS 是桌面优先、本地优先的 Agent 工作空间。桌面 Web 用于聊天、项目任务和产物；Engine 保存会话、执行状态和设备身份；Admin 是按需打开的配置页。Phone 是配对后的远程入口，可以保存多个服务器连接档案。

## 1. 下载与安装

从 [GitHub Releases](https://github.com/justForever17/v8-agent-os/releases) 选择已发布的统一 Preview（`v8-os-vYYYY.MM.DD.N`），按操作系统和 CPU 架构下载，并用同一 Release 的 `SHA256SUMS.txt` 核对文件。

| 平台 | 下载形式 |
| --- | --- |
| Windows x64 / ARM64 | 桌面安装包 |
| macOS 12.3 及以上，Intel / Apple Silicon | DMG |
| Linux x64 / arm64 | AppImage 或 DEB |
| Android | Phone APK |

可下载文件以该次 Release 的资产列表为准，正在构建的 Actions 任务不等于已发布安装包。iOS 尚未提供公开发行；独立 TUI/server 安装包、轻量远程执行器和一键跨设备配置分发也尚未上线。

桌面版仍是 unsigned preview，首次启动可能需要操作系统确认。启动后会自动检查更新，也可从托盘手动检查；下载与安装由用户确认。Linux 凭据存储需要 Secret Service：DEB 声明 GNOME Keyring 依赖，AppImage 需要宿主提供兼容服务。

## 2. 从源码预览

Windows 开发环境需要 Git、Python 3.11 或更高版本、Node.js 22.12.0 或更高版本。从仓库根目录先准备 desktop profile 依赖：

```powershell
$env:V8_AGENT_OS_BOOTSTRAP_INSTALL_ONLY = "1"
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1 --profile desktop --services engine+admin+web
Remove-Item Env:V8_AGENT_OS_BOOTSTRAP_INSTALL_ONLY
```

然后启动桌面预览：

```powershell
.\v8os.cmd preview --rebuild
```

此命令会停止当前源码树拥有的旧预览进程，构建原生 sandbox helper、Admin 与 Web，然后启动 Engine、Web 和 Electron Shell。打开控制台时才启动 Admin。它会重启本仓服务，不是安装器或自动更新命令。

只需要浏览器和服务时，使用：

```powershell
.\v8os.cmd start
.\v8os.cmd open web
```

`start` 默认启动 Engine 和 Web。本机 Web、Shell、桌宠自动连接，不需要 Phone 配对。

| 服务 | 默认入口 |
| --- | --- |
| Engine | `http://127.0.0.1:9530` |
| Web | `http://127.0.0.1:9527`；冲突时在 `19527-19546` 中选择端口 |
| Admin（按需） | `http://127.0.0.1:9528` |

使用 `v8os status --json` 或 `v8os open web` 获取当前 Web 入口，不要在外部脚本中写死回退端口。Engine 和 Admin 的固定端口冲突会明确报错。

裸 `bootstrap.ps1` / `bootstrap.sh` 用于依赖准备和服务启动，不等于完整桌面 Shell。它们默认启动 Engine + Admin；Windows 可显式选择 `--services engine+admin+web`，Linux/macOS bootstrap 当前支持 Engine 或 Engine + Admin。

## 3. 配置模型并开始任务

1. 从桌面打开控制台，在模型中心添加实际可用的供应商和模型，完成连接检查与角色绑定。内置目录仅帮助填写，不覆盖已保存配置。
2. 在 Web 创建任务，选择真实项目目录并确认工作区信任。显示名称不会改变底层路径或读写范围。
3. 说明目标、允许修改的范围和完成标准。日常模式适合问答与短任务；编程模式适合持续项目实施。主理人可直接执行，也可按任务需要委派。
4. 查看执行结果和产物。用户上传属于输入来源；工作区已有文件不会仅因扫描而成为本轮生成的产物。

普通串行文件修改不要求 Git。使用托管并行隔离时，现有非 Git 项目需要显式采用；不会因普通任务静默初始化仓库或替用户提交。当前执行限制不等同于内核级文件系统或离线网络隔离。

Web 工作台支持创意产物画布、图片/视频/音频素材和有源码映射的 UI 修改。Phone 可查看消息和产物，但没有完整画布编辑面。精确抽帧、视频或音频分段需要同一套安装中的 FFmpeg 与 FFprobe 7.0 或更高版本；云端媒体生成还需要配置对应模型或插件。

## 4. 连接 Phone

先在桌面控制台配置手机可达的 HTTPS 网关地址，再生成配对二维码。也可通过本机 CLI 查看 Owner 状态并生成配对信息：

```powershell
.\v8os.cmd config phone owner
.\v8os.cmd config phone pair --base-url https://your-gateway.example
```

首次使用且尚无 Owner 时，先运行 `v8os config phone init`。将一次性配对信息交给自己的 Phone 扫码或粘贴，不要发布到 Issue 或日志。配对由 Engine 管理，Phone 通过 Engine 的受鉴权客户端网关访问会话；关闭 Admin 后仍可使用。

Phone 可以添加、保存和切换多个服务器档案。临时断网时保留草稿与已保存连接；恢复网络后先重连原档案。需要撤销设备时：

```powershell
.\v8os.cmd config phone devices
.\v8os.cmd config phone revoke <deviceId>
```

本机 Web、Shell、桌宠和 CLI 不使用 Phone 配对票据。Network Supervisor 的多设备协作与 Phone 配对是独立配置。

## 5. 插件与配置

在插件管理中心安装所需组件并完成配置、登录和健康检查。`@插件` 可以明确表达使用意图；实际执行仍需要当前任务授权，安装成功不代表所有能力都已可用。

通过 Admin 或 CLI 修改配置，主配置位于 `~/.v8-agent-os/config.json`。Owner、设备、MCP、会话数据库和系统凭据各有对应的管理入口，详见[配置指南](./V8_AGENT_OS_CONFIG_GUIDE_ZH.md)。不要把密钥填入命令行或问题反馈。

## 6. 检查与恢复

```powershell
.\v8os.cmd status --json
.\v8os.cmd doctor --json
.\v8os.cmd logs
```

`status` 检查进程和端口，`doctor` 检查安装、配置和依赖；它们不能代替一次真实任务验证。源码预览构建失败时查看日志，修正依赖后再运行 `preview --rebuild`。使用 `repair --dry-run` 先查看可修复项。

CLI 等待超时不表示后台任务已取消。先用 `v8os sessions show <sessionId>` 或 `v8os sessions turns <sessionId> --limit 3` 查看原任务，避免重复提交。`--no-wait` 只表示提交成功。

反馈问题时附版本、系统/架构、复现步骤和脱敏错误摘要。不要附完整状态库、私有会话、配对票据、密钥或原始诊断日志。

## 7. 继续阅读

- [CLI 命令参考](./V8_AGENT_OS_CLI_REFERENCE_ZH.md)
- [配置指南](./V8_AGENT_OS_CONFIG_GUIDE_ZH.md)
- [API 参考](./V8_AGENT_OS_API_REFERENCE_ZH.md)
- [开发者指南](./V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md)
- [Engine 测试地图](../apps/v8-agent-os-engine/tests/README.md)
