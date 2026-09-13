# 9.13.1 独立验收合同与覆盖矩阵

基线 `53fd9aceb4b9f33694032e6bfa1162cb1c7435c4`。这是待执行合同与功能保留账，行的存在不代表通过。完整来源/hash 在 `reports/surface-matrix.json`；实际证据在独立 report。作者报告只用于定位，关键结论由 experience 重做用户操作、读写对账或故障反例。

## 分项判据

| 分项 | 必须留下的证据 | 不足以通过的证据 |
|---|---|---|
| 功能 | 目标身份/版本、请求、服务端结果或 receipt、回读、重载一致；失败/取消/重复/冲突不伪成功 | 截图、200、按钮存在、mock 调用次数 |
| 便利性 | 从普通入口完成任务的动作数、滚动/搜索/帮助路径、首字段编辑后的保存矩形、dirty/错误恢复 | 字段藏到高级、只靠鼠标 hover、作者说更好用 |
| 视觉 | 同 fixture/主题/viewport 的截图与 computed 值；文字/对齐/间距/卡片/弹层一致；内容不截断 | 一个首页截图、缩字体后容纳更多控件 |
| 性能 | 同机/fixture/构建/冷热状态；热路径 n≥30、median/P95、请求/连接/bytes/帧/资源回落 | 单次 stopwatch、dev 编译耗时、Lighthouse 代替原生 FPS |

任一数据丢失、跨身份串用、保存/安装伪成功、不可达必需操作独立阻断。禁止加权总分抵消。对未支持平台的正确说明可以通过状态诚实性，但不能记该平台功能已通过。

## 逐页能力账

每行都覆盖可适用的 default/custom/loading/empty/error/dirty/saving/cancel/conflict/late-response/reload；重定向页只测其适用项并写 N/A 原因。每个编辑子域记录旧入口、最终新入口、原 handler/API、payload 与保留字段；不能只记录路由。

