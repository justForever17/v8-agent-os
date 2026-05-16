from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke for the runtime-native GitHub star playbook.")
    parser.add_argument("--live-github-star", action="store_true", help="Enable the live GitHub star smoke.")
    parser.add_argument("--allow-real-click", action="store_true", help="Allow a real Star click after pre-state verification.")
    parser.add_argument("--repo", default="TurixAI/TuriX-CUA", help="Repository owner/name. Only TurixAI/TuriX-CUA is allowed.")
    parser.add_argument("--desired-state", choices=["starred", "unstarred"], default="starred", help="Desired repository star state.")
    parser.add_argument("--output-dir", default=str(ENGINE_ROOT / "tests" / "artifacts" / "computer_use_live"))
    parser.add_argument("--require-starred", action="store_true", help="Exit non-zero unless post-state is Starred.")
    args = parser.parse_args()

    repo = str(args.repo or "").strip()
    if repo.lower() != "turixai/turix-cua":
        print(json.dumps({"ok": False, "status": "blocked", "reason": "only_TurixAI_TuriX-CUA_allowed"}, ensure_ascii=False, indent=2))
        return 2
    if not args.live_github_star:
        print(json.dumps({"ok": True, "status": "dry_run_only", "repo": repo}, ensure_ascii=False, indent=2))
        return 0
    if not args.allow_real_click:
        print(json.dumps({"ok": False, "status": "blocked", "reason": "missing_--allow-real-click"}, ensure_ascii=False, indent=2))
        return 2

    from runtimes.computer_use.runtime import computer_use_runtime

    result = computer_use_runtime.execute_github_star_playbook(
        goal=f"去 GitHub 给 {repo} {'取消星标' if args.desired_state == 'unstarred' else '点星标'}",
        allow_real_click=True,
        desired_state=args.desired_state,
    )
    output = {
        "ok": result.get("status") == "succeeded",
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "canonicalUrl": result.get("canonicalUrl"),
        "selectedPlaybook": result.get("selectedPlaybook"),
        "desiredState": result.get("desiredState"),
        "browserTarget": result.get("browserTarget"),
        "preState": result.get("preState"),
        "clickAction": result.get("clickAction"),
        "postState": result.get("postState"),
        "strictDom": result.get("strictDom"),
        "action": result.get("action"),
        "resourceLease": result.get("resourceLease"),
        "status": result.get("status"),
        "reason": result.get("reason"),
        "recommendedNextAction": result.get("recommendedNextAction"),
        "runId": result.get("runId"),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"github_star_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    output["traceArtifact"] = str(output_path)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.require_starred and not output["ok"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
