# V8OS 二进制安装、桌面/TUI/CLI 工作区语义与客户端连接实施细节

日期：2026-06-07
校准：2026-06-27
范围：`v8-agent-os`、`openclaw-v8-bridge`、本机 `out/Claude` 可观察源码/入口、`out/CyberCore` 当前 V8OS adapter。
边界：本文是实施细节文档，不包含代码修改；结论基于本地静态阅读、用户确认的产品取向和既有运行事实。

## 1. 结论先行

V8OS 下一阶段要从“源码目录里启动 Engine/Admin”正规化为三种清晰产品形态，而不是让 Phone、Web、CyberCore、CLI 全部承担同一种入口语义：

1. **桌面 GUI 安装包**：面向普通桌面用户。一个安装包托管 Engine + Admin + Web shell + CyberCore companion；GUI 主窗口是桌面聊天/治理入口，最小化或悬浮时出现 CyberCore 伴侣。
2. **TUI + Phone 形态**：面向服务器、轻桌面或远程设备。TUI 指类似 `out/Claude` 的终端交互客户端，可以在终端里聊天、查看运行态、处理阻塞交互；它连接 Admin BFF，不直接接触 Engine secret。Phone 作为移动交互客户端。
3. **CLI 治理工具**：面向配置、Doctor 自动修复、Provider/MCP/设备接入管理、日志、导出、恢复。CLI 可以保留开发者会话入口，但不再是普通用户的主聊天产品。

关键不是把所有东西塞进一个单文件，而是把运行边界、工作区语义、secret 管理、客户端连接、定时任务、阻塞交互和 Doctor 修复统一起来。

已定稿的核心决策：

- `v8os` 裸命令等价于 `v8os start`，只启动或连接系统，不把当前目录当工作区。
- 桌面 GUI 和 Phone 是正式用户交互面；`os-web` 是桌面 GUI 的本地 shell/回归面，不再独立叙述成正式桌面客户端。
- CyberCore 是桌面 GUI 的伴侣层/最小化形态，不再作为和 Phone/Web 平级的长期主入口。
- `v8os chat` 可作为开发者/高级用户入口；普通用户主路径是桌面 GUI 或 Phone。
- 不再公开细分 `v8os ask` / `v8os run`；若未来保留，只能作为隐藏兼容 alias。
- 未注册路径首次进入会话类命令时，用左右键 `Yes/No` 询问是否信任并创建持久工作区。
- `v8os link` 是设备/客户端连接入口，优先服务 Phone、Custom Client，以及开发/回归用 Web；桌面 GUI 本地组件不要求用户手动 link。
- `v8os doctor` 不只是诊断，还要能生成修复计划、备份、修复、回滚配置错误。
- `v8os sessions`、`v8os schedule`、`v8os inbox` 默认只作用于当前工作区；`--all` 才进入全局治理视角。
- Phone、Web shell、CyberCore companion、自定义客户端都只连接 Admin BFF，不直连 Engine，也不接触内部 secret。

这套设计借鉴 Claude Code 的“薄 CLI shim + 当前目录作为项目上下文 + 可额外添加工作目录”的模型，但不照搬它的裸命令行为。V8OS 裸命令承担系统启动职责，避免用户只想开系统时意外把某个目录绑定成会话工作区。

### 1.1 与目标产品形态的偏差评估

当前文档早期版本仍把 `Phone / CyberCore / Web / CLI` 写成平级统一接入，这是主要偏差。按 2026-06-27 的目标形态，应调整为：

| 入口 | 产品定位 | 当前差距 | 收口方向 |
| --- | --- | --- | --- |
| 桌面 GUI 安装包 | 普通桌面主入口 | 仍停留在 Admin/Web/CyberCore 分散启动 | 做统一 launcher、托盘、最小化 companion、内置 Admin/Web shell |
| Phone | 移动主入口 | 配对链路已接近目标，仍需减少用户记 Admin URL | 保持扫码/复制配对，统一 Owner/device pairing |
| Web | 桌面 GUI 内部 shell 和回归面 | 曾被叙述成独立桌面主入口 | 从产品叙事中降级为 GUI 内嵌/开发回归 |
| CyberCore | GUI 最小化/伴侣层 | 曾被叙述成独立桌面客户端 | 嵌入桌面 GUI 生命周期，不单独承担账号/工作区主链 |
| TUI | 类 Claude Code 的终端交互客户端 | 文档尚未展开 TUI | 设计终端聊天、运行态、审批/ask_user、文件/工作区、link/doctor 的交互壳 |
| CLI | 配置、doctor、接入管理 | 仍混有较重 chat 叙事 | 主打 `doctor/config/link/provider/mcp/logs`，chat 保留高级入口 |

