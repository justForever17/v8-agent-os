# Hook guide

Hooks let the system run commands, scripts, or automation tasks at key lifecycle points.

## Good fits

- Run checks before work starts
- Clean up records after work ends
- Trigger scripts when fixed events fire
- Attach a repeatable post-process to the runtime

## Common execution modes

### Wait before continuing

The hook runs first, then the main flow continues.

Best for:

- Risk checks
- Formatting or lint checks
- Any gate that must pass before continuing

### Run in the background

The current task keeps going while the hook runs separately.

Best for:

- Post-chat cleanup
- Logging, notifications, summaries
- Work that does not need user wait time

## Trigger points

### Session lifecycle

- `on_supervisor_start`: lead flow begins
- `on_supervisor_thinking_start`: Supervisor starts producing a recognizable reasoning stream
- `on_supervisor_thinking_end`: Supervisor reasoning stream ends for this model run
- `on_supervisor_end`: lead flow ends
- `on_chat_end`: one full chat turn finishes

### Agent lifecycle

- `on_agent_start`: an agent starts working
- `on_agent_end`: an agent finishes
- `on_reviewer_start`: review starts
- `on_reviewer_end`: review ends

### Tool lifecycle

- `on_tool_execute_start`: tool execution begins
- `on_tool_execute_end`: tool execution ends

Use commas to watch multiple events. Use `*` to watch all events.

Note: thinking events only describe the Supervisor model reasoning phase. They do not mean the whole chat turn is finished; use `on_chat_end` for cleanup. In-run Supervisor / tool events carry `parent_session_id` / `parent_run_id` as the source by default instead of taking over the active conversation lane, so synchronous hooks do not wait on the chat turn that is currently waiting on them. Tool events wrap actual tool execution and are best for audit, blocking, or light logging, not long blocking work.

## Breakpoint acceptance matrix

When configuring or debugging hooks, check these breakpoints:

| Breakpoint | Expected result |
| :--- | :--- |
| Event match | Event names are spelled correctly; `*` is used only when all events are intentionally watched |
| Supervisor start/end | `on_supervisor_start` and `on_supervisor_end` are not mistaken for ordinary background chat turns |
| Thinking stream | One model reasoning stream fires one start and one end, not one hook call per chunk |
| Tool call | `on_tool_execute_start/end` receives the tool name; failures or timeouts remain observable |
| Session source | In-run hooks retain `parent_session_id` / `parent_run_id` as source; terminal `on_chat_end` can safely attach to the original session |
| Failure handling | Hook errors are logged or surfaced as runtime events instead of polluting normal chat text |

## Action types

### Command

Use this for commands that already run in a shell.

Example:

```text
python scripts/check.py
```

### Python

Use this for modules that live in the Engine Python environment.

The system calls `run(event_name, **kwargs)` inside the module.

Example:

```text
core.hooks.my_python_hook
```

### Automation task

Use this when the event should hand off to a richer automation flow.

Example:

```text
agents.memory_agent
```

## What to put in target

- Command: a shell command
- Python: a module path
- Automation: an importable automation entrypoint

Avoid absolute local paths unless the command truly requires them.

## Practical tips

- Put long-running work in the background
- Keep risk checks near the start of a flow
- Let each hook do one thing
- Validate in a test environment before long-running use

## If something breaks

- Check the target value
- Check the event name spelling
- Confirm the action type
- Verify the command or script can run by itself
- Check the related logs for errors
