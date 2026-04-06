# V8 Agent OS - 引擎开发者文档

如果你是一个准备给 V8 Agent OS 开发新功能、改 bug 或是梳理核心流程的开发者，这篇文档是你开始动手前的**心智共识库**。在我们的宇宙里，写得快远不如写得“稳”和“符合 Runtime 直觉”。

## 0. 当前全局真相链

当前主产品不再按“Engine / Admin / Web / Phone 各自一套局部状态”理解，而是按下面这条全局链路理解：

1. `engine`
   - 唯一 authoritative producer
   - 负责生成 `snapshot`、runtime event、history ledger、history materialized summary
2. `admin`
   - 唯一远端 broker
   - 负责把 engine 私有地址、私有路径、原始 runtime envelope 规范化成 surface 可消费 contract
3. `os-web / os-phone`
   - 视为同一 surface 的两个壳
   - 只消费统一的 realtime/history contract
4. `SessionRealtimeCDC + selectors`
   - 当前会话的唯一前端消费层
   - 组件不得绕过它自行轮询或自行解释 runtime 事件

换句话说：

> 真相不在页面，不在 Admin route 的临时拼装里，也不在 Phone/Web 的局部 reducer 里。  
> 真相只允许从 `engine -> admin -> shared contract -> CDC selector` 这条链往下流。

### 0.1 当前固定边界

以下边界已经锁定，开发时不要再打破：

1. `memory`
   - 底层保障 runtime，不参与前端实时互动
2. `desktop_live`
   - 手工驱动通讯，不进入当前会话主聊天 CDC
3. `plugin_host_channel / channels`
   - 历史层，不进入实时 CDC
4. `plugin_host_tool`
   - 仍属于实时交互层

### 0.2 排障顺序

遇到“页面没更新 / HUD 不一致 / 历史记录乱了 / 某类事件丢了”时，禁止从 UI 现象直接反推。

唯一允许的排障顺序是：

1. `engine`
   - 该事件是否真的被产生？
   - 是否进入 `runtime_events`、`runtimeTimeline`、`snapshot`、history ledger？
2. `admin`
   - 是否被标准化转发？
   - 是否被错误降级、过滤或错误改写成非 surface 可消费资源？
3. shared contract
   - event taxonomy / normalizer / selector 是否覆盖该事件？
4. `web / phone`
   - 组件是否只从 selector 取数？
   - 是否还残留本地 topic 特判、消息扫描、旁路轮询？

如果某个问题只能靠 UI patch 修复，通常说明你还没有找到上游真正的断点。

## 1. 核心开发角色与价值定位

你现在的身份是 **V8 Agent OS Runtime Architect**（不仅是写几个全栈 CRUD 页面而已）。
项目的终极目标是打造一个：**记住上下文、收束无意义噪音、过程透明可见并且强兜底可接管的 OS OS Runtime 机器**。

任何 Feature PR 和 Issue 修复的价值考核顺序：
`正确性 > 可恢复性 > 可观测性 > Runtime 层流转致性 > 兼容旧代码 > 开发铺陈速度`。
**宁可少写点“黑魔法”，不要打破系统执行链条的可恢复性。**

## 2. 理解三仓协作系统边界

当你觉得有个机制需要在哪里改一下，请停下来看一下你所在的仓库上下文：
- **`v8-agent-os` (主产品仓)**
  - 承载整个生态的心脏：包括处理底层的 `v8-agent-os-engine`（负责记忆、调用、图状状态）、管后勤权限数据的 `v8-agent-os-admin`（控制面）和视觉侧的 `v8-agent-os-web`。这是核心调度代码区。
- **`v8-agent-os-site` (静态与叙事仓)**
  - 不做运行时逻辑。它的定位是系统门户包装、官方公开文档透出和新用户认知建立面。
- **`v8-bridge` (OpenClaw生态桥接)**
  - V8 系统面向 OpenClaw 插件群生态的通讯护城河。牵一发而动全身，如果改到工具授权、Channels 管理或 Handoff 机制，必须时刻保证这里是 Fail-Closed 且不会击穿防护的。

## 3. 开发者面对这台机器的心智模型

### Engine 的重量级抽象
系统里最“神圣”的几块主线逻辑：`plugin_host`、`network_supervisor`、`desktop-live`、`runtime-governance`、`operations-center` 以及核心任务管线 `action_executor.py`。
当触及到上述逻辑模块，每一次对任务生命周期（Run Lifecycle）的改动都必须拷问自己：
- 现在的任务在意外中断后，能基于原来的 checkpoint 或 context 重新 Resume (恢复) 吗？
- 这里的调用逻辑产生副作用 (Side Effect) 并且遇到网络抖动发生 Retry 后会发生多次重复执行的灾难吗？
- Timeline 面板还能追溯我的步骤吗？（记录进入 `workflow_ledgers` 或 `run_records` 了吗？）

### 统一的运行时根目录与配置管控
- **真相源唯一性**：现行的系统开发里，根目录被严谨地收束在 `~/.v8-agent-os` 内部。所有新写的基础工具应用及状态数据等，都必须依附于该路径生成，禁止在此之外随意蔓延和自建私有路径池。
- **配置一致性把关**：所有的结构化配置存续于内部 Config Registry 下单向流转并由 `core/storage.py` 来处理落盘对接。如果后续要新增新的环境读取逻辑或控制开关，请调用正规抽象好的接口。杜绝任何硬编码的离散独立配置文件操作（比如直接尝试加载 `xxx_settings.json`）。

## 4. 推荐开发动作与自查流

每次想要做新功能的推演流程：
1. 先定位：这是页面展示层、Admin 分发配置控制层、Engine 执行运行层、还是桥接插件层？
2. 找准逻辑：如果是运行时相关的业务，不要把主要执行调度流丢进前端 Next.js route 或轻量处理端脚本里，主干要拉回 Engine 收束。
3. 可控与可验证：改得尽量“小且可逆”。先想好如果在跑长周期作业（比如挂机爬1000页数据），这个特性一旦抛出 Exception 系统该如何被观测到报错而不是进程静止了。

## 5. 什么时候必须停下来寻求社区或管理讨论？

默认情况你该放开胆量直接开发提交即可，但遇到下面这几类情况：
- 打算修改外部 API 和核心状态的通信语义；
- 大量的不可逆、不向下兼容的字段数据与配置结构迁移；
- 要设计一个之前完全没有规划过的持久化（保存库结构）新模型；
- 会涉及到跨上述提到的多仓权限责任转移的时候。

**停下键盘，编写并探讨技术方案。**
