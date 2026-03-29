# Cron guide

Cron jobs let V8 Agent OS run background tasks on a fixed schedule.

## Cron basics

A standard cron expression has 5 parts in this order: **`minute hour day month weekday`**.

| Field | Meaning | Values | Example |
| :--- | :--- | :--- | :--- |
| 1 | Minute | `0-59` | `0,30` |
| 2 | Hour | `0-23` | `8-10` |
| 3 | Day | `1-31` | `*/2` |
| 4 | Month | `1-12` | `*` |
| 5 | Weekday | `0-7` (`0` and `7` are Sunday) | `1-5` |

If you do not want to hand-write cron, use the preset schedule controls.

## Action types

### Command

Runs a shell command or executable.

Example:

```text
node dist/index.js
```

### Python

Runs a Python script inside the Engine environment.

Example:

```text
scripts/cron_nightly_memory_batch.py
```

### AutomationRuntime task

Hands the job off to AutomationRuntime.

Example:

```text
supervisor
```

## Practical tips

- Lower the frequency first for risky tasks
- Avoid short intervals for long-running jobs
- Use JSON for structured payloads
- Test with Run now before leaving a job enabled

## If something breaks

- Check the cron expression
- Verify the target can run on its own
- Validate the JSON payload
- Inspect execution logs for timeouts or errors
