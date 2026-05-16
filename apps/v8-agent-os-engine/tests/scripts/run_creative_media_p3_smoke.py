from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from runtimes.creative_media.runtime import creative_media_runtime  # noqa: E402


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _asset_id(path: Path, prefix: str) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    return f"p3_smoke_{prefix}_{digest}"


def _scan_media(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    videos: list[Path] = []
    audios: list[Path] = []
    for root in paths:
        if not root.exists():
            continue
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            suffix = item.suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                videos.append(item)
            elif suffix in AUDIO_EXTENSIONS:
                audios.append(item)
    videos.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    audios.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return videos, audios


async def _fill_video_with_live_provider() -> list[dict[str, Any]]:
    job = await creative_media_runtime.create_job(
        {
            "modality": "video",
            "prompt": "5秒稳定镜头，抽象科技背景，缓慢推进，适合 Creative Media P3 smoke test。",
            "ratio": "16:9",
            "resolution": "720p",
            "durationSeconds": 5,
            "wait": True,
            "timeoutSeconds": 360,
        }
    )
    if job.get("status") != "succeeded":
        raise RuntimeError(f"live video fill failed: {job.get('error') or job.get('status')}")
    return list(job.get("artifacts") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Creative Media P3 local post-production smoke.")
    parser.add_argument(
        "--media-root",
        action="append",
        default=[
            str(Path.home() / ".v8-agent-os" / "workspace" / "downloaded_media"),
            str(Path.home() / ".v8-agent-os" / "workspace" / "creative_media"),
        ],
        help="Scan this media root for existing video/audio assets. Can be repeated.",
    )
    parser.add_argument("--live-fill-missing", action="store_true", help="Call real P1 video provider only if no sample video is found.")
    parser.add_argument("--max-clips", type=int, default=2)
    args = parser.parse_args()

    videos, audios = _scan_media([Path(item).expanduser() for item in args.media_root])
    artifacts: list[dict[str, Any]] = []
    if not videos and args.live_fill_missing:
        artifacts = asyncio.run(_fill_video_with_live_provider())
        for artifact in artifacts:
            source_path = str(artifact.get("sourcePath") or "").strip()
            if source_path:
                videos.append(Path(source_path))

    if not videos:
        print(json.dumps({"ok": False, "error": "no sample video found; pass --live-fill-missing to call provider"}, ensure_ascii=False, indent=2))
        return 2

    asset_ids: list[str] = []
    for path in videos[: max(1, args.max_clips)]:
        asset = creative_media_runtime.register_asset(
            {
                "assetId": _asset_id(path, "video"),
                "role": "clip",
                "modality": "video",
                "sourcePath": str(path),
                "metadata": {"smoke": "creative_media_p3", "adoption": "source_path_reference"},
            }
        )
        asset_ids.append(asset["assetId"])
    for path in audios[:1]:
        asset = creative_media_runtime.register_asset(
            {
                "assetId": _asset_id(path, "audio"),
                "role": "voice_or_music_mix",
                "modality": "voice",
                "sourcePath": str(path),
                "metadata": {"smoke": "creative_media_p3", "adoption": "source_path_reference"},
            }
        )
        asset_ids.append(asset["assetId"])

    recipe = creative_media_runtime.compile_recipe(
        {
            "modality": "video",
            "prompt": "把已有生成素材拼成一个短视频，保留原始画面，不调用旧音乐播放器。",
            "durationSeconds": 10,
            "assetIds": asset_ids,
        }
    )
    plan = creative_media_runtime.create_edit_plan(
        {
            "recipeId": recipe["recipeId"],
            "assetIds": asset_ids,
            "subtitleText": "Creative Media P3 本地后期 smoke",
        }
    )
    render = creative_media_runtime.render_edit_plan({"planId": plan["planId"]})
    ok = render.get("status") == "succeeded"
    print(
        json.dumps(
            {
                "ok": ok,
                "recipeId": recipe["recipeId"],
                "planId": plan["planId"],
                "renderJobId": render.get("renderJobId"),
                "status": render.get("status"),
                "artifacts": render.get("artifacts"),
                "error": render.get("error"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
