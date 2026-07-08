# V8OS 发布版本管理基线

更新时间：2026-07-08

## 目标

建立 V8OS 的版本通道、打包边界和发版验收基线。当前只有 Phone 端已有 GitHub 自动打包雏形；桌面版仍是源码树预览壳，不能按正式安装包宣传。

## 版本通道

### `desktop-preview`

用途：桌面版源码树预览和内部验证。

内容：

- Engine
- Admin 生产构建
- Web 生产构建
- Shell
- 受控桌宠

当前入口：

```powershell
.\v8os.cmd preview
```

当前限制：

- 不是安装器。
- 不是系统服务。
- 无自动更新。
- 无代码签名。
- 无桌面 GitHub release workflow。

### `desktop-stable`

用途：正式桌面安装包。尚未实现。

最小要求：

- Windows 安装包优先，后续 macOS / Linux。
- 内置或可靠拉起 Engine。
- Admin/Web 使用生产构建。
- Shell 是唯一本地产品窗口。
- 桌宠由 Shell 受控启动/退出。
- 退出 V8OS 能清理受管子进程。
- 支持自动更新或至少可检测新版本。
- 有 GitHub release workflow、artifact、release notes 和回滚说明。

### `phone-preview` / `phone-production`

用途：Phone 远程交互端。

现状：

- `.github/workflows/phone-build.yml` 已支持 workflow_dispatch。
- `phone-v*` tag 会触发 APK artifact 和 GitHub release。

约束：

- Phone 是唯一远程配对入口。
- Android 目标为 11 及以上。
- iOS 目标为 16.4 及以上。

### `tui-experimental`

用途：未来 TUI版试验。

边界：

- 不依赖 Admin 端。
- CLI/TUI 承担基础配置、鉴权、连接管理、会话和 inbox。
- 不包含 `computer_use` 与 `RPA`。
- 不替代桌面版和 Phone。

### `lite-experimental`

用途：未来极简二进制轻量版。

边界：

- 面向老旧机型和低配边缘设备。
- 只保留基础必需 runtime。
- 额外保留 `network_supervisor`。
- 不承诺直接运行完整 V8OS，也不复制桌面版重依赖。

## Tag 建议

当前可用：

- `phone-vX.Y.Z`：触发 Phone APK release。

建议新增：

- `desktop-preview-vX.Y.Z`：触发桌面预览包。
- `desktop-vX.Y.Z`：触发桌面稳定包。
- `tui-vX.Y.Z-alpha.N`：未来 TUI 试验包。
- `lite-vX.Y.Z-alpha.N`：未来轻量二进制试验包。

在桌面 workflow 未实现前，不创建 `desktop-v*` 正式 tag。

## 桌面发版最小闭环

正式桌面发版前必须具备：

1. 构建 Admin/Web 生产包。
2. 打包 Shell。
3. 打包或启动 Engine。
4. 桌宠以 managed mode 被 Shell 控制。
5. 统一托盘可打开 Web/Admin、启动/退出桌宠、退出 V8OS。
6. 日志写入 `~/.v8-agent-os/logs/cli/` 或发布版等价目录。
7. 退出后清理受管进程。
8. doctor 能定位启动失败、端口占用、Electron/Node/Python 依赖问题。
9. GitHub workflow 上传安装包和校验信息。

## 发版前兼容门禁

涉及以下区域的改动，必须额外跑桌面预览验收：

- Admin/Web topbar 标题栏槽位。
- 登录态识别和本地 trusted session。
- Next build/start 生产模式。
- Electron Shell main/preload/tray IPC。
- 桌宠 managed mode。
- 端口和 Admin/Web/Engine URL 投影。
- 产物资源 URL。

最小命令：

```powershell
.\v8os.cmd preview --rebuild
.\v8os.cmd status --json
.\v8os.cmd doctor --json
.\v8os.cmd stop
```

验收点：

- 不出现 `npm run dev`、Turbopack、HMR。
- Shell 能按登录态进入 Admin 或 Web。
- 托盘能启动/退出桌宠。
- 关闭窗口只隐藏，托盘退出才停止服务。
- Phone 配对仍只属于 Phone。

## Phone 发版门禁

Phone 发版前必须确认：

- `npm ci` 不再因本地 tarball integrity 失败。
- `npm run typecheck` 通过。
- Android build profile 可生成 APK。
- 配对、server profile、音频/附件、产物访问 smoke 通过。

GitHub 当前入口：

```text
phone-v*
```

## 不能宣称的内容

在对应 workflow 和验收完成前，不得宣称：

- 已有桌面正式安装包。
- 已支持自动更新。
- 已支持系统服务安装。
- TUI 已可替代 Admin。
- 极简二进制已能在 ESP32 级设备运行完整 V8OS。

## 发布说明要求

每次 release note 至少写清：

- 版本通道。
- 适用平台。
- 包含哪些端。
- 需要用户手动配置什么。
- 已知限制。
- 回滚或卸载方式。
- 对 Phone 配对、本地 trusted client、Network Supervisor 的影响。
