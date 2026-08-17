#!/usr/bin/env bash
set -euo pipefail

artifact_root="${1:?artifact directory is required}"
expected_arch="${2:?x64 or arm64 is required}"
state_root="${3:?V8 state root is required}"
repo_root="${4:-$(pwd)}"
expected_distro="${5:?ubuntu-22.04 or ubuntu-24.04 is required}"

artifact_root="$(realpath "$artifact_root")"
state_root="$(realpath -m "$state_root")"
repo_root="$(realpath "$repo_root")"
cd "$repo_root"
export DEBIAN_FRONTEND=noninteractive

case "$expected_arch" in
  x64)
    expected_deb_arch="amd64"
    expected_machine="x86_64"
    ;;
  arm64)
    expected_deb_arch="arm64"
    expected_machine="aarch64"
    ;;
  *)
    echo "Unsupported Linux architecture: $expected_arch" >&2
    exit 2
    ;;
esac

case "$expected_distro" in
  ubuntu-22.04|ubuntu-24.04) ;;
  *)
    echo "Unsupported Linux clean-smoke distro contract: $expected_distro" >&2
    exit 2
    ;;
esac

host_machine="$(uname -m)"
if [[ "$host_machine" != "$expected_machine" && ! ( "$expected_arch" == "arm64" && "$host_machine" == "arm64" ) ]]; then
  echo "Linux clean-smoke host mismatch: expected $expected_machine, got $host_machine" >&2
  exit 1
fi

mapfile -t deb_paths < <(find "$artifact_root" -type f -name '*.deb' -print)
mapfile -t checksum_paths < <(find "$artifact_root" -type f -name 'SHA256SUMS-*.txt' -print)
test "${#deb_paths[@]}" -eq 1
test "${#checksum_paths[@]}" -eq 1
deb_path="${deb_paths[0]}"
checksum_path="${checksum_paths[0]}"

(cd "$artifact_root" && sha256sum -c "$(realpath --relative-to="$artifact_root" "$checksum_path")")
test "$(dpkg-deb -f "$deb_path" Architecture)" = "$expected_deb_arch"
package_name="$(dpkg-deb -f "$deb_path" Package)"
test -n "$package_name"

depends="$(dpkg-deb -f "$deb_path" Depends)"
for dependency in libgtk-3-0 libnotify4 libnss3 libxss1 libxtst6 xdg-utils \
  libatspi2.0-0 libuuid1 libsecret-1-0 at-spi2-core gir1.2-atspi-2.0 \
  libgirepository-1.0-1 libcairo2 xdotool wmctrl xclip xsel gnome-keyring \
  libpam-gnome-keyring; do
  if ! tr ',' '\n' <<<"$depends" \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]*[(].+$//; s/[[:space:]]*[|].*$//' \
    | grep -Fqx "$dependency"; then
    echo "DEB runtime dependency is missing: $dependency" >&2
    exit 1
  fi
done
package_contents="$(dpkg-deb -c "$deb_path")"
grep -Fq '/resources/apparmor-profile' <<<"$package_contents"

# These packages are harness tools only. GI, AT-SPI, GTK, and keyring runtime
# dependencies must be pulled by the DEB itself in the next apt transaction.
sudo apt-get update
sudo apt-get install -y \
  binutils file desktop-file-utils dbus-x11 xvfb xauth openssl util-linux \
  apparmor-utils
for harness_tool in openssl unshare apparmor_status apparmor_parser; do
  command -v "$harness_tool" >/dev/null 2>&1 || {
    echo "Linux clean DEB harness tool is unavailable: $harness_tool" >&2
    exit 1
  }
done

audit_root="$(mktemp -d)"
trap 'rm -rf "$audit_root"' EXIT
dpkg-deb -x "$deb_path" "$audit_root/package"
elf_count=0
while IFS= read -r -d '' candidate; do
  file_description="$(file -b "$candidate")"
  [[ "$file_description" == *ELF* ]] || continue
  elf_count=$((elf_count + 1))
  case "$expected_arch" in
    x64) expected_elf_marker='x86-64' ;;
    arm64) expected_elf_marker='ARM aarch64' ;;
  esac
  if [[ "$file_description" != *"$expected_elf_marker"* ]]; then
    echo "ELF architecture mismatch (expected $expected_elf_marker): $candidate: $file_description" >&2
    exit 1
  fi
  while IFS= read -r symbol; do
    [[ -n "$symbol" ]] || continue
    symbol_name="${symbol%%_*}"
    symbol_version="${symbol#*_}"
    case "$symbol_name" in
      GLIBC) ceiling='2.35' ;;
      GLIBCXX) ceiling='3.4.30' ;;
      CXXABI) ceiling='1.3.13' ;;
      *) continue ;;
    esac
    if dpkg --compare-versions "$symbol_version" gt "$ceiling"; then
      echo "ELF requires ${symbol_name}_$symbol_version above the Ubuntu 22.04 baseline (${symbol_name}_$ceiling): $candidate" >&2
      exit 1
    fi
  done < <(
    readelf --version-info "$candidate" 2>/dev/null \
      | grep -oE '(GLIBC|GLIBCXX|CXXABI)_[0-9]+(\.[0-9]+)+' \
      | sort -u
  )
done < <(find "$audit_root/package" -type f -print0)
test "$elf_count" -gt 0

