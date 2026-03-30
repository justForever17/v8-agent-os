# V8 Agent OS

**V8 Agent OS** 不是一个把大模型塞进聊天框里的小玩具。  
它是一整套真正拿来干活的 Agent 系统。

它想解决的不是“看起来聪明”，而是更难也更现实的事：

- 真正强大的记忆系统不该依赖纯 Markdown，也不该任由 Agent 瞎写。
- MCP 和 skills 能不能按任务精准暴露，而不是装得越多越容易炸上下文。
- 有接口的桌面应用可以自动化操作，没接口的应用该如何高效且优雅的由 Agent 操作？
- OpenClaw 强的不是自身能力，而是卓越的生态，如何把 OpenClaw 的插件生态接进来，同时又具备更强大的初始状态。
- 为什么 Agent 只能有记忆系统而不可以有肌肉记忆系统？

如果你要的是一个**基础能力够硬、长期任务不散、记忆系统够强、工具生态够广**的 Agent 系统，这里就是唯一正式入口。

## 仓库里有什么

| 模块 | 路径 | 作用 |
| --- | --- | --- |
| Web | `apps/v8-agent-os-web` | 用户侧聊天界面、移动端入口、客户端体验层 |
| Admin | `apps/v8-agent-os-admin` | 配置中心、控制台、运行状态观测与治理面板 |
| Engine | `apps/v8-agent-os-engine` | 真正的执行平面：runtime、记忆、自动化、MCP、skills、安全与恢复 |

## 为什么它值得认真用

- **长期记忆不是点缀。** 真正的手自一体式混合记忆系统+RAG架构，独立 Memory Agent 自主写入，管理轻松，不删库 agent 记你一辈子。
- **基础能力健全且强大。** 原生拥有许多实用工具，忘记那个一切依靠 SKILL.md 烧 Token 的年代。
- **skills 和 MCP 不靠蛮力堆。** 目录再大，也只暴露当前任务真正需要的那一小块。
- **OpenClaw 生态可以无缝吃到。** 插件、通道、桥接能力保留，在这里再多 Agent Skills、再多 Tools 也能轻松吃下。
- **Runtime 边界是真实的。** Chat、Memory、Extensions ( MCP + Skills )、Automation、Safety、Computer Use、Plugin Host、RPA，各自做自己最擅长的工作和无缝互动，例如 Computer Use 联动 RPA 会产生什么？

这就是它和普通 Agent 应用最大的差别。

## 每个 Runtime 到底负责什么

- **CHAT RUNTIME**  
  负责规划、委派、回答、收束，把每个 run 从第一句话带到最后一个结果。
- **MEMORY RUNTIME**  
  负责偏好、知识、图谱、长期 recall，以及让 Agent 明天继续干活时不是从零开始。
- **EXTENSIONS RUNTIME**  
  负责 MCP 和 skills 的定向筛选，把庞大的工具目录压成当前任务真正需要的能力集合。
- **AUTOMATION RUNTIME**  
  负责 cron、hooks、追踪循环、定时唤醒，以及不会丢上下文的持续任务。
- **SAFETY RUNTIME**  
  负责审批、护栏、审计和高风险动作治理，保证系统敢干活但不乱来。
- **PLUGIN HOST RUNTIME**  
  负责接住 OpenClaw 插件生态、IM 桥接和外部工具通道，让外部能力进得来、用得稳。
- **COMPUTER USE RUNTIME**  
  负责桌面操作、界面接管和视觉执行，适合那些“没有 API 也得做”的任务。
- **RPA RUNTIME**  
  负责重复、确定、结构化的流程自动化，适合可以标准化复用的操作链。
- **NETWORK SUPERVISOR RUNTIME**  
  负责局域网/广域网 Supervisor 协同、远程唤醒和节点协作，是整机未来的网络层。

## 默认本地地址

| 服务 | 地址 |
| --- | --- |
| Web | `http://127.0.0.1:9527` |
| Admin | `http://127.0.0.1:9528` |
| Engine | `http://127.0.0.1:9530` |

## 快速安装

### Windows

```powershell
git clone https://github.com/justForever17/v8-agent-os.git
cd v8-agent-os
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

### Linux / macOS

```bash
git clone https://github.com/justForever17/v8-agent-os.git
cd v8-agent-os
./bootstrap.sh
```

## 推荐配置顺序

**注意：** V8 Agent OS 会大量使用 reranker （重排序）模型。  
如果你完全不使用 reranker，记忆系统、工具暴露和检索准确性都会被拖慢。

1. 先启动 **Engine**
2. 再启动 **Admin**
3. 通过 Admin 完成核心配置
4. 按顺序配置模型、记忆、插件宿主、自动化、系统基础
5. 最后再决定 Web 是源码运行还是打成 app / release

## 很实用的部署建议

- 推荐用小模型专门做记忆侧工作，成本更稳，效果也更划算。
- 如果本地算力不够，可以把 reranker、轻量多模态模型或辅助模型放到 Hugging Face Spaces、魔搭社区等免费服务器上，再配 vLLM 提供服务。
- 如果你习惯 OpenClaw 社区生态，完全可以把它接进来；但即便不接，V8 Agent OS 自己的核心能力也足够强。

## 公开文档

当前对外公开的文档只有这 4 份：

- [Engine API 参考](./docs/ENGINE_API_REFERENCE.md)
- [Engine Core 目录导览](./docs/ENGINE_CORE_DIRECTORY_GUIDE.md)
- [Engine 开发者指南](./docs/ENGINE_DEVELOPER_GUIDE.md)
- [Engine 开发者指南（中文）](./docs/ENGINE_DEVELOPER_GUIDE_ZH.md)

## 赞助 V8 Agent OS

> 如果 V8 Agent OS 真的帮你省下了时间、撑住了复杂任务、获得了灵感，或者让你的 Agent 系统终于开始像一个系统而不是演示，你可以在这里支持它继续成长：[https://afdian.com/a/justforever17](https://afdian.com/a/justforever17)

> “We become what we behold. We shape our tools, and thereafter our tools shape us.”  
> “我们眼之所见重塑了我们；我们塑造了工具，此后工具塑造了我们。”  
> — Marshall McLuhan
