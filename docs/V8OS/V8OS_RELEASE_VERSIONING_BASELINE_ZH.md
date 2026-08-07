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
