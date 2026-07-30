# Creative Artifact Canvas 测试矩阵

更新：2026-07-29
范围：V8OS Web / Phone / Workbench / Creative Media runtime
原则：`Supervisor First, Runtime Grounded`。STATIC、MOCK、REAL-LIVE 与桌面 Preview 的结果分开记录，未跑项不得写成通过。

## 1. 绑定真相

```text
Workspace
├── Media Assets（稳定工作区身份，不保存会话 URL）
│   └── Virtual Folders（项目/剧集/素材/工作稿/产出/成片）
└── Session
    ├── Sources（用户上传）
    ├── Media Asset Uses（显式采用边）
    ├── Runs / Runtime Episodes
    │   └── Artifacts（Agent 产物）
    └── Components
        ├── Web / Phone 消息区
        ├── Workbench 标签与预览
        └── Canvas Graph
            ├── Session Graph / Revision / Run Lock
            ├── 素材、动作、持久结果槽（成功后呈现为可继续处理的素材卡）
            ├── 结果版本与 Run/Tool/Artifact lineage
            └── Workspace Workflow Templates（资源转待绑定输入）
```

硬约束：

1. Session 必须绑定一个明确 Workspace；同 Workspace 的两个 Session 只共享素材身份与虚拟目录，运行态、来源、产物、组件状态和采用边仍完全隔离。
2. Source 必须精确绑定当前 Session；用户上传不能重复登记为 Artifact。
3. Artifact 必须绑定 Session、Run 和工具/job lineage。
4. Canvas Artifact 还必须显式绑定 `canvasOperationId`；禁止用“最近产物”猜测占位卡。
5. 节点、边、选区、缩放、本地存储、文件标签、运行锁和迟到事件全部以 Session 为边界。
6. 工作区素材跨 Session 使用必须先建立目标 Session 的显式采用边；不同物理 Workspace 一律拒绝。
7. 普通 Human Surface 不投影 `include_unbound` 数据，不显示本地路径、内部 ID、binding 或执行合同。
8. Graph 保存、运行和恢复必须同时匹配当前 Session、Workspace authority、graphId 与 revision；前端不得成为执行计划真相。

## 2. 结果状态与时限

| 状态 | 定义 |
|---|---|
| PASS | 在当前候选上实际执行，断言和证据完整 |
| FAIL | 任一硬断言失败，或超过对应时限 |
| PARTIAL | 主链成立，但明确列出的验收子项尚未满足；不得对外称完整通过 |
| BLOCKED | Provider、配置、权限或环境明确阻断 |
| NOT-RUN | 尚未执行 |
| HISTORICAL | 旧候选曾通过，最终候选仍需复跑 |

Real-live 时限：

- 发送后 10 秒内：唯一用户消息出现，Canvas 锁定并建立一个占位卡。
- 60 秒内：必须出现首个参数合法、能力精确匹配的 Provider job；同一种参数修复错误最多重试 2 次。
- 图片编辑 5 分钟内：必须成功、明确失败或取消；超过 5 分钟直接 FAIL 并取消。
- terminal 后 5 秒内：Run、父 episode、子 episode、后台命令和 Canvas 锁必须全部停止。
- `run.cancel` 后 5 秒仍有模型、扩展或工具进度事件：FAIL。