| Admin 路由 | 必须保留的动作/子视图与观察点 |
|---|---|
| `/` | 登录状态与重定向目标；无循环 |
| `/login` | 首次 owner、已有登录、冲突转登录、密码错、存储不可用、自动填充、键盘提交；失败不清非秘密输入 |
| `/admin/verify` | 实际 verifyRequest 链接、返回；不可达不可误报正常 |
| `/admin` | 入门可跳过/重开；审批跳正确对象、运行状态/刷新、统计独立失败 |
| `/admin/model-hub` | 国际/本地/自建/platform/OAuth 接入；provider/model/七模态/voice；创建编辑探测测试删除、默认角色、试听上传；未知健康不涂绿 |
| `/admin/models/providers/[id]` | 精确 id/channel/modelRef；直接深链、回退；base URL/凭据替换清除、角色与连接测试 |
| `/admin/chat-runtime` | 主管/专家稳定 tab；query/back；嵌入编辑器无双标题/双保存 |
| `/admin/supervisor` | 名称/头像/模型/温度/反思/工作方式；完整 prompt；工具与视觉角色授权 |
| `/admin/subagents` | 创建/编辑/删除、职责 prompt、工具/Skill 选择、空 allowlist；Family/Research shards/递归预算/external worker 各自保存 |
| `/admin/engineering-lane` | 工作区、普通非 Git 执行、写集/版本/恢复引用、验证与风险记录、执行参数 |
| `/admin/research-runtime` | 网站精确域授权、来源/URL/readRef、资料详情、归档/恢复/删除、pending 恢复；限制可见 |
| `/admin/creative-media` | 能力映射、fallback 顺序、制作详情/配方/关键帧/角色/质量/费用、远端 unknown、归档墓碑；不误删本地文件 |
| `/admin/music` | 原收藏链接与迁移目的地；实际功能去向可发现 |
| `/admin/desktop-automation` | 主机状态、依赖安装、识别模型/权限/锁屏、坐标映射、平台缺能力诚实状态 |
| `/admin/rpa` | canvas/steps/properties/variables/elements/run/diagnostics/JSON；捕获录制、定位、秘密变量、草稿模板、执行取消证据 |
| `/admin/desktop-pet` | 预览/尺寸/播报/视觉附件、事件配置；Shell lifecycle/close ack；语音/摄像头同意、Linux 状态 |
| `/admin/extensions` | Skills/MCP 列表、scope、命令/ZIP/JSON 导入、预筛调用设置、env/headers 凭据引用、重连/健康/启用/授权 |
| `/admin/extensions/store` | Skills/MCP、国际/ModelScope、搜索分页/详情 README、来源版本、安装/取消/失败/恢复、手工登录续接；详见 X 组 |
| `/admin/plugins` | store/installed/grants/jobs/doctor；连接配置权限活动、dry-run、已有登录、Godot 步骤、更新卸载回滚 |
| `/admin/memory` | 11 tabs：context/preferences/logs/knowledge/workflows/artifacts/graph/agent/upload/config/runtime；编辑检索引用归档、源文件；详见 G 组 |
| `/admin/memory/runtime` | redirect 保留原定位 query 与返回 |
| `/admin/settings/memory` | redirect 到同配置 owner；原阈值/继承模型回读不变 |
| `/admin/automation` | cron/hooks/wake ingress；稳定 tabs、深链与返回；人工聊天不被非人类触发策略误伤 |
| `/admin/automation/cron` | 新建/编辑/启停/立即运行/删除；timezone/DST/next run、内置保护、target/recovery、nudge/execute |
| `/admin/automation/hooks` | 事件/命令/Python/runtime/RPA 目标、重复名、启停删除；精确 sourceMetadata 保真 |
| `/admin/network-supervisor-runtime` | peers/邀请/连接/任务、relay/第三方应用；Phone 与 peer 分界、派发/ACK/重试/取消/防重放 |
| `/admin/users` | owner/已配对手机、生成/过期/消费/撤销、profile/instance、锁定与注销 |
| `/admin/runtime-governance` | 能力状态、锁定核心、可见/启用/直接暴露分开、重置/策略提交、专业路由试投入口 |
| `/admin/operations-center` | overview/approvals/runs/evidence/advanced、focusRun/focusSession/query、分页；审批/cancel/retry 绑定原 run、原始证据可达 |
| `/admin/safety-control` | 安全方式/模板差异、规则搜索与精确编辑、用户免审与例外、模型 owner、待处理问题 |
| `/admin/stability-strategy` | queue/reject/preempt、strict durability 与恢复路径；不偷偷更改策略；保存域明确 |
| `/admin/projects-workspaces` | 列表/默认目录、文件夹选择失败手输、物理路径与信任、非 Git、完整 AGENTS/空值/版本、换项目草稿 |
| `/admin/system-base` | 通用/服务/存储/S3/能力包/远程观察/浏览器 profile；精确地址/allowedOrigins/secret 专业入口；提权与解锁分开 |
| `/admin/advanced-governance` | 具名问题/配置/日志/恢复索引；搜索能定位实际字段，无平行保存 owner |

| 其它入口 | 完整子视图/交互范围 |
|---|---|
| Admin shared shell | Sidebar/窄屏 drawer、Topbar 搜索/同义词/字段定位、inbox、DeviceConnectDialog、语言/主题/debug、桌面观察、右键/Shell 窗口控制 |
| 通用浮层 | Dialog/Select/Menu/Toast/Help/AvatarCrop：Tab/focus trap、Escape/dirty、触屏、滚动与 focus restore；隐藏项无命中/Tab |
| Web `/` `/connect` `/connect/error` | 本机trusted client进入与重定向、连接失败/重试/返回；不套Phone配对；不强制重建原会话 |
| Web `/chat` | composer/history/queue/attachments/context refs、Settings/appearance/voice/avatar、Canvas/产物、终端、Admin/Shell 往返 |
| Web `/rpa` `/specs` `/ui-patch` | RPA专业工作台/执行取消、spec查看与审批、UI patch预览/应用/失败；返回原会话，目标workspace/session不串用 |
| Phone `/` `/login` `/pair` | 冷启动与热恢复分离、扫描/粘贴/权限失败/重试；旧 profile 可恢复；不把热恢复当首次配对 |
| Phone `/chat` `/sessions` | 中文 IME/selection/语音转写/附件/技能插件/队列、new 与 return 区别、历史分页搜索/重命名/删除/continue、上滑不拉底 |
| Phone `/connect` | 独立已配对 profiles 与当前 authority peers 分栏；搜索/状态/last seen/单项切换；peer 只用已授权 link 和本机 neighbor 会话，不宣称通用远端隧道 |
| Phone `/artifacts` | scope/list/detail 独立失败、分页/缩略图/文本/视频/WebView、权限与取消下载 |
| Phone `/approvals` `/specs` | 摘要→详情；target/key/revision 固定；单项 busy、失败不影响其他项、切 profile 后旧卡不可提交 |
| Phone `/rpa` `/desktop-live` | 显式打开、关闭/离页释放、回聊天草稿、授权目标重验；不把桌面预览替代真实执行 |
| Phone `/settings` `/+not-found` | 头像/背景/语音/外观、真注销、连接管理；SafeArea/软键盘、失效路由返回 |

