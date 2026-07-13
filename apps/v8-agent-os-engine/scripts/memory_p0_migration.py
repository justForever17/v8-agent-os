from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.memory_p0_migration import memory_p0_migration_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan/apply/rollback the V8OS Memory P0 migration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--idempotency-key")
    apply_parser.add_argument("--plan-digest")
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("manifest", type=Path)
    rollback_parser.add_argument("--offline", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = memory_p0_migration_service.plan()
    elif args.command == "apply":
        result = memory_p0_migration_service.apply(
            idempotency_key=args.idempotency_key,
            expected_plan_digest=args.plan_digest,
        )
    else:
        result = memory_p0_migration_service.rollback(manifest_path=args.manifest, offline=args.offline)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
