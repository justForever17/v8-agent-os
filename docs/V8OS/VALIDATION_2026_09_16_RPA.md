# RPA runtime / Studio 验收记录（2026-09-16）

候选基线：`04f872229980af131dce6e1871a60a6d99eba9f8`；分支：`codex/rpa-runtime-916`。
本轮仅交付候选分支，不推送主线、不发布 tag、不改 CI/版本。主仓之外仅使用本任务隔离状态与临时窗口/文件，证据不入库。

## 执行真相与改动

- **CODE_FACT**：CU 原先已保存 trace，编译器也没有独立 RPA LLM 审查代理。真正断点在 Studio 没有绑定 CU 会话的自动录制入口、录制恢复和导出/执行的版本与回执边界。明确的 `llm_call` 节点仍保留。
- **CANDIDATE_FACT**：CU 原始 trace 是动作证据 owner；RPA recording 只按绑定会话、目标和捕获窗口投影。source run/step、actor、toolCall、observation 保留。暂停恢复使用源游标，原子写入并序列化停止/回调，重复与迟到事件不增加动作；未确认/去重动作不被当作已执行动作编入草稿。
- **CANDIDATE_FACT**：FlaUI 与 Playwright 捕获回调采用单次在途请求，ACK 才推进序号；事件身份、body hash、generation 冲突明确拒绝。停止撤销令牌并终止绑定进程。候选不是 proof；必须由新鲜目标查询证明唯一，证明绑定 locator 与目标，修改后重新验证。
- **CANDIDATE_FACT**：草稿保存使用版本 CAS。运行不再隐式覆盖草稿，准备文件各有独立路径和 hash，审批恢复绑定脚本、命令、变量、actor 与工作区。重复 runId 不能二次派发或改目标；同名录制与重复停止不覆写手工编辑。
- **CANDIDATE_FACT**：手工画布/普通 trace 草稿执行当前 Robot 脚本；trace 只是来源，不再被当成另一份可绕过编辑的执行计划。显式 `computer_use_playbook` 保留原入口。Robot 失败、超时或取消后删除了自动 CU 重放路径。
- **CANDIDATE_FACT**：直接 RPA 审批接回既有 ERC 投递 CAS；批准只恢复原准备版本。启动时发现已进入 executing 的丢失 worker，原子保存 unknown/reconciliationRequired、blocked delivery、run/event/snapshot；不会自动重放。
- **CANDIDATE_FACT**：子进程收到取消后结束进程树；派发前已取消时不启动。执行未知保持未知。episode handoff 不再把未知/待审批当作 ready。Robot 子进程仅传递白名单运行身份，动作借用父 run，不结束父生命周期。
- **CANDIDATE_FACT**：真实回放修复了桥接目标丢失、trace 内部参数误传、Robot 命名参数类型、不同输入值共用变量名、画布 action 在批准模板时丢失，以及批准成功模板因缺少 trace 历史立即被冻住的问题。
- **CANDIDATE_FACT**：Studio 保护未保存内容、刷新/重载草稿与变量、编辑期间返回的旧保存/校验、完整步骤 JSON、条件/循环范围。Web 显示真实回执、历史和停止；Engine 还未登记任务时显示准备中并禁用停止。

## 可复现验证入口

所有 live 命令必须显式 `--live`，且只针对隔离 `V8_AGENT_OS_HOME`。Python 使用环境中已安装的匹配版本；native SDK/包通过已有受管工具链与能力包入口获取。

