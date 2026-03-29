#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../../.." && pwd)"
WORKSPACE_DIR="$REPO_ROOT/.bootstrap-workspace"
LOG_DIR="$WORKSPACE_DIR/logs"

REPO_URL="https://github.com/justForever17/v8-agent-os.git"
REPO_DIR="$WORKSPACE_DIR/v8-agent-os"

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
ensure_command git "Install Git first."
ensure_command python "Install Python 3.11+ first."
ensure_command npm "Install Node.js 20+ first."

step "Syncing repository"
sync_repo "$REPO_URL" "$REPO_DIR"

ENGINE_DIR="$REPO_DIR/apps/v8-agent-os-engine"
ADMIN_DIR="$REPO_DIR/apps/v8-agent-os-admin"

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
printf "Engine: http://127.0.0.1:9530\n"
printf "Admin : http://127.0.0.1:9528\n"
printf "Web   : install and package separately from apps/v8-agent-os-web\n"
printf "Logs  : %s\n" "$LOG_DIR"