可行性判断：这个目标真实可行，但不能理解为“把现有 dev server 包一层壳”。真正的产品化缺口在五件事：统一 launcher 托管生命周期、单 Owner + device pairing、地址免记忆和 Tailscale/LAN 可达性、桌面 GUI/CyberCore 生命周期整合、TUI 与 CLI 的职责拆分。只要这些里程碑逐步完成，当前 Engine/Admin/Phone/Web/CyberCore 代码可以演进到目标形态，不需要推倒重做。

## 2. 代码事实

### 2.1 当前启动脚本只能启动服务，不是安装包

当前 `bootstrap.ps1` / `bootstrap.sh` 能完成：

- 创建 Engine `.venv` 并安装 Python 依赖。
- 安装 Admin npm 依赖。
- 生成 Admin `.env.local`，包括 `NEXTAUTH_URL`、`NEXTAUTH_SECRET`、`NEXT_PUBLIC_APP_VERSION`。
- 拉起 Engine `main.py` 与 Admin `npm run dev`。

它不能完成：

- 安装 `v8os` 命令到 PATH。
- 提供二进制 launcher。
- 以非源码目录运行 Admin/Engine。
- 作为系统服务或守护进程管理生命周期。
- 生成 Phone/TUI/Web shell/Custom Client 可扫码或可复制的连接 manifest。

因此它适合作为开发启动脚本，不适合作为正式安装入口。

### 2.2 Admin 的 Auth secret 仍然需要环境注入

Admin Auth 事实：

- Admin 使用 NextAuth/Auth.js credentials provider。
- 用户数据存在 `~/.v8-agent-os/users.json`。
- Admin session cookie 名为 `v8-agent-os-admin.session-token`。
- `NEXTAUTH_SECRET` / `AUTH_SECRET` 用于 Auth.js session、JWT、cookie 签名。
- `bootstrap.ps1` 已通过 `scripts/ensure-admin-auth-secret.mjs` 自动维护 `.env.local` 的环境注入。

结论：

- `NEXTAUTH_SECRET` 不是 Engine 运行配置，不应混入公开 `config.json` 主体。
- 用户不再需要手写 `.env.local`；后续二进制 launcher 应复用同一 secret helper。
- 安装器或 launcher 应首次生成并持久保存 Admin app secret，然后在启动 Admin 时注入环境变量。

推荐保存位置：

- `~/.v8-agent-os/secrets/admin-auth-secret`
- 文件权限尽可能收窄。
- CLI、日志、Doctor 不打印明文，只显示存在性、生成时间和是否可读取。

Admin env template 已统一为 `~/.v8-agent-os/config.json`，并明确 secret 不属于公开运行配置。

### 2.3 运行配置真相在 `~/.v8-agent-os/config.json`

Admin server 侧已经通过 `runtime-config.ts` 和 `bridge-config.ts` 读取：

- `systemBase.bridge.engineBaseUrl`
- `systemBase.bridge.engineWsBaseUrl`
- `systemBase.bridge.adminBaseUrl`
- `systemBase.bridge.desktopLiveBridgeBaseUrl`
- `systemBase.bridge.internalSecret`
- `systemBase.remoteLink`

Engine 默认端口：

- Engine: `http://127.0.0.1:9530/v1`
- Admin: `http://127.0.0.1:9528`
- Web: `http://127.0.0.1:9527`

结论：

- `.env` 只服务前端/Next.js 启动环境。
- `config.json` 是 runtime truth。
- 二进制 launcher 的职责是读取 `config.json`、补齐 secret、推导环境变量，再启动 Admin/Engine。

### 2.4 Phone / Web shell / CyberCore companion 的连接路径应统一到 Admin BFF

Phone 当前行为：

- 默认消费 Admin 生成的五分钟单次配对链接，不要求用户记忆 Admin URL。
- 配对走 `/api/client/pairing/consume`；账号密码登录仅保留为高级回退。
- token 存系统安全存储，Owner 可在 Admin 撤销设备会话。
- 会话、上传、实时事件都走 Admin BFF。

Web shell 当前定位：

- 作为桌面 GUI 内嵌聊天 shell 和本地回归面。
- 可以消费同一配对/Owner session 体系，但不再承担“独立桌面正式客户端”的产品叙事。
- 不应出现公开普通用户注册语义。

CyberCore companion 当前定位：

- 只作为桌面 GUI 最小化、悬浮、语音和轻量动画伴侣层。
- V8OS 模式下不应维护第二套 persona、memory、runtime 或工作区真相。
- 连接仍走 Admin BFF 的 client auth、conversation、chat-submit、upload、realtime、audio 接口。
- Gemini/OpenAI 直连模式可以作为 CyberCore 独立 demo 保留，但不能进入 V8OS 正式主链叙事。

