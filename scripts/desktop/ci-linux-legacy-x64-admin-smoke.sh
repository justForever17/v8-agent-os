#!/usr/bin/env bash
set -euo pipefail

shell_exe="${1:?installed Electron executable is required}"
resource_root="${2:?installed V8OS resource root is required}"
state_root="${3:?isolated V8 state root is required}"

shell_exe="$(realpath "$shell_exe")"
resource_root="$(realpath "$resource_root")"
state_root="$(realpath -m "$state_root")"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "Legacy x64 Admin smoke must run on an x86_64 host" >&2
  exit 2
fi

for harness_tool in qemu-x86_64-static cpuid curl openssl sha256sum tar; do
  command -v "$harness_tool" >/dev/null 2>&1 || {
    echo "Legacy x64 Admin smoke tool is unavailable: $harness_tool" >&2
    exit 1
  }
done

test -x "$shell_exe"
legacy_cpu_model="phenom,fxsr-opt=off"
node_version="24.18.1"
node_archive_name="node-v${node_version}-linux-x64.tar.xz"
node_archive_sha256="d6c664df3f3f61458e8c277585571328522d705166723a7c7823a9253a4d15a0"
packaged_node_version="$(ELECTRON_RUN_AS_NODE=1 "$shell_exe" -p "process.versions.node")"
if [[ "$packaged_node_version" != "$node_version" ]]; then
  echo "Packaged Electron embeds Node $packaged_node_version, but the legacy CPU harness is pinned to $node_version" >&2
  exit 1
fi
admin_root="$resource_root/apps/v8-agent-os-admin"
engine_python="$resource_root/apps/v8-agent-os-engine/.python/bin/python3"
test -x "$engine_python"
standalone_root="$admin_root/.next/standalone"
server_path=""
for candidate in \
  "$standalone_root/apps/v8-agent-os-admin/server.js" \
  "$standalone_root/server.js"; do
  if [[ -f "$candidate" ]]; then
    server_path="$candidate"
    break
  fi
done
if [[ -z "$server_path" ]]; then
  echo "Packaged Admin standalone server is missing" >&2
  exit 1
fi

cpuid_output="$(qemu-x86_64-static -cpu "$legacy_cpu_model" "$(command -v cpuid)" -1 -r 2>&1)"
basic_ecx="$(awk '$1 == "0x00000001" && $2 == "0x00:" { for (i = 1; i <= NF; i++) if ($i ~ /^ecx=/) { sub(/^ecx=/, "", $i); print $i; exit } }' <<<"$cpuid_output")"
extended_ecx="$(awk '$1 == "0x80000001" && $2 == "0x00:" { for (i = 1; i <= NF; i++) if ($i ~ /^ecx=/) { sub(/^ecx=/, "", $i); print $i; exit } }' <<<"$cpuid_output")"
if [[ ! "$basic_ecx" =~ ^0x[0-9a-fA-F]+$ || ! "$extended_ecx" =~ ^0x[0-9a-fA-F]+$ ]]; then
  echo "Unable to read the emulated Phenom CPUID contract" >&2
  echo "$cpuid_output" >&2
  exit 1
fi
basic_ecx_value=$((16#${basic_ecx#0x}))
extended_ecx_value=$((16#${extended_ecx#0x}))
if (( (basic_ecx_value & (1 << 20)) != 0 )); then
  echo "The emulated legacy CPU unexpectedly exposes SSE4.2" >&2
  exit 1
fi
if (( (extended_ecx_value & (1 << 6)) == 0 )); then
  echo "The emulated legacy CPU must expose SSE4a to guard against confusing it with SSE4.2" >&2
  exit 1
fi

smoke_root="$state_root/legacy-x64-admin"
runtime_home="$smoke_root/home"
log_path="$smoke_root/admin-qemu.log"
tool_root="$(mktemp -d)"
mkdir -p "$runtime_home"
chmod 700 "$smoke_root" "$runtime_home"

node_archive="$tool_root/$node_archive_name"
curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
  -o "$node_archive" "https://nodejs.org/dist/v${node_version}/${node_archive_name}"
echo "$node_archive_sha256  $node_archive" | sha256sum -c -
tar -xJf "$node_archive" -C "$tool_root"
node_binary="$tool_root/node-v${node_version}-linux-x64/bin/node"
test -x "$node_binary"

port="$("$engine_python" - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)"
auth_secret="$(openssl rand -hex 32)"
checkpoint_key="$(openssl rand -hex 32)"
server_dir="$(dirname "$server_path")"
admin_pid=""

cleanup() {
  if [[ -n "$admin_pid" ]] && kill -0 "$admin_pid" >/dev/null 2>&1; then
    kill -TERM "$admin_pid" >/dev/null 2>&1 || true
    stopped=false
    for _ in $(seq 1 20); do
      if ! kill -0 "$admin_pid" >/dev/null 2>&1; then
        wait "$admin_pid" >/dev/null 2>&1 || true
        stopped=true
        break
      fi
      sleep 0.25
    done
    if [[ "$stopped" != true ]]; then
      echo "Legacy x64 Admin did not stop after SIGTERM" >&2
      kill -KILL "$admin_pid" >/dev/null 2>&1 || true
      wait "$admin_pid" >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$tool_root"
}
trap cleanup EXIT

(
  cd "$server_dir"
  env \
    V8_AGENT_OS_HOME="$runtime_home" \
    HOME="$runtime_home" \
    USERPROFILE="$runtime_home" \
    AUTH_SECRET="$auth_secret" \
    NEXTAUTH_SECRET="$auth_secret" \
    AUTH_TRUST_HOST=true \
    AUTH_URL="http://127.0.0.1:$port" \
    NEXTAUTH_URL="http://127.0.0.1:$port" \
    V8_CHECKPOINT_AES_KEY="$checkpoint_key" \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=127.0.0.1 \
    PORT="$port" \
    qemu-x86_64-static -cpu "$legacy_cpu_model" "$node_binary" "$server_path"
) >"$log_path" 2>&1 &
admin_pid=$!

ready=false
for _ in $(seq 1 240); do
  if ! kill -0 "$admin_pid" >/dev/null 2>&1; then
    wait "$admin_pid" || true
    echo "Packaged Admin exited on the legacy x64 CPU before readiness" >&2
    cat "$log_path" >&2
    exit 1
  fi
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/login" || true)" == "200" ]]; then
    ready=true
    break
  fi
  sleep 0.5
done
if [[ "$ready" != true ]]; then
  echo "Packaged Admin did not become ready on the legacy x64 CPU" >&2
  cat "$log_path" >&2
  exit 1
fi

for _ in $(seq 1 3); do
  sleep 5
  if ! kill -0 "$admin_pid" >/dev/null 2>&1; then
    wait "$admin_pid" || true
    echo "Packaged Admin became unstable on the legacy x64 CPU" >&2
    cat "$log_path" >&2
    exit 1
  fi
  test "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/login")" = "200"
done

echo "V8OS_LINUX_LEGACY_X64_ADMIN_SMOKE_OK"
