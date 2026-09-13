# Engine Test Scripts Map

`tests/scripts/` 存放开发者手动执行的诊断、dry-run、seed、live smoke 和 benchmark wrapper。它不是 Admin 普通页面入口，也不是默认 pytest 回归入口。

运行原则：

- 默认脚本不得烧模型额度、联网、点击真实桌面或写真实 memory。
- 真实模型、联网、媒体生成、Android/桌面设备、真实点击和副作用必须显式带 `--live`、`--allow-side-effects`、`--allow-real-click` 或脚本 README 中声明的等价开关。
- 报告默认写到 `~/.v8-agent-os/reports/...`；不要把私有 live 报告提交进 Git。
- 如果脚本被 Admin、cron、runtime 或部署流程正式调用，应迁移到 `apps/v8-agent-os-engine/scripts/` 并补稳定调用契约。

## 受控系统操作

`run_system_operations_windows_live.py --help` 给出分阶段入口。`status --live` 只显示组件和凭据是否配置；`privilege` / `unlock` 另需 `--allow-side-effects`，使用用户已在 Admin 配置的凭据，只打印去敏结果。`unlock` 会真实锁定当前会话再核验解锁，须提前告知用户；不自动重试认证。`lifecycle` 会请求 Windows UAC，移除并恢复两个自有组件，同时核对系统原有登录 Provider 未变。`--source-unlock-client` 仅标记源码客户端候选验证，不证明默认安装副本。

`system_operations_posix_fixture.py --live --allow-side-effects` 只在明确许可的隔离 Linux/WSL root 环境运行：创建唯一临时测试账户及 sudo 规则，验证认证/空应用 stdin/错误密码/非零退出/超时/长中文/关闭输出后的取消，最后移除自建账户与规则。它不读取真实用户密码，也不证明 macOS 行为。

普通测试不得调用以上副作用入口。批准恢复的真实模型/Web 联测与原生认证层分开记录；具体问题反例还见 `test_chat_transcript_cleanup.py` 中审批暂停与稳定 ToolCall ID 用例。

## Dry-run / Export / 诊断报告

| 脚本 | 用途 | 副作用 |
| --- | --- | --- |
| `export_context_management_assessment.py` | 导出超长上下文管理评估报告。 | 写本地报告。 |
| `export_child_delegation_contract_dry_run.py` | 导出 Subagent → 孙 agent 任务契约与 handoff 回流空运行矩阵，检查孙 agent 拿到的是可执行任务而不是孤立 ID。 | 写本地报告；不调用模型、不写 DB、不改工作区。 |
| `export_memory_capability_assessment.py` | 导出 V8OS memory capability 评估报告。 | 写本地报告。 |
| `export_native_tools_output_dry_run.py` | 导出 native tools dry-run output，检查工具输出脏数据；高风险工具用 `tests/fixtures/tool_output_surface/high_risk_replays.json` 做脱敏回放；可用 `--require-production-client-surface` 强制走 `packages/session-realtime` 的真实客户端 Surface。 | 默认写 `docs/tools/`；不执行真实副作用。 |
| `export_prompt_cache_dry_run_matrix.py` | 导出 Prompt Cache provider patch / segment hash / cache decision 空运行矩阵。 | 写本地报告。 |
| `export_reasoning_effort_request_dry_run.py` | 导出 Supervisor 临时推理强度控制的 provider 请求格式空运行矩阵，覆盖 OpenAI/OpenRouter、Anthropic、Gemini。 | 写本地报告；不调用模型、不联网、不消耗额度。 |
| `export_runtime_deep_observation_matrix.py` | 导出 supervisor / subagent / registry / extensions / memory governance 深度观察矩阵。 | 写本地报告。 |
| `export_skill_tool_output_surface_dry_run.py` | 导出 `fetch_skill_instructions` 原始输出与 agent 可见输出，检查 SKILL.md 完整优先、相对路径续读、入口元数据降噪。 | 写本地报告；不调用模型、不写 DB、不改持久工作区。 |
| `export_spec_runtime_distribution_dry_run.py` | 导出已审批 Spec → runtime/subagent 任务分发空运行矩阵，验证 Kiro-style requirements/design/tasks 可追踪到 agent 可见片段、frameworkDigest、detailRef 和 compact tool surface。 | 可写 `docs/chatruntime/runtime_deep_observation_reports/`；不调用模型、不写 DB、不改持久工作区。 |
| `export_supervisor_first_contract_dry_run.py` | 导出 Supervisor First / Runtime Grounded 系统提示词和关键工具说明空运行门禁。 | 写本地报告；不调用模型、不写 DB、不改工作区。 |
| `explain_safety_command_dry_run.py` | 解释 SafetyRuntime 对命令的规范化、解码、路径面和 verdict；不执行命令。 | 无真实命令副作用。 |
| `run_bocha_provider_live_audit.py --live --config <existing-config.json>` | 只读指定配置的 Bocha 域，以内存凭据向官方 Web Search API 查一个公开技术问题；输出 HTTP 状态、结果数量、耗时和错误类别。 | 显式联网并可能计费；无 `--live` 不读配置、不导入 provider。不会写回配置、复制密钥、输出响应正文或请求头；403 不直接认定为无效 key。 |
| `run_context7_source_live_audit.py --live --isolated-root <new-directory>` | 从既有 `mcp.json` 仅选择 Context7，复用真实 MCP manager 和 Research 文档入口，最多 resolve/query 两次调用；报告正文 SHA-256、官方来源链接、示例相关性与耗时。 | 显式联网，可能消耗 Context7 配额；不调用模型、不启动其他 MCP、不修改源配置。独立临时连接使用现有凭据，仅默认配置访问在内存投影；不保存密钥或原始文档。 |
| `probe_memory_durable_thresholds.py` | 探测 durable memory 阈值与写入判定。 | 仅诊断；确认参数后再运行。 |
| `replay_memory_session.py` | 按 fixture 回放 memory session。 | 依赖 fixture。 |
| `verify_text_reasoning_timeline.py` | 验证文本 reasoning timeline 展示/契约。 | 无真实 provider 必要时才跑。 |

