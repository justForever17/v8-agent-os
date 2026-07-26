# Creative Artifact Canvas 测试矩阵

更新：2026-07-26
范围：V8OS Web / Phone / Workbench / Creative Media runtime
原则：`Supervisor First, Runtime Grounded`。STATIC、MOCK、REAL-LIVE 与桌面 Preview 的结果分开记录，未跑项不得写成通过。

## 1. 绑定真相

```text
Workspace
└── Session
    ├── Sources（用户上传）
    ├── Runs / Runtime Episodes
    │   └── Artifacts（Agent 产物）
    └── Components
        ├── Web / Phone 消息区
        ├── Workbench 标签与预览
        └── Canvas 节点、边、占位卡、素材抽屉与运行锁
```

硬约束：

1. Session 必须绑定一个明确 Workspace；同 Workspace 的两个 Session 仍必须完全隔离。
2. Source 必须精确绑定当前 Session；用户上传不能重复登记为 Artifact。
3. Artifact 必须绑定 Session、Run 和工具/job lineage。
4. Canvas Artifact 还必须显式绑定 `canvasOperationId`；禁止用“最近产物”猜测占位卡。
5. 节点、边、选区、缩放、本地存储、文件标签、运行锁和迟到事件全部以 Session 为边界。
6. 普通 Human Surface 不投影 `include_unbound` 数据，不显示本地路径、内部 ID、binding 或执行合同。

## 2. 结果状态与时限

| 状态 | 定义 |
|---|---|
| PASS | 在当前候选上实际执行，断言和证据完整 |
| FAIL | 任一硬断言失败，或超过对应时限 |
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
| EVT-08 | INTEGRATION | 快速双击/Enter+按钮/网络重试 | idempotency 阻止重复消息和 Run | NOT-RUN |
| EVT-09 | RUNTIME | Supervisor 终态 Human Surface | 精确 artifact/source/mask/job/operation ID 与绝对路径只留结构化 Runtime Surface | PASS（回归；未重复付费 Live） |

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
| WIN-03 | DESKTOP | Live 期间无 PowerShell/cmd/Windows Terminal 弹窗 | PASS（当前候选；首轮曾 FAIL） |
| WIN-04 | DESKTOP | 后台进程仍可被日志和终端面板观察 | PASS |

## 10. 构建、性能、回滚与提交门禁

| ID | 模式 | 通过标准 | 状态 |
|---|---|---|---|
| BUILD-01 | STATIC | Web 合同测试全过、TypeScript 通过 | PASS |
| BUILD-02 | STATIC | Phone 定向合同与 typecheck 通过 | PASS |
| BUILD-03 | STATIC | Engine 目标测试通过 | PASS（隔离补丁 465） |
| BUILD-04 | STATIC | Admin 合同、session-realtime build/verify 通过 | PASS（3 + 10） |
| BUILD-05 | PREVIEW | `node apps\v8-agent-os-cli\bin\v8os.mjs preview --rebuild` 通过 | PASS |
| PERF-01 | BROWSER | 100 节点/200 边相对基线退化不超过 10% | NOT-RUN |
| PERF-02 | BROWSER | 30 次 Canvas/消息/3D 切换无持续内存增长 | NOT-RUN |
| SAFE-01 | STATIC | scoped `git diff --check` 通过 | PASS |
| SAFE-02 | ROLLBACK | scoped patch apply/reverse-apply check 通过 | PASS |
| SAFE-03 | ROLLBACK | 隔离副本实际回滚与再应用 smoke 通过 | PASS |
| SAFE-04 | GIT | staged hunk 不含 Memory/Research/Plugin 等旁线 | PASS |
| SAFE-05 | GIT | scoped commit 完成且无 secret/日志/本地生成物 | NOT-RUN |

## 11. 当前证据

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