## 3. 绑定与串区矩阵

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| BIND-01 | INTEGRATION | W1/S-A 刷新、重启 Web | Session 仍绑定 W1，不重新发现工作区 | PASS |
| BIND-02 | INTEGRATION | 同一 W1 下建立 S-A、S-B | source/artifact/node/edge/tab/localStorage 集合互斥 | PASS |
| BIND-03 | INTEGRATION | W1/S-A 与 W2/S-C | S-C 无法搜索、预览、下载 W1/S-A 资源 | NOT-RUN |
| BIND-04 | INTEGRATION | S-A 运行中切换 S-B | S-B 不继承锁、占位卡、进度或错误 | PARTIAL |
| BIND-05 | INTEGRATION | S-A 异步响应前切到 S-B | 迟到 source/artifact/event 被 Session gate 丢弃 | PASS |
| SRC-01 | INTEGRATION | Web/Canvas 上传图片、视频、音频、3D | 每项只新增一个当前 Session source | PARTIAL |
| SRC-02 | INTEGRATION | 上传后离开并重进 S-A | 媒体内容仍可读，不退化为永久占位 | PASS（图片） |
| SRC-03 | INTEGRATION | S-B 猜测 S-A source ID/URL | 服务端内容路由 fail closed | NOT-RUN |
| ART-01 | REAL-LIVE | S-A 生成文件产物 | session/run/workspace/tool lineage 精确 | PASS |
| ART-02 | INTEGRATION | S-B 猜测 S-A artifact detail/content | detail 与 content 两层均拒绝 | NOT-RUN |
| ART-03 | INTEGRATION | 普通聊天 Artifact 无 Canvas lineage | 可进素材抽屉，但不能填任意占位卡 | NOT-RUN |
| ART-04 | INTEGRATION | Canvas 产物回填 | 仅显式 `canvasOperationId` 更新原占位卡 | PASS |
| MAT-01 | STATIC | Creative Media 素材纪律 | 角色、用途、manifest、lineage、owner 与 artifact proof 均有明确合同 | PASS |
| MAT-02 | UNIT | 工作区素材列表 | 只返回当前 Session 所绑定物理 Workspace 的素材；不同 Workspace fail closed | PASS |
| MAT-03 | UNIT | 素材目录 | 目录只保存虚拟组织关系，不修改文件路径、来源或产物 lineage | PASS |
| MAT-04 | INTEGRATION | 同 Workspace 跨 Session 复用素材 | 可见但未采用不可执行；显式采用后只新增 use edge，不复制文件/URL | PASS |
| MAT-05 | UNIT | Session 删除 | 删除来源 Session 后，工作区素材身份仍存在；删除 Session 只级联其 use edge | PASS |
| MAT-06 | UNIT | 内部蒙版 | `canvas_mask` 不进入工作区素材库和素材目录 | PASS |

## 4. 消息区与 Canvas 同步矩阵

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| EVT-01 | STATIC | 审查 Canvas 提交入口 | 复用 ChatClient 正常发送，无第二套 Canvas API | PASS |
| EVT-02 | INTEGRATION | Canvas 发送一次 | 仅一个 user message、一个 Run | PASS |
| EVT-03 | Web + Phone | 用户气泡 | 中文只显示“本消息来自画布”，英文只显示“This message is from Canvas”；不显示附件、合同、路径、ID | PASS |
| EVT-04 | RUNTIME | Canonical 请求 | Supervisor 仍收到精简结构化 execution contract | PASS |
| EVT-05 | REAL-LIVE | 宽屏同时观察消息区和 Canvas | 同一时点可见 Supervisor 进度、Canvas 锁和占位卡 | PASS |
| EVT-06 | REAL-LIVE | 成功终态 | 对应占位卡填充、消息区终态、Canvas 5 秒内解锁 | PASS |
| EVT-07 | REAL-LIVE | failed/cancelled/timeout | 错误可见、无伪产物、Canvas 5 秒内解锁 | PARTIAL |
| EVT-08 | UNIT / CONTRACT | 快速双击/同帧重复点击/另一 operation 抢占 | 前端同步门禁阻止重复提交；Engine 原子拒绝同 Session 第二个 active Graph Run | PASS（Graph 10；Web 10） |
| EVT-09 | RUNTIME | Supervisor 终态 Human Surface | 精确 artifact/source/mask/job/operation ID 与绝对路径只留结构化 Runtime Surface | PASS（回归；未重复付费 Live） |
| EVT-10 | CONTRACT | 失败状态从实时事件切换到历史重载 | `recoverable_failed` 保持失败图标，不因状态从瞬时 `failed` 细化后消失 | PASS（Web 10；指定 V8ID 持久证据） |

同步截图至少保留：

1. T0：一个 Human Surface 用户消息；
2. T1：消息区进度 + Canvas 锁定/占位；
3. T2：精确 Provider/model/tool job；
4. T3：成功回填并解锁，或明确失败并解锁。

## 5. 手动画布交互

