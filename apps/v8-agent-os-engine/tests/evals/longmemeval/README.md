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

Local official checkout and data convention:

- Official repo: `E:\Projects\v8chat\_external\LongMemEval`
- Official lite evaluator venv: `E:\Projects\v8chat\_external\LongMemEval\.venv-lite`
- Cleaned data: `E:\Projects\v8chat\data\longmemeval`

Generate a smoke hypothesis file:

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\evals\longmemeval\harness.py --input E:\Projects\v8chat\data\longmemeval\longmemeval_oracle.json --output E:\Projects\v8chat\data\longmemeval\out\smoke_oracle_5.jsonl --split oracle --limit 5
```

Generate a real V8OS model-backed sample after ModelHub connection tests pass:

```powershell
apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\evals\longmemeval\harness.py --input E:\Projects\v8chat\data\longmemeval\longmemeval_oracle.json --output E:\Projects\v8chat\data\longmemeval\out\v8os_oracle_5.jsonl --split oracle --limit 5 --answerer v8os --model-id gpt-5.5
```

Run the official evaluator on a generated JSONL file:

```powershell
E:\Projects\v8chat\_external\LongMemEval\.venv-lite\Scripts\python.exe E:\Projects\v8chat\_external\LongMemEval\src\evaluation\evaluate_qa.py gpt-4o E:\Projects\v8chat\data\longmemeval\out\v8os_oracle_5.jsonl E:\Projects\v8chat\data\longmemeval\longmemeval_oracle.json
```

LongMemEval is an offline benchmark. It does not require waiting three real days or manually running three long workflows; the runner ingests timestamped history and answers each question once.
