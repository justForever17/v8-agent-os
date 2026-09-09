# Supervisor 能力可达性验证（2026-09-09）

基准：`ceb1e90581a24a80b529c882eec6a7728ad2ca6d` 后续工作树。此记录服务 Supervisor 直接执行、浏览器会话/视频和真实工具证明；不是安装包发布说明，也不代表所有场景通过。

## 变更及真实边界

- 只读应用/媒体/浏览器发现与按需工具组共用原 owner；已加载工具不再被 Extensions 预筛丢弃。新 run 的 grant 过期，同 run 恢复保持；子/孙代理只用自己的 TaskBrief 授权。
- 浏览器工具复用现有 profile/session/lease；当前 DOM/AX、精确元素、用户接管、取消与 Safety 检查保留。登录读取使用原 persistent context；旧 Scrapling CDP 流程会建立无登录的新 context。
- 会话域观察不含 cookie 值；已观测域/配置域和复用开关控制读取。跨域文档跳转需重新授权，不把此导航约束宣传为所有子资源的网络沙箱。
- 浏览器代理的安全阶段/跳转域错误不再丢成通用 503；未授权跳转不重复搜索重试，失败的 profile 读取更新最近读取状态。只传稳定错误码与域名，不传带凭据的 URL。
- 最后独立复查发现自动 profile 拒绝仍落到 static/reader 的遗漏，现权限/身份失败立即结束该抓取链；typed 错误先于自由文本 timeout 分类，域名中的 timeout 不再制造网络失败。最近读取仅归并 root/www，其他子域分开。
- HTML5 视频工具取得实际帧、取样时间、播放器信息和已加载字幕，并恢复播放；不支持绕过 DRM，也不证明任意 iframe/音轨/全片理解。
- 桌面可见结果保留有界控件及精确定位属性；点击/输入使用已有 automation ID/type 合同。历史 hint 只给匹配控件排序；窗口类不污染子控件类，旧句柄恢复仍核当前目标和坐标。
- 桌面原语不能结束借用的 chat run。图步数达到安全边界时保留 checkpoint/episode 并暂停；Web/Phone 区分可续接身份和正在运行。共享包升级到 `0.0.44`，四端 tarball/integrity 同步。
- Research 共用 `ANSWER_BODY_CONTRACT`：先交付有用结论，限制独立；未重写旧答案，不加字数/语义评分硬门禁。格式指导不等于事实审阅可靠性保证。

## 本机 live 证据

以下是相同本机配置的分散样本，不是统计性能基准。原始私有报告留在临时验证目录，未入仓。

| 场景 | 结果 | 时间与限制 |
| --- | --- | --- |
| 本地自有网页，真实 Supervisor 填写、点击、回读、关闭 | 通过；无隐藏委派或命令替代；Web live/reload 一致 | 98.05 秒，`browser-0909-c2` |
| 本地隔离浏览器多播放器/登录 fixture | 14 步通过；旧 Scrapling 路径 401，原 context 读取成功；实际红绿蓝帧、时间及字幕正确 | 不调用模型；`browser-video-candidates-0909-b` |
| 文档重定向授权 | 默认 6 项通过、1 项 live skip；显式 Edge live 7 项通过，跨域/端口目标请求为零 | 包含同域携带 HttpOnly 会话读取，原标签保留；不证明所有网络流量隔离 |
| 百度会话搜索 | 早轮 3 条实际结果，`profileUsed=true`；后轮跳转 `wappass.baidu.com` 被授权边界阻止 | 早轮 5.89 秒成功不代表登录永久有效；最终正常入口约 3.77 秒返回明确的非重试授权错误，未将其当成功 |
| Metaso | 公开搜索 3 条结果约 0.906 秒；profile 读取约 5.94 秒 | 登录 SPA 只取得 143 字正文，不能宣称完整搜索页已实读 |
| Context7 已配置 key | 5 个官方来源、7,812 字符的文档结果；真实 MCP owner | resolve 3.45 秒、query 5.27 秒；无 LLM、无其他 MCP 启动 |
| 抖音视频 `video-0909-c` | 同一视频 2/5/8 秒实际三帧联合视觉、逐帧摘要、恢复播放与自有关页回执；Web live/reload 一致 | 346.18 秒，与委派并跑，非独立性能样本；出现登录浮层，主管关闭后读取可访问视频，不证明登录仍有效或全片理解 |
| 单张生成图片 `media-0909-b` | 查真实模型→直接生成→实际视觉核查→交付，无隐藏导演/子代理；1024² PNG 字节/hash 与内容核对通过 | 99.08 秒；另实际点击 Web 消息产物，右侧工作台加载同一 1024² 图片，不用文件名代替预览验收 |
| 原生桌面 `desktop-0909-i` | 真实输入与 Submit 回执、截图和主管最终答复一致；无其他 Agent/脚本替代；Web live/reload 通过 | 176.16 秒；两次视觉约 19.17 秒，全部模型合计约 74.97 秒（含视觉，不能重复相加） |
| 两名子代理 `delegation-0909-c` | 两名实际 worker 分别读应用与图片模型目录，两份真实 handoff、两份父级接受；Web live/reload 一致 | 177.29 秒。仍有参数纠正；“无窗口”曾被理解成“无浏览器进程”，已补目录统计口径，不声称零纠错 |
| 最终原生窗口窄验证 `native-focus-0909-b/c` | 两轮输入/提交及取消后零副作用，主窗/小对话框/输入后截图正确，清理自有窗口与进程 | 0 次模型；输入约 14–15 秒、点击约 12–13 秒。前台是另一窗口，焦点复用正确未跳过；正向提速收益未得到物理证明 |

