# V8 Agent OS - Detailed Configuration Guide

The configuration system of V8 Agent OS might feel slightly different from traditional flat configuration models. Discard the old mindset of hunting for scattered JSON files; here is the current absolute source of truth and specification.

## 1. Single Source of Configuration Truth

Under the V8 Agent OS architecture, to align various fragmented sub-feature options (ranging from specifying large models to defining workflow states), adhere to the following unified data constraint protocol:
- **Core Data and Persistence Directory**: Universally resides in the computing user's `~/.v8-agent-os/` backbone.
- **Aggregate File of Primary Configurations**: All structural core configuration information is ultimately centralized and handed over to the single-entry `~/.v8-agent-os/config.json`, organized by distinct "Config Domains".

All active parameter settings take only the values generated here as authoritative results.

## 2. Core Config Domains

By modifying through the Admin panel or editing `config.json` directly (which dynamically reloads), you will frequently interact with the following domains:

- **`models`**: 
  Defines specific LLMs invoked by all conversational and reasoning layers, including role assignments used by proxies (`models.roles.supervisor`, `models.roles.default`), etc. Never forget to configure your Reranker.
- **`mcp`**:
  Determines which Model Context Protocol (MCP) services to load. Configures environment variables and nodes for various skills or extended services.
- **`workspace`**:
  The current engineering tree, whitelisted permissible edit directories, etc. For safety reasons, do not allow workspace configurations to point directly to the physical system root directory.
- **`supervisor` / `networkSupervisorRuntime`**:
  Contains meta-settings for agents and default prompt strategy pools (typically referencing mapping content from `V8_AGENT_OS.md`).
- **`memory`**:
  Long-term memory configurations, affecting the capabilities and ranges of permanent storage and context retrieval.
- **`music` / `audio`**:
  Maps relevant music and audio parameter settings. Do not revert to modifying or reading former independent files.
- **Other critical domains**: `hooks`, `cron`, `automationRuntime`, `runtimeStability`, `safety`, `projects`, `systemBase`, etc., are all funneled uniformly under this tree.

## 3. Independent & Highly Sensitive Peripheral Config Files

Apart from `config.json`, the following files remain temporarily stored as independent standalone files within the `~/.v8-agent-os/` root directory due to their sensitivity or strong binding to specific machine hardware (Do not modify unless absolutely necessary):

- **`network_supervisor_secrets.json`** / **`network_supervisor_state.json`**:
  Used to store highly sensitive network communication tokens, cross-platform authentication secrets, and Supervisor cross-platform connection states.
- **`computer_use.json`**:
  Records related parameters when Computer Use mode interacts with the desktop.
- **`plugin.json`**:
  A foundation startup manifest logging directly installed local or third-party host plugins.
- **Various DB Storage**:
  `state.db` and `checkpoints.db` save long conversations, graph-based flow trees, and runtime breakpoint data.
- **`V8_AGENT_OS.md`**:
  The default external guidance-level System Prompt / descriptive constraint template source.

## 4. Deprecated or Temporary Cache Content (For Troubleshooting)

As developers and operators, you must accurately differentiate between what represents configuration truth and what is merely a byproduct of temporary files:
- `extensions_runtime_cache.json` and `skills_inventory_cache.json` are generated strictly as **temporary cache files** for computational or synchronization nodes. They face imminent destruction or self-resetting at any moment and strictly cannot be used as a dependency source for foundational system settings.
- The vast majority of low-level parameters have been migrated and consolidated into `config.json` for unified management. We advocate that if your code involves reading settings, ensure you recognize its actual grounded domain structure, avoiding treating external cache as an internal configuration fact.

## 5. Best Practices
**Always prioritize using the Admin console (9528) to mutate configurations.** It natively handles format alignment, stripping legacy fields, and issuing hot-reload signals down to the Engine.
If you must read settings at the code layer, utilize `config_registry_routes.py` or the unified underlying configuration-fetching interfaces. Reject hardcoding file paths or directly invoking `JSON.parse`.
