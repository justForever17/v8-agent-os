# V8 Agent OS

**V8 Agent OS** is a complete agent system built with token efficiency as a core goal.

What it is really trying to solve are harder, more practical problems:

- a truly powerful memory system should not rely on plain Markdown, and it should not let the agent write whatever it wants
- MCP and skills should be exposed precisely for the current task, instead of becoming more dangerous as more of them get installed
- desktop applications with APIs should be automated properly, and applications without APIs should still be handled efficiently and elegantly
- OpenClaw is strong because of its ecosystem, but the real question is how to bring that ecosystem in while starting from a much stronger system foundation

If you want an agent system with stronger core capabilities, long-running stability, automatic progress, and full access to the OpenClaw ecosystem, this is the one official entry point.

## What is in this repository

| Module | Path | Purpose |
| --- | --- | --- |
| Web | `apps/v8-agent-os-web` | User-facing chat UI and remote mobile app surface |
| Admin | `apps/v8-agent-os-admin` | Configuration, control console, and runtime observability |
| Engine | `apps/v8-agent-os-engine` | The real execution plane: runtimes, memory, automation, MCP, and skills |

## Why it is worth using seriously

- **Long-term memory is not decoration.** A true hybrid memory + RAG architecture that outclasses most alternatives on the market. It is easier to manage, and without deleting the database your agent can remember you for life.
- **The built-in capability set is strong and complete.** It comes with many useful native tools, so you can leave behind the era where everything depends on burning tokens through `SKILL.md`.
- **Skills and MCP are not handled by brute force.** No matter how large the catalogue gets, only the small slice that the current task actually needs is exposed.
- **The OpenClaw ecosystem can be absorbed seamlessly.** Plugins, channels, and bridge capabilities remain available, and this system can still comfortably handle more agent skills and more tools.
- **Runtime boundaries are real.** Chat, Memory, Extensions, Automation, Safety, Computer Use, Plugin Host, and RPA each do the kind of work they are best at.

That is the biggest difference between this system and a typical agent application.

## Default local addresses

| Service | URL |
| --- | --- |
| Web | `http://127.0.0.1:9527` |
| Admin | `http://127.0.0.1:9528` |
| Engine | `http://127.0.0.1:9530` |

## Quick install

### Windows

```powershell
git clone https://github.com/justForever17/v8-agent-os.git
cd v8-agent-os
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

### Linux / macOS

```bash
git clone https://github.com/justForever17/v8-agent-os.git
cd v8-agent-os
./bootstrap.sh
```

## Recommended configuration order

**Important:** V8 Agent OS makes heavy use of reranker models. If you do not configure a reranker, memory accuracy and tool exposure quality may both suffer.

1. Start **Engine**
2. Start **Admin**
3. Finish the core configuration through Admin
4. Configure models, memory, plugin host, automation, and system base in that order
5. Only then decide whether Web should run from source or ship as an app / release

## Very practical deployment advice

- It is recommended to use a smaller model for memory-side work. That keeps cost much more stable.
- It is recommended to build a full infrastructure stack if you can. If your hardware is limited, you can deploy rerankers, lightweight multimodal models, or helper models on the free servers provided by Hugging Face Spaces or ModelScope, then serve them through vLLM.
- OpenClaw integration is optional. The core capabilities of V8 Agent OS are already strong enough on their own, but you can still bring in community tools if that matches your workflow.

## Support V8 Agent OS

> If V8 Agent OS truly saves you time, helps you survive complex work, or finally makes your agent system feel like a real system instead of a demo, you can support its continued growth here: [https://afdian.com/a/justforever17](https://afdian.com/a/justforever17)

> “We become what we behold. We shape our tools, and thereafter our tools shape us.”  
> “我们眼之所见重塑了我们；我们塑造了工具，此后工具塑造了我们。”  
> — Marshall McLuhan
