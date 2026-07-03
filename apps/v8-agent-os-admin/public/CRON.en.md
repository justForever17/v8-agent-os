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
agents.runners.memory_maintenance_job
```

### AutomationRuntime task

Hands the job off to AutomationRuntime.

Example:

```text
supervisor
```

## Session attachment

When the Supervisor creates a cron job from an active chat through the Agent tool, the job is bound to that current session. Later scheduled runs should return messages, run state, and recovery markers to the same conversation instead of creating a separate “Cron” conversation in history.

This rule applies to Agent-created jobs only:

- If the user asks in chat for a daily reminder or weekly project digest, later output should stay in that chat.
- Jobs created manually from Admin can still be standalone system automation.
- If a job needs to post to an external channel, configure that channel explicitly in the payload instead of pretending it is a normal chat turn.

## Practical tips

- Lower the frequency first for risky tasks
- Avoid short intervals for long-running jobs
- Use JSON for structured payloads
- Test with Run now before leaving a job enabled

## Breakpoint acceptance matrix

When configuring or debugging cron, check these breakpoints:

| Breakpoint | Expected result |
| :--- | :--- |
| Job creation | Agent-created jobs carry the current `session_id`; Admin-created jobs are not forced into a chat |
| Run now / scheduled run | Execution passes `session_id`, workspace context, and payload into Automation Runtime |
| Conversation history | Attached jobs do not create a separate Cron-group conversation |
| Concurrent trigger | If the same job is still running, the next trigger is skipped or observable instead of writing concurrently into the same session |
| Mid-run failure | The run lands in failed / cancelled / review_required or another diagnosable state and writes audit logs |
| Recovery | Recoverable jobs keep the original run/session binding instead of resuming into a new session |

## If something breaks

- Check the cron expression
- Verify the target can run on its own
- Validate the JSON payload
- Inspect execution logs for timeouts or errors