结论：

- Phone / Web shell / CyberCore companion 都不应该直连 Engine。
- 它们都不应该接触 `NEXTAUTH_SECRET` 或 `systemBase.bridge.internalSecret`。
- 个人实例只创建一个 Owner，不开放普通用户注册；Phone 是移动设备身份，Web shell 是本地/桌面会话，CyberCore 是桌面 companion 身份。
- 自定义客户端只需要单次配对信息、后续 client auth token、可选 session/workspace scope。

## 3. Claude Code / OpenClaw 对照

### 3.1 Claude Code 的可借鉴点

本机 `claude` 全局入口是薄 shim，最终指向 `@anthropic-ai/claude-code/bin/claude.exe`。源码中可观察到：

- 初始化时读取并 realpath 处理 `process.cwd()`。
- `originalCwd`、`projectRoot`、`cwd` 初始都来自启动目录。
- 会话、memory、CLAUDE.md、权限上下文都围绕 project directory。
- `/add-dir` 可以把额外目录加入当前 session 或 local settings。
- 对 home directory、trust、permission 有单独处理。

可借鉴：

- CLI 入口本身尽量薄。
- 工作区语义必须显式、可解释、可增加目录。
- 当前目录是会话上下文，不是全局配置。
- 危险目录和额外目录要有权限/信任提示。

不应照搬：

- V8OS 裸 `v8os` 不应直接进入对话，因为用户已经确认它只用于启动系统。
- V8OS 有桌面 GUI、Phone、TUI、CLI 多形态，不应把 CLI 设计成唯一交互入口。
- V8OS 是 `Supervisor First, Runtime Grounded`：CLI 只是入口，不能让 Supervisor 绕过工作区边界、审批、Spec、runtime handoff 和 proof 链。

### 3.2 OpenClaw bridge 的可借鉴点

`openclaw-v8-bridge` 通过 npm `bin` 暴露：

- `v8-bridge-cli`
- `openclaw-v8-bridge`

入口也是薄 shim，真正逻辑在 `.bridge-cli/v8-bridge-cli.mjs`。它的状态目录是 `~/.openclaw`，主要职责是配置和管理 OpenClaw 插件/网关，不是项目工作区交互。

可借鉴：

- npm/bin 或平台 shim 可以只做命令转发。
- 状态目录和恢复候选要明确。
- CLI 可以优先做诊断、配置恢复和外部桥接。

不应照搬：

- OpenClaw bridge 不是多 runtime 会话 CLI。
- V8OS 的工作区应绑定到 session scope / projects registry，而不是另起一个 CLI 私有 workspace 配置。

## 4. CLI 命令设计

### 4.1 顶层命令

| 命令 | 行为 | 是否读取 cwd |
| --- | --- | --- |
| `v8os` | 等价 `v8os start`，启动/连接 Engine + Admin | 否 |
| `v8os start` | 启动服务，输出 Admin URL、Engine health、连接入口 | 否 |
| `v8os stop` | 优雅停止由 launcher 管理的服务 | 否 |
| `v8os restart` | 重启服务 | 否 |
| `v8os status` | 查看端口、进程、health、连接地址 | 否 |
| `v8os logs` | 查看 Engine/Admin/launcher 日志 | 否 |
| `v8os login/logout/whoami` | CLI 自身登录、退出、查看身份 | 否 |
| `v8os doctor` | 诊断、修复计划、备份、回滚 | 否 |
| `v8os config` | 配置读取、校验、导入导出 | 否 |
| `v8os providers` | Provider / 模型 / 音频 / 媒体接入管理 | 否 |
| `v8os mcp` | MCP server 安装、列表、状态、移除 | 否 |
| `v8os link` | 设备与客户端连接入口 | 否 |
| `v8os inbox` | 查看当前工作区 approval / ask_user | 是 |
| `v8os sessions` | 查看当前工作区 session 列表 | 是 |
| `v8os schedule` | 管理当前工作区定时任务 | 是 |
| `v8os chat` | 高级/开发者会话入口；普通用户优先用 GUI/Phone | 是 |

### 4.2 `v8os` 裸命令

裸命令只做：

1. 检查是否已安装并能读取 `~/.v8-agent-os`。
2. 补齐必要 secret。
3. 启动或连接 Engine/Admin。
4. 输出：
   - Admin URL
   - Engine health
   - Phone / GUI / TUI 连接提示
   - 常用命令：`v8os link`、`v8os doctor`、`v8os config`、`v8os providers`、`v8os inbox`

裸命令绝不：

- 创建 conversation。
- 绑定当前目录。
- 改默认 workspace。
- 自动写 projects registry。

