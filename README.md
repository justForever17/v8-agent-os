<div align="center">
  <img src="./docs/assets/banner.svg" alt="V8 Agent OS Banner" width="800">
</div>

<div align="center">
  <strong>Context Resilience · Tool Pre-filtering · Visually Transparent · Strongly Recoverable</strong>
</div>
<br>

<div align="center">

[![OS](https://img.shields.io/badge/Platform-Win_|_Mac_|_Linux-green.svg?style=for-the-badge&color=050505&labelColor=111111)](#)
[![Node](https://img.shields.io/badge/Runtime-Node.js-orange.svg?style=for-the-badge&color=050505&labelColor=111111)](#)
[![Security](https://img.shields.io/badge/Security-Fail--Closed-red.svg?style=for-the-badge&color=050505&labelColor=111111)](#)

</div>
<br>

> 🌐 [**中文文档请点这里**](./README-ZH.md)

## 🪐 The Epoch-Making Agent Runtime

**V8 Agent OS** is not just another fancy "smart chat wrapper." It is fundamentally engineered as an **Agent Runtime Ecosystem** heavily fortified with security armor and cross-platform asynchronous orchestration capabilities.

If you are tired of "re-explaining your project context to the model," "blind-running tasks crashing midway," or "unknown third-party Skills creating supply-chain blind spots," V8 is built for you. We provide a global memory graph, highly observable task pipelines, and human-in-the-loop manual overrides to ensure long-running task survival.

---

## 🛡️ Core Weapons-Grade Architecture

<table width="100%">
  <tr>
    <td width="50%">
      <h3>1. Fully Decoupled Auto-runtime</h3>
      <p>Structurally isolate the <strong>Control Plane (Admin 9528)</strong> from the <strong>Execution Core (Engine 9530)</strong>. This robust decoupling allows massive background agent tasks to survive browser closes, system suspends, and hold patiently for human-prompt interventions over several days.</p>
    </td>
    <td width="50%">
      <h3>2. Reverse OpenClaw Ecosystem Hijack</h3>
      <p>Through the architectural black magic of <code>v8-bridge</code>, V8 natively hijacks and fully accommodates the massive OpenClaw open-source plugin community, pulling their firepower strictly within our zero-trust secure guardrails.</p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>3. Compute-Aware Model Routing</h3>
      <p>Natively intercept and route trivial classification tasks and long-text data summatives to free, local small models on your hardware. This prevents API bill explosions, reserving your premium frontier AI requests solely for extreme reasoning edges.</p>
    </td>
    <td width="50%">
      <h3>4. Zero-Trust Approval Firewalls</h3>
      <p>Blindly attaching unknown Skills is an open door to hackers. Upon encountering destructive system commands, the backend graph instantly suspends execution, triggering a human-in-the-loop audit on the Control Board. Even a hidden <code>rm -rf</code> cannot escape your gaze.</p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>5. Omniscient MCP & SKILLS Pre-Filtering</h3>
      <p>Load massive integrations without blowing out your token limits. A top-level Reranker dynamically normalizes and pre-filters hundreds of separate MCP modules and native Skills into one context window, exposing only the exact razor-sharp edges the task needs.</p>
    </td>
    <td width="50%">
      <h3>6. Graph-Layered Memory Surgery</h3>
      <p>True memory isn't just dumping a static markdown file. V8 employs a background Memory Agent and projects the data as an interactive UI Node Graph where operators can surgically sculpt, sever, and override individual knowledge clusters.</p>
    </td>
  </tr>
</table>

---

## ⚡ One-Command Bootstrap

Skip the manual dependency hell. Start both the Admin console and the Execution Engine instantly using our cross-platform one-line command (Web UI handled separately).

### Windows (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.ps1 | iex"
```

### macOS / Linux (Bash)
```bash
curl -fsSL https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.sh | bash
```
> *(Note: If you have already cloned the checkout locally, you only need to run `./bootstrap.ps1` or `./bootstrap.sh` inside the directory.)*

---

## ⚙️ Initial Startup Sequence

1. **Crucial First Step**: Access the Admin Control Center: `http://127.0.0.1:9528`
2. **Mandatory Configuration**: Set up your primary main LLM models.
3. **Absolutely Vital**: You MUST configure the Reranker settings. If left unconfigured, the precision of dynamic tool exposure and continuous memory retrieval will suffer a fatal dropdown.
4. Save configurations and proceed to the Web interaction frontend: `http://127.0.0.1:9527` to begin your journey.

---

## 📚 Technical Arsenal (Documentation)

Establish your architectural mental model of the V8 Engine by consulting the guides below:

*   [🚀 Quick Start](./docs/ENGINE_QUICK_START.md)
*   [📖 Developer Guide](./docs/ENGINE_DEVELOPER_GUIDE.md)
*   [🔌 API Reference](./docs/ENGINE_API_REFERENCE.md)
*   [🎛️ Configuration Guide](./docs/ENGINE_CONFIG_GUIDE.md)

---

<div align="center">
  <h3>Support V8 Agent OS Continued Operations</h3>
  <p>If this system helps your team repeat context less, govern background tasks flawlessly, and brings enterprise-scale safety to your automated operations, you can support us here:</p>
  <a href="https://afdian.com/a/justforever17"><strong>https://afdian.com/a/justforever17</strong></a>
</div>
