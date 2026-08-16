# V8OS 桌面安装启动与更新探测测试矩阵

## 范围与基线

本矩阵服务桌面产品主线：`Shell + Engine + Admin + Web + 受控桌宠`。当前受控桌宠只在 Windows/macOS 启用；Electron 43 与 V8OS 当前全屏透明交互实现尚无可靠的跨 X11/Wayland 输入区域合同，因此 Linux fail-closed unavailable，核心 Engine/Admin/Web/Shell 不受影响。问题基线是统一 Preview `v8-os-v2026.08.09.3` 在 Ubuntu 24.04 安装后停留于品牌启动页。日志与包结构证明该版本至少存在三类安装包级缺陷：Next standalone 在运行时改写只读/系统安装目录、桌宠 launcher/runtime 资源不闭合、Shell 未把服务早退和 readiness 失败投影到人类错误面。

自动更新在本阶段只表示“自动探测新版 + 手动进入受控 Release 页面”。它不下载安装包、不静默安装，也不绕过 Windows/macOS/Linux 的系统权限与签名门禁。

## 权威入口与失败面

- 运行时资源：`scripts/run-next-with-managed-auth.mjs` 在 build 阶段预置 standalone static/public；start 阶段只读验证。
- 服务生命周期：CLI 记录 spawn/early-exit/liveness，Shell 等待 canonical readiness，并显示失败服务、阶段和脱敏日志文件名。
- 桌宠：Windows/macOS 的打包 Shell Electron 同时提供 GUI runtime 与 Node mode server host，控制通道、descriptor、PID 和 health 必须一致；Linux 的启动与状态必须结构化返回 `unavailable` 和 `linux_desktop_pet_input_passthrough_unreliable`，不生成桌宠 PID、进程或运行时 descriptor。
- 凭据：Windows Credential Manager、Linux Secret Service、macOS Keychain；无文件 fallback，超时 fail-closed。
- 更新探测：Shell 是打包客户端权威入口；Admin 在 Shell 中复用 bridge，独立浏览器才使用鉴权只读 API。两者只接受 schema 2 统一 tag、当前平台资产和 `SHA256SUMS.txt`。
- 恢复：启动错误页提供受控重试；readiness timeout 的重试会先停止对应仍存活的失败服务，再重新启动。更新探测失败不影响本地启动。

## Harness 分层

| 层级 | 目标 | 通过标准 | 不能替代 |
| --- | --- | --- | --- |
| 单元/合同 | 资源预置、进程早退、凭据 helper、update parser、Admin i18n/状态 | 全部 deterministic 测试通过；秘密不进 argv/env/error | 安装包真实启动 |
| 本机生产预览 | `v8os preview --rebuild` | Admin/Web production build、9530/9528 与 Web 默认 9527/受控回退端口、Shell 受控链通过；Windows/macOS 验证桌宠受控链，Linux 验证桌宠结构化不可用；无终端弹窗 | 系统安装目录与跨 OS 行为 |
| GitHub hosted runner | 六个 Desktop 目标 | 每个目标 runtime/layout 通过；四种包格式按下表执行 smoke；失败上传脱敏诊断；结果只记为 `CI package smoke` | 物理桌面、TCC、Wayland/窗口管理器 |
| 物理主机 | Ubuntu/Windows/macOS 用户安装 | 安装、首次启动、窗口/托盘、错误面、退出清理和更新入口可见；匹配流程完成后才能记为 `physical host verified` | 其他架构/发行版 |

## 包格式矩阵