sudo apt-get install -y "$deb_path"
install_root='/opt/V8 Agent OS'
shell_exe="$install_root/v8-agent-os-shell"
resource_root="$install_root/resources/v8os"
engine_root="$resource_root/apps/v8-agent-os-engine"
engine_python="$engine_root/.python/bin/python3"
test -x "$shell_exe"
test -x "$engine_python"
bundled_apparmor_profile="$resource_root/../apparmor-profile"
apparmor_enabled=false
apparmor_profile_supported=false
verify_apparmor_profile_loaded() {
  if [[ ! -f /etc/apparmor.d/v8-agent-os-shell ]]; then
    echo "Installed DEB did not register the V8 Agent OS AppArmor profile" >&2
    exit 1
  fi

  local apparmor_profiles
  if ! apparmor_profiles="$(sudo apparmor_status 2>&1)"; then
    echo "Unable to inspect the loaded AppArmor profile set as root" >&2
    echo "$apparmor_profiles" >&2
    exit 1
  fi
  if ! grep -Fq 'v8-agent-os-shell' <<<"$apparmor_profiles"; then
    echo "Installed V8 Agent OS AppArmor profile is not loaded" >&2
    exit 1
  fi
}

if apparmor_status --enabled >/dev/null 2>&1; then
  apparmor_enabled=true
  if apparmor_parser --skip-kernel-load --debug "$bundled_apparmor_profile" >/dev/null 2>&1; then
    apparmor_profile_supported=true
  fi
fi

if [[ "$expected_distro" == "ubuntu-24.04" ]]; then
  if [[ "$apparmor_enabled" != true ]]; then
    echo "Ubuntu 24.04 clean smoke requires enabled AppArmor to verify the Electron userns compatibility profile" >&2
    exit 1
  fi
  if [[ "$apparmor_profile_supported" != true ]]; then
    echo "Ubuntu 24.04 AppArmor parser rejected the bundled Electron userns compatibility profile" >&2
    exit 1
  fi
  verify_apparmor_profile_loaded
elif [[ "$apparmor_profile_supported" == true ]]; then
  verify_apparmor_profile_loaded
else
  test ! -e /etc/apparmor.d/v8-agent-os-shell
fi
chrome_sandbox="$install_root/chrome-sandbox"
test -f "$chrome_sandbox"
test "$(stat -c '%U:%G' "$chrome_sandbox")" = 'root:root'
chrome_sandbox_mode="$(stat -c '%a' "$chrome_sandbox")"
if [[ "$chrome_sandbox_mode" != '755' && "$chrome_sandbox_mode" != '4755' ]]; then
  echo "Unexpected chrome-sandbox mode: $chrome_sandbox_mode" >&2
  exit 1
fi

unexpected_owner="$(find "$install_root" -xdev \( ! -user root -o ! -group root \) -print -quit)"
if [[ -n "$unexpected_owner" ]]; then
  echo "DEB install tree contains an entry not owned by root:root: $unexpected_owner" >&2
  exit 1
fi
writable_entry="$(find "$install_root" -xdev ! -type l -perm /022 -print -quit)"
if [[ -n "$writable_entry" ]]; then
  echo "DEB install tree contains a group/world-writable entry: $writable_entry" >&2
  exit 1
fi

installed_files="$(dpkg -L "$package_name")"
desktop_file="$(grep -m1 -E '/usr/share/applications/.+\.desktop$' <<<"$installed_files")"
test -f "$desktop_file"
desktop-file-validate "$desktop_file"

mkdir -p "$state_root"
credential_probe="$state_root/credential-smoke.py"
cat >"$credential_probe" <<'PY'
import secrets

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

from core.security.credentials import CredentialRefStore, LinuxSecretServiceCredentialBackend

assert Atspi.get_desktop(0) is not None
store = CredentialRefStore(LinuxSecretServiceCredentialBackend())
reference = f"cred:v8-system:release-smoke-{secrets.token_hex(12)}"
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
print("V8OS_LINUX_GI_KEYRING_OK")
PY

export V8_AGENT_OS_HOME="$state_root"
export XDG_DATA_HOME="$state_root/xdg-data"
export XDG_CONFIG_HOME="$state_root/xdg-config"
export XDG_RUNTIME_DIR="$state_root/xdg-runtime"
export V8OS_DISABLE_UPDATE_CHECK=1
export V8_CHECKPOINT_AES_KEY="$(openssl rand -hex 32)"
export V8OS_SMOKE_KEYRING_PASSWORD="$(openssl rand -hex 32)"
export V8OS_CREDENTIAL_PROBE="$credential_probe"
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

dbus-run-session -- bash -euo pipefail -c '
  keyring_env="$V8_AGENT_OS_HOME/keyring.env"
  printf "%s" "$V8OS_SMOKE_KEYRING_PASSWORD" \
    | gnome-keyring-daemon --unlock --components=secrets >"$keyring_env"
  while IFS= read -r entry; do
    case "$entry" in
      GNOME_KEYRING_CONTROL=*|SSH_AUTH_SOCK=*) export "$entry" ;;
    esac
  done <"$keyring_env"
  engine_root="/opt/V8 Agent OS/resources/v8os/apps/v8-agent-os-engine"
  engine_python="$engine_root/.python/bin/python3"
  (cd "$engine_root" && xvfb-run -a env PYTHONPATH="$engine_root" "$engine_python" "$V8OS_CREDENTIAL_PROBE")
  xvfb-run -a node apps/v8-agent-os-shell/tests/scripts/run_desktop_install_smoke.mjs \
    --shell-exe "/opt/V8 Agent OS/v8-agent-os-shell" \
    --occupy-default-web-port true \
    --timeout-ms 90000 \
    --startup-budget-ms 90000 \
    --stability-window-ms 15000
'

# The package must be recoverable without mutating the user state root.
sudo apt-get remove -y "$package_name"
test ! -e "$install_root"
test -d "$state_root"
sudo apt-get install -y "$deb_path"
test -x "$shell_exe"
sudo apt-get purge -y "$package_name"
test ! -e "$install_root"

echo "V8OS_LINUX_CLEAN_DEB_SMOKE_OK"