| 层级 | 入口 | 行为 oracle |
| --- | --- | --- |
| 合同/故障 | `tests/rpa/` | 暂停、取消、乱序恢复、并发写、陈旧证明/CAS、敏感 trace、审批版本/命令漂移、启动恢复零重放 |
| 子进程 | `tests/runtime_core/test_process_launch.py`、`test_rpa_feature_pack_subprocess.py` | 取消/超时杀后代、父身份保留、无凭据或 Python 注入 |
| 平台合同 | `tests/runtime_core/test_posix_driver_target_and_receipt.py` | 目标变化不输入、命令失败不是成功、不扩大截图范围、不重复部分输入 |
| UI 行为 | Admin `tests/rpa-editor-behavior.test.cjs`；Web `tests/rpa-stop-confirmation.test.cjs` | 编辑不丢、保存版本一致、状态不伪成功、未确认不能停止；包含能击败旧实现的 mutant |
| 回调 | `tests/scripts/rpa_callback_sender_contract.test.mjs`；`verify_flaui_callback_contract.py` | ACK 丢失同 body 重发、串行、序号冲突、撤销、停止 |
| Windows live | `tests/scripts/run_rpa_recording_live.py --live --isolated-root <fresh> [--state <isolated-with-packs>]` | 真实临时 WinForms 输入/提交→原生工具 trace→草稿；指定 state 再批准并用真实 Robot 回放，同一窗口观察提交结果；模型调用被禁止 |
| 浏览器 live | `node tests/scripts/run_rpa_browser_live.mjs --live --output <fresh>` | 隔离 Edge 两个页、精确 CDP target、歧义拒绝、捕获不触发业务 pointer/click、dispose 恢复输入 |
| Robot live | `tests/scripts/run_rpa_robot_live.py --live --output <fresh>` | 真实文件副作用；超时/取消/重复/批准/恢复/文件变更，无额外一次写入 |
| 生产页面 live | `tests/scripts/run_rpa_surfaces_live.py --live --state <isolated> --output <fresh>` | Admin 编辑/保存/批准→Web 真执行，逐字校验文件；确认运行后停止，后续写入不存在；历史重载一致 |

测试等待异步浏览器查询使用显式 `evaluate` 加有界轮询；本机 Playwright 的 `wait_for_function(async predicate)` 未等待 Promise，不能用它证明 Engine 已登记 run。成功 HTTP 响应也不能证明停止或副作用完成。

## 范围与未验证项

本次最终 Engine 定向矩阵 **312 passed / 3 skipped**（86.92 秒）；跳过项属于非本机 PTY 环境。Admin 行为 12 项、Web 停止行为 2 项、浏览器 sender 5 项通过；FlaUI Release 编译零警告/零错误，生产 Sender 的隔离 C# 行为合同 6 组通过。两端 TypeScript/i18n 与生产构建通过。
Windows `native-replay-11` 完成原生输入提交、8 步录制、审批、真实 Robot 回放，modelCalls=0，临时窗口正常退出、无剩余 HWND；`browser-live-2` 完成精确 CDP/捕获无业务点击/恢复；`robot-final` 完成七类成功与故障副作用检查；`surfaces-final` 完成生产 Admin/Web 全流程及取消/历史重载。计时只证明这次运行，不构成性能比较。

- 本机为 Windows。macOS/Linux 仅完成负向合同；未声称 AX/AT-SPI/Wayland 物理机验收。macOS 多窗口在无法证明焦点归属时返回 target_lost。FlaUI sender 已编译并做行为合同，完整 FlaUI 捕获面板仍需要实体交互验收。
- Web/Admin 验收使用隔离端口的真实生产构建与 Engine；没有运行会重启主仓服务的常规 `v8os preview --rebuild`，未验证 Shell、Phone、干净安装器或跨机器安装。没有进行可比性能采样，不主张延迟改善。
- 敏感 trace 去除输入字面量；敏感草稿只能保存引用。Robot 的受治理凭据引用执行尚未接通，准备阶段明确拒绝该类变量，不能把引用当成明文参数写入 argv/报告。普通变量仍为公开流程输入。
- unknown 需要核对外部状态后由用户决定后续动作。停止不是回滚已经发生的动作；准备阶段不可停止，执行记录确认后才开放停止。外部 `.robot` 的本体 hash 不能证明它引用的全部第三方资源未变。
- 现有 `file_copy/http_request/subflow/llm_call` 节点仍需按实际资源与权限使用；本轮未把 Robot 解释器改造成 OS 内核隔离，也未声称任意第三方 Robot 库已受沙箱保护。

## 集成与回滚

Engine、UI 与测试保持同一候选版本；不混用旧 native/browser sender 与新增有序 ACK 合同。共享 realtime 包未改，无需重打 tgz。
回滚代码时使用提交级 revert 并停止本任务运行；保存源 trace/草稿/旧配置，不清空用户状态。新增字段为记录元数据；已有未确认回执不能通过回滚代码变成可安全重放。
本地验收产物位于任务隔离目录 `.rpa-acceptance-916`，包含合成报告与截图；不提交状态库、凭据、原始运行日志和构建产物。

收尾时间：2026-09-17（Asia/Shanghai）。Engine 提交 `69004f7a`，随后提交 UI 与本记录。全矩阵后新增输入/预期文本碰撞修复，`test_runtime_recording_truth.py` 18 项通过并由独立代理复核。主仓在期间由其他工作推进至 `f46631d1` 且工作区干净；本任务没有重写、变基或推送主仓，集成者需在当前主线上核对冲突后复验。
