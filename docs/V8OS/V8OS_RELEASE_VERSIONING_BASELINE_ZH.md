# V8OS 发布版本管理基线

更新时间：2026-08-16

## 目标

把 V8OS 的发布入口从临时 tag 和自动 changelog，收口为清晰的产品通道、统一版本号、结构化发布说明、校验文件和可复现打包流程。

`release-manifest.json` schema 2 是发布身份和目标矩阵的单一结构化真相。顶层 `release.version`、`release.channel`、`release.tag` 必须彼此一致；Desktop、Phone 及其平台目标只声明是否启用、是否为发布必需项。各产品的 `package.json`、Phone 原生 build number 和安装包文件名是该真相的投影，不得反向形成第二套版本源。

根 `VERSION` 仅保留为 schema 2 生成的兼容 semver 投影，供仍会探测该文件的外部工具过渡使用；它不参与 plan 或 tag 决策。`prepare-release --from-manifest` 会校验投影一致性，准备新版本时会同步重写；完成两个成功统一发布周期后评估删除，禁止手工双写维护。

## 版本通道

### `desktop-preview`

用途：桌面版预览安装包和内部验证。

内容：

- Engine
- Admin 生产构建
- Web 生产构建
- Shell
- 受控桌宠（Windows/macOS；Linux 当前 fail-closed unavailable）

当前状态：

- 已有源码树 `v8os preview`。
- 已有 Windows x64/ARM64、macOS Intel/Apple Silicon、Linux x64/arm64 的 unsigned preview reusable workflow；每个平台只负责构建并上传本次 workflow run 的工件，不直接创建 GitHub Release。
- Linux Desktop Pet 当前因 Electron 43 与 V8OS 全屏透明交互实现尚无可靠的跨 X11/Wayland 输入区域合同而 fail-closed，稳定原因码为 `linux_desktop_pet_input_passthrough_unreliable`；Engine/Admin/Web/Shell 不受影响，且不得为启用桌宠而关闭 Chromium sandbox 或引入未经验证的穿透回退。
- schema 2 当前把 Desktop 及六个桌面目标全部标记为 `required`。统一发布只有在这些目标和 Android 都成功后才可进入最终 fan-in Release。
- 最终 Release 上传 Windows 安装包、macOS DMG、Linux AppImage/DEB、Android APK 和统一的 `SHA256SUMS.txt`。`RUNTIME_PROBE-<platform>.json` 与 `PACKAGE_LAYOUT-<platform>.json` 是 CI 诊断证据，只保留在 GitHub Actions artifact，不进入普通用户的 Release 下载资产列表。
- 尚未签名，不宣传为 stable。客户端可自动探测统一 Preview Release，并由用户手动进入受控下载页；没有自动下载或静默安装。

### `desktop-stable`

用途：正式桌面安装包。尚未实现。

最小要求：

- Windows、macOS、Linux 都必须完成对应平台的实体 GUI/权限验收，才可进入 stable。
- 安装后启动不弹终端黑框。
- Shell 是唯一本地产品窗口。
- Engine/Admin/Web 与平台上已启用的桌宠由 Shell 看护。
- 支持签名、更新、卸载、崩溃日志和修复入口。

### `phone-preview` / `phone-production`

用途：Phone 远程交互端。

当前状态：

- schema 2 当前把 Android 标记为 `enabled: true`、`required: true`；它与 Desktop 一起进入统一发布门禁。
- iOS 因尚未配置非交互签名凭据，明确标记为 `enabled: false`、`required: false`。统一发布中它必须是 disabled/skipped，不会以失败状态阻断 Desktop 和 Android。
- Phone 的独立手动入口与根发布工作流复用同一个本地 composite action；它们只构建所选平台并上传工件，最终 Release 仍由根发布工作流统一创建。统一与过渡期兼容 tag 都只进入根发布工作流，Phone workflow 不再暴露已经实证无法承载 Environment secret 的 `workflow_call` 接口。

约束：

- Phone 是唯一远程配对入口。
- Android 支持 11 及以上。
- iOS 支持目标为 16.4 及以上；在 Apple 签名凭据、注册设备和真实安装验收完成，并显式更新 manifest 前，不进入统一发布矩阵。

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

新版本使用日期型版本号，并由一个统一 tag 触发同一版本的 Desktop 与 Phone 构建：

```text
v8-os-vYYYY.MM.DD.N
```

其中年份限定为 2000–2099，`N` 为同日严格递增的 1–99 且不带前导零。这个固定宽度约束保证 Android `versionCode` 的数值单调性；`prepare-release` 同时拒绝等于或低于当前 manifest 的版本，避免跨产品投影降级。

