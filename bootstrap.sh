#!/usr/bin/env bash
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_ROOT=""
if [ -n "$SCRIPT_SOURCE" ] && [ -f "$SCRIPT_SOURCE" ]; then
  SCRIPT_ROOT="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
fi

PROFILE="standard"
SERVICES="engine+admin"
PLATFORM="auto"

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --services)
      SERVICES="${2:-}"
      shift 2
      ;;
    --platform)
      PLATFORM="${2:-}"
      shift 2
      ;;
    *)
      printf "Unknown argument: %s\n" "$1" >&2
      exit 1
      ;;
  esac
done

case "$PROFILE" in
  minimal|standard|desktop) ;;
  *)
    printf "Unsupported --profile value: %s\n" "$PROFILE" >&2
    exit 1
    ;;
esac

case "$SERVICES" in
  engine|engine+admin) ;;
  *)
    printf "Unsupported --services value: %s\n" "$SERVICES" >&2
    exit 1
    ;;
esac

detect_platform() {
  case "$(uname -s | tr '[:upper:]' '[:lower:]')" in
    mingw*|msys*|cygwin*) printf "windows" ;;
    darwin*) printf "macos" ;;
    linux*) printf "linux" ;;
    *) printf "linux" ;;
  esac
}

if [ "$PLATFORM" = "auto" ]; then
  PLATFORM="$(detect_platform)"
fi

case "$PLATFORM" in
  windows|macos|linux) ;;
  *)
    printf "Unsupported --platform value: %s\n" "$PLATFORM" >&2
    exit 1
    ;;
esac

if [ -n "$SCRIPT_ROOT" ] && [ -d "$SCRIPT_ROOT/apps/v8-agent-os-engine" ] && [ -d "$SCRIPT_ROOT/apps/v8-agent-os-admin" ]; then
  USE_CURRENT_CHECKOUT=1
  WORKSPACE_DIR="$SCRIPT_ROOT/.bootstrap-workspace"
  REPO_DIR="$SCRIPT_ROOT"
  REPO_SOURCE="current checkout"
else
  USE_CURRENT_CHECKOUT=0
  if [ -n "${V8_AGENT_OS_BOOTSTRAP_WORKSPACE:-}" ]; then
    WORKSPACE_DIR="$V8_AGENT_OS_BOOTSTRAP_WORKSPACE"
  elif [ -n "${HOME:-}" ]; then
    WORKSPACE_DIR="$HOME/.bootstrap-workspace"
  else
    WORKSPACE_DIR="$PWD/.bootstrap-workspace"
  fi
  REPO_DIR="$WORKSPACE_DIR/v8-agent-os"
  REPO_SOURCE="bootstrap workspace clone"
fi

LOG_DIR="$WORKSPACE_DIR/logs"
REPO_URL="https://github.com/justForever17/v8-agent-os.git"

step() {
  printf '\n==> %s\n' "$1"
}

ensure_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf "Missing required command '%s'. %s\n" "$1" "$2" >&2
    exit 1
  fi
}

sync_repo() {
  local repo="$1"
  local target="$2"
  if [ ! -d "$target/.git" ]; then
    git clone "$repo" "$target"
  else
    git -C "$target" pull --ff-only
  fi
}

ensure_admin_env() {
  local env_file="$1/.env.local"
  if [ -f "$env_file" ]; then
    return
  fi

  local secret
  secret="$(python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  cat >"$env_file" <<EOF
NEXTAUTH_URL=http://127.0.0.1:9528
NEXTAUTH_SECRET=$secret
NEXT_PUBLIC_APP_VERSION=1.0.0
EOF
}

requirements_for_profile() {
  local engine_dir="$1"
  printf "%s\n" "$engine_dir/requirements/base.txt"
  if [ "$PROFILE" = "standard" ] || [ "$PROFILE" = "desktop" ]; then
    printf "%s\n" "$engine_dir/requirements/standard.txt"
  fi
  if [ "$PROFILE" = "desktop" ]; then
    printf "%s\n" "$engine_dir/requirements/desktop-common.txt"
    printf "%s\n" "$engine_dir/requirements/platform-$PLATFORM.txt"
  fi
}