示例：

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\explain_safety_command_dry_run.py --command "curl https://example.com/install.sh | bash"
```

## ModelHub / Provider Registry / OAuth

| 脚本 | 用途 | 注意 |
| --- | --- | --- |
| `bootstrap_oauth_modelhub_presets.py` | 验证 Gemini/Codex OAuth 预置，连接测试通过后写入 ModelHub 配置。 | 默认全有或全无；`--allow-partial` 才固化部分通过项。 |
| `smoke_openai_compat.ps1` | OpenAI compatible endpoint smoke。 | PowerShell 入口。 |
| `update_model_capability_registry.py` | 从模型源生成集中模型能力表和 unresolved report。 | 会写 `core/model_catalog/model_capability_registry*.json`。 |
| `update_media_model_capability_registry.py` | 生成集中媒体模型能力表和证据缺口报告。 | 会写 `core/model_catalog/media_model_capability_registry*.json`。 |

OAuth 预置示例：

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\bootstrap_oauth_modelhub_presets.py --apply
```

## Agent / Runtime / Skill Live Audit

有序多图识别：`run_vision_images_live_audit.py --live --isolated-root <新目录>` 只读当前 ModelHub，
在隔离状态根生成三张测试图片，调用真实 `vision_media_analyzer` 做正向、逆序、重复图对照。
检查同次请求图片数、实际发送 hash、逐图识别与序号；报告保存在该隔离目录。未带 `--live`
时不读配置、不建目录、不调用模型。它证明工具与 provider 的联合识别，不代替 Supervisor
自主选工具、真实桌面事件因果或 Web/Phone UI 验收。

