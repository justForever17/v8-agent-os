# Engine Test Scripts Map

`tests/scripts/` 存放开发者手动执行的诊断、dry-run、seed、live smoke 和 benchmark wrapper。它不是 Admin 普通页面入口，也不是默认 pytest 回归入口。

运行原则：

- 默认脚本不得烧模型额度、联网、点击真实桌面或写真实 memory。
- 真实模型、联网、媒体生成、Android/桌面设备、真实点击和副作用必须显式带 `--live`、`--allow-side-effects`、`--allow-real-click` 或脚本 README 中声明的等价开关。
- 报告默认写到 `~/.v8-agent-os/reports/...`；不要把私有 live 报告提交进 Git。
- 如果脚本被 Admin、cron、runtime 或部署流程正式调用，应迁移到 `apps/v8-agent-os-engine/scripts/` 并补稳定调用契约。

## Dry-run / Export / 诊断报告

| 脚本 | 用途 | 副作用 |
| --- | --- | --- |
| `export_context_management_assessment.py` | 导出超长上下文管理评估报告。 | 写本地报告。 |
| `export_child_delegation_contract_dry_run.py` | 导出 Subagent → 孙 agent 任务契约与 handoff 回流空运行矩阵，检查孙 agent 拿到的是可执行任务而不是孤立 ID。 | 写本地报告；不调用模型、不写 DB、不改工作区。 |
| `export_memory_capability_assessment.py` | 导出 V8OS memory capability 评估报告。 | 写本地报告。 |
| `export_native_tools_output_dry_run.py` | 导出 native tools dry-run output，检查工具输出脏数据。 | 默认写 `docs/tools/`；不执行真实副作用。 |
| `export_prompt_cache_dry_run_matrix.py` | 导出 Prompt Cache provider patch / segment hash / cache decision 空运行矩阵。 | 写本地报告。 |
| `export_runtime_deep_observation_matrix.py` | 导出 supervisor / subagent / registry / extensions / memory governance 深度观察矩阵。 | 写本地报告。 |
| `export_supervisor_first_contract_dry_run.py` | 导出 Supervisor First / Runtime Grounded 系统提示词和关键工具说明空运行门禁。 | 写本地报告；不调用模型、不写 DB、不改工作区。 |
| `explain_safety_command_dry_run.py` | 解释 SafetyRuntime 对命令的规范化、解码、路径面和 verdict；不执行命令。 | 无真实命令副作用。 |
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

| 脚本 | 用途 | 关键开关 |
| --- | --- | --- |
| `run_agent_quality_live_audit.py` | Agent Quality Matrix live 深度审计。 | `--live --matrix all --write-report` |
| `run_boundary_fast_response_live_audit.py` | 任务边界和 Supervisor 快速首轮响应 live 验收。 | `--live` |
| `run_supervisor_runtime_skill_live_audit.py` | Supervisor / Runtime / Skill 真实断点审计。 | `--live --case ...` |
| `run_engineering_continuation_live_audit.py` | 同 session 工程续接与 debug 路由 live 验收。 | `--live --allow-side-effects` 视 case 而定 |
| `run_huashu_nuwa_skill_live_audit.py` | huashu-nuwa skill 生成、续读、写入和复用 live 验收。 | `--live --allow-side-effects --workspace ...` |
| `run_research_runtime_deep_live_audit.py` | Research Runtime 三层深研 live 审计。 | `--live --write-report` |
| `run_web_source_router_live_audit.py` | Source Router / web read / extract live smoke。 | `--live` |
| `run_tool_surface_live_audit.py` | 工具表面和 detail/ref 输出 live 审计。 | `--live` |

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
| `run_computer_use_github_star_live.py` | Computer Use GitHub star live smoke。 | `--live-github-star --allow-real-click --repo TurixAI/TuriX-CUA` |
| `run_computer_use_real_host_matrix.py` | Computer Use 真机/宿主矩阵。 | `--live` |
| `run_phone_long_task_perf_live_audit.py` | Phone 长任务卡顿 / APK / SSE / projection live audit。 | `--live`；支持 manual phone 观察。 |
| `test_phone_long_task_perf_live_audit.py` | Phone perf audit 脚本 parser/fixture 自测。 | pytest 可跑。 |

Computer Use GitHub star 首次运行通常需要在专用浏览器 profile 中人工登录；未登录应返回 `needs_human_login`，不得假装成功。

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
