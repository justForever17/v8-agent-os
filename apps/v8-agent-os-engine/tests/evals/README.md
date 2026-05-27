# Memory Runtime Internal Eval Suite

This directory contains internal, reusable pytest evaluations for V8 Agent OS memory governance.

These tests are development resources only:

- They must not be linked from the public site or normal Admin user surfaces.
- They must not call external models or execute real tools.
- They should use isolated temp storage or clean up runtime DB records they create.
- They focus on cross-link memory behavior: graph summary injection, canonicalization, summary contamination, external API isolation, and workflow learning eligibility.
- `longmemeval/` contains the internal adapter for the official LongMemEval harness. It only generates official-compatible JSONL and must not be described as an official score until LongMemEval's own `evaluate_qa.py` has been run.

Run from the repository root:

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\evals
```

## Agent Quality Live Audit Boundary

`tests/agent_quality/` is the default fixture/mock matrix for tool-call validation,
context memory, hallucination mitigation, prompt-injection protection, and
multi-agent collaboration. It is safe for normal pytest and must not call live
models or providers.

Live毒点审计只通过脚本显式触发：

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\scripts\run_agent_quality_live_audit.py --live --model-profile mimo --matrix all --write-report
```

该脚本会先运行默认矩阵，再向本地 Engine 发起小批量 live case，并将内部整改报告写入
`apps/v8-agent-os-engine/reports/agent_quality/<timestamp>/AGENT_QUALITY_REMEDIATION_ZH.md`。
没有 `--live` 时脚本必须拒绝执行，以避免普通回归误烧额度。