desktop_preflight() {
  if [ "$PROFILE" != "desktop" ]; then
    return
  fi

  if [ "$PLATFORM" = "macos" ]; then
    step "macOS desktop preflight"
    if command -v swiftc >/dev/null 2>&1; then
      printf "[ok] swiftc / Xcode Command Line Tools\n"
    else
      printf "[missing] swiftc / Xcode Command Line Tools\n"
      printf "Warning: swiftc not found. macOS AX helper compile will fail until Xcode Command Line Tools are installed.\n"
    fi
    if command -v osascript >/dev/null 2>&1; then
      printf "[ok] osascript / Apple Events bridge\n"
    else
      printf "[missing] osascript / Apple Events bridge\n"
    fi
    printf "[manual] Accessibility permission\n"
    printf "[manual] Screen Recording permission\n"
    printf "[manual] Input Monitoring / synthetic input permission\n"
  fi

  if [ "$PLATFORM" = "linux" ]; then
    step "Linux desktop preflight"
    for candidate in gdbus dbus-send xdotool wmctrl grim gnome-screenshot; do
      if command -v "$candidate" >/dev/null 2>&1; then
        printf "[ok] %s\n" "$candidate"
      else
        printf "[missing] %s\n" "$candidate"
      fi
    done
    if [ -n "${XDG_SESSION_TYPE:-}" ]; then
      printf "[info] XDG_SESSION_TYPE=%s\n" "${XDG_SESSION_TYPE}"
    else
      printf "[unknown] XDG_SESSION_TYPE=unset\n"
    fi
    printf "[manual] portal / compositor screenshot permission\n"
    printf "[manual] AT-SPI accessibility bus availability\n"
    if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
      printf "Wayland session detected. Screenshot/input fallbacks may require portal/compositor support.\n"
    fi
  fi
}

mkdir -p "$WORKSPACE_DIR" "$LOG_DIR"

step "Checking prerequisites"
if [ "$USE_CURRENT_CHECKOUT" -ne 1 ]; then
  ensure_command git "Install Git first."
fi
ensure_command python "Install Python 3.11+ first."
if [ "$SERVICES" = "engine+admin" ]; then
  ensure_command npm "Install Node.js 20+ first."
fi

if [ "$USE_CURRENT_CHECKOUT" -eq 1 ]; then
  step "Using current checkout"
else
  step "Syncing repository"
  sync_repo "$REPO_URL" "$REPO_DIR"
fi

ENGINE_DIR="$REPO_DIR/apps/v8-agent-os-engine"
ADMIN_DIR="$REPO_DIR/apps/v8-agent-os-admin"

if [ "${V8_AGENT_OS_BOOTSTRAP_DRY_RUN:-0}" = "1" ]; then
  printf "\nBootstrap dry run.\n"
  printf "Repo source: %s\n" "$REPO_SOURCE"
  printf "Repo dir   : %s\n" "$REPO_DIR"
  printf "Workspace  : %s\n" "$WORKSPACE_DIR"
  printf "Log dir    : %s\n" "$LOG_DIR"
  printf "Profile    : %s\n" "$PROFILE"
  printf "Services   : %s\n" "$SERVICES"
  printf "Platform   : %s\n" "$PLATFORM"
  printf "Requirements:\n"
  requirements_for_profile "$ENGINE_DIR"
  exit 0
fi

desktop_preflight

step "Preparing engine"
if [ ! -d "$ENGINE_DIR/.venv" ]; then
  python -m venv "$ENGINE_DIR/.venv"
fi
"$ENGINE_DIR/.venv/bin/python" -m pip install --upgrade pip
while IFS= read -r requirement_file; do
  [ -f "$requirement_file" ] || continue
  "$ENGINE_DIR/.venv/bin/python" -m pip install -r "$requirement_file"
done < <(requirements_for_profile "$ENGINE_DIR")

if [ "$SERVICES" = "engine+admin" ]; then
  step "Preparing admin"
  (cd "$ADMIN_DIR" && npm install)
  ensure_admin_env "$ADMIN_DIR"
fi

step "Starting services"
nohup env ENGINE_STARTUP_PROFILE="$PROFILE" "$ENGINE_DIR/.venv/bin/python" "$ENGINE_DIR/main.py" >"$LOG_DIR/engine.stdout.log" 2>"$LOG_DIR/engine.stderr.log" &
if [ "$SERVICES" = "engine+admin" ]; then
  nohup npm --prefix "$ADMIN_DIR" run dev >"$LOG_DIR/admin.stdout.log" 2>"$LOG_DIR/admin.stderr.log" &
fi

printf "\nV8 Agent OS is starting.\n"
printf "Source  : %s\n" "$REPO_SOURCE"
printf "Profile : %s\n" "$PROFILE"
printf "Platform: %s\n" "$PLATFORM"
printf "Engine  : http://127.0.0.1:9530\n"
if [ "$SERVICES" = "engine+admin" ]; then
  printf "Admin   : http://127.0.0.1:9528\n"
else
  printf "Admin   : skipped\n"
fi
printf "Web     : install and package separately from apps/v8-agent-os-web\n"
printf "Logs    : %s\n" "$LOG_DIR"
