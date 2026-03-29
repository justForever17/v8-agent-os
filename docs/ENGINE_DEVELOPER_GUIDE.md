# Engine Developer Guide

This guide is for people who need to work on the Engine repo today.

## Start here

Engine is the execution plane.

If you are unsure where a change belongs, use this rule:

- runtime logic belongs in Engine
- UI belongs in Web or Admin

## The current layout

Use `ENGINE_CORE_DIRECTORY_GUIDE.md` as the canonical directory map.

Treat old flat imports as compatibility shells, not as the preferred place to add new code.

## What to check before changing runtime code

If you touch:

- `erc/*`
- `runtimes/*`
- `graph/*`
- `core/action_executor.py`
- `core/plugin_host/*`

ask these questions first:

1. Which runtime owns this behavior?
2. Does it still go through the unified runtime chain?
3. Does it preserve events, ledgers, snapshots, and approvals?
4. Can it recover after interruption?

## Canonical config truth

Look at:

- `~/.v8chat/config.json`
- `~/.v8chat/V8CHAT.md`
- `~/.v8chat/plugin.json`

If you still see an older home path inside a compatibility shell, treat it as legacy. New setup notes and public docs should use `~/.v8chat/`.

Do not create a second truth source just because a UI page is easier to wire.

## Stable local validation

For long-running or recovery-sensitive work:

- avoid `--reload`
- use the local `.venv`
- prefer prod-like startup

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\runtime\run_engine_prod_like.ps1
```

## Active docs worth reading

- [Repository README](../README.md)
- [Engine API Reference](./ENGINE_API_REFERENCE.md)
- [Engine Core Directory Guide](./ENGINE_CORE_DIRECTORY_GUIDE.md)

## Reader-first rule

When you update docs in this repo:

- write for the next reader
- say what is true now
- avoid long internal history unless it changes a real decision
