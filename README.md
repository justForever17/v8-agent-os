# V8 Agent OS

**V8 Agent OS** is for people who are tired of starting the same project conversation from zero.

The promise is simple: keep the useful context alive, keep the tool surface calm, keep long-running work visible, and let successful screen work harden into something reusable.

## Why it feels different

- **Less re-explaining.** Projects, workspaces, scoped memory, and durable recall mean tomorrow starts with continuity instead of amnesia.
- **Less tool noise.** MCP and skills do not need to flood the model just because they are installed; V8 narrows the surface to what the current job actually needs.
- **More visible work.** Workflow projection, artifacts, approvals, realtime updates, and operations-center views make long tasks easier to inspect and steer.
- **Screen work that grows up.** Computer Use, desktop-live, and the path toward RPA turn “it worked once” into something that can become more repeatable.

## Where OpenClaw fits

OpenClaw deserves real credit for making ecosystem breadth impossible to ignore.

V8 is not trying to win by saying “we also have plugins.” The stronger claim is about experience: **bring the ecosystem in, then keep the project context warmer, the tool surface quieter, and the running work easier to inspect and control.**

## Quick install

The public bootstrap entry is one command per platform. It syncs the official repo, installs dependencies, and starts Admin + Engine. Web still ships separately.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.ps1 | iex"
```

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.sh | bash
```

## Already inside a checkout?

If you are already working inside a local checkout, the same bootstrap scripts still work as a secondary path:

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

```bash
./bootstrap.sh
```

## After install

1. Open **Admin**
2. Finish the core configuration there
3. Configure models, memory, plugin host, automation, and system base in that order
4. Decide separately whether Web should run from source or ship as an app / release

**Important:** V8 Agent OS relies heavily on reranker models. If you do not configure a reranker, memory quality and tool exposure quality will both suffer.

## Default local addresses

| Service | URL |
| --- | --- |
| Web | `http://127.0.0.1:9527` |
| Admin | `http://127.0.0.1:9528` |
| Engine | `http://127.0.0.1:9530` |

## What ships in this repository

| Module | Path | Purpose |
| --- | --- | --- |
| Web | `apps/v8-agent-os-web` | User-facing chat UI and mobile entry surface |
| Admin | `apps/v8-agent-os-admin` | Configuration, control console, and runtime observability |
| Engine | `apps/v8-agent-os-engine` | Execution plane for memory, automation, MCP, skills, safety, recovery, and runtime orchestration |

## Read next

- [Engine API Reference](./docs/ENGINE_API_REFERENCE.md)
- [Engine Core Directory Guide](./docs/ENGINE_CORE_DIRECTORY_GUIDE.md)
- [Engine Developer Guide](./docs/ENGINE_DEVELOPER_GUIDE.md)
- [Engine Developer Guide (Chinese)](./docs/ENGINE_DEVELOPER_GUIDE_ZH.md)
- [Network Supervisor Runtime Plan](./docs/NETWORK_SUPERVISOR_RUNTIME_IMPLEMENTATION_PLAN.md)

## Support V8 Agent OS

If V8 Agent OS helps your team repeat itself less, keep long-running work under control, or treat agent systems more like real systems than demos, you can support continued development here:

[https://afdian.com/a/justForever17](https://afdian.com/a/justforever17)
