from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from erc.safety_guardian import safety_guardian  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Explain a SafetyRuntime command decision without executing it.")
    parser.add_argument("command", help="Command string to analyze.")
    parser.add_argument("--runtime-kind", default="local_dry_run", help="Runtime source label for the analysis.")
    parser.add_argument("--workspace-path", default="", help="Optional workspace path used for path-plane classification.")
    args = parser.parse_args()

    runtime_context = {"runtime_kind": args.runtime_kind}
    if args.workspace_path:
        runtime_context["workspace_path"] = args.workspace_path
    payload = safety_guardian.explain_system_command(args.command, runtime_context=runtime_context)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
