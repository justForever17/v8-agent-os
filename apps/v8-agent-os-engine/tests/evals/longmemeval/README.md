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

## V8OS retrieval metrics

Use the retrieval evaluator for deterministic, provider-free measurements of the current `unified_recall` path. It reports candidate and accepted Recall@K, MRR, nDCG, scope precision, encoding coverage, no-answer false positives, channel counts, and p50/p95 retrieval latency. Ground-truth evidence comes from `answer_session_ids`, with turn-level `has_answer` as a compatibility fallback; this is retrieval evidence, not official answer-quality scoring.

Keep the downloaded dataset and result JSON outside the repository:

```powershell
.\apps\v8-agent-os-engine\.venv\Scripts\python.exe apps\v8-agent-os-engine\tests\evals\longmemeval\retrieval_eval.py `
  --input <path-outside-repo>\longmemeval_s_cleaned.json `
  --output <path-outside-repo>\v8os-keyword.json `
  --split longmemeval_s_cleaned --limit 20 --top-k 8 --strategy keyword
```

`oracle` measures encoding and evidence matching without distractor sessions. `longmemeval_s_cleaned` and `longmemeval_m_cleaned` are the meaningful retrieval-noise paths. Use `--granularity session|turn` for an ingestion-granularity ablation; it is an evaluation control, not a production migration. Run the official evaluator separately for answer correctness; do not convert retrieval metrics into a LongMemEval QA score.