### 4.3 高级会话入口：`v8os chat`

`v8os chat` 才执行 workspace 解析。它可以保留给开发者、TUI 环境和自动化脚本，但普通桌面用户的主入口应是桌面 GUI，移动用户主入口应是 Phone。推荐形态：

```powershell
v8os chat
v8os chat "解释这个项目结构"
v8os chat --workspace E:\Projects\test7
v8os chat --project test7
v8os chat --session <sessionId>
```

语义：

- `v8os chat` 无参数：进入交互式会话。
- `v8os chat "..."`：直接提交一条用户消息并流式显示结果。
- 这条入口同时覆盖过去的“问问题”和“跑任务”，但不是普通用户必须记住的主路径。
- `ask/run` 不作为公开产品命令；若保留，只能作为兼容 alias，并在帮助信息中隐藏或提示迁移到 `chat`。

会话命令创建或恢复 conversation 后，走 Admin BFF：

- `POST /api/client/conversations`
- `PUT /api/client/sessions/:id/scope`
- `POST /api/client/chat-submit`
- `/api/client/realtime/sessions/:id/...`

## 5. 工作区解析、信任与创建

### 5.1 工作区解析优先级

工作区解析优先级固定为：

1. `--workspace <path>`
2. `--project <id>`
3. `--session <id>` 已有 scope
4. 当前目录命中的已注册 project workspace
5. 当前目录 `cwd`

解析结果绑定当前 session，并作为本次 CLI 操作的活动边界。

### 5.2 路径规范化

CLI 需要：

- 对 cwd 和 `--workspace` 做 `realpath/resolve`。
- Windows 下保留盘符并统一路径比较策略。
- 处理 symlink，避免同一个目录出现多个 workspace 身份。
- 检查路径存在、是目录、可读。
- 对会写文件的任务额外检查可写。

### 5.3 未注册路径的信任提示

当前目录未注册、但安全可用时，`v8os chat` 弹出左右键选择：

```text
当前目录尚未注册为 V8OS 工作区：
E:\Projects\test1

是否信任并创建持久工作区？

[Yes] 信任并创建
[No ] 取消
```

交互规则：

- 左右键移动选择，Enter 确认。
- `Yes`：写入 projects/workspace registry，下次同路径不再询问。
- `No`：取消本次 `chat`，不创建 session，不写配置。
- `Esc/Ctrl+C`：等同 `No`。
- `--workspace <path>` 仍可触发同样信任流程，除非路径已注册。

### 5.4 危险目录拒绝自动创建

以下目录不允许自动创建工作区：

- 用户 home 目录。
- 系统根目录：`C:\`、`/`。
- Windows 系统目录、Program Files、WindowsApps。
- V8OS 安装目录。
- 不存在、不可读或不可写目录。
- 网络/云盘路径出现权限异常时。

提示用户：

```text
当前目录不适合作为 V8OS 工作区。
请切换到项目目录，或使用 v8os chat --workspace <path>。
```

### 5.5 非交互与脚本模式

支持：

```powershell
v8os chat "检查项目" --json
v8os chat "检查项目" --no-interactive
v8os chat "检查项目" --workspace E:\Projects\test7 --yes
```

规则：

- `--json` 输出机器可读事件，不打印装饰文本。
- `--no-interactive` 遇到需要信任确认时直接失败，返回结构化错误。
- `--yes` 只允许用于显式 `--workspace` 或已注册工作区。
- `--yes` 不允许在未知 cwd 上静默创建工作区，避免脚本在错误目录污染 registry。

## 6. Workspace Activity Boundary

所有涉及 agent 的活动范围都建立在拉起路径解析出的工作区上。

### 6.1 受约束对象

以下能力默认受当前 workspace scope 约束：

- Chat session。
- Engineering 文件读写、proof、workspace digest。
- Research 经验包、来源摘要和 handoff 归属。
- Creative Media 素材请求、artifact refs。
- Computer Use / RPA 的 workspace 相关 traces、uploads、workflow。
- Skill 发现、workspace skill root、relative reference 续读。
- Memory 注入、记忆维护和外部 thread scope。
- Upload / artifact / attachments。
- Schedule / automation / cron。
- approval / ask_user / inbox。

### 6.2 不越界原则

CLI 不得因为用户在某个目录启动就让 agent 访问其他项目。越界只有三种合法方式：

1. 用户显式传入 `--workspace` 或 `--project`。
2. 用户在会话中明确授权额外路径。
3. runtime 通过受管审批获取临时读取/写入权限。

所有越界授权都应进入 ledger / runtime events / proof 或 approval 记录。

## 7. 二进制安装与 Launcher

### 7.1 Windows v1 包形态

第一版推荐安装目录 bundle，而不是单文件 exe：

```text
V8OS/
  bin/
    v8os.exe
    v8os.cmd
  engine/
    runtime/
    app/
  admin/
    standalone/
    static/
  node/
  python/
  launcher/
  resources/