| ID | 模式 | 断言 | 状态 |
|---|---|---|---|
| UI-01 | BROWSER | 展开即完整画布，无固定分区和底部常驻输入框 | PASS |
| UI-02 | BROWSER | 工具栏、菜单、素材抽屉均悬浮，不挤压画布 | PASS |
| UI-03 | BROWSER | `+` 在标签滚动条内紧跟最后标签，菜单仅“文件/画布” | PASS |
| UI-04 | BROWSER | 默认横卡；图片/视频按固有宽高比 | PASS |
| UI-05 | BROWSER | 标题 hover 显示完整名称 | PASS |
| UI-06 | BROWSER | 左右端口可拖线，同端口支持多线 | PASS |
| UI-07 | BROWSER | 左键框选完成后才出现输入/删除动作 | PASS |
| UI-08 | BROWSER | 边 hover 出现评论按钮和小输入框 | PASS |
| UI-09 | BROWSER | 其他产物以微缩图留在素材抽屉，拉出才成节点 | PASS |
| UI-10 | BROWSER | 运行中拖动、删除、连线、蒙版、上传全部禁用 | PASS |
| UI-11 | CONTRACT | 抽帧、分割和生成结果 | 结果槽解析为真实 Artifact 媒体类型，可继续蒙版、连动作或在文件管理器中定位；不生成预览/下载终点卡 | PASS（Web 10；Shell 1） |
| UI-12 | BROWSER | 画布级滚轮始终以鼠标位置缩放，不再上下/横向平移；菜单、抽屉、时间轴和编辑器保持自身滚动 | PASS（当前源码浏览器；桌面待复跑） |

### 5.0 十维交互审计

| 维度 | V8OS 落地断言 | 当前源码证据 | 状态 |
|---|---|---|---|
| 画布导航 | 滚轮以指针为中心缩放；空格/中键/手形工具平移；显示全部、聚焦选区、缩放数值与小地图互相一致 | 浏览器 `scale(0.55) -> scale(0.888841)`；菜单滚动时 viewport 不变；小地图/聚焦合同存在 | PASS |
| 节点操作 | 单选、框选、多选、拖拽、复制、粘贴、重复、对齐、分布和拓扑整理全部可撤销；运行中锁定修改 | 浏览器框选 3 项、节点拖动后 Undo 可用且 Ctrl+Z 恢复；history/graph 纯模块定向契约 | PASS |
| 连线交互 | 左右 typed ports、多线、合法性说明、环路/重复/容量拒绝；普通边可重连或断开，固定动作结果边不可拆 | 浏览器普通边 `2 -> 1 -> Ctrl+Z -> 2`，固定边拖拽后仍为 2；全程无 message/run/task POST | PASS |
| 右键菜单 | 按选区/节点/边/空白过滤；首层最多 5 项，其余搜索展开；菜单滚轮不驱动画布 | 浏览器菜单 `scrollTop=30` 且 viewport 不变；overlay 合同校验首层限制 | PASS |
| 状态反馈 | 配置真相、输入数量、治理来源、运行/等待/失败/过期、进度、重试和预检原因均可见；禁止仅靠 prompt 猜“已配置” | 当前动作卡显示“已完成 / 已配置 / video 1/1 / 受治理 / 本地”；Engine validate 预检可打开且不触发执行 | PASS |
| 素材预览 | 图片/视频/音频/3D/PSD 使用对应查看器；离屏暂停、URL 就绪缓存和稳定占位避免重开白屏；时间轴媒体只预载 metadata | 当前源码连续 5 次概览/画布切换恢复 85/61/60/58/52ms，视频均 readyState=4、图片 1920x1080、零新增媒体请求；3D 复用 ModelViewer | PASS |
| 效率工具 | 快捷键、框选后浮动工具条、批量对齐/分布、拓扑整理、小地图、工作区模板和“运行到此”服务重度使用 | typecheck/合同通过；当前源码浏览器验证框选工具条与 Undo | PASS |
| 容错设计 | 所有图编辑默认惰性；pointer cancel 回滚；无效连接保留原边；Escape 关闭临时浮层；运行锁与 Session gate fail closed | 11 项 Web Canvas 契约、25 项 Engine Graph/Creative 定向测试；断线/撤销实测零执行请求 | PASS |
| 视觉动效 | 数据类型线色、关系虚线、选中高亮/非相关降噪、拖线反馈与运行状态过渡明确，并尊重 reduced-motion | 当前源码浏览器视觉 smoke；连接颜色/透明度/`motion-reduce` 合同 | PASS |
| 生成业务 | Session 可恢复 Graph、显式运行、动作持久结果槽、旧版本缩略图、素材/动作分层、Engine 权威编译与模板去 Session 化 | Graph/Creative Engine 定向 25 项；当前源码预检与结果槽浏览器 smoke | PASS |

说明：十维 PASS 表示当前源码在对应定向合同/浏览器 harness 下成立；Web production build、桌面 Preview、100 节点/200 边性能基准仍按第 10 节独立门禁，不能由本表替代。

