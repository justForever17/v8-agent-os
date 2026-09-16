#!/usr/bin/env bash
set -euo pipefail
umask 077
unset PYTHONPATH PYTHONHOME
bundle_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$bundle_root"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'This package requires glibc Linux x64.' >&2; exit 1; }
getconf GNU_LIBC_VERSION >/dev/null 2>&1 || { echo 'This package requires glibc; musl is not supported.' >&2; exit 1; }
[[ "$(id -u)" != 0 ]] || { echo 'Install and run as a dedicated unprivileged user.' >&2; exit 1; }
node -e 'if (+process.versions.node.split(".")[0] < 20) process.exit(1)' || { echo 'Node.js >=20 is required.' >&2; exit 1; }
python_bin="${V8_SERVER_PYTHON:-python3.11}"
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3,11), "Python 3.11 is required"'
sha256sum --check --quiet SHA256SUMS
engine_root="$bundle_root/apps/v8-agent-os-engine"
# flock releases on cancellation/crash. Only this installer's checksum-bound
# receipt authorizes resuming a venv; a pre-existing unmanaged venv is preserved.
exec 9>"$bundle_root/.server-install.lock"
flock -n 9 || { echo 'Another server installation is running in this directory.' >&2; exit 1; }
receipt="$bundle_root/.server-install-state"
checksum="$(sha256sum SHA256SUMS | cut -d ' ' -f 1)"
phase=dependencies
if [[ -e "$receipt" ]]; then
  [[ -f "$receipt" && ! -L "$receipt" ]] || { echo 'Invalid server installation receipt.' >&2; exit 1; }
  read -r previous_checksum phase < "$receipt"
  [[ "$previous_checksum" == "$checksum" && "$phase" =~ ^(dependencies|browser|verify|complete)$ ]] || { echo 'Installation inputs changed; extract a fresh version directory.' >&2; exit 1; }
elif [[ -e "$engine_root/.venv" || -L "$engine_root/.venv" ]]; then
  echo 'This version directory has an unmanaged venv. Extract a fresh version directory for repair or upgrade.' >&2
  exit 1
fi
[[ ! -L "$engine_root/.venv" ]] || { echo 'Refusing to modify a symlinked venv; extract a fresh version directory.' >&2; exit 1; }
save_phase() {
  phase="$1"
  printf '%s %s\n' "$checksum" "$phase" > "$receipt.tmp"
  mv -f -- "$receipt.tmp" "$receipt"
}
failed_install() {
  status=$?
  if [[ "$status" != 0 ]]; then
    printf 'Server installation failed during %s (exit %s). Fix the reported dependency/network/system-library error, then rerun this install.sh to resume. Existing configuration and credentials were preserved.\n' "$phase" "$status" >&2
  fi
}
trap failed_install EXIT
if [[ "$phase" == dependencies ]]; then
  save_phase dependencies
  "$python_bin" -m venv "$engine_root/.venv"
  "$engine_root/.venv/bin/python3" -m pip install --disable-pip-version-check --require-hashes -r "$engine_root/requirements/server-linux-x64.lock"
  save_phase browser
fi
"$engine_root/.venv/bin/python3" -m pip check
export PLAYWRIGHT_BROWSERS_PATH="$engine_root/.playwright-browsers"
unset DISPLAY WAYLAND_DISPLAY
# Shared Agent browser uses CDP with Chromium new headless, so install the full
# Chromium executable, but neither Firefox/WebKit nor a second headless shell.
if [[ "$phase" == browser ]]; then
  "$engine_root/.venv/bin/python3" -m playwright install --no-shell chromium
  save_phase verify
fi
"$engine_root/.venv/bin/python3" "$bundle_root/scripts/server/verify_server.py" --bundle "$bundle_root" --browser
save_phase complete
echo 'Server package installed. Use ./v8os config credentials and ./v8os service install; see README.md.'
