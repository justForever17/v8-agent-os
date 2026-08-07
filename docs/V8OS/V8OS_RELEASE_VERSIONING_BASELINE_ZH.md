# V8OS 发布版本管理基线

更新时间：2026-07-08

## 目标

把 V8OS 的发布入口从临时 tag 和自动 changelog，收口为清晰的产品通道、统一版本号、结构化发布说明、校验文件和可复现打包流程。

## 版本通道

### `desktop-preview`

用途：桌面版预览安装包和内部验证。

内容：

- Engine
- Admin 生产构建
- Web 生产构建
- Shell
- 受控桌宠

当前状态：

- 已有源码树 `v8os preview`。
- 已有 Windows x64/ARM64、macOS Intel/Apple Silicon、Linux x64/arm64 的 unsigned preview workflow；每个平台只负责构建和上传工件。
- GitHub tag `v8-os-desktop-vYYYY.MM.DD.N` 在所有构建 job 成功后由 fan-in release job 创建 GitHub Release，上传 Windows 安装包、macOS DMG、Linux AppImage/DEB 和 `SHA256SUMS.txt`。`RUNTIME_PROBE-<platform>.json` 与 `PACKAGE_LAYOUT-<platform>.json` 是 CI 验收证据，保留在对应 workflow artifact，而不占用普通下载资产列表。
- 尚未签名，没有自动更新，不宣传为 stable。

### `desktop-stable`

用途：正式桌面安装包。尚未实现。

最小要求：

- Windows、macOS、Linux 都必须完成对应平台的实体 GUI/权限验收，才可进入 stable。
- 安装后启动不弹终端黑框。
- Shell 是唯一本地产品窗口。
- Engine/Admin/Web/桌宠由 Shell 看护。
- 支持签名、更新、卸载、崩溃日志和修复入口。

### `phone-preview` / `phone-production`

用途：Phone 远程交互端。

当前状态：

- GitHub tag `v8-os-phone-vYYYY.MM.DD.N` 会构建 Android APK；只要 Android 成功，汇总 job 就发布 APK 和 `SHA256SUMS.txt`，iOS 未选择或因非交互签名不可用而失败不会阻断 Android 发布。
- 旧 `phone-v*` tag 只作为历史发布入口，不再用于新版本。

约束：

- Phone 是唯一远程配对入口。
- Android 支持 11 及以上。
- iOS 支持 16.4 及以上；`workflow_dispatch` 可受控构建 IPA，但它不随 Phone tag 发布，直到 Apple 签名与注册设备真实验收完成。

### `tui-experimental`

用途：未来 TUI 版试验。

边界：

- 不依赖 Admin 端。
- CLI/TUI 承担基础配置、鉴权、连接管理、会话和 inbox。
- 不包含桌面操作与 RPA。

### `lite-experimental`

用途：未来极简二进制轻量版。

边界：

- 面向老旧机型和低配边缘设备。
- 只保留基础必需能力。
- 额外保留网络连接能力。
- 不承诺在 ESP32 级设备运行完整 V8OS。

## Tag 规则

新版本统一使用日期型版本号：

```text
v8-os-phone-vYYYY.MM.DD.N
v8-os-desktop-vYYYY.MM.DD.N
```

示例：

```powershell
node scripts/release/prepare-release.mjs --product phone --version 2026.07.08.1
node scripts/release/prepare-release.mjs --product desktop --version 2026.07.08.1
```

默认是 dry-run。真正准备本地提交和 annotated tag 时加 `--apply`：

```powershell
node scripts/release/prepare-release.mjs --product phone --version 2026.07.08.1 --apply
node scripts/release/prepare-release.mjs --product desktop --version 2026.07.08.1 --apply
```

`desktop` 当前默认仍准备 preview 通道，但 tag 已统一为 `v8-os-desktop-v...`。preview/stable 的差异由 release channel、发布说明和 workflow 门禁表达，不再拆成两套桌面 tag。

脚本不会自动 push。推送 tag 前应先完成对应通道的验收。

## 发布说明要求

GitHub Release 正文必须是结构化产品说明，而不是只有自动 changelog：

- 下载对象和适用平台。
- 安装或更新方式。
- 本次版本重点。
- 已知限制。
- `SHA256SUMS.txt` 校验说明。
- 完整 changelog 链接。

自动 release notes 只作为补充，不作为正式发布页主体。

## 桌面发版门禁

桌面 preview 或 stable 发版前必须确认：

1. Admin/Web 使用生产构建。
2. Shell 启动后无 dev server、Turbopack、HMR 日志。
3. Engine 无控制台黑框，日志进入 V8OS 日志目录。
4. Shell 可按登录态进入 Admin 或 Web。
5. 托盘能打开 Web/Admin、启动/退出桌宠、退出 V8OS。
6. 退出后清理受管进程。
7. 产物资源在 Shell/Web 与 Phone 可访问。
8. `SHA256SUMS.txt` 与发布资产同批生成。
9. 每个平台的 `RUNTIME_PROBE-<platform>.json` 必须证明 Engine Python、Admin/Web 生产构建、Shell resources、桌宠构建产物和平台适配依赖存在；`PACKAGE_LAYOUT-<platform>.json` 必须证明安装包内资源布局完整。Git 与 FFmpeg/FFprobe 7.0+ 等未内置依赖必须明确标为 degraded，低于 7.0 或二者缺失任一项均不算满足 V8OS 媒体基线。Linux 的 `xdotool`、`wmctrl` 与 `xclip/xsel` 是 X11 桌面操作的宿主依赖：DEB 必须声明，AppImage 必须在探针中明确提示宿主缺失，不能伪装成已随包提供。
10. Windows 可运行 CI 安装 smoke；macOS/Linux 的构建、包内布局与运行时依赖可在 CI 验证，但 GUI、TCC/辅助功能、X11/Wayland 与窗口管理器行为必须在同平台实体主机另行验收，不能被 CI 构建成功替代。

最小命令：

```powershell
.\v8os.cmd preview --rebuild
.\v8os.cmd status --json
.\v8os.cmd doctor --json
.\v8os.cmd stop
```

## Phone 发版门禁

Phone 发版前必须确认：

- `npm ci` 不再因本地 tarball integrity 失败。
- `npm run typecheck` 通过。
- Android APK 能成功构建；iOS 手动构建须在具备 Apple 签名与注册设备时单独验收。
- 配对、server profile、音频/附件、产物访问 smoke 通过。

### Phone CI 触发矩阵

| 触发 | profile | 构建 | 发布行为 |
| --- | --- | --- | --- |
| `workflow_dispatch` / `android` | 用户所选 | Android APK；`production` 为 AAB | 仅上传临时 artifact |
| `workflow_dispatch` / `ios` | 用户所选 | iOS IPA | 仅上传临时 artifact |
| `workflow_dispatch` / `all` | 用户所选 | Android 与 iOS 并行 | 仅上传临时 artifact |
| `v8-os-phone-vYYYY.MM.DD.N` tag | `preview` | Android APK；iOS job skipped | 汇总 job 在所有 job 终态确定后创建 APK Release |

## 不能宣称的内容

在对应 workflow 和验收完成前，不得宣称：

- 桌面版已进入 stable。
- 已有自动更新。
- 已支持系统服务安装。
- TUI 已可替代 Admin。
- 极简二进制已能在 ESP32 级设备运行完整 V8OS。
