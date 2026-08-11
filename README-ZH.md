<div align="center">
  <img src="./docs/assets/banner.svg" alt="V8 Agent OS" width="860">
</div>

<div align="center">
  <strong>一个本地优先的 Agent 工作空间：让长期任务、项目交付、手机远程协作和多媒体创作在同一套系统里运行。</strong>
</div>

<br>

<div align="center">

[English](./README.md) · [快速开始](./docs/V8_AGENT_OS_QUICK_START_ZH.md) · [CLI 命令](./docs/V8_AGENT_OS_CLI_REFERENCE_ZH.md) · [配置指南](./docs/V8_AGENT_OS_CONFIG_GUIDE_ZH.md) · [开发者指南](./docs/V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md) · [发布页](https://github.com/justForever17/v8-agent-os/releases)

</div>

## V8 Agent OS 是什么

V8 Agent OS 是一套面向个人和小团队的本地 Agent OS。它把聊天、项目工作区、模型接入、长期记忆、任务编排、手机端远程协作和桌宠伴随器放在一个可治理的产品壳里。

你可以把它理解为一个“主理人中枢”：你提出目标，它可以在日常模式下快速处理，也可以切换到编程模式直接完成长期项目工作，或把明确子任务交给深度调研、多媒体创作和子代理协作；你仍然能看到过程、接管关键决策，并在任务结束后拿到可验证的产物。

## 适合谁

- 想把 AI 助手长期用于真实项目，而不是只做一次性问答的人。
- 经常需要调研、写代码、生成素材、整理资料、制作演示或交付项目的小团队。
- 希望在电脑上运行主系统，同时用手机远程查看进度、补充信息和处理确认的人。
- 需要更清晰地管理模型、工具、记忆、产物和权限边界的高级用户。

## 核心体验

### 桌面版

桌面版是当前主线。它把 V8OS 的聊天界面、控制台、运行核心和桌宠伴随器收进一个本地产品壳。你不需要记端口，也不需要反复打开多个终端；启动 V8OS 后，主界面负责聊天和任务，控制台负责模型、工作区、记忆、桌宠和系统设置。

### 手机端

Phone 是唯一远程交互入口。它用于查看正在运行的会话、接收确认请求、发送语音或附件、浏览产物，并在离开电脑时继续保持对任务的掌控。

### 主理人中枢

主理人中枢负责理解你的目标、选择合适路线，并在结果回流后做最后把关。它不是简单地把工具堆给模型，而是按角色、工作区、任务合同和当前授权投影恰好需要的能力。

### 专门工作模式

- 编程模式：主理人可以直接处理长期项目工作；需要隔离并行、失败恢复或独立证明时，再进入 Engineering episode 或委派子 Agent。
- 深度调研：做多源搜索、证据整理和调研包。
- 多媒体创作：生成图片、视频、音频、音乐和 3D 素材；Web 端的创意产物画布可组织工作区素材、建立引用关系并发起受治理的编辑任务。
- 记忆系统：保存偏好、知识和长期项目背景，但不会替代当前证据。
- 桌宠伴随器：跟随当前会话状态，播放动作和语音，并能把录音或截图作为附件交给主理人中枢。

### 受治理的项目执行

工作区绑定、操作系统和命令环境会由 Engineering Kernel 在任务开始时提供。涉及写入的子任务使用强类型任务合同：串行、低风险修改直接在已信任的绑定工作区内受精确写集约束执行；只有并行隔离、风险控制或长期恢复确有必要时，才使用托管 worktree 形成候选变更。V8OS 不会因普通任务静默初始化 Git、移动分支或替你提交；隔离候选仍需验证后再应用回原工作区。当前跨平台 sandbox 属于受控的部分隔离，不等同于内核级文件系统或离线网络沙箱。

用户上传的文件作为本轮输入来源保存；Agent 通过写入、下载、Spec 或创意媒体工具生成的文件才进入会话产物。工作区中原本存在或手工复制的文件不会仅因被扫描到就冒充本轮产物。创意产物画布中的素材库属于工作区，跨会话复用前需要由当前会话显式采用；跨工作区引用会被拒绝，蒙版等内部编辑资源不会进入普通素材库。

### 插件管理中心

插件管理中心从签名目录安装受审的 CLI、MCP、Skill 与 UI 组件，凭据只以引用进入运行时。组件仍落入现有 Skill 与 MCP 真相面，不建立另一套私有资源仓。`@插件` 是用户的强提示，但不是唯一入口：当当前任务确实需要一个已安装、已配置且健康的插件时，主理人中枢可以为本轮创建最小 task grant，并只把该插件的精确组件包投影给当前执行。直接子 Agent 只能继续传递明确更小的组件子集，最多到一层孙 Agent，不能再扩散。插件上机发现是只读的，不会接管普通 MCP 配置；CLI 执行器也只有在有效授权投影出受审命令后才出现。安装、补配置、读取密钥和持续会话授权仍由用户控制。内置精选目录已包含 GitHub、Figma、高德地图、火山引擎 MediaKit CLI 和 Cloudflare Wrangler；MediaKit 使用全量命令 schema 同步，Wrangler 可通过受治理的浏览器登录流程建立本机 profile。

## 快速开始

### 下载预览版

请前往 [GitHub Releases](https://github.com/justForever17/v8-agent-os/releases) 下载：

- Desktop Preview：Windows x64/ARM64 安装包、macOS Intel/Apple Silicon DMG，以及 Linux x64/arm64 AppImage 或 DEB。
- Android Phone Preview：手机端 APK。

桌面版当前仍是 unsigned preview。客户端会在启动完成后自动检查统一 Preview Release，也可从托盘手动检查；下载和安装仍由用户确认，不会静默执行。Windows 可能提示安全确认，正式签名与受签名保护的自动安装会在后续版本完善。Linux 使用桌面 Secret Service 保存密钥：DEB 会声明 GNOME Keyring 依赖，AppImage 则要求宿主提供兼容的 Secret Service。

### 从源码树预览

适合开发者和早期试用者：

```powershell
.\v8os.cmd preview --rebuild
```

该命令会重新构建 Admin、Web 与原生 sandbox helper，停止当前源码树拥有的旧预览进程，再启动 Engine、Admin、Web 和桌面 Shell。完成后你会看到 V8OS 桌面窗口，而不是多个开发服务器页面。

### 连接手机

手机端仍通过桌面控制台生成的配对二维码连接。扫码成功后，Phone 会保存本地连接档案；以后即使网络短暂失败，也不会丢失已保存的连接。

## 当前状态

| 产品形态 | 状态 | 说明 |
| --- | --- | --- |
| 桌面版 | Preview | 已提供 Windows x64/ARM64、macOS Intel/Apple Silicon、Linux x64/arm64 unsigned preview；支持自动检测更新与托盘手动检查，签名、自动下载安装和稳定版仍在后续阶段。 |
| Phone | Preview | Android APK 是必需发布目标；iOS 目标为 16.4 及以上，但在非交互签名配置完成前保持禁用。 |
| TUI 版 | 未实现 | 面向终端用户和服务器环境，计划剥离控制台页面依赖。 |
| 轻量版 | 长期规划 | 面向低配设备和边缘运行场景，会裁剪重型依赖。 |

## 安全与边界

V8OS 默认本地优先运行。桌面 Web、控制台和桌宠属于本机可信客户端；Phone 是远程客户端，需要配对连接。多设备协作、第三方插件授权和网络连接属于高级能力，不会混入普通本机使用流程。

系统会尽量把普通用户界面保持清晰：你看到的是结果、风险、下一步和可打开的产物；内部调度、原始模型响应、审计记录和恢复信息保留在诊断面。模型接入会尽量保留供应商原生的 system、tool 与 reasoning 消息合同，但任何供应商托管工具都不能越过当前会话的工具和授权边界。

## 文档入口

- [快速开始](./docs/V8_AGENT_OS_QUICK_START_ZH.md)
- [配置指南](./docs/V8_AGENT_OS_CONFIG_GUIDE_ZH.md)
- [开发者指南](./docs/V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md)
- [Creative Media Runtime](./docs/creative-runtime/V8OS_CREATIVE_MEDIA_RUNTIME_PUBLIC_OVERVIEW_ZH.md)
- [Extensions Runtime](./docs/extensions/V8OS_EXTENSIONS_RUNTIME_PUBLIC_OVERVIEW_ZH.md)
- [API 参考](./docs/V8_AGENT_OS_API_REFERENCE_ZH.md)
- [产品化总纲](./docs/V8OS/V8OS_PRODUCTIZATION_MASTERPLAN_ZH.md)
- [发布版本基线](./docs/V8OS/V8OS_RELEASE_VERSIONING_BASELINE_ZH.md)

## 参与和反馈

V8OS 仍处在快速产品化阶段。欢迎通过 GitHub Issues 提交问题、建议和复现步骤。若你正在测试桌面预览版或 Phone 端，请在反馈里附上版本号、平台、复现路径和关键截图。
