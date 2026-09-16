# Linux server/TUI acceptance gap audit — 2026-09-16

Source baseline: `4f8ee540554def3bd302fd99e89d76137ca700ae`
(`v8-os-v2026.09.16.1`). This records the current source and focused tests;
it does not declare a released TUI/server distribution.

## Five ordered gaps

1. **P0: CLI run completion truth.** The release's `waitForAssistant` returned
   the first nonempty assistant text before inspecting failure, and timed out
   with exit code 0. `chat_completion.test.mjs` runs the actual Node CLI through
   a local HTTP Engine fixture: text plus failed/cancelled/degraded, intermediate
   delivery while running, stale other-run messages, timeout without cancel or
   duplicate submit, and explicit no-wait. The candidate requires the submitted
   run ID, successful run state and the latest same-run message's completed state.
   Unknown completion returns nonzero with session/run references. A later real
   provider replay found that the first candidate requested `turns?limit=50`,
   while native FastAPI permits at most 10; Engine had completed the task and
   real file proof, but CLI swallowed the repeated HTTP 422 and timed out. The
   candidate now requests 10; nontransient HTTP 4xx fails immediately with an
   unknown outcome, preserving task references without replay. The HTTP fixture
   now enforces the native limit and proves deterministic 422 is read once.
   This is an HTTP/process contract; the corrected candidate still requires
   the parent's full real-provider acceptance.
2. **P1: no server runtime profile or dependency closure.**
   `core/runtime/startup_profile.py` accepts only `minimal`/`desktop`; `server`
   normalizes to `minimal`. Minimal still includes `creative_media`; registry
   migration/defaults/probes can add desktop families. The server design's exact
   base set remains `chat, memory, extensions, automation, network_supervisor,
   engineering, research, plugin_manager`. Creative/media/voice/vector/document
   execution should remain optional; `computer_use/rpa/desktop_live` are excluded
   only from the future server distribution, not deleted from the desktop source.
   `requirements/minimal.txt` still installs Chroma, edge_tts and yt-dlp. Do not
   advertise `ENGINE_INSTALL_PROFILE=server` as an implemented install option.
3. **P1: no independent headless install/upgrade acceptance.**
   Engine now owns identity and `ClientListeners`; CLI defaults to Engine+Web,
   with `--only engine` available. No full-screen TUI command or server product
   exists in `release-manifest.json`. A desktop Linux package/green build does
   not prove a clean server without Next/Electron/GUI dependencies. The existing
   cold-start harness still requests Engine/Admin/Web and cannot establish
   Admin-absent server acceptance.
4. **P1: shared browser remains coupled to desktop implementation.**
   `core/tools/web_fetcher.py` imports
   `runtimes.computer_use.browser_automation` for the governed background browser.
   `ensure_agent_browser_background` does request headless mode even when desktop
   computer-use is disabled, but that is source evidence only. Before pruning,
   validate JS extraction/profile scope/cancel/process cleanup with no DISPLAY,
   Wayland or Xvfb and the exact independent package dependencies.
5. **P1: server Phone/peer execution and recovery matrix remains incomplete.**
   Engine-native identity, Phone gateway, peer routes, server credentials and
   private Unix socket exist. Missing server-level evidence is a clean install
   with real Phone pairing/refresh/revoke, two independent Supervisor instances,
   task proof and duplicate/ack-loss/restart recovery while SSH/TUI exits.
   A route allowlist, open port or socket request is not that proof.

## Focused evidence in this audit

- Windows Node: initial nine completion cases passed; the frozen 9.16.1 chat
  implementation in an isolated copied CLI failed all nine. Eight exercise
  outcomes/exit codes/side effects; one checks explicit no-wait wording.
- Added cases proving that an earlier completed message cannot hide the latest
  same-run streaming message and a completed recovery does not revive a prior
  failed message. That 11-case pass did not detect the native API limit defect;
  it is not live evidence. Enforcing the native limit made the prior candidate
  fail. Corrected candidate: 12 completion cases passed, including prompt
  deterministic-HTTP-error handling; 4 workspace authority cases plus the
  existing redirect test also passed.
- CLI full suite before the tenth case: 110 passed, 1 platform skip, 0 failures.
- After the native API-limit and workspace authority changes: CLI full suite
  117 passed, 1 platform skip, 0 failures. The Engine HTTP daily-entry fixture
  also passed after migration from obsolete Admin endpoints.
- WSL Ubuntu 22.04, Python: actual AES-GCM credential lifecycle/faults and AF_UNIX
  permissions/lifecycle/replacement protection: 13 passed in 3.13 seconds.
- WSL has no Linux Node installed, so Linux CLI process execution was not run.
- No actual provider, real Phone, two-instance delegation, headless Chromium,
  SSH/tmux/IME, clean server package or resource budget is claimed here.

The workspace CLI path is also now Engine-native: `/v1/projects` uses the
existing `engine_client` owner; `client_api` preserves only the response shape
and `requireOk` error hints. The obsolete local JWT issuer/cache/refresh writer
was removed. Dual-server process tests verify an environment origin override
cannot export this instance's proof or cached token, redirects are not followed,
401 writes are not replayed, and a failed trust operation does not switch the
local workspace. Frozen 9.16.1 implementations failed all four new cases.
An actual isolated Engine at port 23930 then passed both CLI `workspace create
--select` and `workspace select`: Engine project readback confirmed the exact
path/trusted state and local config matched its project ID. The prior workspace
was restored in `finally`; the test did not stop Engine or change its bridge.

Reproduction from the repository:

```powershell
node --test apps/v8-agent-os-cli/tests/chat_completion.test.mjs
wsl -d Ubuntu-22.04 -- bash -lc 'cd /mnt/e/Projects/v8chat/.codex-worktrees/9131-integration/apps/v8-agent-os-engine && /home/sunny/.cache/v8os-auth-validation/bin/python -m pytest tests/core/test_linux_server_credentials.py tests/client_surface/test_client_listeners_linux.py -q'
```

The WSL path is this audit machine's environment, not an installation contract.
The project's TUI Skill remains the design source; its 2026-09-14 Admin-owned
auth observations are historical and must not override the 9.16.1 Engine code.
