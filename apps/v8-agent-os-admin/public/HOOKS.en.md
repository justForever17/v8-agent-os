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