### 5.1 可恢复执行图与工作区模板

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| GRAPH-01 | UNIT | 保存草稿后重载 Session | 节点、边、关系说明、参数、viewport 与 revision 从 Engine 恢复 | PASS（Graph 10） |
| GRAPH-02 | UNIT | `运行到此` 且画布含无关未配置动作 | 只编译目标祖先子图；无关草稿不阻断 | PASS |
| GRAPH-03 | UNIT | S-B 提交 S-A graphId/revision/source | Engine fail closed；旧 revision 返回冲突 | PASS |
| GRAPH-04 | UNIT | 动作重复运行 | 固定结果槽更新最新 Artifact，同时按 version 保留历史缩略图 | PASS |
| GRAPH-05 | CONTRACT | 前端执行提交 | 只提交 graphId/revision/targets；Engine 从持久图重新编译资源、关系和精确动作 | PASS（Web 10；Engine Creative 20） |
| GRAPH-06 | BROWSER | 框选素材/动作/结果 | 仅选区存在实际动作时出现“运行到此”；配置、连线和关系说明永不自动发送/执行 | PASS（当前源码浏览器；桌面待复跑） |
| GRAPH-07 | BROWSER | 从左右类型端口拖线 | 输入/输出方向与媒体类型受约束；空白落线只显示兼容动作或上传/选择素材，不再建立预览/下载终点 | PASS（合同；桌面待复跑） |
| GRAPH-08 | BROWSER | 已连接边端点附近拖拽 | 可重连；空白释放即断开；动作到所属结果槽的内部边不可拆 | PASS（当前源码浏览器；桌面待复跑） |
| GRAPH-09 | UNIT | 保存工作区模板 | 至少含一个动作；Session resource 转为 typed input，不保存 source/artifact/session URL | PASS |
| GRAPH-10 | UNIT | 同工作区另一 Session 实例化模板 | 图可恢复但未绑定输入不可执行；显式绑定素材后才可运行 | PASS |
| GRAPH-11 | UNIT | 不同物理工作区读取/删除模板 | workspace_key authority 拒绝；同工作区删除后双方均不可见 | PASS |
| GRAPH-12 | MIGRATION | 从已发布 v2 Session 本地画布迁移 | 首次写入 Engine/v3 后删除 v2 key，旧图不能在清缓存后复活 | PASS（合同；桌面待复跑） |

兼容债务：旧 Graph `sink` 节点只在 Engine 输入解析层保留两个完整客户端迁移周期；当前 Web 加载时直接丢弃，且不再创建或渲染。迁移期须补旧 Graph 读取量观测，降至可忽略后删除 Engine parser 分支。

### 5.2 Supervisor 模式控制器

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| MODE-01 | CONTRACT | Web / Phone / Shared 六态合同 | 仅允许 `auto / engineering / research / creative_media / computer_use / rpa`，非法值 fail closed | PASS（Shared 47） |
| MODE-02 | STATIC | 双端模式菜单与中英文本 | 智能/编程/调研/媒体创作/桌面操作/RPA 六项完整；Web 当前项具单选读屏语义 | PASS |
| MODE-03 | CONTRACT | 普通、排队、语音、文件点评与 Canvas 发送 | 每条消息保存发送瞬间模式；Canvas 特权仍是独立字段 | PASS（Web 93；Phone 6） |
| MODE-04 | RUNTIME | 智能模式 | 不新增强制路由，保留 Supervisor 自主选择和原 Engineering 触发 | PASS |
| MODE-05 | RUNTIME | 五种显式模式 | 首个持久动作必须进入所选权威 Runtime，并沿用 episode / ledger / proof / recovery / artifact 主链 | PASS |
| MODE-06 | RUNTIME | Canvas 与普通媒体附件 | 合法 Canvas 合同优先于菜单模式；普通媒体创作附件仍先走正常 Vision preflight | PASS |
| MODE-07 | RUNTIME | Spec 未批准 / 已批准 | 未批准时所有执行 Runtime 被 gate；批准后恢复 Canvas > 显式模式 > Engineering 优先级 | PASS |
| MODE-08 | INTEGRATION | 延迟队列与 promoted guidance | 入队模式不回写新的 Session 默认；同模式旧 episode 不吞掉新消息，新 episode 建立后停止重复路由 | PASS（Engine chat_runtime 390） |
| MODE-09 | INTEGRATION | 快速连续选择与跨会话失败 | Web / Phone 按 Session 串行 PATCH；旧响应、A 会话失败均不能覆盖 B；失败回到服务端确认值 | PASS（合同/类型检查） |
| MODE-10 | COMPAT | 旧版日常/编程客户端 | 旧 `daily` 明确迁移为 `auto`，旧 `engineering` 迁移为 `engineering`，不会被历史 research/rpa 静默遮蔽 | PASS |
| MODE-11 | RUNTIME | Human Surface 事件 | 显式模式/Canvas 不显示伪“编程未命中”；仅 auto 下真实 Engineering 决策可见且 payload 真相扁平 | PASS |
| MODE-12 | BROWSER | 延迟 PATCH、刷新与切会话 | 最终选择等于最后一次点击，刷新后保持，会话间不漂移 | NOT-RUN |
| MODE-13 | REAL-LIVE | 新会话显式定向 | 消息区可见正常 Supervisor 进度，且建立所选 Runtime episode 与终态 proof | PASS（Auto 21.09s；Engineering 57.16s） |

