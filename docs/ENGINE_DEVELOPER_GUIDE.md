# V8 Agent OS - Engine Developer Guide

If you are a developer preparing to build new features, fix bugs, or unravel core workflows for V8 Agent OS, this document serves as your **core consensus library** before you begin coding. In our universe, writing code quickly is far less important than writing it "stably" and "in accordance with Runtime intuition."

## 1. Core Development Role and Value Proposition

Your identity here is a **V8 Agent OS Runtime Architect** (not just someone writing a few full-stack CRUD pages).
The ultimate goal of this project is to forge an **OS Runtime Machine that remembers context, filters meaningless noise, is transparently observable, and is strongly fault-tolerant and human-interceptible**.

The hierarchy of value assessment for any Feature PR or Issue fix is:
`Correctness > Recoverability > Observability > Runtime Flow Consistency > Backward Compatibility > Development Speed`.
**It is infinitely better to use less "black magic" than to shatter the recoverability of the system execution chain.**

## 2. Understanding the Three-Repo Collaboration Boundary

When you feel a mechanism needs a tweak somewhere, pause and review your repository context:
- **`v8-agent-os` (Main Product Repo)**
  - Hosts the heartbeat of the entire ecosystem: including handling the foundation `v8-agent-os-engine` (handles memory, invocations, graph states), the logistical data permissions layer `v8-agent-os-admin` (control plane), and the visual layer `v8-agent-os-web`. This is the core scheduling code zone.
- **`v8-agent-os-site` (Static Narrative Repo)**
  - Does NOT handle runtime logic. Its purpose is the system portal packaging, official public documentation exhibition, and establishing initial cognitive understanding for new users.
- **`openclaw-v8-bridge` (OpenClaw Ecosystem Bridge)**
  - V8 system's communication moat facing the OpenClaw plugin ecosystem. Moving one part affects the whole. If you modify tool authorization, Channels management, or the Handoff mechanism, you must ensure it remains Fail-Closed and does not breach defenses.

## 3. The Developer's Mental Model of this Machine

### The Heavyweight Abstractions of the Engine
The most "sacred" mainline logic modules in the system are: `plugin_host`, `network_supervisor`, `desktop-live`, `runtime-governance`, `operations-center`, and the core task pipeline `action_executor.py`.
When touching these logic modules, every modification to the task Run Lifecycle must confront these questions:
- If the current task suffers an unexpected interrupt, can it Resume from its original checkpoint or context?
- Does the invocation logic produce a Side Effect, and if Network Jitter occurs causing a Retry, will it result in a catastrophic duplicate execution?
- Can the Timeline panel still trace my steps? (Did it get recorded into `workflow_ledgers` or `run_records`?)

### Unified Runtime Root Directory and Config Governance
- **Uniqueness of the Source of Truth**: In the current system development, the root directory is strictly consolidated within `~/.v8-agent-os`. All newly written base utility applications and state data must branch out of this path. It is forbidden to sprawl or create private path pools outside of this.
- **Enforcing Configuration Consistency**: All structured configurations exist within the internal Config Registry flowing unidirectionally and handled for disk persistence by `core/storage.py`. If you need to add new environment reading logic or control toggles later, please invoke the formally abstracted interfaces. Eliminate any hardcoded disparate discrete configuration file operations (like attempting to load `xxx_settings.json` directly).

## 4. Recommended Development Flow and Self-Check

The deduced procedure every time you want to build a new feature:
1. **Locate**: Is this the page presentation layer, Admin distribution control layer, Engine execution runtime layer, or the bridge plugin layer?
2. **Find the Logic**: If it is a runtime-related business logic, do not throw the primary execution scheduling flow into Next.js routes or lightweight handler scripts; the mainline MUST be pulled back to the Engine.
3. **Controllable and Verifiable**: Make changes as "small and reversible" as possible. Think ahead: if running a long-cycle job (like scraping 1,000 pages of data unattended), and this feature throws an Exception, how will the system observe the error instead of the process just freezing silently?

## 5. When Must You Stop and Seek Community/Management Discussion?

By default, you should be bold and commit direct developments, but when encountering the following scenarios:
- Planning to alter external APIs and the communication semantics of core states;
- Massive irreversible, non-backward-compatible migrations of field data and configuration structures;
- Designing a completely unplanned new persistence (database structure) model;
- Operations involving cross-repository permission or responsibility transfers as mentioned above.

**Stop typing, compose, and discuss the technical proposal.**

## 6. Shared Contract Layer: `packages/session-realtime`

`packages/session-realtime` is not an incidental helper package. It is the shared contract layer consumed by:

1. `apps/v8-agent-os-admin`
2. `apps/v8-agent-os-web`
3. `apps/v8-agent-os-phone`

It owns the stable definitions for:

1. authoritative snapshot schema
2. runtime event taxonomy / normalization
3. message lifecycle and exact-node patch rules
4. `AdminResourceRef` / `AdminProcessRef`
5. CDC store selectors and derived session state

When you modify this package, do not assume the consumers automatically picked up the change. The default discipline is:

1. build `packages/session-realtime`
2. if consumers rely on a packed tarball, pack it
3. reinstall/update the consumer dependency
4. run at least one `admin/web/phone` build or typecheck pass

## 7. Workspace vs. Channel Delivery Planes

The current resource contract distinguishes three different concerns:

1. main workspace
2. project workspace
3. OpenClaw channel delivery staging

Rules:

1. Project workspaces are first-class runtime surfaces and may live at arbitrary absolute roots.
2. Visible project/main workspace files must flow through the scoped workspace resource resolver rather than raw `/workspace/...` guesses.
3. `plugin_host` inbound downloads belong to the V8 workspace plane.
4. OpenClaw outbound staging / TTS transcoding under `~/.openclaw/media/outbound/...` belongs to `channel_delivery_stage`.
5. `channel_delivery_stage` must remain outside the workspace resolver and surface through artifact-content URLs only.