```

原因：

- Admin/Next.js、Engine/Python、native helper、runtime assets 都不是天然单文件形态。
- 安装目录 bundle 更容易做升级、回滚、日志、Doctor。
- 后续可再封装 NSIS/MSIX，但不应第一步追求单 exe。

### 7.2 Admin standalone

当前 Admin `next.config.ts` 尚未设置 standalone 输出。正式包需要：

- Admin build 产出 standalone server。
- 复制 `.next/static`、`public`、必要 package assets。
- launcher 注入：
  - `AUTH_SECRET`
  - `NEXTAUTH_SECRET`
  - `NEXTAUTH_URL`
  - `AUTH_TRUST_HOST`
  - `NEXT_PUBLIC_APP_VERSION`

Admin 不应要求用户手写 `.env.local`。

### 7.3 Engine runtime

第一版可保留 Python 内核：

- 安装时创建私有 venv 或携带 frozen Python runtime。
- launcher 负责启动 `apps/v8-agent-os-engine/main.py` 或打包后的 engine entry。
- 日志写入 `~/.v8-agent-os/logs`。
- 端口与 bridge 配置从 `config.json` 读取。

后续可评估 PyInstaller/Nuitka/frozen runtime，但不要在第一版把所有 runtime/native helper 一次性冻结。

### 7.4 PATH 与升级

安装器负责：

- 把 `V8OS/bin` 加入 PATH。
- 检查旧 PATH 中是否有旧 `v8os`。
- `v8os --version` 输出 launcher、engine、admin、config schema 版本。
- 支持 `v8os upgrade` 和 `v8os rollback` 的文档预留。

## 8. Secret 与配置治理

### 8.1 Secret 分类

| Secret | 用途 | 保存位置 | 是否进入客户端 |
| --- | --- | --- | --- |
| Admin Auth Secret | NextAuth/Auth.js session/JWT 签名 | `~/.v8-agent-os/secrets/admin-auth-secret` | 否 |
| Bridge Internal Secret | Admin ↔ Engine 内部鉴权 | `~/.v8-agent-os/config.json` 或 secrets 引用 | 否 |
| CLI Client Token | CLI 登录 Admin BFF | `~/.v8-agent-os/client-auth/cli.json` | 仅 CLI 本地 |
| Device Token | Phone/TUI/Web shell/CyberCore companion 可撤销访问/刷新 token | `~/.v8-agent-os/mobile_app_tokens.json` | 仅对应设备本地 |
| Web Session | Web 自身 Auth.js session | Web app secret/cookie | 否 |
| Provider API Key | 模型/工具供应商 | config/secrets 管理 | 否 |

### 8.2 CLI 登录与 token 缓存

CLI 不持有 Engine secret，也不伪造 Admin session。推荐命令：

```powershell
v8os login
v8os logout
v8os whoami
```

行为：

- `v8os login` 走 Admin client auth。
- token 存入 `~/.v8-agent-os/client-auth/cli.json`。
- `v8os logout` 删除 CLI token，不影响 Phone、TUI、桌面 GUI 内部 Web shell 或 CyberCore companion token。
- `v8os whoami` 显示当前 Admin BFF、用户、token 过期时间。

## 9. 设备与客户端连接：`v8os link`

### 9.1 交互式入口

`v8os link` 进入方向键选择：

```text
选择要连接的客户端：

> Phone
  TUI
  Web shell（开发/回归）
  Custom Client
