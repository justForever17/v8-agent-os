#!/usr/bin/env bash
set -euo pipefail
bundle_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$bundle_root"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'This package requires glibc Linux x64.' >&2; exit 1; }
[[ "$(id -u)" != 0 ]] || { echo 'Install and run as a dedicated unprivileged user.' >&2; exit 1; }
node -e 'if (+process.versions.node.split(".")[0] < 20) process.exit(1)' || { echo 'Node.js >=20 is required.' >&2; exit 1; }
python_bin="${V8_SERVER_PYTHON:-python3.11}"
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3,11), "Python 3.11 is required"'
sha256sum --check --quiet SHA256SUMS
engine_root="$bundle_root/apps/v8-agent-os-engine"
[[ ! -e "$engine_root/.venv" ]] || { echo 'This version directory already has a venv. Extract a fresh version directory for repair or upgrade.' >&2; exit 1; }
"$python_bin" -m venv "$engine_root/.venv"
"$engine_root/.venv/bin/python3" -m pip install --require-hashes -r "$engine_root/requirements/server-linux-x64.lock"
"$engine_root/.venv/bin/python3" -m pip check
export PLAYWRIGHT_BROWSERS_PATH="$engine_root/.playwright-browsers"
unset DISPLAY WAYLAND_DISPLAY
# Shared Agent browser uses CDP with Chromium new headless, so install the full
# Chromium executable, but neither Firefox/WebKit nor a second headless shell.
"$engine_root/.venv/bin/python3" -m playwright install --no-shell chromium
"$engine_root/.venv/bin/python3" "$bundle_root/scripts/server/verify_server.py" --bundle "$bundle_root" --browser
echo 'Server package installed. Use ./v8os config credentials and ./v8os service install; see README.md.'
