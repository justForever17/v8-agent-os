# V8 Agent OS - Engine API Reference

This guide outlines the core communication and invocation interfaces provided by the Engine (default port `9530`) to external layers (Web, Admin, and network plugin tiers). Because the V8 Agent OS architecture must handle long-lifecycle tasks, the underlying foundation relies heavily on event streams, Server-Sent Events (SSE), and Websocket connections.

*(Note: System features iterate rapidly; this document serves as a quick reference and mechanism guide. In case of specific parameter conflicts or path discrepancies, strictly defer to the actual source code implemented within the `apps/v8-agent-os-engine/api/` directory.)*

## 1. Core Communication Protocol Breakdown

### 1.1 HTTP / REST API
Primarily used for configuration pulling/writing, fetching short-lived states, and manipulating runtime entities:
- **`/api/config/*`**: 
  Driven by `config_registry_routes.py`. It controls the entire system's backbone configuration plane, covering top-level domains such as `supervisor`, `mcp`, `models`, etc. **Please completely eliminate the practice of independently modifying scattered JSON files locally**. Instead, use this set of interfaces to initiate Registry requests to the Engine, which will then persist them via `storage.py`.
- **`/api/runtime/*` / `/api/workflow/*`**: 
  Runtime management interfaces, such as listing currently active Graph Nodes, retrieving the Skills Inventory, issuing Retry commands, and halting operations.
- **`/api/governance/approve` (or related recovery state APIs)**: 
  Reactivates (Resume/Retry) and grants permission to tasks that are paused with an `approval_required` status.

### 1.2 SSE (Server-Sent Events)
Used for scenarios that do not require frequent and dense client interactions, but demand low-latency, active push mechanisms:
- **`/api/stream/events`**: 
  Carries the vast majority of streaming text generation pushes from LLMs, as well as fine-grained runtime agent execution progress and run records.

### 1.3 WebSocket
Used for full-duplex real-time control, high-frequency data penetration, and long-lived connections transferring IO from underlying external devices:
- **`/ws/terminal` / `/ws/desktop-live`** (or other related WS channels): 
  Penetrates and transmits backend terminal process IO or system desktop operation streams to the interface and client.
  *【Development Hub Warning】*: When the Web frontend attempts to invoke these persistent pipes, it must adhere to the network hierarchy. Typically, it must first route the connection request through the Admin's Proxy mechanism to the Engine (9530). Do not attempt an out-of-bounds direct connection from the Web tab.

## 2. Key Runtime Mechanisms Analysis

### 2.1 Task Approval & Takeover (Operations & Runtime Governance)
For tasks requiring interception (i.e., destructive system commands, highly sensitive operations, or external requests exceeding sandbox expectations), the Engine will not execute them immediately; it will block.
- The backend suspends the task node (Paused at the graph layer).
- Returns data similar to `status: paused`, `reason: approval_required` via stream events or query interfaces.
- The client receives the instruction and pops up the corresponding ApprovalCard, returning decision-making authority to the human operator.
- Once the human approves, the relevant Resume interface is called to seamlessly continue the backend execution environment. This exemplifies the indispensable **Recoverable Design**.

### 2.2 OpenClaw Ecosystem Mount Layer (Plugin Host / Network Supervisor)
To gracefully accommodate OpenClaw's wide-area network/open protocol plugin clusters and multi-channel synchronization, the Engine assumes secure master-slave isolation and handshakes.
- **Authentication Handshake Interfaces**: Receives external communications from `v8-bridge`, matches tool verification permissions, and validates Gateway/Channel tokens alongside Handoff Tokens.
- **Fail-Closed Principle**: Any out-of-bounds behavior not registered in the internal inventory whitelist or failing validation will be outright rigorously rejected to prevent data contamination.

## 3. Request and Delivery Style Guidelines
When writing request bodies or modifying the API:
- Maintain clear boundaries for events: Do not attempt to bundle all side effects together into a single, coarse-grained, stateless interface submission.
- Alterations to Artifacts and Workflow Ledgers must strictly synchronize with the system's own message events and the Action Executor lifecycle.
