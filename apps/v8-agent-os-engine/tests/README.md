# Engine Tests Map

`apps/v8-agent-os-engine/tests/` 是 Engine 的测试与开发验收地图。可复用的 pytest、合成 fixtures、eval harness 和诊断脚本可以进入 Git；真实 API key、Bearer token、cookie、私有聊天全文、本机隐私路径、内部验收报告、大型生成物与原始日志须保留在公开仓库外。

本 README 是选测试入口的第一站；手动脚本的详细清单见 [tests/scripts/README.md](scripts/README.md)。

## 快速入口

从仓库根目录运行完整 Engine tests：

```powershell
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests -q
```

常用领域抽跑：

```powershell
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\runtime_core -q
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\chat_runtime -q
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\memory -q
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\model_control -q
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\agent_quality -q
```

CI 使用四个互斥且并集完整的文件分片；本地可复现其中一个分片：

```powershell
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\run_pytest_shard.py --shard-index 0 --shard-count 4
```

分片器每次自动发现全部 `tests/**/test_*.py`，按统一换行后的文件大小确定性均衡，Windows 与 Linux 的 Git 换行转换不会改变分片；它不改变普通 pytest 的离线边界，也不替代发布前按需执行的 live/Preview/物理机验收。

涉及真实模型、联网、媒体生成、Android 设备、桌面动作或高成本 benchmark 的脚本必须显式带 `--live` 或等价开关；普通 pytest 默认不得烧额度。

## 测试目录地图

| 路径 | 类型 | 覆盖主线 | 典型验证点 |
| --- | --- | --- | --- |
| `tests/agent_quality/` | mock / fixture matrix | 主链 Agent 质量门禁 | 工具调用正确性、上下文记忆、幻觉抑制、Prompt 注入、多智能体协作。 |
| `tests/agents/` | unit / fixture | Subagent registry | specialist registry、能力快照、专家族 prompt。 |
| `tests/artifacts/` | unit | Artifact surface | artifact 展示和策略边界。 |
| `tests/chat_runtime/` | unit / integration | ChatRuntime / Supervisor | canonical transcript、queue/guidance、Spec mode、runtime episode finalization、message delete、realtime lane。 |
| `tests/contracts/` | contract | Shared realtime projection | session realtime projection matrix。 |
| `tests/core/` | unit / integration | Core services | research broker、tool registry、tool surface、workspace digest、provider catalog。 |
| `tests/creative_media/` | unit / integration | Creative Media Runtime | work order、provider plan、artifact/job/recipe contract。 |
| `tests/erc/` | contract | ERC / reasoning surface | reasoning surface contract。 |
| `tests/evals/` | eval harness | Benchmark / memory eval | memory benchmark matrix、LongMemEval harness、external API memory isolation；详见 [evals/README.md](evals/README.md)。 |
| `tests/extensions/` | unit / integration | Skills / MCP / Plugin Manager | skill loader、dynamic discovery、prefilter、插件特权隔离、artifact validator。 |
| `tests/fixtures/` | data | Stable fixtures | 只放可提交、稳定、无隐私的测试数据。 |
| `tests/memory/` | unit / integration | Memory Runtime | lifecycle、durable policy、visual enrichment、workflow memory、vector sync degraded、reasoning sanitization。 |
| `tests/model_control/` | unit / integration | ModelHub / provider control plane | model ref、connection tester、reasoning payload contract、embedding/rerank limits、media model capability registry。 |
| `tests/network/` | unit / integration | Network Supervisor / brokers | OpenAI-compatible adapter、memory adapter、prompt context、S3/web brokers。 |
| `tests/prompt_cache/` | unit | Prompt Cache | prompt cache gateway。 |
| `tests/rpa/` | unit | RPA Studio | recording capture anchors。 |
| `tests/runtime_core/` | unit / integration | Runtime Fabric / tool governance | runtime episodes、projection、engineering lane、computer use、delegation、tool routing、native tool dry-run。 |
| `tests/safety/` | unit / integration | Safety Runtime | command guardian、approval、active defense、workspace commands、skill review ledger。 |
| `tests/scripts/` | manual harness scripts | Dry-run / live smoke / diagnostics | 开发者手动执行脚本；详见 [tests/scripts/README.md](scripts/README.md)。 |
| `tests/workspace_artifacts/` | unit | Workspace artifacts | scoped workspace resource、share workspace file tool。 |

## 按目标选择测试

