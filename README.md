<div align="center">
  <img src="./docs/assets/banner.svg" alt="V8 Agent OS" width="860">
</div>

<div align="center">
  <strong>A local-first Agent workspace for long-running tasks, project delivery, mobile collaboration, and creative media work.</strong>
</div>

<br>

<div align="center">

[中文](./README-ZH.md) · [Quick Start](./docs/V8_AGENT_OS_QUICK_START_ZH.md) · [CLI Reference](./docs/V8_AGENT_OS_CLI_REFERENCE_ZH.md) · [Configuration](./docs/V8_AGENT_OS_CONFIG_GUIDE_ZH.md) · [Developer Guide](./docs/V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md) · [Releases](https://github.com/justForever17/v8-agent-os/releases)

</div>

## What Is V8 Agent OS?

V8 Agent OS is a local-first Agent OS for people who want AI to work inside real projects, not just answer one-off prompts. It brings chat, workspaces, model management, long-term memory, task orchestration, mobile access, artifacts, and a desktop companion into one governable product.

At the center is the Supervisor: a user-facing coordinator that can handle a short request in Daily mode, work directly on a longer project in Engineering mode, or delegate a bounded task to a specialist, then check the result before handing it back to you.

## Who It Is For

- Builders who want an AI assistant to stay with a real project over time.
- Small teams that need research, coding, asset generation, documentation, and delivery proof in one place.
- Users who run the main system on a desktop but want a phone to monitor progress, answer questions, and send attachments.
- Power users who care about model routing, memory, tool boundaries, artifacts, and recoverable execution.

## Core Experience

### Desktop App

The desktop app is the main product line. It brings the chat surface, control center, engine, and desktop companion into a local shell so users do not need to remember service ports or keep several terminals open.

### Phone

Phone is the remote interaction surface. It is used to follow active sessions, answer blocking questions, send voice or files, inspect artifacts, and stay connected when you are away from the desktop.

### Supervisor

The Supervisor coordinates the work. Tools are projected from the current role, workspace, task contract, and grants instead of being exposed as one undifferentiated catalog.

### Specialized Modes

- Coding Mode: the Supervisor can work directly on a long-running project, using Engineering episodes or subagents when isolation, parallel work, recovery, or independent proof is useful.
- Research: source-backed search, evidence sorting, and research packs.
- Creative Media: images, video, audio, music, and 3D assets, with a Web Creative Artifact Canvas for arranging workspace media, connecting references, and starting governed edits.
- Memory: preferences, knowledge, and long-running project context.
- Desktop Companion: follows the active session, plays actions and speech, and can send voice or snapshots as attachments.

### Governed Project Execution

The Engineering Kernel provides the bound workspace, operating system, and command environment at the start of a task. Writes use typed task contracts. Low-risk serial changes run directly in the trusted bound workspace under an exact write set; managed worktrees are reserved for parallel isolation, risk containment, or durable recovery. V8OS does not silently initialize Git, move your branch, or commit on your behalf, and isolated candidates still require validation before they reach the original workspace. The current cross-platform sandbox provides partial enforcement, not a kernel filesystem jail or a guaranteed offline network namespace.

User uploads remain session sources. Files written or downloaded by governed Agent, Spec, or Creative Media tools become session artifacts. Existing workspace files do not become artifacts merely because the system discovers them. The Creative Artifact Canvas keeps reusable media at workspace scope and requires explicit adoption by the current session; cross-workspace references are rejected, and internal edit masks are not promoted into the normal asset library.

### Plugin Manager

Plugin Manager installs reviewed CLI, MCP, Skill, and UI components from a signed catalog while keeping credentials behind opaque references. Components still materialize in the existing Skill and MCP stores instead of a private plugin-only store. `@plugin` is a strong user hint, not the only entry: when a task clearly benefits from an installed, configured, healthy plugin, the Supervisor may create the smallest task-scoped grant and project only that package's exact components. A direct subagent may pass an explicitly smaller component subset to one grandchild layer, but no farther. Machine discovery is read-only and does not claim ordinary MCP configuration; the CLI executor appears only when an active grant projects a reviewed command profile. Installation, configuration, secret access, and lasting session grants remain user-controlled. The curated catalog includes GitHub, Figma, AMap, the Volcengine MediaKit CLI, and Cloudflare Wrangler. MediaKit uses full command-schema synchronization, while Wrangler supports a governed browser-login flow for its local profile.

## Quick Start

### Download a Preview

Go to [GitHub Releases](https://github.com/justForever17/v8-agent-os/releases):

- Desktop Preview: Windows x64/ARM64 installers, macOS 12.3+ Intel/Apple Silicon DMGs, and Linux x64/arm64 AppImage or DEB packages. Check each published release for available downloads and checksums.
- Android Phone Preview: APK package.

The desktop build is currently an unsigned preview. After startup, the client checks the unified Preview Release automatically, and the tray also provides a manual check. Downloads and installation always require user confirmation; no update is installed silently. Windows may show a security confirmation and macOS may require an explicit system confirmation before first launch. Code signing and signed automatic installation remain future work. Linux stores secrets through the desktop Secret Service; the DEB declares GNOME Keyring while AppImage users must provide a compatible Secret Service on the host. Linux Wayland input restrictions and macOS accessibility permissions are surfaced explicitly rather than silently bypassed.

### Run From Source

For developers and early testers:

```powershell
.\v8os.cmd preview --rebuild
```

This rebuilds Admin, Web, and the native sandbox helper, stops preview processes owned by the current source tree, and starts Engine, Web, and the desktop Shell. Admin starts when you open the control center. Local chat and the companion connect automatically without a login or pairing step.

### Connect Phone

Generate a pairing code in the desktop control center or with `v8os config phone pair --base-url https://your-gateway.example`, then scan or paste it in Phone. Engine owns pairing and sessions, so the control center can stay closed. Phone keeps a profile for each server and preserves drafts and saved connections during temporary network failures.

Use **Connect Phone** in the control-center top bar. Before pairing, configure an HTTPS Phone gateway reachable from the phone; `127.0.0.1` on the phone refers to the phone itself. The dialog uses the Engine's configured Phone address and links to settings when it is missing. A valid address still needs working network routing and a trusted TLS certificate.

### Linux Server and terminal UI

The unified release includes a Linux x64 Server archive and a public npm Core Base package. The npm route needs Node.js 22+ and automatically installs the matching portable Linux x64 Engine on first use; it does not need system Python or a second CLI installation. The alternative Server archive is manually managed and needs Python 3.11 and Node.js 20+; follow the [Server installation guide](./scripts/server/README.md) for that route.

```sh
npm install -g @v8-agent-os/v8-agent-os
v8os
```

You can also install the matching `V8OS-TUI-<release-version>.tgz` asset with npm. `v8os` is the unified Engine control and terminal entry; `v8os-tui` remains a direct interactive compatibility entry. The portable npm Core Base currently supports Linux x64 only. Windows, macOS, Linux ARM64, and the full desktop flow remain separate release products. Closing TUI leaves Engine, Phone and trusted peer connections running. Server excludes desktop automation and installs media, voice, document and vector capabilities only when requested.

## Current Status

| Product | Status | Notes |
| --- | --- | --- |
| Desktop | Preview | Windows x64/ARM64, macOS Intel/Apple Silicon, and Linux x64/arm64 unsigned preview builds are available. Automatic update detection and a manual tray check are included; signing, automatic installation, and stable releases are still future work. |
| Phone | Preview | Android APK is the required release target. iOS targets 16.4 and later but remains disabled until non-interactive signing is configured. |
| TUI / Server | Preview | Independent Linux x64 Server archive and public npm TUI package; actual availability follows the published release assets. Server ARM/musl are not supported yet. |
| Lightweight remote executor | Experimental | Android Phone includes a locally enabled, app-scoped executor with Stop controls. ESP32-C3 source and build instructions are available; physical hardware acceptance remains pending. See the [executor boundaries](./apps/v8-agent-os-engine/runtimes/network_supervisor/executors/README.md). |
| Cross-device configuration distribution | Preview | Phone prepares, confirms and tracks model policy/role distribution to trusted peers, with per-device results and rollback. Credentials, grants and workspace paths are excluded. |

## Safety and Boundaries

V8OS is local-first by default. Desktop Web, Admin, Shell, and the companion are trusted local clients. Phone is the remote client and uses pairing. Multi-device collaboration, third-party plugin authorization, and network connections are advanced surfaces and are kept separate from ordinary local use.

User-facing surfaces should stay clean: status, results, risks, next steps, and artifacts. Internal scheduling data, raw model responses, audit records, and recovery metadata stay in diagnostics. Provider-native system, tool, and reasoning message contracts are preserved where supported, but provider-hosted tools cannot escape the session's bound tool and grant surface.

## Documentation

- [Quick Start](./docs/V8_AGENT_OS_QUICK_START_ZH.md)
- [Configuration Guide](./docs/V8_AGENT_OS_CONFIG_GUIDE_ZH.md)
- [Developer Guide](./docs/V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md)
- [CLI Reference (Chinese)](./docs/V8_AGENT_OS_CLI_REFERENCE_ZH.md)
- [API Reference](./docs/V8_AGENT_OS_API_REFERENCE_ZH.md)

## Feedback

V8OS is still moving quickly toward a more polished product. If you test the desktop preview or Phone build, please open a GitHub Issue with the version, platform, reproduction path, and screenshots or logs when possible.
