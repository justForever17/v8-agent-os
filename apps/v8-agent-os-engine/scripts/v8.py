from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.system_doctor import system_doctor_service  # noqa: E402
from acp_bridge.stdio_server import main as acp_stdio_main  # noqa: E402


def _print_human_report(payload: dict) -> None:
    summary = payload.get("summary") or {}
    print(f"V8OS Doctor: {summary.get('status', 'unknown')}")
    counts = summary.get("counts") or {}
    print(
        "Checks: "
        f"ok={counts.get('ok', 0)} "
        f"warning={counts.get('warning', 0)} "
        f"error={counts.get('error', 0)} "
        f"info={counts.get('info', 0)}"
    )
    print("")
    for item in payload.get("checks") or []:
        status = str(item.get("status") or "info").upper()
        print(f"[{status}] {item.get('title')}: {item.get('summary')}")
    actions = (payload.get("repairPlan") or {}).get("actions") or []
    if actions:
        print("")
        print("Repair plan (plan-only):")
        for action in actions:
            confirm = "requires confirmation" if action.get("requiresConfirmation") else "safe to inspect"
            print(f"- {action.get('title')} ({confirm}): {action.get('description')}")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="v8", description="V8 Agent OS local CLI")
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor", help="Run local system doctor")
    doctor.add_argument("--json", action="store_true", help="Print JSON output")
    doctor.add_argument("--repair-plan", action="store_true", help="Print only the repair plan")
    subparsers.add_parser("acp", help="Run the V8OS ACP stdio bridge")
    args = parser.parse_args(argv)

    if args.command == "acp":
        return acp_stdio_main()

    if args.command == "doctor":
        payload = system_doctor_service.run()
        if args.repair_plan:
            payload = payload.get("repairPlan") or {}
        if args.json or args.repair_plan:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_human_report(payload)
        status = ((payload.get("summary") or {}).get("status") if isinstance(payload, dict) else None) or "ok"
        return 1 if status == "error" else 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