## 可重放行为反例

通用 fixture 由 `fixtures.py` 生成。故障使用可控 Promise/响应，而非竞争概率或任意 sleep；UI 交互结束以可观察 DOM/服务端状态为同步点。本文编号即后续证据 ID。

| ID | 输入/操作 | Oracle 与必需证据 |
|---|---|---|
| A01 | 首字段编辑，不滚动；50 字段+三行错误；390×844/1024×768/1440×900/200%文字 | 保存矩形完全可见；末字段可滚到 footer 上方；dirty/失败文案不遮输入 |
| A02 | 改一字段，未知嵌套/false/0/空串/secret 占位不碰，保存回读重载 | 只有目标字段变化；placeholder 不覆盖实际 secret；API/config owner 不变 |
| A03 | A/B 两域草稿；A 成功/B 503；409 冲突、取消与迟到回包 | 各域独立状态；B 草稿保留；无“全部已保存”；target/version 不漂移 |
| A04 | 列表搜长名→对象详情→取消/保存；全部旧动作逐一调用 | 卡片紧凑且至少一个常显入口；完整 ID/职责/参数可读可复制；unknown 非 healthy |
| A05 | 鼠标 hover、Tab/focus、触摸帮助、Escape，边界屏幕与长帮助 | 同一内容可达、不裁切、可消除遮挡；有链接用可点帮助面板 |
| A06 | 从普通导航找低频参数/单位解释/排障；按真实字段名和任务词搜索 | 打开准确分组并聚焦字段；技术参数保留精确编辑，无秘密索引 |
| A07 | 每页 503/403/empty/部分失败，刷新与 retry | 标题与导航可用；spinner 仅 pending；失败不能变空成功；成功区保留 |
| A08 | 中文 composing+Enter、textarea 换行、对话框 Escape/Tab、双击提交 | 不误提交；一次 mutation；dirty 退出去向明确；恢复原焦点 |
| A09 | theme/语言/窄屏切换；反复展开关闭20次 | 不重挂载草稿；隐藏层零焦点/命中；监听器/RAF/observer 回落 |
| G01 | global+两工作区共享同 entityId，各有独立边；另一个 scope 403 | global 一份，visual identity 含 cluster；无跨 scope 假连线；403 不泄漏计数 |
| G02 | 6/20/100 clusters，长标签；公转/自转/hover/拖动/点击/空白 | 几何不穿越；hover 停靠微近、点选惯性靠近；空白回自然轨道，标签水平 |
| G03 | 节点菜单 CRUD/关系/定位，运动中编辑与版本冲突 | 原功能可达；target canonicalEntityId/scope/version 固定；删视觉实例不误删共享实体 |
| G04 | 暂停/reduced motion/离 tab/后台；20次打开关闭；有界 nodes/edges/bytes | 单时钟；隐藏无持续绘制；global/local-only计数口径明确；partial非完整 |
| W01 | A→Admin→A，选择文本/草稿/附件/滚动/背景；Shell20次切面 | 同文档/会话/实例恢复；无 loadURL 硬刷新；详细订阅唯一 |
| W02 | A→B→A与点当前会话；两套草稿、输入法与附件 | 同目标 no-op；不同目标独立；返回不是 new；selection/引用可恢复 |
| W03 | v1 提交延迟2s时输入 v2；成功/timeout/旧失败；重复ACK | v2不被清/覆盖；v1 clientMessageId 一致；未知结果先对账 |
| W04 | A pending queue→B detail 慢/失败；omission与明确[]；乱序snapshot/live | B不显示A队列；A保留；omission不清空，明确空按权威收敛 |
| W05 | 本地崩溃/刷新/renderer回收与重启 | 已授权恢复窗口内草稿/未决意图不丢；无法恢复范围如实说明 |
| B01 | 两图一视频，重排/移除/多上传/取消/保存失败/Phone改头像 | playlist与仍引用素材保留；revision往返；失败不发布草稿列表 |
| B02 | 图片自定义间隔、load慢；视频ended；旧canplay/ended迟到 | load完成才计时；视频播完仅推进一次；旧世代回调无效 |
| B03 | 棋盘/色块sRGB、明暗主题、桌面/窄屏侧栏、设置预览 | 空白背景与侧栏对应裸素材区域无系统性蒙色/blur；菜单仍可读 |
| B04 | play拒绝/404/全部损坏、后台/暂停/恢复、20次切面 | 错误局部且有恢复；无死循环；最多1活动video、当前+下一图片，后台暂停 |
| T01 | A/B cwd含中文空格，scope/start/list迟到；双击/响应丢失/取消创建 | 精确conversation/workspace/revision；真实pwd吻合；单createRequestId单PTY；迟到创建可查 |
| T02 | 同processId每200ms新projection、语言/状态切换 | xterm/WS保持；只身份/授权变化重建；目标稳定 |
| T03 | WS1006/ticket过期/旧socket迟到close/terminate500 | disconnected不伪terminated；单重连；失败外显；只有Engine停止证明才终止成功 |
| T04 | 两观察端+REST+慢消费者，UTF8/ANSI/OSC每字节分块 | cursor/seq一致；观察不偷走输出；中🙂不乱码；gap明确 |
| T05 | 有限50MiB输出、慢网络/低CPU、Ctrl-C、长粘贴、resize | 队列bytes有界；完整日志hash可取；输入顺序/ack、控制及时、真实exit；不偷偷重放离线Enter |
| T06 | 向上滚/选中复制、切终端、分栏拖动/0尺寸/Engine重启 | 阅读位置保持；最终cols/rows正确；重启不伪复活；隐藏不终止 |
| P01 | 当前会话重选、A/B草稿切换、history返回 | input/selection/附件/插件/Skill不丢；new与return分开 |
| P02 | 慢提交v1+新输入v2、迟到process/scope/detail | 新草稿保留；完整SessionKey+generation阻止旧回包，即使abort无效 |
| P03 | A queue→B慢读；snapshot遗漏；后台/前台对账 | 不串queue；未决输入不被当缓存驱逐；每次恢复一次合并 |
| P04 | 独立profiles A/B/A；同authority授权peer；未授权link | 正确实例/主体/会话；两个拓扑分别证据；不越出已有peer会话能力 |
| P05 | 两authority同session-1/message-1/resource-1，cursor/tombstone不同 | messages/cursor/tombstone/draft/resource均分区；操作A不覆盖/抑制B |
| P06 | 同authority LAN→已验证隧道；opaque大小写ID | 缓存/草稿命中；地址只改transport generation；未验证endpoint不带凭据 |
| P07 | SecureStore reject、SQLite失败、activation中断、10×401 | 不报保存成功，旧profile可用；凭据分小条目；当前profile单refresh，另一profile不注销 |
| P08 | Chat→Connect/Settings→Chat20次，返回键/Chat按钮分开 | 导航栈不线性堆叠；活动页/设备一条流；后台无装饰轮询 |
| P09 | 1/5/20/100 profiles与peers分开；50%离线、400ms RTT、2%丢包 | 摘要先出，单慢设备不阻塞全表；选中优先，有界并发 |
| P10 | Android AVD release/物理Android/iPhone；锁屏30s/5min/杀进程/换网 | 平台分层；恢复全文/seq/queue与草稿；无设备项NOT_RUN，不用web结果代替 |
| P11 | 1k/10k sessions、50/500/5000消息、1000节点单消息、10/100/500event/s | 输入/停止响应，上滑不拉底；复制/展开全文hash与seq完整；30min流内存记录 |
| P12 | 20次产物WebView/视频/桌面预览开关、键盘/旋转/CJK/语音 | 句柄解码/订阅回落；SafeArea/键盘不遮发送保存；返回原selection与草稿 |
| X01 | 默认/手动国内/重载/切回；国际失败 | 默认国际；无地区/失败自动切源；选择持久，国际能力不退化 |
| X02 | A detail慢B快；切源/关闭再打开；立即安装 | 完整provider+id+candidate digest+target revision一致；旧结果不能安装A |
| X03 | old-query后到、中文composition、Enter/清空、分页/详情返回 | 当前query不被覆盖；partial/总数口径诚实；过滤/位置保留 |
| X04 | A/B安装并发、A先结束、重复提交、skipped/conflicts/partial | 单项busy；同目标幂等；receipt决定措辞；不虚报已安装/已健康 |
| X05 | 100段README+长配置+390键盘/200%缩放 | 详情标题/目标/安装footer始终可达；tabs稳定；文档滚动不隐藏主导航 |
| X06 | 同名双来源、ZIP含scripts/references/assets、遍历/超量/冲突 | 完整安装、containment/预算拒绝、目标锁与恢复；不只复制SKILL.md |
| X07 | HTTP/SSE/stdio配置、专属endpoint、替换冲突/secret占位 | 托管/本地/最终业务API可达分层；CredentialRef；保留未改字段，冲突不覆盖 |
| X08 | 需手工登录/部署后返回、断网/超时/取消/重试 | 原操作恢复与收据对账；不自动部署云资源/跑网页命令/全局镜像/关闭TLS |
| X09 | 冷/热搜索详情，安装后回列表，静置/后台 | 无README N+1、单flight、有界缓存/请求、安装只失效目标，无全目录强刷 |