兼容债务：旧 `supervisorWorkMode` 只保留两个完整客户端迁移周期；单独使用旧字段时 Engine 记录
`legacy_supervisor_work_mode_used`，调用量降至可忽略后删除。新客户端不得再写旧字段。

### 5.3 Canonical reasoning 与模型身份

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| REASON-01 | CONTRACT | Supervisor / 直接子 Agent 模型调用 | 使用真实 provider stream；内部 Memory/工具模型不被误改为流式 | PASS |
| REASON-02 | CONTRACT | OpenAI-compatible 累积 reasoning 流 | 累积快照转增量且不重复；工具续接只回放同一 provider/modelRef 的官方字段 | PASS（241 项 reasoning/model/ERC/chat 广泛回归） |
| REASON-03 | REAL-LIVE | `minimax-cn::MiniMax-M3` Supervisor | 60 秒内出现 canonical reasoning 与正文；终态 snapshot 和重载保留 ThinkingCard | PASS（21.39s；11 个 delta；历史重载可展开思考卡） |
| REASON-04 | REAL-LIVE | `custom-sub-285ee689::gpt-5.6-sol` Supervisor | 同名跨 Provider 仍保持精确 modelRef；仅展示 provider 返回的公开 summary，不推测闭源隐藏 CoT | PASS（Chat Completions 15.66s；未返回公开 summary，未伪造卡片） |
| REASON-05 | ROLLBACK | 临时切换 Supervisor 到 GPT 后撤销 | `config_broker` 精确事务回滚，Supervisor 恢复原 MiniMax 绑定 | PASS |
| REASON-06 | REAL-LIVE | SUB GPT OpenAI Responses summary | `/responses` 原始 SSE 必须返回 `response.reasoning_summary_*`，随后产生 canonical reasoning 与思考卡片 | BLOCKED（Web 18.49s 完成；`summary=auto/detailed` 原始 SSE 均无 summary 事件） |

### 5.4 音视频时间选区

| ID | 模式 | 断言 | 状态 |
|---|---|---|---|
| TIME-01 | CONTRACT | 视频/音频使用专用时间轴，不再让用户填写自然语言或两位小数秒参数 | PASS |
| TIME-02 | CONTRACT | Canvas 合同只传 `probeFingerprint` + frame/sample index，并保留资源与 `canvasOperationId` | PASS |
| TIME-03 | LOCAL | Engine 自有成对 FFmpeg/FFprobe 7+ 无窗口执行，原文件不变且不调用 Provider/MediaKit | PASS |
| TIME-04 | LOCAL | 视频按 PTS/time base 建立 frame boundary；输出 postflight 帧数等于 `[start,end)` | PASS（24fps 真实短片，12/12 帧） |
| TIME-05 | LOCAL | 音频按 sample index 分割；无损输出 postflight 样本数等于 `[start,end)` | PASS（48kHz 真实音频，12000/12000 样本） |
| TIME-06 | LOCAL | 视频按 `frameIndex` 抽取单帧；产出正式 PNG artifact，postflight 仅 1 帧 | PASS（真实短片第 9 帧，PNG 签名与 1/1 帧） |
| TIME-07 | CONTRACT | 首次打开先用 ffprobe stream header 返回近似 FPS/time base/帧数；后台逐帧 probe 完成前禁止保存或执行动作 | PASS（Engine 5；当前源码浏览器；桌面待复跑） |
| TIME-08 | BROWSER | 独立播放头与开始/结束手柄拖动时合并为约 20Hz 快速 seek，视频帧合成后回写实际位置；松手才做精确 seek，保存按钮才写入 Graph 参数 | PASS（Web 10；桌面待复跑） |
| TIME-09 | BROWSER | 完整 frame boundary 到达后保留用户已选秒数并吸附到最近真实边界，显示实际边界时间；首次读取已有 Graph 参数时保留 frame/sample index | PASS（当前源码 `F602 -> F602 / 20.06667s`；桌面待复跑） |
| TIME-10 | REAL-LIVE | 新片段按当前 Session/Run/tool/operation lineage 登记并回填持久结果槽 | NOT-RUN |
| TIME-11 | UNIT | 同一未变文件的并发精确 probe | 共享一个 ffprobe 任务；每个调用仍保留自己的 Session resource truth，文件指纹变化后不复用 | PASS（Engine exact 6） |
| TIME-12 | BROWSER | 时间轴初次打开和拖动 seek | 媒体使用 Range 友好的 metadata preload，不先下载整段文件；精确边界仍由受治理 ffprobe 提供 | PASS（合同；桌面网络证据待复跑） |

