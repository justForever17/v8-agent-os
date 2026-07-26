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
- 已有 Windows unsigned preview installer workflow。
- GitHub tag `v8-os-desktop-vYYYY.MM.DD.N` 会创建 GitHub Release，上传 unsigned preview installer、zip、`RUNTIME_PROBE.json` 和 `SHA256SUMS.txt`。
- 尚未签名，没有自动更新，不宣传为 stable。

### `desktop-stable`

用途：正式桌面安装包。尚未实现。

最小要求：

- Windows 安装包优先，后续 macOS / Linux。
- 安装后启动不弹终端黑框。
- Shell 是唯一本地产品窗口。
- Engine/Admin/Web/桌宠由 Shell 看护。
- 支持签名、更新、卸载、崩溃日志和修复入口。

### `phone-preview` / `phone-production`

用途：Phone 远程交互端。

当前状态：

- GitHub tag `v8-os-phone-vYYYY.MM.DD.N` 会构建 Android APK，创建 GitHub Release，上传 APK 和 `SHA256SUMS.txt`。
- 旧 `phone-v*` tag 只作为历史发布入口，不再用于新版本。

约束：

- Phone 是唯一远程配对入口。
- Android 支持 11 及以上。
- iOS 支持 16.4 及以上，正式发布仍属后续。

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
9. `RUNTIME_PROBE.json` 必须证明 Engine Python、Admin/Web 生产构建、Shell resources、桌宠构建产物和 Playwright Chromium 存在；Git 与 FFmpeg/FFprobe 7.0+ 等未内置依赖必须明确标为 degraded，低于 7.0 或二者缺失任一项均不算满足 V8OS 媒体基线。

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
- Android APK 能成功构建。
- 配对、server profile、音频/附件、产物访问 smoke 通过。

## 不能宣称的内容

在对应 workflow 和验收完成前，不得宣称：

- 桌面版已进入 stable。
- 已有自动更新。
- 已支持系统服务安装。
- TUI 已可替代 Admin。
- 极简二进制已能在 ESP32 级设备运行完整 V8OS。
