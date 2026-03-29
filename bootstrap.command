#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$SCRIPT_DIR/bootstrap.sh"
exec "$SCRIPT_DIR/bootstrap.sh"