### 5.5 Admin 身份投影

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| ID-01 | UNIT | Admin 修改 Supervisor 昵称、身份标签和头像后读取历史消息 | canonical `agentId=supervisor` 使用当前 Admin 展示资料，不回退成 `Supervisor / SUPERVISOR` | PASS（Engine projection 17） |
| ID-02 | UNIT | 实时 `agent.started` 使用自定义昵称 | Supervisor 判定只依赖 canonical agent id，不拿可配置文案猜角色 | PASS（Engine projection 17） |
| ID-03 | UNIT / SHARED | 自定义 Subagent 结束并恢复会话 | terminal event 与共享投影保留注册 Agent 的昵称、身份标签和头像，不被 Supervisor 资料覆盖 | PASS（Engine identity 5；Shared 10） |
| ID-04 | WEB / PHONE | 右侧概览和详情 | Web 与 Phone 同时显示 Subagent 昵称和身份标签，长文本不撑破布局 | PASS（typecheck；桌面/真机待复跑） |

### 5.6 PSD 分层操作

| ID | 模式 | 场景 | 硬断言 | 状态 |
|---|---|---|---|---|
| PSD-01 | UNIT | 读取 PSD | 受治理 helper 返回画布尺寸、可见图层树、层级、边界和合成预览，不把绝对路径投影到 Human Surface | PASS（Engine Creative 定向） |
| PSD-02 | UNIT | 多张图片合成 PSD | 图层顺序、位置、缩放、透明度和可见性写入新 Artifact，原素材不变 | PASS（Engine Creative 定向） |
| PSD-03 | UNIT | 编辑已有 PSD 图层 | 只接受结构化图层变更并生成新 Artifact/version；lineage 绑定当前 Session/Run/tool/operation | PASS（Engine Creative 定向） |
| PSD-04 | BROWSER | Canvas 操作 PSD | 图层树、拖拽位置、层级调整和参数编辑由专用浮层表达，动作仍需“运行全部/运行到此”才执行 | PASS（typecheck；桌面待复跑） |

## 6. 蒙版局部编辑 Real-live

流程：S-A 上传原图 → 仅绘制头部蒙版 → 发送 `image.edit` → 使用已启用且 endpoint 精确为 `image.edit` 的模型 → 新图片绑定当前 operation → 回填占位卡。

| ID | 断言 | 状态 |
|---|---|---|
| MASK-01 | 请求同时含 `sourceId`、`maskSourceId`、`canvasOperationId`、`operationKind=image.edit` | PASS |
| MASK-02 | Engine 仅按 runtime context 当前 Session 解析 source；跨 Session fail closed | PASS |
| MASK-03 | 显式模型不能绕过 enabled 与精确 operationKind 门禁 | PASS |
| MASK-04 | Provider multipart 同时收到原图与 mask | PASS |
| MASK-05 | 5 分钟内得到真实新图，不是原图 preview、文字计划或等待卡 | PASS |
| MASK-06 | 输出尺寸、构图、背景、服装和姿势保持；仅目标区域明显改变 | PASS |
| MASK-07 | 蒙版边缘自然，无硬边、透明破口或原目标残留 | PASS |
| MASK-08 | 原图仍是 Source，新图是独立 Artifact，lineage 完整 | PASS |
| MASK-09 | terminal 后消息区终态与 Canvas 解锁同步 | PASS |

视觉验收必须保留原图、蒙版、结果图三图证据。

## 7. 连线语义 Real-live

