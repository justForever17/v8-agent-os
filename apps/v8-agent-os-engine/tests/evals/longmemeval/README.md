# LongMemEval Official Harness Adapter

This directory contains V8OS internal adapters for the official LongMemEval benchmark.

- Official repository: https://github.com/xiaowu0162/LongMemEval
- This adapter is a development resource only and must not be linked from Admin or public user surfaces.
- The adapter generates official-compatible `question_id / hypothesis` JSONL.
- It does not claim an official score. Official scoring must be performed with LongMemEval's `src/evaluation/evaluate_qa.py`.

Recommended first step:

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\evals\longmemeval
```

Generate a smoke hypothesis file:

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\evals\longmemeval\harness.py --input path\to\longmemeval_oracle.json --output out\hypotheses.jsonl --split oracle --limit 5
```
