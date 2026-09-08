# 2026-09-08 桌面主线候选修复验收

已提交源码基准：`fa3fe2ee3d7764cce41af79483949dc9d52d6f57`（9.7.1）。本记录覆盖其后的候选改动；发布提交、CI、tag 和原生安装包结果以对应 Git 记录与 Actions 为准。私有运行记录、模型输入和截图不随源码分发。

## 已验证行为

| 修复面 | 代码与合同验证 | 实际执行证据与边界 |
| --- | --- | --- |
| 自动输出与完整参数 | factory/adapter 最终边界、用户 fixed、协议数值预算、截断原始 JSON、同模型有界恢复及完整 hash；不能让 SDK 修补半截参数后执行 | 配置中的 MiniMax-M3 原生长写入超过 4,096 个供应商报告输出 token；同 worker 创建与两次局部编辑、完整文件和浏览器交互通过。并非所有 provider 已实测 |
| 工程进展与恢复 | 慢上下文准备不阻塞事件循环；心跳不冒充有效进展；取消、租约过期、晚到结果、队列存活和父级精确 handoff 修复反例 | 专用临时工作区的父级验收修复完成，文件由 draft 改为 approved；Web 实时和刷新结果一致。不会代替用户初始化 Git 或提交工程 |
| Research 获取 | 精确 URL、候选选择、正文/链接分页、实读证据、来源与原始问题归因；已删除无生产调用的旧语义规划/调和分支 | 本轮既有真实 Research 完成来源读取及独立审阅；后续复用验证使用保存快照，不冒充重新验证外部网络 |
| 成熟答案读取与独立核验 | 32K/80K 完整分页、限制、原始 read observation 索引、来源快照 hash；答案阅读不能替代来源阅读 | 新会话真实 Supervisor→Verification Engineer：实读 6 份完整来源快照、7 条原始 claimId/引用键/URL 无错位，父级 ACCEPT，未重新 Research 或写工程文件；Web 四项 live/reload parity 均通过 |
| 资料权限 | 持久用户/session/工作区授权、限量前过滤、ID 修改锁内重查；伪 parent、跨用户/工作区、撤销、来源会话删除、项目改名与无工程目录会话反例 | 旧测试答案的真实 session 来源可恢复，同用户同可信工作区复用通过。来源不明的旧 global 项不会自动开放；Admin 管理入口保留 |
| 文件版本凭据 | 同 actor/run 成功写入续期，外部变更、跨 actor、过期、冲突和原子写失败回滚 | 原生连续编辑通过；没有承诺操作系统级 CAS，也未新增分段写事务协议 |
| Computer Use | 真实窗口绑定、激活水印排除、小对话框保留、本地应用与浏览器任务区分、原始/重绑证据 | Windows 解锁桌面上的自有窗口：主窗口 1,350×750、对话框 330×195，截图颜色与目标句柄、真实输入、提交回执和退出均通过。锁屏样本保持失败，不能只凭图像尺寸通过 |
| Minimal approval | 普通安装目录读取/零参数应用启动仍经 Safety 与审计；OS/V8/凭据保护、提权及复杂 shell 不获例外 | 工具入口合同通过；未对用户现场应用批量执行命令，不宣传内核级隔离 |
| CLI/Linux 存活 | Linux `/proc` 僵尸状态与 EPERM/缺失进程的保守判断；Shell 退出邻接回归 | 真实 WSL Linux 的自有子进程由旧 kill(0)“存活”纠正为僵尸终态；Windows 实际 CLI tarball 的 help、活 PID、死 PID 与源码 hash 通过。不是 Ubuntu 24.04 物理托盘退出验收 |
| Extensions | 原后台 owner 刷新；route 缓存绑定目录、工具 schema/session 与完整授权；MCP 通知合并、取消、旧会话晚到、失败 stale 及 Skill 扫描轮转 | 真正任务筛选仍基于当前请求；无目录变化不等待完整刷新。第三方 MCP 通知采用合同/fault 验证，未逐一真实登录验收。外部 Skill 目录仍有有界后台扫描 |

