# Internal Diagnostic Scripts

这里存放 Engine 内部开发/评测用脚本。它们不是 Admin 普通页面的功能入口，也不应被公开站点直接引用。

## 当前脚本

- `export_context_management_assessment.py`：导出超长上下文管理评估报告。
- `export_memory_capability_assessment.py`：导出记忆能力双轨评分与评测报告。
- `export_runtime_deep_observation_matrix.py`：导出 supervisor / subagent / registry / extensions / memory governance 的当前活体深度观察矩阵。
- `bootstrap_oauth_modelhub_presets.py`：验证 Gemini/Codex OAuth 预置，连接测试通过后才写入 ModelHub 配置。
- `explain_safety_command_dry_run.py`：本地解释 SafetyRuntime 对命令的规范化、解码、路径面和预期 verdict；不执行命令。
- `export_prompt_cache_dry_run_matrix.py`：导出 Prompt Cache provider patch / segment hash / cache decision 空运行矩阵。
- `export_native_tools_output_dry_run.py`：按原生工具导出 dry-run output Markdown，用于检查工具输出里的脏数据；默认写入 `docs/tools/`，不调用 `ask_user`，副作用/外部网络/真实桌面动作只记录 blocked。
- `probe_memory_durable_thresholds.py`：探测 durable memory 阈值与写入行为。
- `replay_memory_session.py`：按 fixture 回放 memory session。
- `run_creative_media_live_smoke.py`：Creative Media P1 live smoke，显式 `--live` 才调用真实 provider。
- `run_creative_media_p3_smoke.py`：Creative Media P3 本地拼接 smoke，默认只使用本地样例资产。
- `run_creative_media_p4_live_smoke.py`：Creative Media P4 live provider smoke，需要显式 live 参数和环境变量凭据。
- `run_creative_media_project_live_smoke.py`：项目级 Creative Media live smoke，验证 workspacePath/projectId 语义。
- `run_computer_use_github_star_live.py`：Computer Use P1-B GitHub star live smoke；只有显式 `--live-github-star --allow-real-click --repo TurixAI/TuriX-CUA` 才会真实点击。浏览器 lane 默认用 `~/.v8-agent-os/browser-profiles/computer_use/<browser>` 专用调试 profile 复用登录态；首次运行需要在弹出的专用 Chrome/Edge 中手动登录 GitHub，未登录返回 `needs_human_login`。
- `run_prompt_cache_streaming_live_matrix.py`：Prompt Cache streaming telemetry live matrix，`--require-all` 会把缺凭据视为失败。
- `seed_phone_file_preview_session.py`：为 phone 文件预览链路准备内部 smoke session。
- `smoke_openai_compat.ps1`：OpenAI compat smoke 诊断脚本。
- `update_model_capability_registry.py`：从 BenchLM `/models`、pricing、leaderboard 与 DataLearner 列表生成集中模型能力表和 unresolved report；会写入 `core/model_catalog/model_capability_registry*.json`。
- `update_media_model_capability_registry.py`：从 Creative Media provider matrix、精确模型能力 overrides 与 `docs/creative-runtime/多媒体.md` 生成集中媒体模型能力表和图标/证据缺口报告；会写入 `core/model_catalog/media_model_capability_registry*.json`。

## 与 `engine/scripts` 的区别

- 如果脚本服务 runtime cron、Admin 按钮、外部兼容命令或部署流程，应放在 `apps/v8-agent-os-engine/scripts/`。
- 如果脚本只用于开发者复验、内部评估、临时诊断或非公开 benchmark，应放在本目录。

移动脚本时要同步更新：

- 脚本里的 `ENGINE_ROOT` 计算。
- `docs/chatruntime/*RUNBOOK*.md` 中的复跑命令。
- 脚本输出报告里的自引用命令。

## OAuth ModelHub 预置

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\bootstrap_oauth_modelhub_presets.py --apply
```

默认是全有或全无：Gemini 与 Codex 都通过连接测试才写入；失败会恢复原配置，不留下半成品 provider/model。开发排查时可加 `--allow-partial` 只固化通过的一侧。

默认探针为 `--probe-mode light`，只发起一次最小 chat 请求，适合初始化时确认 OAuth 与模型名可用；如需复用 ModelHub 的完整能力矩阵，可显式追加 `--probe-mode full`，但这会额外测试 streaming / tools / structured output，更容易触发 Gemini CLI 配额或临时限流。