| 目标 | 建议入口 |
| --- | --- |
| 改 ChatRuntime / Supervisor / queue / transcript | `tests/chat_runtime/` + 受影响的 `tests/runtime_core/` |
| 改 Runtime Episode / Engineering / Delegation / Computer Use | `tests/runtime_core/` |
| 改工作区拓扑、自动 Git、worktree 或原生工程沙箱 | `tests/runtime_core/test_engineering_sandbox.py` + `test_workspace_capability.py` + `test_delegation_fanout.py` |
| 改 Memory 抽取、注入、scope、视觉增强 | `tests/memory/` + `tests/evals/` 中相关 memory eval |
| 改全局身份、偏好审计或显式知识修订 | `tests/memory/test_memory_runtime_durable_policy.py` + `test_knowledge_lineage_p0.py` + `test_memory_lifecycle_p1.py` + `tests/supervisor/test_supervisor_identity_contract.py` |
| 改 ModelHub、provider、reasoning、embedding/rerank | `tests/model_control/` |
| 改 Agent 指导、提示前缀、关键上下文与缓存用量 | `tests/prompt_cache/`、`tests/model_control/test_model_token_policy.py`，真实 provider 使用显式 live harness |
| 改 Skill / MCP / Plugin Manager | `tests/extensions/` + `tests/plugin_manager/` |
| 改 Research / Web source / evidence pack | `tests/core/test_research_broker.py`、`tests/core/test_research_ledger_experience_pack_lifecycle.py`、`tests/scripts/run_research_runtime_deep_live_audit.py` |
| 改 Research Agent 检索/实读/审核 | `test_research_agent.py`、`test_research_model_call.py`、`test_research_review_retirement.py`；固定证据与实际 reviewer 对照先行，再做真实联网链 |
| 改生成预算/工具参数完整性/连续写入 | `tests/model_control/test_model_token_policy.py`、`test_llm_chat_adapter_streaming.py`、`test_model_failover_service.py`、`tests/runtime_core/test_workspace_capability.py`；真实长写入用 `run_supervisor_runtime_skill_live_audit.py --live --allow-side-effects --case engineering_long_write --web-url <Web>`，只创建临时可信工作区，不把 fixture 长度要求提升为产品门槛 |
| 改 Creative Media | `tests/creative_media/` + `tests/scripts/run_creative_media_*` |
| 改 Safety / permissions / command gate | `tests/safety/` + `tests/runtime_core/test_runtime_tool_access.py` |
| 改受控提权/解锁/OS 凭据 | `tests/safety/test_system_operation*.py`、`test_runtime_resource_boundary.py`，原生 `native/v8-session-unlock` / `native/v8-system-operations` 协议与 CLI 测试；真实认证、锁屏、安装/卸载仅显式 `run_system_operations_windows_live.py --live --allow-side-effects`，不能在普通 pytest 自动运行 |
| 改 Phone/Web realtime 投影 contract | `tests/contracts/` + `tests/chat_runtime/test_session_realtime_runtime_lane_contract.py` |
| 做真实长任务或端到端验收 | 先读 [tests/scripts/README.md](scripts/README.md)，再选择对应 `run_*_live_*` |

## 跨域回归入口

跨域问题可按下表选择已有测试入口：

| 整改面 | 事实源 / 验收入口 |
| --- | --- |
| Run Ledger / Runtime Lifecycle | `tests/runtime_core/test_run_ledger_service.py`、`tests/runtime_core/test_runtime_episode_runner.py` |
| External API Compat | `tests/network/test_network_supervisor_openai_compat.py` |
| Model Role Doctor | `tests/model_control/test_model_ref_control_plane.py`、`tests/model_control/test_model_role_doctor.py` |
| Skill / Workspace Integrity | `tests/extensions/test_skill_loader_readonly_integrity.py`、`tests/extensions/test_skill_loader_dynamic_discovery.py`、`tests/safety/` |
| Projection / Phone-Web | `tests/contracts/`、`tests/chat_runtime/test_session_realtime_runtime_lane_contract.py` |

## 脚本与 pytest 的边界

- `tests/**/*.py` 中以 `test_` 开头的文件是默认 pytest 回归入口，默认应可离线或 mock 化运行。
- `tests/scripts/` 是开发者手动执行入口，包含 dry-run、export、seed、live audit、benchmark wrapper 和本地诊断。
- 如果脚本开始被 Admin 页面、cron、runtime 或部署流程调用，应移动到 `apps/v8-agent-os-engine/scripts/`，并补调用契约。
- Live 脚本必须有显式 `--live`、`--allow-side-effects`、`--allow-real-click` 或同等开关；不能作为默认 pytest 自动消耗额度。

## 新增测试落位规则

- 新增 pytest 优先放入对应一级领域目录，不把 `test_*.py` 直接放在 tests 根目录。
- 根目录只保留 `conftest.py`、`README.md`、`fixtures/`、`scripts/`、`evals/` 和初始化文件。
- 不在测试文件里重复手写 `sys.path`；Engine 根路径由根 `conftest.py` 注入。
- 涉及真实 provider、真实桌面、真实手机、联网搜索和媒体生成的验收，先做 fixture/mock 测试，再补 `tests/scripts/` live harness。
- 评估脚本不得写入真实 durable memory，除非脚本名、README 和运行参数明确说明。

## 报告与输出

- 可提交 fixture / expected output 放在对应测试目录或 `tests/fixtures/`。
- 内部 live 报告默认写入 `~/.v8-agent-os/reports/...`，不要写入仓库。
- 如必须生成临时仓库内报告，需确认 `.gitignore` 覆盖并在最终交付里说明。
# CI 平台边界

Ubuntu 的 portable Engine 分片会输出并跳过需要 Windows UIA／shell、Linux Secret Service、原生 sandbox、已安装插件、外部向量／视觉模型或本机 Preview 服务的合同。这些项目仍会被本机全量 pytest 与对应的 Windows／isolated live gate 执行；CI 的 pending 列表不代表测试通过，也不能替代匹配平台证据。分片脚本不带 `--portable-ci` 时不跳过任何文件。
