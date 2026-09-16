#!/usr/bin/env bash
# Release-test session only: keep the real desktop credential backend, without
# depending on a runner's login keyring or putting fixture secrets in its env.
set -euo pipefail
set +x

if [[ "${1:-}" != "--in-session" ]]; then
  exec dbus-run-session -- bash "${BASH_SOURCE[0]}" --in-session "$@"
fi
shift
engine_root="${1:?packaged Engine root required}"
shift
[[ "$#" -gt 0 ]] || { echo 'A smoke command is required' >&2; exit 2; }
[[ "${V8_AGENT_OS_HOME:-}" = /* ]] || { echo 'An absolute isolated V8_AGENT_OS_HOME is required' >&2; exit 2; }
[[ "$engine_root" = /* ]] || { echo 'An absolute packaged Engine root is required' >&2; exit 2; }
engine_python="$engine_root/.python/bin/python3"
test -x "$engine_python"

umask 077
export XDG_DATA_HOME="$V8_AGENT_OS_HOME/xdg-data"
export XDG_CONFIG_HOME="$V8_AGENT_OS_HOME/xdg-config"
export XDG_RUNTIME_DIR="$V8_AGENT_OS_HOME/xdg-runtime"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR"
# Exercise desktop Secret Service, even if the host also has server credentials.
unset V8_AGENT_OS_CREDENTIAL_KEY_FILE CREDENTIALS_DIRECTORY
keyring_env="$V8_AGENT_OS_HOME/keyring.env"
openssl rand -hex 32 | gnome-keyring-daemon --unlock --components=secrets >"$keyring_env"
while IFS= read -r entry; do
  case "$entry" in
    GNOME_KEYRING_CONTROL=*|SSH_AUTH_SOCK=*) export "$entry" ;;
  esac
done <"$keyring_env"

PYTHONPATH="$engine_root" "$engine_python" - <<'PY'
import secrets
from core.security.credentials import CredentialRefStore, LinuxSecretServiceCredentialBackend

store = CredentialRefStore(LinuxSecretServiceCredentialBackend())
reference = f"cred:v8-system:appimage-smoke-{secrets.token_hex(12)}"
value = secrets.token_urlsafe(32)
try:
    assert store.put(value, reference=reference, namespace="system") == reference
    assert store.resolve(reference) == value
    assert store.delete(reference) is True
    assert store.status(reference).configured is False
finally:
    try:
        store.delete(reference)
    except Exception:
        pass
print("V8OS_LINUX_SECRET_SERVICE_OK")
PY

"$@"