## 联合实测计时与判据

最后一次保存答案联测使用实际配置的 MiniMax-M3，未改 provider 或输出预算：提交响应 366 ms，Engine 首个工作事件约 249 ms，首段正文约 10.8 s，整体约 407 s。六个保存来源正文在约 2.3 s 内依次返回；剩余耗时包含多个模型调用、委派参数纠错和长篇最终答案生成，不能统称网页读取慢，也不能据此承诺低延迟或零重试。

七项判据分别是当前 run 完成、当前 worker 实读来源、原始编号绑定、交付保留至少五个原来源、未重新研究/工程、Web 实时刷新一致、父级明确接受。它们均通过。独立核验表的关键日期、原文/转载 URL 与保存来源正文已对照；自动脚本仍保留 `semanticTruthAssessed=false`，不把传输/引用检查冒充通用事实或法律审查。

本次同题经过多轮失败与修正，属于 replay/validation；不是未见过的 holdout。早期失败记录保留：看不到保存读取工具、只读被误配成无工具、仅说已委派、只读答案而未核对原来源、临时编号替代原始编号，都不得因最终状态 completed 而通过。

## 回归与构建

- 本轮 47 个 Engine 改动测试文件合并运行：2,140 passed、68 subtests passed；两处旧夹具未提供会话身份被新权限检查正确拒绝。已补隔离 DB 身份，相关文件 71 项复测通过，未放宽权限或改掉原正文/哈希断言。
- Research 权限/读取/旧 broker/Surface 组合 449 项通过；最后 scope、ledger 生命周期与 Admin 合同组合 46 项通过。以上与合并运行有重叠，不相加。
- Extensions/Plugin/MCP 邻接组合 295 项通过；Computer Use 定向与相邻回归、Safety 四组、文件版本与预算回归通过。具体命令按 `apps/v8-agent-os-engine/tests/README.md` 和 `tests/scripts/README.md` 选择。
- 共享事件包测试、四端 TypeScript、包 integrity 检查通过；Admin/Web 生产构建和 `v8os preview --rebuild` 成功，加载更新后的 Engine/Admin/Web/Shell。
- Shell 存活/托盘/退出邻接 15 项通过；CLI 组合回归通过（保留一个环境 skip），独立 CLI tarball 烟测通过；发布治理 31 项通过。
- 固定证据验收入口补 `--live`：默认不读取输入、不加载配置、不获取日志锁、不调用模型；46 项脚本合同通过。
- Extensions 最后否定请求回归 88 项、19 subtests 通过；真实接口确认“不做编程”不再推导 code/create，也不召回代码 Skill/context7；明确 GSAP 请求仍排名第一。五次热请求约 199–322 ms，匹配分析约 1.3–2.1 ms，目录等待为 0。仍可出现弱相关咨询候选，候选不等于执行授权；未宣称任意自然语言筛选完全准确。
- Python 编译、diff 检查、新增文本与共享包隐私审查通过。删除旧 Research 私有分支的依据与回滚点见 `apps/v8-agent-os-engine/tests/core/research_private_retirements.md`。

## 未承诺的范围与回滚

用户明确延后 Ubuntu 托盘物理问题，待同版本日志再查；Windows 特定商业应用、任意已登录网站、所有 provider、所有 CPU/GPU 和原生发布包均不能由本次 Preview 替代验收。真实安装包由发布 workflow 继续构建及烟测。

源码改动使用独立提交，可在隔离分支通过 revert 回退并重跑对应合同；不回退或改写用户运行数据库。自动输出、读取权限、文件完整性、MCP 授权与原始引用的反例不得为回退便利而关闭。剩余兼容边界和下一轮观测见多 Agent 技术债台账 005–007。