```

也支持非交互：

```powershell
v8os link --surface phone
v8os link --surface tui
v8os link --surface web
v8os link --surface custom
```

### 9.2 输出内容

所有 surface 都输出：

- Admin URL。
- 五分钟、单次使用的配对票据与 consume endpoint。
- realtime endpoint。
- upload endpoint。
- 当前 LAN/VPN 可达性。
- loopback 警告。
- 可复制链接/二维码/manifest；其中不含永久 token 或内部 secret。
- 当前默认工作区和当前目录解析出的工作区。

Phone manifest 包含 `surface=phone`。
TUI manifest 包含终端交互客户端需要的 Admin BFF、client auth、realtime endpoint 和当前工作区提示。
Web manifest 只用于桌面 GUI 内部 shell 或本地开发/回归入口。
Custom Client manifest 只暴露 Admin BFF，不暴露 Engine 端口或 internal secret。

### 9.3 OS Web 定位

`os-web` 不再承担桌面正式入口叙事。它保留为：

- 本地快速测试路径。
- 回归验证路径。
- 桌面 GUI 内部 Web shell。

因此 Web 可以在 `v8os link` 中以开发/回归入口出现，但产品主入口是桌面 GUI、Phone、TUI；CyberCore 是桌面 GUI 的最小化/伴侣层，不再作为平级连接目标。

### 9.4 CyberCore companion 定位

CyberCore 不再单独作为“桌面客户端”让用户理解和配置。正式产品里它应由桌面 GUI launcher 管理：

- GUI 正常打开时，CyberCore 可以隐藏或作为轻量侧边伴侣。
- GUI 最小化、托盘化或语音唤醒时，CyberCore 出现。
- 它复用桌面 GUI 的 Owner/session/device 状态，不要求用户二次登录或单独记 Admin URL。
- V8OS 模式下只做语音、动画、右键快捷输入、文件/工作区入口和轻量状态展示，不创建第二套 runtime/persona/memory。

### 9.5 TUI 定位

TUI 是类 Claude Code 的终端交互客户端，不是普通命令集合。它应支持：

- 在当前工作区启动/恢复会话。
- 通过 Admin BFF 收发消息和实时事件。
- 处理 approval / ask_user。
- 展示简洁 runtime/subagent/tool 状态。
- 调用 `v8os doctor/config/link/providers/mcp` 的治理能力，但不持有 Engine internal secret。

## 10. Doctor：诊断、修复、回滚

### 10.1 命令结构

```powershell
v8os doctor
v8os doctor all
v8os doctor config
v8os doctor models
v8os doctor models --role supervisor
v8os doctor providers --probe
v8os doctor desktop
v8os doctor plugins
v8os doctor memory
v8os doctor audio
v8os doctor network
v8os doctor repair config --dry-run
v8os doctor repair config
v8os doctor rollback <repairId>
```

### 10.2 数据来源

可复用现有能力：

- Engine `system_doctor_routes.py`
- Model Role Doctor
- provider probe / model control plane
- desktop tools readiness
- plugin_host doctor
- memory/vector health
- audio STT/TTS readiness
- remoteLink / bridge config
- config migration ledger

CLI 输出应分级：

- `OK`
- `DEGRADED`
- `BLOCKED`
- `CONFIG_MISSING`
- `PROVIDER_MISMATCH`
- `NETWORK_UNREACHABLE`
- `REPAIR_AVAILABLE`

并给出下一步命令，而不是只打印 raw JSON。

### 10.3 Config 修复范围

`v8os doctor repair config` 至少覆盖：

- `config.json` JSON 格式错误。
- 历史 `~/.v8chat` 路径迁移到 `~/.v8-agent-os`。
- 缺失 `AUTH_SECRET` / `NEXTAUTH_SECRET`。
- 缺失 `systemBase.bridge.internalSecret`。
- Admin/Engine/Web URL 与实际监听地址不一致。
- 缺失默认配置域。
- env template 或本地 `.env.local` 中的历史路径提示。

修复规则：

- `--dry-run` 只输出计划，不改文件。
- 正式修复前备份到 `~/.v8-agent-os/backups/config_repairs/<repairId>/`。
- 修复记录写入 `~/.v8-agent-os/config_repair_ledger.json`。
- 修复后自动运行 `v8os doctor config` 二次验证。

### 10.4 格式严重损坏时的恢复

如果 `config.json` 无法解析且没有可用备份：

1. 原文件移动为 `config.json.broken.<timestamp>`。
2. 生成最小可启动 config scaffold。
3. 标记 `DEGRADED`，提示用户重新配置模型、音频、provider。
4. 保留 broken 文件路径，便于人工恢复。

## 11. 工作区 Session 查询

### 11.1 命令

```powershell
v8os sessions
v8os sessions --all
v8os sessions --workspace E:\Projects\test7
v8os sessions --project test7
v8os sessions show <sessionId>
v8os sessions resume <sessionId>
```

### 11.2 默认行为

`v8os sessions` 默认先解析当前路径工作区，只列出绑定该工作区的 session。

显示字段：

- session id / title。
- 最近用户消息时间。
- workspace / project。
- run 状态。
- pending approval / ask_user 数量。
- 是否 hidden/governance-only。

`--all` 才显示全局 session，并明确标出各自 workspace。
后台 memory maintenance、Computer Use probe、RPA 手动 run、governance-only session 不应混入普通工作区会话列表。

## 12. 工作区定时任务

### 12.1 命令

第一版支持查、建、启停、删除：

```powershell
v8os schedule list
v8os schedule create --name "daily check" --cron "0 9 * * *" --message "检查本项目状态"
v8os schedule enable <jobId>
v8os schedule disable <jobId>
v8os schedule delete <jobId>
```

可选参数：

```powershell
--workspace <path>
--project <id>
--session <id>
--json
```

### 12.2 工作区绑定

创建定时任务时必须绑定工作区：

- 优先使用 `--workspace` / `--project`。
- 否则使用当前路径解析出的工作区。
- 如果当前路径未注册，先执行信任创建流程。
- 不允许创建没有 scope 的用户定时 agent 任务。

任务 payload 必须写入：

- `workspacePath` / `workspaceId` / `projectId`
- `triggerSource=cron`
- `source=automation`
- `createdBy=cli`

### 12.3 触发后的历史隔离

定时任务触发的 agent 活动：

- 标记为 automation/cron。
- 进入对应 workspace ledger。
- 不显示成用户刚刚发起的聊天 running。
- 如果需要用户介入，进入 `v8os inbox`，并可由 Phone/Admin/TUI/桌面 GUI/CLI 处理。

## 13. Approval / ask_user CLI 化

### 13.1 Inbox

`v8os inbox` 默认聚合当前工作区：

- pending approvals
- pending ask_user interactions
- recoverable runtime blocks
- waiting external tool resume

每条显示：

- ID
- 来源 session/run/episode
- runtime kind
- 用户可读摘要
- 创建时间
- 是否后台治理事件
- 推荐动作

`v8os inbox --all` 才显示全局阻塞项。

### 13.2 操作命令

```powershell
v8os approve <approvalId>
v8os reject <approvalId> --reason "..."
v8os ask answer <interactionId> "..."
v8os ask cancel <interactionId>
v8os run resume <runId>
v8os run cancel <runId>
```

CLI 处理阻塞交互时必须：

- 不新建会话。
- 不把后台治理事件点亮为聊天 running。
- 不绕过 runtime gate。
- 写入 run ledger / runtime events。
- 默认只允许处理当前工作区阻塞项；跨工作区需要 `--all` 或显式 session/run id。

## 14. 实施顺序

### Phase 1：文档与 launcher 骨架

- 写本文档。
- 新增 `v8os` launcher 骨架。
- 裸命令只启动系统。
- 注入 Admin secret/env。
- 输出 status/link 基础信息。

### Phase 2：单 Owner、设备配对与地址免记忆

- 首次启动初始化唯一 Owner 管理员。
- Phone 扫码/复制配对，TUI/Web shell/Custom Client 消费同一 pairing 体系。
- Admin topbar 提供“连接设备”入口，显示二维码、复制链接、LAN/Tailscale 可达地址。
- 删除公开普通用户注册、旧账号密码客户端登录叙事。

### Phase 3：Admin standalone 与 Engine 托管

- Admin 改为 standalone build。
- launcher 托管 Engine/Admin 进程。
- `status/stop/restart/logs` 可用。
- 日志和 pid 进入 `~/.v8-agent-os`。

### Phase 4：桌面 GUI 安装包

- 打包 Engine runtime、Admin standalone、Web shell、桌面 GUI shell。
- 桌面 GUI 管理启动、停止、托盘、自动恢复、日志入口。
- GUI 主窗口承载 Web shell/Admin 关键治理入口。
- CyberCore companion 嵌入 GUI 生命周期，最小化/悬浮/语音时出现。

### Phase 5：TUI 终端交互客户端

- 提供类似 `out/Claude` 的终端会话体验。
- 通过 Admin BFF 连接，不直连 Engine。
- 支持工作区信任、聊天、实时事件、approval/ask_user、简洁工具/运行态。
- 与 Phone/桌面 GUI 共享 session、device、workspace 语义。

### Phase 6：CLI 工作区与会话

- `v8os chat` 支持 cwd/workspace/project/session scope。
- 实现未注册路径 Yes/No 信任创建。
- 实现危险目录拒绝。
- 与 Admin、Phone、TUI、Web shell、CyberCore companion 的 session scope 保持一致。

### Phase 7：Link / Doctor / Inbox / Sessions / Schedule

- `v8os link` 服务 Phone、TUI、Web shell、Custom Client；桌面 GUI 内部组件由 launcher 自动注入连接状态。
- `v8os doctor repair config` 支持备份、修复、回滚。
- `v8os inbox` 接入 approval / ask_user。
- `v8os sessions` 默认当前工作区。
- `v8os schedule` 支持查、建、启停、删除。

### Phase 8：安装包、升级与回滚

- Windows installer。
- PATH 注册。
- 升级/回滚。
- Phone/TUI/Web shell/Custom Client connection manifest。

### Phase 9：普通用户易用性验收

- 首次安装后不打开终端也能进入桌面 GUI。
- 手机只需扫码即可连接，不需要记端口。
- TUI 只需 `v8os tui` 或 launcher 内入口即可进入终端交互。
- CLI 的 `doctor repair` 能自动修复常见 config、secret、provider、MCP、网络地址问题。
- 所有端都只展示人类可理解状态，不暴露 internal secret、raw JSON、Engine 私有端口。

## 15. 验收矩阵

### 启动

- 任意目录执行 `v8os`：只启动系统，不创建 workspace，不创建 session。
- `v8os status` 能显示 Engine/Admin health。
- `v8os stop` 能停止 launcher 管理的进程。

### 工作区

- `cd E:\Projects\test1; v8os chat`：若未注册，出现左右键 Yes/No。
- 选择 Yes 后创建持久工作区并进入会话。
- 选择 No 后不创建 session、不写 registry。
- home/system root/安装目录执行 `v8os chat` 会拒绝自动绑定。
- 已注册 project workspace 能被当前目录命中。

### 会话

- `v8os chat` 进入交互式会话。
- `v8os chat "检查这个项目"` 复用当前工作区，创建/恢复 session 并提交消息。
- `v8os chat --workspace E:\Projects\test7` 优先使用指定 workspace。

### 客户端连接

- `v8os link` 可选择 Phone/TUI/Web shell/Custom Client。
- Phone 使用扫码或复制链接进入同一套会话。
- TUI 使用输出 Admin BFF/client auth 信息进入终端交互。
- Web shell 作为桌面 GUI 内部入口或本地测试入口仍可获得连接信息。
- CyberCore companion 由桌面 GUI launcher 注入连接状态，不要求用户单独 link。
- 输出不包含 Engine internal secret。

### Secret

- 删除 Admin Auth Secret 后，launcher 能重新生成。
- CLI/日志不打印 secret 明文。
- Phone/TUI/Web shell/CyberCore companion 不接触任何 internal secret。

### Doctor

- `v8os doctor config` 能识别 JSON 格式错误、缺 secret、历史路径、bridge URL 不一致。
- `v8os doctor repair config --dry-run` 输出计划但不改文件。
- `v8os doctor repair config` 备份、修复、记录 ledger。
- `v8os doctor rollback <repairId>` 可恢复。

### Sessions / Schedule / Inbox

- `v8os sessions` 只列当前工作区 session。
- `v8os sessions --all` 显示全局并标注 workspace。
- `v8os schedule create` 创建绑定当前工作区的定时任务。
- 定时任务触发后 run/session scope 不越界。
- `v8os inbox` 默认只列当前工作区阻塞项。
- `v8os inbox --all` 显示全局治理项。

## 16. 风险与注意事项

- 不要把 `NEXTAUTH_SECRET` 塞入公开配置 UI；它是 app secret。
- 不要让 `v8os` 裸命令绑定 cwd，否则用户在任意目录启动系统都会污染 session/workspace 语义。
- 不要保留公开 `ask/run` 入口叙事，否则 CLI 语义会继续分裂。
- 不要让 `--yes` 在未知 cwd 上静默创建工作区。
- 不要让 Phone/TUI/Web shell/CyberCore companion 直连 Engine；否则权限、CORS、internal secret、runtime gate 都会变脆。
- 不要为了 CLI 方便绕过工作区边界、审批、Spec、runtime handoff 和 proof 链；CLI 只是入口，不改变 `Supervisor First, Runtime Grounded`。
- 不要把 Admin/Web 的 `.env` 当 runtime truth；它只是 Next.js 启动环境。
- 不要让定时任务成为无 scope 的全局 agent 活动；所有 schedule 都必须绑定工作区或明确是治理任务。

## 17. 最终形态

理想用户体验：

```powershell
# 安装后任意终端可用，只启动系统
v8os

# 输出系统状态和连接入口
V8OS Engine: OK http://127.0.0.1:9530
V8OS Admin : OK http://127.0.0.1:9528
Link       : v8os link

# 在项目目录里显式进入会话
cd E:\Projects\test7
v8os chat

# 首次未注册时询问
是否信任并创建工作区 E:\Projects\test7 ?
[Yes] [No]

# 直接提交一条任务
v8os chat "检查这个项目并给出修复建议"

# 统一连接客户端
v8os link

# 进入终端交互客户端
v8os tui

# 不打开 Admin 处理阻塞
v8os inbox
v8os ask answer ask_123 "选择方案 A"

# 管理当前工作区定时任务
v8os schedule list
v8os schedule create --name "daily check" --cron "0 9 * * *" --message "检查本项目状态"

# 修复配置
v8os doctor repair config --dry-run
v8os doctor repair config
```

这时 V8OS 才算从“源码启动的多端原型”进入“可安装、可诊断、可连接、可恢复、以工作区为边界的本地 Agent OS”形态。
