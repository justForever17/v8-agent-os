# V8 Agent OS

**V8 Agent OS** 适合那些已经受够了“同一个项目反复解释、同一批工具反复打扰、任务一跑起来就越来越黑盒”的人。

它想给你的不是另一页更聪明的聊天界面，而是一套更适合长期项目和持续任务的 Agent 系统：记得住、收得住、看得见，也接得住。

## 为什么它会更省心

- **少重讲。** projects、workspaces、scoped memory 和 durable recall 让明天从上下文里接着来，而不是又从零开始。
- **少工具噪音。** MCP 和 skills 不会因为已经安装就一股脑冲进上下文，系统会把注意力收在当前任务真正需要的那几样能力上。
- **任务可见可接管。** workflow projection、artifacts、approvals、realtime updates 和 operations-center 让长任务更像你能盯住的工作流，而不是失控的黑盒。
- **屏幕操作可沉淀。** Computer Use、desktop-live 和通往 RPA 的路径，能把“会点一次”慢慢长成更稳定的复用执行。

## OpenClaw 在这里扮演什么角色

OpenClaw 值得认真对待，因为它把生态、插件、渠道和 dashboard 的广度拉到了大家都不能忽视的程度。

V8 不想和它比“有没有这些”。更关键的体验差异在于：**把生态接进来以后，这台机器是不是仍然更少重讲、更少工具噪音，也更容易在中途看见、审批和接管。**

## 快速安装

公开 bootstrap 入口现在已经统一成按平台的一条命令：同步官方仓库、安装依赖，并拉起 Admin + Engine。Web 端继续独立分发。

### Windows

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.ps1 | iex"
```

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.sh | bash
```

## 如果你已经在本地 checkout 里

如果你已经在本地 checkout 里工作，同一套 bootstrap 脚本仍可以作为次级路径直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

```bash
./bootstrap.sh
```

## 安装后建议顺序

1. 先打开 **Admin**
2. 在 Admin 里完成核心配置
3. 按顺序配置模型、记忆、插件宿主、自动化和系统基础
4. 最后再决定 Web 是源码运行还是打成 app / release

**注意：** V8 Agent OS 会大量使用 reranker 模型。如果你不配置 reranker，记忆质量和工具暴露质量都会一起掉下来。

## 默认本地地址

| 服务 | 地址 |
| --- | --- |
| Web | `http://127.0.0.1:9527` |
| Admin | `http://127.0.0.1:9528` |
| Engine | `http://127.0.0.1:9530` |

## 这个仓库里有什么

| 模块 | 路径 | 作用 |
| --- | --- | --- |
| Web | `apps/v8-agent-os-web` | 用户侧聊天界面与移动端入口 |
| Admin | `apps/v8-agent-os-admin` | 配置中心、控制台、运行时观测面 |
| Engine | `apps/v8-agent-os-engine` | 记忆、自动化、MCP、skills、安全、恢复与 runtime orchestration 的执行平面 |

## 继续读

- [Engine API 参考](./docs/ENGINE_API_REFERENCE.md)
- [Engine Core 目录导览](./docs/ENGINE_CORE_DIRECTORY_GUIDE.md)
- [Engine 开发者指南](./docs/ENGINE_DEVELOPER_GUIDE.md)
- [Engine 开发者指南（中文）](./docs/ENGINE_DEVELOPER_GUIDE_ZH.md)
- [Network Supervisor Runtime 方案](./docs/NETWORK_SUPERVISOR_RUNTIME_IMPLEMENTATION_PLAN_ZH.md)

## 支持 V8 Agent OS

如果 V8 Agent OS 真的帮你的团队少重讲、少折腾，并且更放心地把长任务交给 Agent，欢迎在这里支持后续开发：

[https://afdian.com/a/justForever17](https://afdian.com/a/justforever17)