| ID | 断言 | 状态 |
|---|---|---|
| EDGE-01 | 合同含 edgeId、from/to node 与 resource ID、方向、用户评论 | PASS |
| EDGE-02 | S-A 的边不能引用 S-B 节点或资源 | NOT-RUN |
| EDGE-03 | 边评论只形成一次正常聊天消息和 Run | NOT-RUN |
| EDGE-04 | Supervisor 正确复述起点、终点和关系方向 | NOT-RUN |
| EDGE-05 | 实际工具参数与连线角色一致，不把连线当装饰 | NOT-RUN |
| EDGE-06 | 产物填入本次 operation 的目标卡，不填最近空卡 | NOT-RUN |

“理解”以实际工具参数、输入资源、输出落点和 lineage 一致为准，不以自然语言自述为准。

## 8. Workbench / 3D / 文件预览

| ID | 模式 | 断言 | 状态 |
|---|---|---|---|
| VIEW-01 | REAL-LIVE | 新 Markdown 经 artifact content endpoint 打开 | PASS |
| VIEW-02 | REAL-LIVE | 新 JSON 可查看源码/格式化内容 | PASS |
| VIEW-03 | INTEGRATION | 非法 JSON/大 Markdown 显示诊断，不空白 | NOT-RUN |
| VIEW-04 | BROWSER | 消息气泡 `.glb` Viewer 可旋转、缩放、释放资源 | NOT-RUN |
| VIEW-05 | BROWSER | Canvas 复用同一 3D Viewer 契约 | PASS（合同） |
| VIEW-06 | INTEGRATION | 私有资源不发送给第三方 Viewer | NOT-RUN |

## 9. 取消、熔断与 Windows 桌面体验

| ID | 模式 | 断言 | 状态 |
|---|---|---|---|
| GOV-01 | UNIT | 同类参数 shape 错误超过 2 次即失败，不无限修复 | PASS |
| GOV-02 | UNIT | Creative Media episode 有 wall-clock deadline | PASS |
| GOV-03 | INTEGRATION | `run.cancel` 5 秒内停止子 Agent 模型/工具事件 | PASS（当前候选；首轮曾 FAIL） |
| GOV-04 | INTEGRATION | cancel 同步取消父/子 episode、lease 与命令进程 | PASS（当前候选；首轮曾 FAIL） |
| WIN-01 | UNIT | Windows 同步命令使用 `CREATE_NO_WINDOW` | PASS |
| WIN-02 | UNIT | Windows command-session fallback 使用 `CREATE_NO_WINDOW` | PASS |
| WIN-03 | DESKTOP | Live 期间无 PowerShell/cmd/Windows Terminal 弹窗 | PASS（12 秒高频采样：Engine `skills list` 的 cmd/conhost 窗口句柄均为 0；首轮曾 FAIL） |
| WIN-04 | DESKTOP | 后台进程仍可被日志和终端面板观察 | PASS |

## 10. 构建、性能、回滚与提交门禁

| ID | 模式 | 通过标准 | 状态 |
|---|---|---|---|
| BUILD-01 | STATIC | Web 合同测试全过、TypeScript 通过 | PASS（Canvas 合同 11、typecheck、1025 个 i18n 键与 production build 通过） |
| BUILD-02 | STATIC | Phone 定向合同与 typecheck 通过 | PASS（当前 Canvas Human Surface 合同 1、typecheck、i18n 通过） |
| BUILD-03 | STATIC | Engine 目标测试通过 | PASS（Graph、Creative Media、PSD、时间轴、身份投影定向 40） |
| BUILD-04 | STATIC | Admin 合同、session-realtime build/verify 通过 | PASS（Admin 定向 6；共享契约 47；Admin production build；完整 Admin 套件另有 3 项既有源码断言失败） |
| BUILD-05 | PREVIEW | `node apps\v8-agent-os-cli\bin\v8os.mjs preview --rebuild` 通过 | PASS（当前 Graph 候选 136s；Engine/Admin/Web/Shell 拉起，9530/9528/9527 均为 200） |
| PERF-01 | BROWSER | 100 节点/200 边相对基线退化不超过 10% | NOT-RUN |
| PERF-02 | BROWSER | 30 次 Canvas/消息/3D 切换无持续内存增长 | NOT-RUN |
| PERF-03 | CONTRACT | 画布包含 idle 动作卡但无运行任务 | 不启动素材目录轮询；只有 Session 运行或结果槽处于 reserved/running/waiting 才每 3.5 秒对账 | PASS（Web 10；网络面板待复跑） |
| SAFE-01 | STATIC | scoped `git diff --check` 通过 | PASS |
| SAFE-02 | ROLLBACK | scoped patch apply/reverse-apply check 通过 | PASS（当前 52 文件 staged patch） |
| SAFE-03 | ROLLBACK | 隔离副本实际回滚与再应用 smoke 通过 | PASS（detached worktree 两轮 apply -> rollback，均恢复干净） |
| SAFE-04 | GIT | staged hunk 不含 Memory/Research/Plugin 等旁线 | PASS（`database.py` 仅暂存 Canvas 建表 hunk；Research lease hunk留在工作树） |
| SAFE-05 | GIT | scoped commit 完成且无 secret/日志/本地生成物 | NOT-RUN |

