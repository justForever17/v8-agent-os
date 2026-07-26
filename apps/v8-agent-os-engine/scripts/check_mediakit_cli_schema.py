from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from runtimes.plugin_manager.cli_capability_sync import (  # noqa: E402
    CliCapabilitySyncError,
    sync_mediakit_capabilities,
)


def _default_executable() -> str:
    discovered = shutil.which("mediakit-cli")
    if discovered:
        return discovered
    return str(
        Path.home()
        / ".v8-agent-os"
        / "plugins"
        / "volcengine-mediakit"
        / "bin"
        / ("mediakit-cli.exe" if sys.platform == "win32" else "mediakit-cli")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover every MediaKit CLI schema and enforce upgrade compatibility.",
    )
    parser.add_argument("--executable", default=_default_executable())
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--plugin-id", default="volcengine-mediakit")
    parser.add_argument("--profile-id", default="mediakit-cli")
    parser.add_argument("--force", action="store_true", help="Rescan schemas even when the CLI version is unchanged.")
    parser.add_argument(
        "--allow-breaking",
        action="store_true",
        help="Replace the last-known-good snapshot even when compatibility checks find a breaking change.",
    )
    args = parser.parse_args()
    try:
        result = sync_mediakit_capabilities(
            executable=str(Path(args.executable).expanduser()),
            plugin_id=str(args.plugin_id),
            profile_id=str(args.profile_id),
            target_path=args.snapshot.expanduser(),
            block_breaking_upgrade=not args.allow_breaking,
            force_refresh=bool(args.force),
        )
    except CliCapabilitySyncError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    safe_result = {
        "ok": bool(result.get("ok")),
        "accepted": bool(result.get("accepted")),
        "classification": result.get("classification"),
        "previousVersion": result.get("previousVersion"),
        "candidateVersion": result.get("candidateVersion"),
        "actionCount": result.get("actionCount"),
        "addedActions": result.get("addedActions"),
        "removedActions": result.get("removedActions"),
        "issues": result.get("issues"),
        "snapshotPath": result.get("snapshotPath"),
        "compatibilityPath": result.get("compatibilityPath"),
        "candidatePath": result.get("candidatePath"),
        "cached": bool(result.get("cached")),
    }
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    return 0 if result.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