旧的产品 tag 在过渡期仍可触发对应产品的兼容发布：

```text
v8-os-desktop-vYYYY.MM.DD.N
v8-os-phone-vYYYY.MM.DD.N
```

兼容入口自首个成功的统一 Release 起保留两个成功统一发布周期，随后按专门的废弃变更移除。周期按 GitHub 上实际成功的统一 Release 计数，不在准备版本时静默递减；旧 `desktop-v*` / `phone-v*` 仅作历史记录，不重新启用。截至 `v8-os-v2026.08.09.3` 只完成第 1 个成功周期，且该版本已在 Ubuntu 24.04 实机暴露安装后启动故障；修复版完成统一发布和 Ubuntu 实机安装启动验收前，不得提前移除旧产品 tag 兼容入口。

准备统一版本时，默认命令是 dry-run：

```powershell
node scripts/release/prepare-release.mjs --version 2026.08.08.1 --channel preview
```

真正准备本地提交和 annotated tag 时加 `--apply`：

```powershell
node scripts/release/prepare-release.mjs --version 2026.08.08.1 --channel preview --apply
```

脚本会同步更新 schema 2 manifest 和全部已启用产品的版本投影，只创建 `v8-os-v...` 统一 tag。当前治理入口只接受 `preview`；在签名、受控下载/安装和 stable 实机安装门禁落地前，manifest、prepare 与 plan 都会拒绝 `stable`，防止 unsigned preview 被发布为正式 latest。

脚本不会自动 push。推送 tag 前应先完成对应通道的验收。

## 统一发布编排

根工作流 `.github/workflows/release.yml` 是发布编排入口：

1. `plan` 读取并校验 schema 2 manifest，解析统一或过渡期兼容 tag。
2. Desktop reusable workflow 只构建并上传桌面工件；Phone 的凭据承载 job 由根工作流直接绑定 `release` Environment，并与独立 Phone workflow 复用无权限的本地 composite action。两条构建路径都不拥有 Release 写权限。
3. `release-gate` 汇总所有必需产品结果；当前 Desktop 全目标和 Android 任一失败都会阻断发布，disabled iOS 必须保持 skipped。
4. 唯一的 `publish` job 下载同一 workflow run 的工件、重算统一校验和、过滤诊断 JSON，再一次性创建 GitHub Release。

手动 `workflow_dispatch` 的 `dry-run` 只解析 manifest 和矩阵，不构建、不发布；`build` 可验证完整构建但不创建 Release。只有受支持的 tag push 才允许 `publish`。

`preview` channel 创建的 GitHub Release 必须设置 `prerelease: true` 且不能成为 latest；只有通过 stable 门禁的 stable channel 才能成为正式 Release。工作流、schema dry-run 或本地测试通过都不等于真实统一 Preview 已发布成功，仍需以 GitHub Actions 全部必需 job、Release 元数据和下载资产实测为准。

## PR CI 与密钥边界

Pull Request 只运行始终可见的最终 `CI Gate`：静态检查、单元/合同测试、manifest/matrix dry-run 和轻量客户端 smoke。PR 不运行 EAS、全平台 Electron 打包、真实 provider 或高成本 live 测试，也不得声明或读取发布密钥。

Web 当前的 PR 门禁执行 TypeScript、i18n 与完整客户端合同测试，但暂不执行全量 ESLint。干净检出基线中的 Web ESLint 仍有两类存量失败：CommonJS 合同测试被 TypeScript 规则误扫，以及 Canvas 源码中的 Hooks 规则错误。该缺口登记为 P1 技术债，必须在下一迭代为 CommonJS 测试配置准确的 ESLint override，并逐项修复真实源码错误；禁止通过全局关闭规则掩盖。连续两次干净检出通过后恢复 Web lint 门禁。Admin 与 Phone lint 继续作为当前 PR 阻断项。