## 11. 当前证据

当前源码十维浏览器 smoke：

- 独立 Web dev 实例运行在 `localhost:9547`，复用当前 Engine，但不替代 production build 或桌面 Preview。
- 画布滚轮按指针缩放；菜单内部滚轮只滚菜单；节点拖动、框选、撤销、预检和窄屏 Composer 边界均通过。
- 普通边拖到空白后边数 `2 -> 1`，Ctrl+Z 后恢复为 2；固定动作结果边拖拽后仍为 2；过程无消息、Run 或 Canvas task POST。
- 精确时间轴加载期保留 `frameIndex=602` 且禁用输入，不显示临时伪时间；完整 boundary 到达后仍为 `F602 / 20.06667s` 并恢复编辑。
- 连续 5 次“概览 -> 画布”切换，媒体恢复耗时为 85/61/60/58/52ms；视频每次 `readyState=4`，结果图每次为 1920x1080，无等待占位且没有新增媒体内容请求。该证据不替代 PERF-02 的 30 次内存测试。
- 当前 `/chat` 在不挂载 Canvas 时也会出现 React `Maximum update depth exceeded` 告警；已确认属于全局聊天页既存基线，本轮未用画布通过结论掩盖，production/preview 阶段仍须单列观察。
- 当前 production preview 中打开既有 Canvas 会话，消息终态、视频素材卡、动作卡、持久结果素材卡、缩略图、受治理状态和“运行全部”均恢复；该次 Chromium smoke 无 console error 或 page error。

文件产物 Live：

- 真实新会话内生成 JSON 与 Markdown；两者均通过当前会话右侧栏的受治理内容入口加载。
- 原始 Session、Run、Artifact 标识和本机路径只保留在非版本化验收记录中，不写入仓库。

首轮蒙版 Live（判定 FAIL）：

- 发送、锁定和同步进度成立；真实编辑产物、operation lineage 与正常终态不成立。
- 子 Agent 发生无界工具循环；Run cancel 后仍继续执行，最终通过重启 Engine 终止。
- 唯一图片 Artifact 是原图 preview，不得作为编辑成功证据。

当前候选蒙版 Live（判定 PASS）：

- 来源、内部蒙版、Creative job 与结果 Artifact 均验证为同一 Session / Run / Workspace lineage。
- 内部蒙版实际为 `surfaceVisible=false`、`previewable=false`、`downloadable=false`，且未绑定 Human message。
- OpenAI `images/edits/gpt-image-2` 实际执行 `operationKind=image.edit`，生成了局部换头结果并通过视觉验收。
- Web 用户气泡实际只显示“本消息来自画布”；Vision 分析作为后台进度出现，但未阻塞同 Run 的 Creative job 建立。
- 该次历史 Supervisor 终态曾回显内部 ID，当前候选已在 typed handoff/Human Surface 分离层补回归；未为此重复触发付费图片生成。

子代理认知纪律与桌面候选：

- 旧 managed Creative Director 模板曾要求 provider prompt 默认英文；本机备份证据不纳入版本库。
- 当前 managed 模板改为完整保留源语言语义；运行时 charter 先于 persona 注入。
- OpenAI/Anthropic provider 出站测试均保持 charter/persona 为 System authority；精确 Canvas 正常路径断言 Director/subagent 调用数为 0。
- `v8os preview --rebuild` 成功；Engine/Admin/Web/Shell 后台进程窗口句柄均为 0，Engine 最终 `/health` 200，9530/9527/9528 均可用。

## 12. 过期 Session 删除

只有 Engine 权威生命周期明确为 expired/deleted/tombstone，且无 active Run/episode/queue/lease/handoff，才可通过官方 Session 删除入口处理。年龄、标题、旧 content URL 404、资源丢链都不是过期证据。

测试会话只允许在提交完成后通过官方 Session 生命周期入口按已核对清单删除；禁止直接修改数据库。