| 平台与格式 | 安装/挂载约束 | 必测行为 | 状态 |
| --- | --- | --- | --- |
| Windows x64/ARM64 NSIS | 静默安装到一次性目录 | Engine/Admin/Web/Shell 就绪；桌宠存活并建立受鉴权控制连接；Credential Manager；卸载与端口清理 | 本地 x64 unpacked 包启动已有证据；当前候选 `CI package smoke` pending，NSIS 安装/卸载与 ARM64 实体机仍 pending |
| Linux x64/ARM64 DEB | 安装到 root-owned `/opt/V8 Agent OS`，普通用户运行 | Secret Service put/read/delete；预占 9527 后选择受控回退端口；Xvfb；Engine/Admin/Web/Shell 稳定；桌宠启动非零拒绝，启动/状态均返回结构化 `unavailable` 和 `linux_desktop_pet_input_passthrough_unreliable`、无 PID/进程/运行时 descriptor；外部端口保留与清理 | 不可用合同已有 `source/contract`；当前候选 `CI package smoke` 与 Ubuntu 22.04/24.04 实体机均 pending |
| Linux x64/ARM64 AppImage | 解包后整个包树 `a-w` | 不写包树；Engine/Admin/Web/Shell 稳定；桌宠启动非零拒绝，启动/状态均返回结构化 `unavailable` 和 `linux_desktop_pet_input_passthrough_unreliable`、无 PID/进程/运行时 descriptor；清理 | 不可用合同已有 `source/contract`；当前候选 `CI package smoke` 与实体机均 pending |
| macOS x64/ARM64 DMG | `hdiutil` 只读挂载 | Keychain put/read/delete；挂载树启动；Engine/Admin/Web/Shell 就绪；桌宠存活并建立受鉴权控制连接；detach | 当前候选 `CI package smoke` pending；TCC/通知与两种架构实体机均 pending |

## 更新探测矩阵

- 版本：同版、升级、降级、跨日、非法日期/N、旧 Desktop/Phone tag。
- Release：preview/stable/draft、缺当前平台资产、缺/非法/冲突 checksum。
- 网络：200、403/429、404、超时、断网、畸形 JSON、1 MiB Release 响应上限、64 KiB checksum 上限。
- 并发与性能：自动检查仅在产品 surface ready 后延迟一次；人工并发合并；无轮询；网络失败不阻断启动。
- Human Surface：能力包弹层高度受视口限制，内部使用自绘滚动条；显示当前/最新版本、检查状态和进入下载页动作，不展示 raw payload。
- 安全：不打包 GitHub token；只打开本仓统一 tag 的受控 URL；不自动下载或执行资产。

## 发布与回滚门禁

1. 本地合同、生产构建、`preview --rebuild`、diff-check 和 reverse-apply 全绿。
2. 修复提交推送后，六平台 Desktop workflow 全绿；诊断 JSON 只留 Actions artifact。
3. 创建新的不可变统一 Preview tag，Android 与 Desktop required gate、fan-in Release、prerelease 和 9 项 checksum 全绿。
4. Ubuntu 24.04 物理机安装新 DEB，首次启动进入产品页且不再停留于品牌页；验证 Engine/Admin/Web/Shell 稳定、桌宠以 `linux_desktop_pet_input_passthrough_unreliable` 保持不可用且无 PID/运行时 descriptor，再验证退出清理与更新区。只有匹配流程完成后才可记为 `physical host verified`。
5. 只有完成第 2 个成功统一发布周期和上述 Ubuntu 实机验收后，才能移除旧 `v8-os-desktop-v*` / `v8-os-phone-v*` 触发与兼容解析。历史 tag/Release 保留，不删除审计记录。

回滚只回退本轮源码提交并重新发布更高版本号，不移动或覆盖已经推送的 tag。旧 `.09.3` 应标记为已知 Linux 启动故障版本，不作为回滚目标。

## 残余风险

- GitHub Xvfb 无法证明真实 Wayland/X11、托盘和窗口管理器行为。
- Linux Desktop Pet 当前为 `blocked`；在可靠输入穿透实现完成并通过真实 X11/Wayland 物理验收前，不得把 hosted CI 的结构化不可用通过改写成桌宠可用。
- unsigned Windows/macOS Preview 仍会触发系统安全确认；没有真正的自动安装链。
- AppImage 无法声明宿主 Secret Service 依赖，缺失时只保证快速、可诊断失败。
- 第一个包含新版探测器的客户端只能在后续更高版本发布后，实证“发现更新”通知链。
