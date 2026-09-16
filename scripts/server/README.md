# Linux Server

V8OS Server runs Engine and the CLI on Linux without Admin, Web, Electron, a desktop session or local audio devices. Supervisor, subagents, research, engineering, text memory, scheduling, plugins, headless Chromium, Phone pairing and trusted Supervisor networking remain available. Admin can be installed separately as an optional configuration client.

The initial package targets glibc Linux x64 with Python 3.11 and Node.js 20 or newer. Ubuntu 22.04 and 24.04 are the validation targets; ARM and musl are not supported by this package. The archive contains source and a hashed production dependency lock. Python packages and the pinned Chromium binary download during installation; this is not an offline bundle. No TUI renderer is included yet.

## Install

Extract the archive into a permanent, version-specific directory owned by a dedicated unprivileged user. Verify its published SHA256 checksum. Install Python 3.11 with venv support, Node.js, Git and Chromium's system libraries. Do not run Engine as root.

```bash
./install.sh
```

The installer checks Python dependencies and the shared Agent browser against a temporary state directory. If Chromium reports missing system libraries, an administrator can run the installed interpreter's `-m playwright install-deps chromium`. After fixing a dependency, download or system-library error, rerun `./install.sh` in the same directory to resume. The installer verifies the archive checksums and its saved progress before continuing. It refuses to adopt a pre-existing unmanaged virtual environment; use a fresh version directory in that case.

Use the existing Engine credential owner to create a key. Keep the key directory private and back up the key separately from the encrypted state. A path is passed to the service; key contents are never passed on the command line.

```bash
export V8_AGENT_OS_HOME="$HOME/.v8-agent-os"
mkdir -m 700 -p "$HOME/.config/v8os-keys"
./v8os config credentials init --key-file "$HOME/.config/v8os-keys/server.key"
```

An administrator must enable lingering for this account (`loginctl enable-linger <account>`), so the systemd user manager survives SSH logout. Then install the service:

```bash
./v8os service install --bundle "$PWD" --key-file "$HOME/.config/v8os-keys/server.key"
./v8os service status --json
./v8os config models list --json
```

Engine listens on loopback. Phone remote access uses the existing Engine client gateway and its configured transport; do not publish the bare Engine control port. Use `./v8os config phone` for owner initialization, pairing tickets, device listing and revocation, and `./v8os config network` for trusted peer configuration. Configuration changes use Engine transactions. See `./v8os --help` for current command arguments.

## Manage and upgrade

```bash
./v8os service stop
./v8os service start
./v8os service restart
./v8os service status --json
```

Extract and install each upgrade in a **new** directory. While the old service is running, invoke `service upgrade --bundle /absolute/path/to/new/version` from either installed CLI. The old package remains available. If readiness fails, the manager restores the prior unit and running state; `service rollback` also recovers an interrupted switch. This rolls back the executable version, not application data migrations. Review version-specific migration requirements before downgrading.

`service uninstall` stops and disables the unit and removes its management receipt. It preserves configuration, encrypted credentials, the key, conversations, artifacts, capability packs and both extracted package directories. Remove those separately only when they are no longer needed. Service logs are available through `journalctl --user -u v8os-server.service`.

One account has one `v8os-server.service` bound to one state root. Multiple Agents and projects run inside that Engine. A service refuses to adopt an unrelated process already listening on the requested port.

## Optional capabilities

The default eight runtime families are chat, memory, extensions, automation, network_supervisor, engineering, research and plugin_manager. Server always excludes desktop computer use, desktop streaming and RPA, even when an older configuration or an installed desktop pack requests them.

```bash
./v8os packs list
./v8os packs install document_ingestion --dry-run
./v8os packs install document_ingestion
./v8os service restart
```

Available optional packs include `document_ingestion`, `vector_memory`, `creative_media` and `cloud_voice`. Installation uses the same transaction, receipt verification and recovery mechanism as Admin. Explicit installation requires a running Engine, downloads dependencies and does not automatically restart a running task. A failed or incompatible receipt is reported as unavailable. After restart, installed capabilities become visible.

Document ingestion adds Office/PDF parsing and keeps text retrieval available without a vector model.

Base memory uses the canonical SQLite/FTS5 store and knowledge graph. Unselected vector support is a normal state. Selecting vector support requires configuring an embedding model and restarting Engine; a reranker is optional. Failures then appear as degraded, while text memory remains available. Startup rebuilds vector projections from current canonical revisions and tombstones. Media processing may additionally require FFmpeg and configured providers; local ASR/OCR and large models are not included.

Headless Chromium uses the governed Agent browser profile and domain permissions. It does not reuse personal browser cookies. Sites requiring interactive challenges may need user assistance. Browser control and web research remain distinct from excluded desktop automation.

Chromium requires a usable OS sandbox. On Ubuntu installations that restrict unprivileged user namespaces, an administrator may need a narrow policy for the installed Chromium executable; see the [Chromium guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md). The package does not change that policy or pass `--no-sandbox`. Browser library installation follows the [pinned Playwright installer](https://playwright.dev/python/docs/browsers).