| 脚本 | 用途 | 关键开关 |
| --- | --- | --- |
| `run_agent_quality_live_audit.py` | Agent Quality Matrix live 深度审计。 | `--live --matrix all --write-report` |
| `run_network_compat_live_audit.py` | 真实已配置 Supervisor provider，经独立本机 Engine HTTP 验证 OpenAI/Anthropic compat、外部工具长尾、身份拒绝、ask_user 同 run 恢复。OpenAI 另核 Safety 审批前不外发动作与取消；模型未触发目标分支必须记录缺口。源码配置只读入内存，测试配置改变经 config_broker，不输出密钥。 | `--live --isolated-root <不存在目录> --port <空闲端口> [--protocol anthropic]`；OpenAI 可用 `--approval-only` 验证原生批准到原 run 交付，或 `--tool-only` 用两次模型调用验证坏请求重试、完整结果 receipt 和交付 ID；不替代 Admin relay/UI 或外部设备实测。 |
| `run_network_peer_live_audit.py` | 签名 HTTP 配对 → 真实 Supervisor 读取专用 input.txt → 写 output.txt 并读回 → 验签结果和 ACK → 重复任务不重写。回调对端是协议 fixture，不是第二台物理机或第二个模型。配置经 config_broker。 | `--live --isolated-root <不存在目录> --port <空闲端口>`；真实 provider、临时本机服务和专用文件副作用。 |
| `run_acp_live_audit.py` | 真实 Node CLI ACP stdio → Admin → Supervisor；核对文本、ask_user、取消、审批及 session/load 回放。审批 case 只删除本轮新建的 acceptance.txt，必须同时验审批事件和文件结果；approval-external 验外部临时目录，reject 验拒绝不执行。 | `--live --case prompt|ask|cancel|approval|approval-external [--approval-decision approve|reject]`；需要已运行的本机 Admin，创建专用会话和工作区，不代表 Zed/JetBrains 编辑器实测。 |
| `run_boundary_fast_response_live_audit.py` | 任务边界和 Supervisor 快速首轮响应 live 验收。 | `--live` |
| `run_browser_broker_live_audit.py` | 真实本地托管浏览器：自有表单动作、原 context 登录读取、旧观察/歧义/用户接管反例；`--video` 加多播放器候选及真实红绿蓝帧/时间/字幕校验。隔离 profile/端口/状态，不调用模型，不清理用户浏览器。 | `--live --isolated-root <新目录>`；缺 `--live` 不读配置、不创建状态、不启动浏览器。视频 fixture 需 FFmpeg。 |
| `run_supervisor_capabilities_live.py` | 真实配置 Supervisor 的应用/媒体发现、专用 GUI、浏览器表单、单张生成、指定两子代理、授权视频网站任务；记录实际回执/产物、模型时间及 Web live/reload。工具失败、没有关页、视觉仅被调用均不能算完整成功，内容仍须人工核实。 | `--live --case ... --web-url ... --output-dir <新目录>`；GUI/网页/媒体/视频另须 `--allow-side-effects`，视频须明确授权 URL；desktop 需解锁，只操作自建窗口。`--resume-report` 仅复用本 harness 的 media/video 会话。 |
| `run_supervisor_runtime_skill_live_audit.py` | Supervisor / Runtime / Skill 真实断点审计；`engineering_long_write` 验证临时工作区长原生参数、同 worker 版本续写、浏览器交互与 Web live/reload；`engineering_parent_acceptance_repair` 验证工程完成后父级发现缺口、以精确 handoff 引用派发一次修复并核对最终文件；`research_delegated_verification` 核对来源、原始引用、独立复核和最终呈现。保留失败 run，不以 completed 代替子项通过。 | `--live --case ...`；工程另需 `--allow-side-effects`，浏览器不可用须报告未验证。 |
| `run_engineering_continuation_live_audit.py` | 同 session 工程续接与 debug 路由 live 验收。 | `--live --allow-side-effects` 视 case 而定 |
| `run_engineering_sandbox_live_audit.py` | 真实验证工作区 → Git 基线 → Supervisor/子/孙 Agent 独立 worktree → 沙箱租约 → 验收交付闭环。只接受空白专用工作区。 | `--live --allow-side-effects --workspace ...` |
| `run_huashu_nuwa_skill_live_audit.py` | huashu-nuwa skill 生成、续读、写入和复用 live 验收。 | `--live --allow-side-effects --workspace ...` |
| `run_spec_mode_project_live_audit.py` | Spec Mode 简易真实闭环验收：requirements → design → tasks 审批 → runtime 执行 → index/README 交付；默认先建空 Spec shell 与 clarification evidence，不预写阶段文档；自动回答 Spec 澄清 `ask_user`，并显式报告 workspace binding/trust/side-effect 阻断。 | `--live --workspace ... --write-report`；默认不传 `modelProfile`，使用 Admin 已配置 supervisor 模型；默认 `--safety-approval-mode reduced`；默认等待窗口 480 秒，`--no-bootstrap-spec-shell` 可复现纯 `/spec new` 路径。 |
| `run_research_runtime_deep_live_audit.py` | Research 分层审计；语义/版本/载体/请求归因对照使用合成证据。`--fixed-bundle` 禁止重新获取证据，用保存来源重放；`--review-only` 只重审已有候选稿或已接受正文及读取记录，不调用 writer。`--agent-question` 使用实际获取和配置模型；答案生命周期另核验保存答案及来源的公开恢复、查改复用、归档/恢复/删除，只操作本次样本。各层边界见下文。 | 所有真实模型模式均需 `--live`；报告用 `--write-report`。仅 review-only 可带 `--original-request-file`、`--expect-review-decision`。 |
| `run_research_runtime_fixed_bundle_acceptance.py` | 固定证据的追加式验收记录与连续通过判据；使用配置中的真实 writer/reviewer，不重新抓取来源。 | 必须显式 `--live`；无该开关，在读取证据/配置或获取记录锁之前退出。命令：`python tests/scripts/run_research_runtime_fixed_bundle_acceptance.py --live --bundle <ledger.json> --bundle-id <id> --attempt-log <attempts.jsonl>`。冻结证据和实际报告不要提交仓库。 |
| `run_web_source_router_live_audit.py` | Source Router / web read / extract live smoke。 | `--live` |
| `run_tool_surface_live_audit.py` | 工具表面和 detail/ref 输出 live 审计。 | `--live` |