## 视觉/性能合同与测量规格

协调指定：共享 motion 100/140/180ms，ease-out `(.22,1,.36,1)`，panel radius 12px、control radius 8px、settings width 1040px；Admin scope topbar 48px；AdminSaveBar 在非滚动末行复用原 handler。不同端不强制像素尺寸相同，语义与行为必须一致。

视觉逐页分别检查 light/dark、1440×900、1024×768、768×1024、390×844/touch、200%文字、keyboard、reduced motion。卡片长名、英文、中文、空状态、unknown/error 需要独立样本；无截图的状态不写像素验收通过。静态样例仅是 DESIGN_TARGET。

每次性能记录字段：`commit, fixtureVersion/hash, machine, OS, browser/device, buildMode, DPR, viewport, network, coldOrWarm, scenario, sampleIndex, durationMs, requestCount, responseBytes, activeStreams, longTasks, memory`。Phone另记JS/UI帧、release构建与AVD/physical标识；Shell/PTY另记真实进程与input→ack→write→paint。

Admin设置/搜索/详情至少30个同环境热样本；Phone热返回/本地设备选择P95≤200ms、即时反馈≤100ms为设计目标。同机同fixture同冷热状态n≥30后才比较10%退化；单次dev导航只作观察。20次开关检查连接/RAF/listener/observer/decoder，GC后heap趋势与长流稳定段另记，不用一次内存值作泄漏结论。

## 候选交接要求

协调交入精确整合 commit、各域隔离地址、schema与fixture适配变化、已知限制后复验。运行前核对实际源码/包integrity与目标进程版本；按功能账填实际新入口，再对FAIL和共享原语受影响项执行，扩展到完整矩阵。作者修复同一函数不自动翻转本报告。

统一 Preview、生产构建/Shell、真实 provider、最终打包/CI/发布由协调负责；本lane仅记录收到且可独立核查的证据。Phone已探测无实体设备；协调提供Pixel_8/Small_Phone AVD可选，启动与资源由Phone任务协调，physical仍NOT_RUN。