EAS 与未来签名 job 必须显式绑定受保护的 GitHub `release` Environment，`EXPO_TOKEN` 和签名材料只能作为该 Environment 的 secret 注入这些 job；普通构建、plan、gate 和 PR job 不得获得这些 secret。本仓 `.3` 统一 tag 的 root-to-called workflow 组合已经实证：called workflow 内声明的 Environment secret 为空，而相同 job 的直接 `workflow_dispatch` 可以正常读取。因此统一发布必须由根工作流中的普通 Android/iOS job 直接承载 Environment，构建步骤只通过不读取 `secrets` context 的本地 composite action 复用；凭据预检为空时立即失败，禁止恢复仓库级 secret 或使用 `secrets: inherit` 绕过。

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
5. 托盘能打开 Web/Admin、退出 V8OS；Windows/macOS 还必须能启动/退出桌宠，Linux 必须显示桌宠不可用且不能提供启动动作。
6. 退出后清理受管进程。
7. 产物资源在 Shell/Web 与 Phone 可访问。
8. `SHA256SUMS.txt` 与发布资产同批生成。
9. 每个平台的 `RUNTIME_PROBE-<platform>.json` 必须证明 Engine Python、Admin/Web 生产构建、Shell resources、桌宠构建产物和平台适配依赖存在；`PACKAGE_LAYOUT-<platform>.json` 必须证明安装包内资源布局完整。Git 与 FFmpeg/FFprobe 7.0+ 等未内置依赖必须明确标为 degraded，低于 7.0 或二者缺失任一项均不算满足 V8OS 媒体基线。Linux 的 `xdotool`、`wmctrl` 与 `xclip/xsel` 是 X11 桌面操作的宿主依赖：DEB 必须声明，AppImage 必须在探针中明确提示宿主缺失，不能伪装成已随包提供。
10. Windows 必须由 NSIS 静默安装到一次性目录后启动安装树应用，验证 Engine/Admin/Web/Shell、桌宠存活与受鉴权控制连接以及退出清理；该证据仍不替代用户真机的安装器交互与系统安全提示。Linux x64/arm64 必须把 DEB 安装到 root-owned `/opt/V8 Agent OS`，再以普通 runner 用户在独立 D-Bus、Secret Service 与 Xvfb 会话中启动；同一构建还必须解包 AppImage、把整个包树改成只读。Linux 两种格式都必须证明 Engine/Admin/Web 在 90 秒内就绪、Shell 持续存活，并证明桌宠启动被非零退出码拒绝，启动与状态响应均为结构化 `unavailable`、携带 `linux_desktop_pet_input_passthrough_unreliable`，且没有桌宠 PID、存活进程或运行时 descriptor。macOS 必须以只读方式挂载 DMG，在挂载树内完成 Keychain round-trip，并验证 Engine/Admin/Web/Shell、桌宠存活与受鉴权控制连接以及退出清理。上述 hosted runner 结果只能记为 `CI package smoke`；TCC/辅助功能、真实 X11/Wayland、托盘、窗口管理器及安装器交互必须在同平台实体主机另行验收，不能据此标记为 `physical host verified`。

### Linux DEB 非 root 启动门禁与跨平台凭据合同

Admin/Web 的 Next standalone 静态资源必须在 production build 完成后预置；安装包运行时只验证 `server.js`、`.next/static` 与 `public` 完整性，不得删除、创建或复制 `/opt`、AppImage 挂载目录或 macOS app bundle 内的文件。Shell 和桌宠所需 launcher 必须作为显式 package resources 进入布局门禁。任一核心服务在 spawn 后立即退出时，CLI 不得写入“已启动”记录；Shell 应尽快显示失败服务、阶段和 `~/.v8-agent-os/logs/cli/<file>` 日志引用，并允许用户重试，不能继续显示与正常启动相同的空白品牌页直到超时。

凭据存储按宿主平台固定选择：Windows Credential Manager、Linux Freedesktop Secret Service、macOS Keychain。禁止自动选择明文/文件型 keyring，也禁止在安全存储不可用时把密钥回退到 `config.json`。Linux DEB 声明 `gnome-keyring` 与登录解锁集成依赖；AppImage 使用者必须自行提供可用的 Secret Service 实现和用户 D-Bus 会话。Linux/macOS 原生调用由短命 helper 承载并受 6 秒硬超时约束，密钥只经匿名 stdin/stdout 传输；写入或删除超时必须标记为结果不确定，携带原 reference 供后续对账，不能偷偷生成另一条引用。CI 使用一次性凭据执行 put/read/delete，不把测试密码、keyring 数据或环境输出带入 artifact。安全存储缺失属于可诊断的启动失败，不得伪装成模型或 checkpoint 故障。

### Linux pyatspi 兼容性技术债登记

`pyatspi2` 未发布可由 portable Python 直接解析的 PyPI 包。Linux desktop preview 因此从 GNOME 官方固定版本归档复制纯 Python `pyatspi` 前端，并以 GNOME 发布的 SHA-256、预期包目录、许可证文件和导入探针共同阻断来源或布局漂移。归档下载失败不得降级为缺失 AT-SPI；许可证必须保留在 `.python/THIRD_PARTY_NOTICES/pyatspi2-COPYING`，Linux x64 与 ARM64 的包内布局和 runtime probe 都必须通过。

