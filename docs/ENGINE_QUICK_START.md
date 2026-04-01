# V8 Agent OS - Quick Start Guide

Welcome to **V8 Agent OS**. If you are tired of repeatedly explaining the context to chat assistants, and fed up with "black box" agents executing blindly without your intervention, you have come to the right place. This is an ecosystem of Runtime machines equipped with global memory, transparent observability, and interruptible human-approval workflows.

This guide will help you (or new members of your team) glance through and locally cold-start this machine in very little time.

## 1. System Roles (Layers You Need to Know)

Once the system boots, different service ports handle different operation facades:
- **Web (Default Port: `9527`)**: The client-side facade presented to the user, including chat history bubbles, Timeline observation graphs, etc.
- **Admin (Default Port: `9528`)**: The control plane and monitoring center. It acts as the dashboard of the machine, used for configuring complex settings (like which MCP skills to install, or which models to swap in).
- **Engine (Default Port: `9530`)**: The lowest and most critical engine backend. Long-context comprehension, plugin invocation triggers, and all graph and workflow transit judgments happen entirely here.

## 2. One-Command Bootstrap

Do not manually run `npm install` for every single package dependency. From the root repository directory, the system provides a foolproof bootstrapping command.

### Windows
Run the following using PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```
*(If you don't have the repository cloned yet, you can use the remote pull command: `powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/justForever17/v8-agent-os/main/bootstrap.ps1 | iex"`)*

### macOS / Linux
Run via Bash terminal:
```bash
./bootstrap.sh
```

*(These commands will automatically handle dependencies and pull up the required background services. The first boot takes a little longer to download dependencies, please be patient.)*

## 3. Initial Configuration tuning (Required Sequence)

After a successful installation and boot, do not immediately open the front-end chat interface to talk.

1. **Step 1, Open the Admin Panel**: Visit `http://127.0.0.1:9528` in your browser.
2. **Step 2, Basic Connectivity (Mandatory)**:
   - Navigate to **Models (Model Configuration)**, and make sure to configure the main reasoning base LLM.
   - **Crucial Warning:** It is highly recommended to properly configure the Reranker option. Without the Reranker system, the accuracy of memory extraction and recall will suffer a cliff-like collapse.
3. **Step 3, Ecosystem Integration**:
   Depending on your workflow needs (e.g., reading local system code or integrating to an external Web Server), load your dependencies via MCP.
4. **Final Step, Open the Web Interface**: Once the above configurations are saved properly, visit `http://127.0.0.1:9527` to truly start enjoying the immersive multi-modal collaboration and development workflow brought by V8 Agent OS.

## 4. "Pitfall Avoidance" Rules for Beginners

- **Single Source of Configuration Truth**: Wherever you need to manually confirm or modify backend configuration parameters, navigate directly to `~/.v8-agent-os/config.json` in your system home directory. All foundational definitions and environment options are consolidated under this main trunk; there is no need to dig for other files.
- **Look for Root Errors**: If the interface responds strangely, do not stare exclusively at the `9527` page's Network tab for API errors. The most comprehensive, detailed, and unadulterated trace errors will typically be printed in the background Terminal running the Engine (`9530` port).
- **Do NOT bypass "Approvals" to save time**: When you notice the system is stalled while requesting to delete critical local directories, this is not a bug. Use the Admin backend or the interactive UI Resume button to grant permission. The approval flow is a core characteristic of long-running tasks.

Next, if you are interested, you can check out the other configuration documentation or look directly at the developer build manual. Enjoy!