调研验收按证据层级分别记录，不能互相替代：

- **固定证据真实 reviewer**：`--live --fixed-bundle <ledger.json> --bundle-id <id> --review-only` 优先重放保存的 `candidateDraft`；没有候选稿时使用已接受答案的完整正文、coverage 和 limitations，不把旧 accept 当新审阅结果。来源与候选内容冻结，禁止补搜和重写。仅协议成功说明得到有效审阅结论，不自动证明语义正确。
- **已知正反例判别**：在上述命令追加 `--expect-review-decision revise` 或 `accept`；预期必须来自事先核实的该样本判据，不能事后改成模型实际返回值。结论不符则报告失败；固定证据与合成证据对照都不能证明当前网络可用。
- **原始请求归因**：仅在 review-only 中用 `--original-request-file <original.txt>` 提供实际原始用户消息（UTF-8，可有 BOM）。它与 bundle 的派生研究 question 分开传入；未提供时保持来源未知，不拿派生问题补作用户原话。报告记录原请求 hash；原文文件和冻结私有证据不得提交仓库。缺少 `--live` 会在读取输入文件或调用模型前退出。
- **新鲜网络与完整产品链**：`--live --agent-question <问题> --seed-url <已知正文URL>` 验证 Research 获取、实际模型审阅与保存；需要强制刷新既有题目时使用明确带 `force_refresh` 的 case 并核对获取 receipts。还须以 Supervisor 的 `research_delegated_verification` 联测验证委派、最终交付和 Web live/reload 一致性，不能用固定 reviewer 回放或单个 Research 调用代替这条端到端链。

`engineering_parent_acceptance_repair` 是小文件恢复验收，需 `--live --allow-side-effects --case engineering_parent_acceptance_repair`，使用新建专用会话和临时工作区：先让工程 worker 写入 `acceptance-note.txt=draft` 并结束，再由 Supervisor 按最终 `approved` 要求，以当前 producer episode 和 handoff 的精确引用提交 `parentAcceptance`。验收核对原 episode 完成、唯一修复 lineage、原写集、修复完成、实际文件内容（当前允许首尾空白）和 Web live/reload 一致性；复用旧 completed、仅说“已发起等待”、错误引用或文件仍为 draft 都不能通过。该小样本不替代长参数/连续写入/浏览器交互验收。

## Creative Media Live / Smoke

| 脚本 | 用途 | 关键开关 |
| --- | --- | --- |
| `run_creative_media_live_smoke.py` | Creative Media P1 provider live smoke。 | `--live` |
| `run_creative_media_p3_smoke.py` | Creative Media P3 本地拼接 smoke。 | 默认本地样例资产。 |
| `run_creative_media_p4_live_smoke.py` | Creative Media P4 live provider smoke。 | 需要 live 参数和 provider 凭据。 |
| `run_creative_media_project_live_smoke.py` | 项目级 Creative Media live smoke，验证 workspacePath / projectId。 | `--live` |

## Computer Use / Phone / Device Live

| 脚本 | 用途 | 关键开关 |
| --- | --- | --- |
| `run_computer_use_real_host_matrix.py` | Computer Use 真机/宿主矩阵。 | `--real-host`；需要真实输入探针时显式增加 `--allow-input`。 |
| `run_computer_use_joint_live_audit.py` | Computer Use 联合验收：Agent 浏览器秘塔图片下载、QQ音乐自绘界面动作，以及 Supervisor → Computer Use episode/handoff 闭环。 | `--live --phase direct|supervisor|all --case metaso|qqmusic|all`；真实测试环境可显式加 `--cleanup-test-processes`。自有专用窗口可用 `--live --phase direct --case custom --task-brief-file <JSON> --workspace <临时目录> --max-rounds 8`，禁止全局清理。 |
| `run_phone_long_task_perf_live_audit.py` | Phone 长任务卡顿 / APK / SSE / projection live audit。 | `--live`；支持 manual phone 观察。 |
| `test_phone_long_task_perf_live_audit.py` | Phone perf audit 脚本 parser/fixture 自测。 | pytest 可跑。 |

