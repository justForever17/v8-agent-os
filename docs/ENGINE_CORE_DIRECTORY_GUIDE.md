# Engine Core Directory Guide

This guide shows the current canonical structure under `apps/v8-agent-os-engine/core`.

Use it when you need to know where new code belongs today.

## Canonical packages

- `core/config/`
  - config loading, config resolution, canonical config helpers
- `core/context/`
  - scope detection, workspace resolution, delegation context, context policies
- `core/models/`
  - model factory, control plane, provider compatibility, connection testing
- `core/memory/`
  - memory store, memory routing, memory backend health, shared memory helpers
- `core/automation/`
  - cron, hooks, automation-facing execution helpers
- `core/runtime/`
  - runtime health, runtime projection, runtime-specific compatibility shims
- `core/system_tools/`
  - baseline native tools, command presets, computer-use tool surface
- `core/observability/`
  - audit logging, runtime health summaries, debugging helpers
- `core/documents/`
  - document parsing / chunking helpers
- `core/security/`
  - credential sniffing and safety/security utilities
- existing specialized packages kept as-is:
  - `core/plugin_host/`
  - `core/audio/`
  - `core/oauth/`
  - `core/tools/`

## Migration rules

1. Runtime-owned logic should move to the owning runtime package whenever possible.
2. Cross-runtime shared logic should move into a domain package under `core/`.
3. New imports should prefer canonical package paths under the new domains.
4. Old flat files may remain temporarily, but they should stop gaining new responsibility.
5. If a flat module is still the implementation source today, add a wrapper under the new domain first, then migrate call sites incrementally.

## Canonical wrappers already in place

- `core/runtime/agents.py`
- `core/runtime/extensions_runtime.py`
- `core/runtime/supervisor_tool_policy.py`
- `core/runtime/projection.py`
- `core/runtime/health.py`
- `core/system_tools/baseline.py`
- `core/system_tools/computer_use_tool_surface.py`
- `core/system_tools/native.py`
- `core/system_tools/command_presets.py`
- `core/context/delegation.py`
- `core/context/scope.py`
- `core/context/workspace.py`
- `core/models/factory.py`
- `core/models/control_plane.py`
- `core/models/provider_compatibility.py`
- `core/memory/backend_health.py`
- `core/memory/store.py`
- `core/memory/router.py`
- `core/automation/cron.py`
- `core/automation/hooks.py`
- `core/observability/audit.py`
- `core/documents/parser.py`
- `core/security/credentials.py`

## What to expect

- Not every flat module has been physically moved yet.
- Legacy imports may still exist as thin compatibility shims.
- Runtime and API semantics should stay stable while imports are being cleaned up.

## Recommendation for new code

When touching code in active runtime paths, prefer imports like:

- `from core.models.factory import llm_factory`
- `from core.models.control_plane import model_control_plane`
- `from core.models.provider_compatibility import normalize_provider_error`
- `from core.context.workspace import workspace_resolution_service`
- `from core.context.scope import detect_scope`
- `from core.system_tools.native import NATIVE_TOOLS`
- `from core.system_tools.command_presets import read_command_preset`
- `from core.memory.store import memory_store`
- `from core.runtime.projection import build_chat_projection_snapshot`

## Deletion policy

Old flat modules should only be removed after:

1. canonical wrappers exist
2. active imports have been migrated
3. runtime startup and core runtimes have passed regression checks