桌面失败轮保留：初轮签名不接收 invocation metadata；原语提前完成父 run；控件 top-five 投影丢定位；背景窗口被当成已聚焦；历史 hint 救回不匹配的输入框并误报按钮成功。最终 GUI 回执测试能够拒绝这些假成功。

旧视频判据把取样时间写死为 2/6/10 秒，错误拒绝任务允许的 2/5/8 秒。现对照同次媒体结果的实际时间、帧路径、成功联合分析、摘要和真实关页；5 个反例覆盖缺帧、错时、错图和未分析等失败。c 原始报告仍保留旧判据失败，新判据对同一证据重放通过，未为修测试又付费跑视频。

桌面无模型拆时：focus 约 5.28–5.59 秒、type_text 约 7.10–7.31 秒、click 约 5.29–5.55 秒，settle 仅约 0.37–0.55 秒；其余准备/收尾约 1.4–2.3 秒。仍需定位观察与动作层成本。真实取消事件曾被 native/Agent 映射成通用 error，最后修复保留 cancelled，并对同一 DB 事件离线重放，未再执行桌面动作。

最后通过现有 Admin 配置入口核对的复用值为 true→true，不沿用早轮关闭快照。正常取源不传临时 profile override：Metaso 当前配置 API 返回 3 条结果约 0.69 秒，这不是网页登录态证明。Bocha 按用户要求未再调用。

## 模型与内容反例

- 真实 M3 最小双工具 probe 约 4.52 秒模型耗时，两个完整参数均未执行；仅证明该短场景，未出现真实交错分片。长委派上下文仍出现 `tool_call_chunks` 中 18 字符不完整参数，finish reason 为 tool_calls；整批被拒绝，不能补齐 JSON 执行。
- 新诊断只记参数源、索引、长度、hash、是否完整 JSON，不保存私有参数值。SDK 原生 delta → Factory patch → bound adapter 的同步/异步交错反例覆盖完整、首项截断、次项截断。
- 固定 evidence 的三次 Research M3 验证**未通过人工语义审阅**：writer 曾增加无依据的排他性条件，reviewer 仍 accept；另一次因 harness 调用次数耗尽未审阅。内容组织有改善不等于事实正确，不能隐去失败或继续对同样例无限付费重试。
- 未上报 usage 的零值是存储占位，不能声称视觉/失败模型调用免费或零 token。恢复回合的计时只关联当前 run，不累加同 session 历史模型调用冒充本轮耗时。

## 回归与产品验证

- 改动测试文件的 Engine 回归：32 个文件，843 passed / 67.64 秒；随后获取边界补修的最终相关组 83 passed / 4.38 秒，含内部 profile→fallback 故障注入和 root/www 状态反例。上述组重叠，不累计相加。
- UIA 最后精确目标恢复组：67 passed，包含 17 条真实元素构建的假 OS wrapper 测试；旧实现被其中 10 条反例检出。不是物理操作证明。
- 浏览器 Node 定向 26 项完成（其中一项显式 live 默认跳过）；播放器真实浏览器结果另列。
- 共享暂停状态与 Web/Phone 卡片行为测试通过；4 端已安装同一 `0.0.44` tgz。源码 Preview 的 Admin/Web production rebuild 成功，并启动 Engine/Admin/Web/Shell。
- 跨平台安装包、Ubuntu 物理桌面、macOS、全部 provider：未在本轮验证。此前误启动的全量 pytest 已中止，不将其输出计为本轮全量通过。

## 验收入口与回滚

`tests/scripts/run_supervisor_capabilities_live.py --live --case <discovery|desktop|browser|media|delegation|video> --web-url <本机Web> --output-dir <新目录>`；desktop/browser/media/video 还须 `--allow-side-effects`，video 须明确授权的 `--video-url`。真实窗口只操作自建 fixture，退出清理自己的进程。

副作用由原工具/session/lease/Safety/job/receipt owner 记录。报告同时检查实际回执、工具终态、产物字节与 Web 对照；缺关页/视觉失败/非空准备文本均不能成为通过依据。报告先逐字段脱敏再序列化，防空 token 字段破坏 JSON。

本轮没有状态库迁移。旧单输入视觉、原 runtime 入口、无 grant/显式禁止及旧参数调用由定向回归覆盖；撤销工具组只收回后续能力，不撤销已完成的外部副作用。若回滚代码，需同步回滚共享 tarball 与四端 lock 并重建 Preview，不强制移动已发布 tag。

## 尚未关闭

Research 固定证据的事实错误与审阅漏检、Metaso SPA 完整实读、百度当前验证/跳转边界，以及原生观察/动作层高耗时仍未关闭。跨平台安装包与任意已登录站点未验证。已通过的指定委派、直接媒体、视频和桌面场景不替代这些残余验收。

本轮按范围本地提交；产品 tag/发布 CI 不是本记录的通过项。工作区根 AGENTS 与三份项目 Skill 的源码/分发包另外维护，不属于主仓 Git。