该路径只适用于 Linux 打包，不改变共享 Python requirements。上游若提供可验证且适配 portable runtime 的发行包，先在两个 Linux 架构上完成安装、包内导入和桌面运行探针，再移除手工复制；连续两个成功桌面发布周期通过后关闭本登记。

### Windows ARM64 兼容性技术债登记

适用范围仅限 Windows ARM64 unsigned desktop preview。`langgraph-checkpoint-sqlite` 3.1.1 声明依赖 `sqlite-vec>=0.1.6`，但 `sqlite-vec` 0.1.9 未在 PyPI 发布 `win_arm64` wheel 或 sdist，原生 Windows ARM64 Python 因此无法完成常规依赖解析。

V8OS 当前只使用 `SqliteSaver` 与 `AsyncSqliteSaver` 的普通 SQLite checkpoint 表，不导入 `sqlite_vec`、`vec0` 或 SQLite extension loading；向量记忆由独立向量存储实现。受控兼容入口不修改共享 requirements 真相，只在 Windows ARM64 打包临时副本中精确过滤一次该依赖，随后固定安装已审计的 `langgraph-checkpoint-sqlite==3.1.1`，并使用原生 ARM64 Python 执行同步与异步 checkpoint 写入、读取和关闭后重开读取。`pip check` 只允许当前这一条已登记缺口，其他依赖缺失必须阻断安装包构建。运行时探针把 checkpoint saver 作为必需能力，把 `sqlite_vec` 明确记录为 degraded 可选能力，不能伪装成已随包提供。普通 Windows ARM64 开发或 CLI 安装仍应使用原始 requirements 并明确暴露上游解析失败，不能绕过此受控打包入口。

`tiktoken` 0.13.0 同样未发布 `win_arm64` wheel，但在 PyPI 提供 sdist。Windows ARM64 打包入口使用固定的 `setuptools-rust==1.13.0`、runner 原生 Rust 工具链，以及经 SHA-256 固定校验的 Python 官方 `pythonarm64` 3.11.9 NuGet 开发包构建 `tiktoken==0.13.0`。必须验证 wheel 标签为 `win_arm64` 且原生扩展可导入，再进入完整 requirements 解析；构建所需的头文件和导入库必须在安装依赖后移除并重新验证原生扩展，禁止复用 x64 wheel、把开发 SDK 带入最终包，或把源码构建失败降级为缺失 tokenizer。

`chromadb` 1.5.9 未发布 `win_arm64` wheel；未固定版本并使用 `--prefer-binary` 时，pip 会在该平台选择仍有通用 wheel 的旧版 `chromadb`，导致不同桌面平台使用不同存储后端。桌面发布 profile 因此固定 `chromadb==1.5.9`。Windows ARM64 打包入口使用最后一个仍为 CPython 3.11 发布 `win_arm64` wheel 的 `numpy==2.4.6`、`maturin==1.14.1`、runner 原生 Rust 工具链和 Python 3.11.9 开发包，从经 SHA-256 固定的官方 sdist 构建当前 Rust/ABI3 wheel；构建矩阵恢复 Chroma 1.5.9 源码标签声明但 PyPI sdist 未携带的 Rust 1.92.0，仅 Windows ARM64 使用该 pin，其余桌面目标继续使用 stable。若改用当前 Rust 1.97.1，未带上游后续 `recursion_limit` 属性的 1.5.9 blockstore 会在类型布局查询阶段失败。构建环境显式暴露嵌入式 Python 的 `Scripts` 目录，并同时提供 CPython 3.11 与 stable ABI 所需的 `python311.lib`、`python3.lib`。Chroma 的 protobuf 代码生成使用经 SHA-256 固定的 Protocol Buffers 官方 `protoc` 35.1 Windows x64 构建工具，并在 Windows on ARM runner 上先执行版本检查；该工具只参与构建且不会进入安装包。Chroma 上游同平台 wheel 构建还要求把 Visual Studio ARM64 LLVM 的 `clang.exe` 显式加入构建 PATH，并给 `simsimd` 传入 Windows SDK 架构宏 `CFLAGS_aarch64_pc_windows_msvc=-D_ARM64_=1`；V8OS 在进入 Rust 编译前验证 clang 可执行，缺失时立即阻断。

