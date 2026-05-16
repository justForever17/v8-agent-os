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
