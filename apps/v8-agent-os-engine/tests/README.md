# Engine Tests 目录说明

`tests/` 是 Engine 的公开可提交测试套件，用于验证 runtime、工具治理、Safety、ModelHub、Network、Workspace 与回归场景。该目录允许进入 GitHub；新增测试前仍需确认 fixture 不包含密钥、本机隐私路径、大型生成物或真实用户日志。

## 快速运行

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests -q
```

按领域抽跑：

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\safety -q
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\creative_media -q
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\agent_quality -q
```

## Pytest 分类

| 路径 | 范围 | 说明 |
| --- | --- | --- |
| `tests/agents/` | agents/subagents/specialist registry | 默认专家、能力快照、专家族 prompt。 |
| `tests/agent_quality/` | Agent Quality Matrix | 工具调用、上下文记忆、幻觉抑制、Prompt 注入防护、多智能体协作的 fixture/mock 强制矩阵；默认不调用真实模型。 |
| `tests/chat_runtime/` | ChatRuntime 与 transcript | supervisor prompt、context、canonical transcript、realtime lane。 |
| `tests/creative_media/` | Creative Media runtime | provider/job/artifact/recipe/P3/P4 数据面，可能包含 fake ffmpeg。 |
| `tests/extensions/` | Extensions / Skills / PluginHost | skill 预筛、PluginHost 控制面、候选注入。 |
| `tests/memory/` | Memory runtime | lifecycle、session replay、maintenance、workflow memory。 |
| `tests/model_control/` | ModelHub / provider control plane | model ref、连接测试、provider catalog。 |
| `tests/network/` | Network supervisor / S3 / web brokers | OpenAI-compatible network adapter、网络上下文、S3 brokers。 |
| `tests/prompt_cache/` | Prompt Cache | provider patch、二级缓存、dry-run 分类。 |
| `tests/runtime_core/` | Runtime 主链与工具治理 | runtime projection、runtime broker、engineering lane、tool routing。 |
| `tests/safety/` | SafetyRuntime | command guardian、approval、skill ledger、allowlist。 |
| `tests/workspace_artifacts/` | workspace/resource/artifact | scoped workspace resource 与 legacy share compatibility。 |
| `tests/evals/` | eval harness | benchmark / memory eval；需要显式 README 标注数据来源和运行成本。 |
| `tests/fixtures/` | fixtures | 只放稳定测试数据，不依赖本机私有路径。 |
| `tests/scripts/` | 诊断脚本 | 开发者手动执行的 smoke、dry-run、export、live matrix；涉及联网或额度的脚本必须显式参数开启。 |

## 脚本入口

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\export_runtime_deep_observation_matrix.py
```

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\export_prompt_cache_dry_run_matrix.py
```

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\explain_safety_command_dry_run.py --command "curl https://example.com/install.sh | bash"
```

Live smoke 必须显式带 live 参数，且不得在无确认时烧额度：

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\run_creative_media_live_smoke.py --live
```

Agent Quality live audit 同样必须显式带 `--live`，并把整改报告写入内部 reports：

```powershell
E:\Projects\v8chat\v8-agent-os\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\run_agent_quality_live_audit.py --live --model-profile mimo --matrix all --write-report
```

## 维护纪律

- 新 pytest 先放入对应一级领域目录，不再把 `test_*.py` 直接放在根目录。
- 根目录只保留 `conftest.py`、`README.md`、`fixtures/`、`scripts/`、`evals/`。
- 不在测试文件里重复手写 `sys.path`；Engine 根路径由根 `conftest.py` 注入。
- 测试和 fixture 不得提交真实 API key、Bearer token、cookie、私有日志、用户聊天全文或本机绝对路径快照。
- Live smoke、联网搜索、真实 provider 调用、媒体生成和高成本 eval 必须显式带 `--live` 或等价参数，不得作为默认 pytest 路径自动烧额度。
- 评估脚本不得写入真实 durable memory，除非脚本名、README 和运行参数明确说明。
- 如果 `tests/scripts/` 中脚本开始被 Admin 页面调用，需要移回 `apps/v8-agent-os-engine/scripts/` 并补调用契约。