Chroma 1.5.9 的 `Cargo.lock` 仍锁定 `generator` 0.8.8；该版本已声明 Windows ARM64 分支，但发布到 crates.io 的包缺少对应实现文件。上游 `generator` 0.8.9 已补齐 Windows ARM64 实现，并在官方 `windows-11-arm` runner 上通过 stable/nightly 测试。受控打包入口只在已校验 Chroma sdist 的临时源码副本中，把唯一一条已知版本与 checksum 的 `generator` 0.8.8 lock 记录替换为 0.8.9 官方 checksum；任一版本、checksum 或命中数量漂移都立即阻断，不修改仓库 requirements 或上游源码真相。Chroma 更新 lock 至 0.8.9 以上或发布原生 `win_arm64` wheel 后必须删除该兼容补丁。

构建后必须验证 wheel 标签和 `chromadb_rust_bindings` 原生导入，并由两个独立 Python 进程完成向量写入退出与重开查询；发布 runtime probe 把当前 Chroma Rust binding 作为必需能力，不得静默回退到旧版 HNSW 后端。

Chroma 1.5.9 的传递依赖 `grpcio` 1.83.0 和 `httptools` 0.8.0 也没有官方 `win_arm64` wheel；PyYAML 6.0.3 只为 CPython 3.12 及以上发布 Windows ARM64 wheel，当前固定的 CPython 3.11.9 无可用 wheel。Windows ARM64 打包入口必须从经 SHA-256 校验的官方 sdist 在原生 runner 构建固定版本，离线安装到受控 wheelhouse，并用 constraints 与 `--only-binary` 阻止主 resolver 重新下载源码或替换版本。PyYAML 明确关闭可选 LibYAML 扩展并验证安全加载/写回；`grpcio` 和 `httptools` 必须验证 CPython 3.11 `win_arm64` wheel、原生扩展 PE Machine `0xAA64`、HTTP 请求解析及本机 gRPC unary 往返。`grpcio` 的 Windows ARM64 上游支持仍未正式完成，因此该路径只能称为 V8OS 自持兼容构建，不能宣传成上游官方支持。

`grpcio` 会生成深层 C/C++ 对象路径。构建必须使用短源码目录、短 `TEMP/TMP`、`GRPC_PYTHON_BUILD_USE_SHORT_TEMP_DIR_NAME=1` 和 sdist 已携带的生成源码，禁止在常规 pip 临时目录中失败后盲目重试整轮编译。Windows ARM64 的 Python runtime 与 job 超时预算单独放宽；其他桌面目标保持原预算。后续应把已验证兼容 wheel 独立缓存或发布到受治理的内部 wheelhouse，安装包 job 只消费与源码哈希、Python ABI、runner image 和工具链绑定的工件，不能直接信任未验证的社区 wheel。

移除条件：对应上游发布可验证的 `win_arm64` wheel 后，先在原生 Windows ARM64 runner 验证 wheel 架构、扩展加载、gRPC/HTTP 行为、Chroma 跨进程持久化和 checkpoint/tokenizer 回归，再分别移除临时 requirements 过滤、`--no-deps`、checkpoint pin 或原生源码构建；升级到已带完整 recursion limit 修复并声明新版 Rust 基线的 Chroma 后，同步移除 Rust 1.92.0 pin。`chromadb` 的桌面发布版本固定仍应保留，直到发布清单能够统一锁定和审计 Python 依赖。连续两个桌面发布周期通过安装 smoke 后关闭相应登记。如果 V8OS 在此之前新增任何 `sqlite_vec` 或 `vec0` 调用，Windows ARM64 构建必须立即改为阻断。

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
| `workflow_dispatch` / `ios` | 用户所选 | 仅在外部签名凭据已配置时尝试 iOS IPA | 仅上传临时 artifact，不改变 manifest 的 disabled 状态 |
| `workflow_dispatch` / `all` | 用户所选 | 按显式选择尝试 Android 与 iOS | 仅上传临时 artifact，不创建 Release |
| `v8-os-vYYYY.MM.DD.N` tag | manifest channel | Desktop 全目标与 Android；iOS disabled/skipped | 根 fan-in job 创建一个统一 Release |
| 过渡期 `v8-os-phone-vYYYY.MM.DD.N` tag | manifest channel | 仅 Phone 中启用的平台 | 根 fan-in job 创建兼容 Phone Release；两个成功统一发布周期后废弃 |

## 不能宣称的内容

在对应 workflow 和验收完成前，不得宣称：

- 桌面版已进入 stable。
- 已有受签名保护的自动下载或静默安装。
- 已支持系统服务安装。
- TUI 已可替代 Admin。
- 极简二进制已能在 ESP32 级设备运行完整 V8OS。