Computer Use 联合验收使用 V8OS 专用 Agent 浏览器 profile；未登录或遇到 CAPTCHA 时必须报告 blocked，不得绕过或假装成功。

自定义 direct case 的 JSON 必须含 `taskBriefId`、`goal`、显式 `writeSet`（可为空）和非空 `acceptanceContract` 字符串数组；只在 `--live` 后读取。该入口为截图产物建立专属 session/run，不调用秘塔、QQMusic 或任何浏览器/进程全局清理。运行前自行创建并确认测试窗口归属，结束后只清理自己创建的进程。报告通过仅表示 runtime 完成协议通过，仍须对照窗口实际状态与截图产物读取验收；Windows 实测不能代替 Ubuntu 物理验收。

## Benchmark / Eval

| 脚本 | 用途 | 注意 |
| --- | --- | --- |
| `run_longmemeval_official_live_benchmark.py` | LongMemEval official live wrapper。 | 高成本，必须显式 live。 |
| `run_longmemeval_v2_official_live_benchmark.py` | LongMemEval-V2 official live wrapper。 | 高成本，报告需区分 script / reader / evaluator error。 |
| `summarize_longmemeval_v2_scores.py` | 汇总 LongMemEval-V2 分数。 | 不调用模型。 |
| `run_prompt_cache_streaming_live_matrix.py` | Prompt Cache streaming telemetry live matrix。 | `--require-all` 会把缺凭据视为失败。 |

## Seed / Demo Session

| 脚本 | 用途 | 注意 |
| --- | --- | --- |
| `seed_execution_map_demo_session.py` | 生成执行地图 / runtime / subagent 演示 session。 | 不应污染真实用户历史。 |
| `seed_phone_file_preview_session.py` | 生成 Phone 文件预览链路演示 session。 | 内部 smoke。 |

## Forensics / Migration / Incident

| 脚本 | 用途 | 注意 |
| --- | --- | --- |
| `windows_profile_incident_forensics.py` | Windows profile incident 取证。 | 读取本机状态，注意隐私输出。 |
| `test_windows_profile_incident_forensics.py` | 取证脚本 fixture 测试。 | pytest 可跑。 |
| `migrate_misplaced_ai_werewolf_game.py` | 迁移历史误放置 artifact。 | 运行前确认路径和目标。 |

## 维护清单

- 新增脚本后必须更新本 README 的分类表。
- 脚本输出路径默认使用 `~/.v8-agent-os/reports/...` 或临时目录。
- 任何会真实写文件、安装依赖、启动进程、点击桌面、调用 provider、联网调研的脚本，都必须在参数名和帮助文本中显式暴露风险。
- 如果脚本中出现真实账号、token、cookie、私有日志、完整用户聊天内容，应改成 redacted evidence 或 fixture。
## 已保存 Research 答案的跨会话核验

`run_supervisor_runtime_skill_live_audit.py --case saved_research_verification --live --saved-experience-id <active-pack-id> --saved-evidence-id <current-bundle-id> --web-url <running-Web-url> --write-report --output-dir <audit-directory>`

此入口只使用指定的已接受答案，以新会话验证读取、独立子任务、原始 claimId/引用键/URL 与父级接受、Web live/reload 一致性，不重复触发 Research 获取或工程写入。缺 ID、包已归档、版本引用不符或未启用 `--live` 均在提交前拒绝。报告中的 `savedResearchVerificationAudit` 不能用 `ready`、自报 ACCEPT 或父级自己写的核验表替代子任务原始证明；语义质量仍须逐条对照来源人工核查。

`workerReadEvidence` 只承认当前子任务成功返回的来源正文快照，并核对保存的内容哈希；读过答案正文、列出 URL、发起但失败的读取均不算独立来源核验。答案追加的原始读取索引只用于定位，`read_` 前缀及原始 URL 不得由验收器自动补全或改写。UI 工具预览的分页范围可能小于实际模型输入，截断定位需对照模型调用的消息哈希，不能只看卡片预览推断模型漏读。
