#!/usr/bin/env bash
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_ROOT=""
if [ -n "$SCRIPT_SOURCE" ] && [ -f "$SCRIPT_SOURCE" ]; then
  SCRIPT_ROOT="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
fi

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

mkdir -p "$WORKSPACE_DIR" "$LOG_DIR"

step "Checking prerequisites"
if [ "$USE_CURRENT_CHECKOUT" -ne 1 ]; then
  ensure_command git "Install Git first."
fi
ensure_command python "Install Python 3.11+ first."
ensure_command npm "Install Node.js 20+ first."

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
  exit 0
fi

step "Preparing engine"
if [ ! -d "$ENGINE_DIR/.venv" ]; then
  python -m venv "$ENGINE_DIR/.venv"
fi
"$ENGINE_DIR/.venv/bin/python" -m pip install --upgrade pip
"$ENGINE_DIR/.venv/bin/python" -m pip install -r "$ENGINE_DIR/requirements.txt"

step "Preparing admin"
(cd "$ADMIN_DIR" && npm install)
ensure_admin_env "$ADMIN_DIR"

step "Starting engine and admin"
nohup "$ENGINE_DIR/.venv/bin/python" "$ENGINE_DIR/main.py" >"$LOG_DIR/engine.stdout.log" 2>"$LOG_DIR/engine.stderr.log" &
nohup npm --prefix "$ADMIN_DIR" run dev >"$LOG_DIR/admin.stdout.log" 2>"$LOG_DIR/admin.stderr.log" &

printf "\nV8 Agent OS is starting.\n"
printf "Source: %s\n" "$REPO_SOURCE"
printf "Engine: http://127.0.0.1:9530\n"
printf "Admin : http://127.0.0.1:9528\n"
printf "Web   : install and package separately from apps/v8-agent-os-web\n"
printf "Logs  : %s\n" "$LOG_DIR"
