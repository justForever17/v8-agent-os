# LongMemEval harness adapter

This development harness generates `question_id / hypothesis` JSONL compatible with [LongMemEval](https://github.com/xiaowu0162/LongMemEval). A generated file is not an official score; use the benchmark's evaluator and record its version, model, and data split separately.

From the repository root, run the offline adapter tests:

```powershell
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe -m pytest apps\v8-agent-os-engine\tests\evals\longmemeval
```

Keep the benchmark checkout, downloaded datasets, and generated results outside this repository. Pass your own input and output paths:

```powershell
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\evals\longmemeval\harness.py --input <dataset.json> --output <smoke.jsonl> --split oracle --limit 5
```

The default `smoke` answerer only checks adapter plumbing. For a real model run, explicitly select `--answerer v8os --model-id <configured-model-id>` after configuring the provider. This calls the provider and may incur cost; it is not part of the offline test suite. The harness uses timestamped benchmark history and does not require waiting several real days.

Run the official evaluator from a separately prepared LongMemEval checkout using that checkout's documented dependencies and command. Keep hypotheses, model responses, evaluation outputs, and credentials out of Git.
