from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

import httpx

from core.artifact_store import artifact_store
from core.audio.tts_provider import TTSManager
from core.database import db
from core.model_control_plane import model_control_plane
from core.storage import storage
from erc.runtime_registry import runtime_registry

from .catalog import (
    load_audio_music_recipe_library,
    load_provider_matrix,
    load_resolution_presets,
    load_video_recipe_library,
    load_visual_recipe_library,
    normalize_provider_status,
    resolve_image_size,
    resolve_video_resolution,
)
from .recipe import creative_recipe_compiler


JOB_STORE_FILE = "creative_media/jobs.json"
EDIT_PLAN_STORE_FILE = "creative_media/edit_plans.json"
RENDER_JOB_STORE_FILE = "creative_media/render_jobs.json"
SUPPORTED_MODALITIES = {"image", "video", "voice", "music", "model3d"}
# music/model3d intentionally stay schema/catalog-only in P2; adapters can be added without changing the job envelope.
EXECUTABLE_MODALITIES = {"image", "video", "voice"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_filename(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return normalized[:80] or fallback


def _jsonable_request(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload or {})
    for key in ("apiKey", "api_key", "authorization", "Authorization"):
        safe.pop(key, None)
    return safe


def _list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [item.strip() for item in re.split(r"[,，;\n]", value)]
    else:
        raw = [str(item or "").strip() for item in list(value or [])]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _build_openai_image_payload(*, model: str, prompt: str, size: str, response_format: str = "b64_json") -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": response_format,
    }


def _build_volcengine_image_payload(
    *,
    model: str,
    prompt: str,
    size: str,
    response_format: str = "url",
    seed: int = -1,
    image_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "seed": seed,
        "response_format": response_format,
    }
    if image_urls:
        payload["image"] = image_urls
    return payload


def _build_volcengine_video_payload(
    *,
    model: str,
    prompt: str,
    ratio: str,
    resolution: str,
    duration: int,
    seed: int = -1,
    image_urls: Optional[list[str]] = None,
    generate_audio: bool = True,
    watermark: bool = False,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if prompt:
        content.append({"type": "text", "text": prompt})
    for index, url in enumerate(image_urls or []):
        role = "first_frame" if index == 0 else "last_frame" if index == 1 else "reference_image"
        content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
    return {
        "model": model,
        "content": content,
        "ratio": ratio,
        "resolution": resolution,
        "duration": duration,
        "seed": seed,
        "watermark": watermark,
        "generate_audio": generate_audio,
    }


class CreativeMediaRuntime:
    kind = "creative_media"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "CreativeMediaRuntime",
            "summary": "负责图片、视频、语音、音乐与未来 3D 媒体 job 的 provider 适配、轮询和 artifact 交付。",
            "responsibilities": [
                "归一化媒体 provider 请求格式。",
                "持久化媒体 job 状态。",
                "把生成结果登记为 runtime artifact。",
            ],
            "routingKeywords": ["image", "video", "voice", "music", "creative_media", "artifact"],
            "acceptedInputs": ["media job request"],
            "producedOutputs": ["image artifact", "video artifact", "audio artifact", "media job status"],
            "supportsResume": True,
            "supportsRepair": False,
            "visibility": "internal",
            "metadata": {
                "p1": True,
                "p2": True,
                "p3": True,
                "supervisorToolSurface": False,
                "managedToolGroups": ["creative_media.core"],
                "managedToolNames": [
                    "creative_media_catalog",
                    "creative_media_resolutions",
                    "creative_media_create_job",
                    "creative_media_get_job",
                    "creative_media_list_jobs",
                    "creative_media_job_artifacts",
                    "creative_media_compile_recipe",
                    "creative_media_get_recipe",
                    "creative_media_list_recipes",
                    "creative_media_register_asset",
                    "creative_media_list_assets",
                    "creative_media_create_character_bible",
                    "creative_media_get_character_bible",
                    "creative_media_list_character_bibles",
                    "creative_media_register_keyframe",
                    "creative_media_get_keyframe",
                    "creative_media_list_keyframes",
                    "creative_media_create_edit_plan",
                    "creative_media_get_edit_plan",
                    "creative_media_list_edit_plans",
                    "creative_media_render_edit_plan",
                    "creative_media_get_render",
                    "creative_media_list_renders",
                ],
            },
        }

    def catalog(self) -> dict[str, Any]:
        matrix = load_provider_matrix()
        return {
            **matrix,
            "runtimeAdapters": [
                {"id": "openai_images", "modalities": ["image"], "executable": True},
                {"id": "volcengine_ark", "modalities": ["image", "video"], "executable": True},
                {"id": "v8_audio_tts", "modalities": ["voice"], "executable": True},
                {"id": "catalog_only", "modalities": ["music", "model3d"], "executable": False},
            ],
        }

    def resolutions(self) -> dict[str, Any]:
        return load_resolution_presets()

    def recipe_libraries(self) -> dict[str, Any]:
        return {
            "visual": load_visual_recipe_library(),
            "video": load_video_recipe_library(),
            "audioMusic": load_audio_music_recipe_library(),
        }

    def compile_recipe(self, request: dict[str, Any]) -> dict[str, Any]:
        return creative_recipe_compiler.compile_recipe(dict(request or {}))

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        return creative_recipe_compiler.get_recipe(recipe_id)

    def list_recipes(self, *, modality: str | None = None, recipe_kind: str | None = None) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_recipes(modality=modality, recipe_kind=recipe_kind)

    def register_asset(self, request: dict[str, Any]) -> dict[str, Any]:
        return creative_recipe_compiler.register_asset(dict(request or {}))

    def list_assets(self, *, modality: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_assets(modality=modality, role=role)

    def create_character_bible(self, request: dict[str, Any]) -> dict[str, Any]:
        return creative_recipe_compiler.create_character_bible(dict(request or {}))

    def get_character_bible(self, bible_id: str) -> dict[str, Any] | None:
        return creative_recipe_compiler.get_character_bible(bible_id)

    def list_character_bibles(self) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_character_bibles()

    def register_keyframe(self, request: dict[str, Any]) -> dict[str, Any]:
        return creative_recipe_compiler.register_keyframe(dict(request or {}))

    def get_keyframe(self, keyframe_id: str) -> dict[str, Any] | None:
        return creative_recipe_compiler.get_keyframe(keyframe_id)

    def list_keyframes(
        self,
        *,
        recipe_id: str | None = None,
        role: str | None = None,
        character_bible_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_keyframes(
            recipe_id=recipe_id,
            role=role,
            character_bible_id=character_bible_id,
        )

    def _read_versioned_store(self, filename: str, key: str) -> dict[str, Any]:
        payload = storage.read_json(filename)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return {"version": 1, key: {}}
        payload.setdefault(key, {})
        return payload

    def _write_versioned_store(self, filename: str, key: str, values: dict[str, Any]) -> None:
        storage.write_json(filename, {"version": 1, key: dict(values or {})})

    def _artifact_source_path(self, artifact_id: str) -> str:
        normalized = str(artifact_id or "").strip()
        if not normalized:
            return ""
        record = db.get_runtime_artifact(normalized)
        if not record:
            return ""
        return str(record.get("source_path") or record.get("sourcePath") or "").strip()

    def _resolve_media_path(self, ref: dict[str, Any]) -> tuple[str, bool]:
        raw_path = (
            str(ref.get("sourcePath") or ref.get("source_path") or "").strip()
            or self._artifact_source_path(str(ref.get("artifactId") or ref.get("artifact_id") or ""))
            or str(ref.get("workspacePath") or ref.get("workspace_path") or "").strip()
        )
        if not raw_path:
            return "", False
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = storage.base_dir / "workspace" / raw_path
        return str(path), path.exists() and path.is_file()

    def _probe_duration_seconds(self, path: str) -> float | None:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                return None
            payload = json.loads(result.stdout or "{}")
            duration = float(((payload.get("format") or {}).get("duration") or 0))
            return duration if duration > 0 else None
        except Exception:
            return None

    def _asset_refs_by_ids(self, asset_ids: list[str]) -> list[dict[str, Any]]:
        if not asset_ids:
            return []
        assets = self.list_assets()
        by_id = {str(item.get("assetId") or ""): dict(item) for item in assets}
        return [by_id[item] for item in asset_ids if item in by_id]

    def _select_edit_plan_assets(self, request: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        explicit_asset_ids = _list_of_strings(request.get("assetIds") or request.get("asset_ids"))
        explicit_video_ids = _list_of_strings(request.get("videoAssetIds") or request.get("video_asset_ids"))
        explicit_audio_ids = _list_of_strings(
            request.get("audioAssetIds")
            or request.get("audio_asset_ids")
            or request.get("voiceAssetIds")
            or request.get("musicAssetIds")
        )
        explicit_assets = self._asset_refs_by_ids([*explicit_asset_ids, *explicit_video_ids, *explicit_audio_ids])
        inline_assets = [dict(item) for item in list(request.get("assets") or []) if isinstance(item, dict)]
        candidates = [*explicit_assets, *inline_assets]
        if not candidates:
            candidates = self.list_assets()

        video_refs: list[dict[str, Any]] = []
        audio_refs: list[dict[str, Any]] = []
        for asset in candidates:
            modality = str(asset.get("modality") or "").strip().lower()
            path, exists = self._resolve_media_path(asset)
            suffix = Path(path).suffix.lower() if path else ""
            enriched = {**asset, "resolvedPath": path, "pathExists": exists}
            if modality == "video" or suffix in VIDEO_EXTENSIONS:
                video_refs.append(enriched)
            elif modality in {"voice", "music", "audio"} or suffix in AUDIO_EXTENSIONS:
                audio_refs.append(enriched)
        return video_refs[:12], audio_refs[:6]

    def _plan_subtitle_segments(self, request: dict[str, Any], *, total_duration: float) -> list[dict[str, Any]]:
        raw_segments = request.get("subtitles") or request.get("subtitleSegments") or request.get("subtitle_segments")
        segments: list[dict[str, Any]] = []
        if isinstance(raw_segments, list):
            for index, item in enumerate(raw_segments):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("caption") or "").strip()
                if not text:
                    continue
                start = max(0.0, float(item.get("start") or item.get("startSeconds") or index * 3))
                end = max(start + 0.5, float(item.get("end") or item.get("endSeconds") or min(total_duration, start + 3)))
                segments.append({"start": start, "end": min(end, max(total_duration, end)), "text": text})
        subtitle_text = str(request.get("subtitleText") or request.get("subtitle_text") or "").strip()
        if subtitle_text and not segments:
            segments.append({"start": 0.0, "end": max(1.0, total_duration), "text": subtitle_text})
        return segments

    def create_edit_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        plan_id = str(payload.get("planId") or payload.get("editPlanId") or payload.get("id") or f"cm_edit_{uuid.uuid4().hex}").strip()
        recipe_id = str(payload.get("recipeId") or payload.get("recipe_id") or "").strip()
        recipe = self.get_recipe(recipe_id) if recipe_id else None
        scope = self._scope_fields(payload, recipe or {})
        video_refs, audio_refs = self._select_edit_plan_assets(payload)
        if not video_refs:
            raise ValueError("creative media edit plan requires at least one video asset")

        timeline: list[dict[str, Any]] = []
        cursor = 0.0
        for index, asset in enumerate(video_refs, start=1):
            duration = self._probe_duration_seconds(str(asset.get("resolvedPath") or "")) or float(payload.get("defaultClipDurationSeconds") or 5)
            clip = {
                "clipId": f"clip_{index:02d}",
                "assetId": asset.get("assetId"),
                "artifactId": asset.get("artifactId"),
                "sourcePath": asset.get("sourcePath"),
                "resolvedPath": asset.get("resolvedPath"),
                "pathExists": bool(asset.get("pathExists")),
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "durationSeconds": round(duration, 3),
                "role": asset.get("role") or "clip",
                "title": asset.get("title") or asset.get("assetId") or f"clip-{index}",
            }
            timeline.append(clip)
            cursor += duration

        target_duration = float(payload.get("targetDurationSeconds") or payload.get("durationSeconds") or cursor)
        render_profile = {
            "format": "mp4",
            "width": int(payload.get("width") or (payload.get("renderProfile") or {}).get("width") or 1280),
            "height": int(payload.get("height") or (payload.get("renderProfile") or {}).get("height") or 720),
            "fps": int(payload.get("fps") or (payload.get("renderProfile") or {}).get("fps") or 30),
            "videoCodec": "libx264",
            "audioCodec": "aac",
        }
        now = utc_now_iso()
        plan = {
            "planId": plan_id,
            **scope,
            "kind": "creative_media_edit_plan",
            "recipeId": recipe_id,
            "status": "planned",
            "timeline": timeline,
            "tracks": {
                "video": timeline,
                "audio": [
                    {
                        "assetId": asset.get("assetId"),
                        "artifactId": asset.get("artifactId"),
                        "sourcePath": asset.get("sourcePath"),
                        "resolvedPath": asset.get("resolvedPath"),
                        "pathExists": bool(asset.get("pathExists")),
                        "modality": asset.get("modality"),
                        "role": asset.get("role") or "audio",
                        "musicKind": asset.get("musicKind"),
                    }
                    for asset in audio_refs
                ],
                "subtitles": self._plan_subtitle_segments(payload, total_duration=target_duration or cursor),
            },
            "renderProfile": render_profile,
            "lineage": {
                "recipeId": recipe_id,
                "assetIds": [str(asset.get("assetId") or "") for asset in [*video_refs, *audio_refs] if asset.get("assetId")],
                "sourceRefs": _list_of_strings(payload.get("sourceRefs") or payload.get("source_refs")),
                "parentPlanId": str(payload.get("parentPlanId") or payload.get("parent_plan_id") or "").strip(),
            },
            "qualityGates": {
                "missingVideoFiles": [clip for clip in timeline if not clip.get("pathExists")],
                "missingAudioFiles": [item for item in audio_refs if not item.get("pathExists")],
                "musicBoundary": "Creative Media audio/music tracks must be artifact or asset ledger refs; legacy MusicTrack is not used.",
                "estimatedDurationSeconds": round(cursor, 3),
                "targetDurationSeconds": round(target_duration, 3),
            },
            "recipeSnapshot": {
                "modality": (recipe or {}).get("modality"),
                "recipeKind": (recipe or {}).get("recipeKind"),
                "prompt": (recipe or {}).get("prompt"),
            } if recipe else {},
            "createdAt": now,
            "updatedAt": now,
        }
        store = self._read_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans")
        plans = dict(store.get("editPlans") or {})
        previous = dict(plans.get(plan_id) or {})
        plan["version"] = int(previous.get("version") or 0) + 1
        plan["createdAt"] = previous.get("createdAt") or now
        plans[plan_id] = plan
        self._write_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans", plans)
        return plan

    def get_edit_plan(self, plan_id: str) -> dict[str, Any] | None:
        return dict((self._read_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans").get("editPlans") or {}).get(str(plan_id)) or {}) or None

    def list_edit_plans(self, *, recipe_id: str | None = None) -> list[dict[str, Any]]:
        plans = list((self._read_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans").get("editPlans") or {}).values())
        normalized_recipe_id = str(recipe_id or "").strip()
        result = [dict(item) for item in plans if not normalized_recipe_id or str(item.get("recipeId") or "") == normalized_recipe_id]
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def _save_render_job(self, job: dict[str, Any]) -> dict[str, Any]:
        store = self._read_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs")
        jobs = dict(store.get("renderJobs") or {})
        job["updatedAt"] = utc_now_iso()
        jobs[str(job["renderJobId"])] = job
        self._write_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs", jobs)
        return job

    def get_render(self, render_job_id: str) -> dict[str, Any] | None:
        return dict((self._read_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs").get("renderJobs") or {}).get(str(render_job_id)) or {}) or None

    def list_renders(self, *, plan_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list((self._read_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs").get("renderJobs") or {}).values())
        normalized_plan_id = str(plan_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        result = [
            dict(item)
            for item in jobs
            if (not normalized_plan_id or str(item.get("planId") or "") == normalized_plan_id)
            and (not normalized_status or str(item.get("status") or "").lower() == normalized_status)
        ]
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def _srt_timestamp(self, seconds: float) -> str:
        total_ms = max(0, int(round(float(seconds) * 1000)))
        hours, remainder = divmod(total_ms, 3600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _write_srt(self, subtitles: list[dict[str, Any]], render_job: dict[str, Any]) -> Path | None:
        if not subtitles:
            return None
        path = self._output_path(render_job, "subtitles", ".srt")
        lines: list[str] = []
        for index, item in enumerate(subtitles, start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            lines.extend(
                [
                    str(index),
                    f"{self._srt_timestamp(float(item.get('start') or 0))} --> {self._srt_timestamp(float(item.get('end') or 0))}",
                    text,
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _record_post_artifact(self, *, file_path: Path, kind: str, mime_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return artifact_store.record_artifact(
            artifact_kind=kind,
            mime_type=mime_type,
            title=file_path.name,
            source_path=str(file_path),
            metadata={
                **metadata,
                "origin": "creative_media_post_production",
                "pathPlane": "runtime",
                "storageClass": "runtime_artifact",
                "surfaceVisible": True,
            },
            source_component="creative_media_runtime",
            node="creative_media_post_production",
        )

    def _build_ffmpeg_render_command(
        self,
        *,
        ffmpeg: str,
        video_paths: list[str],
        audio_paths: list[str],
        output_path: Path,
        profile: dict[str, Any],
    ) -> list[str]:
        width = max(64, int(profile.get("width") or 1280))
        height = max(64, int(profile.get("height") or 720))
        fps = max(1, int(profile.get("fps") or 30))
        command = [ffmpeg, "-y"]
        for path in video_paths:
            command.extend(["-i", path])
        for path in audio_paths:
            command.extend(["-i", path])

        filters: list[str] = []
        video_labels: list[str] = []
        for index in range(len(video_paths)):
            label = f"v{index}"
            filters.append(
                f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[{label}]"
            )
            video_labels.append(f"[{label}]")
        video_out = "[vout]"
        if len(video_labels) == 1:
            filters.append(f"{video_labels[0]}null{video_out}")
        else:
            filters.append(f"{''.join(video_labels)}concat=n={len(video_labels)}:v=1:a=0{video_out}")

        audio_out = ""
        if audio_paths:
            audio_labels: list[str] = []
            audio_offset = len(video_paths)
            for index in range(len(audio_paths)):
                label = f"a{index}"
                filters.append(f"[{audio_offset + index}:a]aresample=44100,volume=1.0[{label}]")
                audio_labels.append(f"[{label}]")
            audio_out = "[aout]"
            if len(audio_labels) == 1:
                filters.append(f"{audio_labels[0]}anull{audio_out}")
            else:
                filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=shortest:dropout_transition=0{audio_out}")

        command.extend(["-filter_complex", ";".join(filters), "-map", video_out])
        if audio_out:
            command.extend(["-map", audio_out, "-shortest"])
        else:
            command.append("-an")
        command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
        if audio_out:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.append(str(output_path))
        return command

    def render_edit_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        plan_id = str(payload.get("planId") or payload.get("editPlanId") or payload.get("id") or "").strip()
        plan = self.get_edit_plan(plan_id) if plan_id else None
        if not plan:
            if payload.get("plan") and isinstance(payload.get("plan"), dict):
                plan = dict(payload["plan"])
                plan_id = str(plan.get("planId") or f"cm_edit_{uuid.uuid4().hex}")
            else:
                raise ValueError("creative media render requires planId or plan")
        render_job_id = str(payload.get("renderJobId") or f"cm_render_{uuid.uuid4().hex}").strip()
        now = utc_now_iso()
        scope = self._scope_fields(payload, plan)
        job = {
            "renderJobId": render_job_id,
            **scope,
            "planId": plan_id,
            "status": "running",
            "artifacts": [],
            "diagnostics": {},
            "error": None,
            "createdAt": now,
            "updatedAt": now,
            "completedAt": None,
        }
        self._save_render_job(job)

        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found")
            video_paths = [
                str(item.get("resolvedPath") or "")
                for item in list((plan.get("tracks") or {}).get("video") or plan.get("timeline") or [])
                if str(item.get("resolvedPath") or "").strip()
            ]
            audio_paths = [
                str(item.get("resolvedPath") or "")
                for item in list((plan.get("tracks") or {}).get("audio") or [])
                if str(item.get("resolvedPath") or "").strip()
            ]
            video_paths = [path for path in video_paths if Path(path).exists()]
            audio_paths = [path for path in audio_paths if Path(path).exists()]
            if not video_paths:
                raise ValueError("creative media render requires at least one existing video file")

            output_path = self._output_path(job, "final-video", ".mp4")
            command = self._build_ffmpeg_render_command(
                ffmpeg=ffmpeg,
                video_paths=video_paths,
                audio_paths=audio_paths,
                output_path=output_path,
                profile=dict(plan.get("renderProfile") or {}),
            )
            result = subprocess.run(command, capture_output=True, text=True, timeout=int(payload.get("timeoutSeconds") or 900), check=False)
            job["diagnostics"] = {
                "ffmpegPath": ffmpeg,
                "ffmpegReturnCode": result.returncode,
                "ffmpegCommand": command,
                "stderrTail": (result.stderr or "")[-4000:],
                "videoInputs": video_paths,
                "audioInputs": audio_paths,
            }
            if result.returncode != 0 or not output_path.exists():
                raise RuntimeError(f"ffmpeg render failed with code {result.returncode}")

            artifacts = [
                self._record_post_artifact(
                    file_path=output_path,
                    kind="video",
                    mime_type="video/mp4",
                    metadata={**scope, "creativeMediaRenderJobId": render_job_id, "creativeMediaEditPlanId": plan_id, "modality": "video"},
                )
            ]
            srt_path = self._write_srt(list((plan.get("tracks") or {}).get("subtitles") or []), job)
            if srt_path:
                artifacts.append(
                    self._record_post_artifact(
                        file_path=srt_path,
                        kind="subtitle",
                        mime_type="application/x-subrip",
                        metadata={**scope, "creativeMediaRenderJobId": render_job_id, "creativeMediaEditPlanId": plan_id, "modality": "subtitle"},
                    )
                )
            edl_path = self._output_path(job, "edit-decision-list", ".json")
            edl_path.write_text(json.dumps({"plan": plan, "renderJobId": render_job_id}, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts.append(
                self._record_post_artifact(
                    file_path=edl_path,
                    kind="report",
                    mime_type="application/json",
                    metadata={**scope, "creativeMediaRenderJobId": render_job_id, "creativeMediaEditPlanId": plan_id, "modality": "metadata"},
                )
            )
            job["status"] = "succeeded"
            job["artifacts"] = artifacts
            job["completedAt"] = utc_now_iso()
            return self._save_render_job(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_render_job(job)

    def _read_jobs(self) -> dict[str, Any]:
        payload = storage.read_json(JOB_STORE_FILE)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return {"version": 1, "jobs": {}}
        payload.setdefault("jobs", {})
        return payload

    def _write_jobs(self, payload: dict[str, Any]) -> None:
        storage.write_json(JOB_STORE_FILE, {"version": 1, "jobs": dict(payload.get("jobs") or {})})

    def _save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = self._read_jobs()
        jobs = dict(payload.get("jobs") or {})
        job["updatedAt"] = utc_now_iso()
        jobs[str(job["jobId"])] = job
        payload["jobs"] = jobs
        self._write_jobs(payload)
        return job

    def _scope_fields(self, request: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, str]:
        fallback = fallback or {}
        return {
            "projectId": str(request.get("projectId") or request.get("project_id") or fallback.get("projectId") or "").strip(),
            "workspaceId": str(request.get("workspaceId") or request.get("workspace_id") or fallback.get("workspaceId") or "").strip(),
            "workspacePath": str(request.get("workspacePath") or request.get("workspace_path") or fallback.get("workspacePath") or "").strip(),
        }

    def _new_job(self, *, modality: str, adapter: str, request: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "jobId": f"cm_{uuid.uuid4().hex}",
            **self._scope_fields(request),
            "modality": modality,
            "adapter": adapter,
            "status": "queued",
            "request": _jsonable_request(request),
            "providerTaskId": None,
            "artifacts": [],
            "error": None,
            "providerResponse": {},
            "createdAt": now,
            "updatedAt": now,
            "completedAt": None,
        }

    def get_job(self, job_id: str, *, refresh: bool = True) -> dict[str, Any] | None:
        job = (self._read_jobs().get("jobs") or {}).get(str(job_id))
        if not job:
            return None
        return job

    async def refresh_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id, refresh=False)
        if not job:
            return None
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        if job.get("adapter") == "volcengine_ark" and job.get("modality") == "video":
            return await self._poll_volcengine_video_job(job)
        return job

    def job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get_job(job_id, refresh=False) or {}
        return [dict(item) for item in list(job.get("artifacts") or []) if isinstance(item, dict)]

    def list_jobs(self, *, modality: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list((self._read_jobs().get("jobs") or {}).values())
        normalized_modality = str(modality or "").strip().lower()
        normalized_status = str(status or "").strip().lower()
        result: list[dict[str, Any]] = []
        for job in jobs:
            if normalized_modality and str(job.get("modality") or "").strip().lower() != normalized_modality:
                continue
            if normalized_status and str(job.get("status") or "").strip().lower() != normalized_status:
                continue
            result.append(dict(job))
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    async def create_job(self, request: dict[str, Any]) -> dict[str, Any]:
        modality = str(request.get("modality") or "").strip().lower()
        if modality not in SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported creative media modality: {modality or 'missing'}")
        if modality not in EXECUTABLE_MODALITIES:
            job = self._new_job(modality=modality, adapter="catalog_only", request=request)
            job["status"] = "failed"
            job["error"] = f"{modality} is catalog-only in P2; runtime execution is reserved for a later phase."
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        if modality == "image":
            return await self._create_image_job(request)
        if modality == "video":
            return await self._create_video_job(request)
        if modality == "voice":
            return await self._create_voice_job(request)
        raise ValueError(f"Unsupported creative media modality: {modality}")

    async def _create_image_job(self, request: dict[str, Any]) -> dict[str, Any]:
        adapter = str(request.get("adapter") or "").strip().lower()
        provider_id = str(request.get("providerId") or request.get("provider_id") or "").strip()
        if not adapter:
            adapter = "volcengine_ark" if "volc" in provider_id.lower() or str(request.get("provider") or "").lower() in {"volcengine", "seedream"} else "openai_images"
        job = self._new_job(modality="image", adapter=adapter, request=request)
        self._save_job(job)
        try:
            if adapter == "volcengine_ark":
                return await self._run_volcengine_image_job(job, request)
            if adapter == "openai_images":
                return await self._run_openai_image_job(job, request)
            raise ValueError(f"Unsupported image adapter: {adapter}")
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    async def _create_video_job(self, request: dict[str, Any]) -> dict[str, Any]:
        adapter = str(request.get("adapter") or "volcengine_ark").strip().lower()
        job = self._new_job(modality="video", adapter=adapter, request=request)
        self._save_job(job)
        try:
            if adapter != "volcengine_ark":
                raise ValueError(f"Unsupported video adapter: {adapter}")
            job = await self._submit_volcengine_video_job(job, request)
            if bool(request.get("wait", False)):
                timeout_seconds = max(15, min(int(request.get("timeoutSeconds") or request.get("timeout_seconds") or 240), 600))
                poll_interval = max(2, min(int(request.get("pollIntervalSeconds") or request.get("poll_interval_seconds") or 8), 30))
                deadline = asyncio.get_event_loop().time() + timeout_seconds
                while job.get("status") not in {"succeeded", "failed", "cancelled"} and asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(poll_interval)
                    job = await self._poll_volcengine_video_job(job)
                if job.get("status") not in {"succeeded", "failed", "cancelled"}:
                    job["status"] = "running"
                    self._save_job(job)
            return job
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    async def _create_voice_job(self, request: dict[str, Any]) -> dict[str, Any]:
        job = self._new_job(modality="voice", adapter="v8_audio_tts", request=request)
        self._save_job(job)
        try:
            text = str(request.get("text") or request.get("prompt") or "").strip()
            if not text:
                raise ValueError("voice job requires text")
            provider = TTSManager.get_provider()
            audio = bytearray()
            async for chunk in provider.synthesize_stream(text):
                if chunk:
                    audio.extend(chunk)
            if not audio:
                raise RuntimeError("TTS provider returned no audio bytes")
            path = self._output_path(job, "voice", ".mp3")
            path.write_bytes(bytes(audio))
            artifact = self._record_local_artifact(
                file_path=path,
                job=job,
                kind="audio",
                mime_type="audio/mpeg",
                metadata={"provider": "v8_audio_tts", "origin": "provider_result", "textLength": len(text)},
            )
            job["status"] = "succeeded"
            job["artifacts"] = [artifact]
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    def _mcp_volc_env(self) -> dict[str, str]:
        config = storage.read_json("config.json")
        servers = (((config.get("mcp") or {}).get("mcpServers") or {}) if isinstance(config, dict) else {})
        env = dict(((servers.get("jimeng_visual_generation") or {}).get("env") or {}))
        return {str(k): str(v) for k, v in env.items() if v is not None}

    def _volc_credentials(self) -> dict[str, str]:
        env = self._mcp_volc_env()
        api_key = str(os.getenv("VOLC_API_KEY") or env.get("VOLC_API_KEY") or "").strip()
        return {
            "apiKey": api_key,
            "baseUrl": str(os.getenv("VOLC_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
            "imageModel": str(os.getenv("VOLC_IMAGE_MODEL") or env.get("VOLC_IMAGE_MODEL") or "doubao-seedream-4-0-250828"),
            "videoModel": str(os.getenv("VOLC_VIDEO_MODEL") or env.get("VOLC_VIDEO_MODEL") or "doubao-seedance-1-0-pro-fast-251015"),
        }

    def _openai_image_provider(self, request: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        requested_provider = str(request.get("providerId") or request.get("provider_id") or "").strip()
        requested_model = str(request.get("model") or request.get("modelId") or request.get("model_id") or "gpt-image-2").strip()
        candidates: Iterable[tuple[str, dict[str, Any]]] = providers.items()
        if requested_provider:
            provider = providers.get(requested_provider)
            if not provider:
                raise ValueError(f"Provider not found: {requested_provider}")
            candidates = [(requested_provider, provider)]
        preferred: list[tuple[str, dict[str, Any]]] = []
        fallback: list[tuple[str, dict[str, Any]]] = []
        for provider_id, provider_data in candidates:
            models = dict((provider_data or {}).get("models") or {})
            if requested_model not in models:
                continue
            provider_meta = dict((provider_data or {}).get("provider") or {})
            name = str(provider_meta.get("name") or provider_id).lower()
            target = preferred if "local2" in name or "local2" in provider_id.lower() else fallback
            target.append((provider_id, provider_data))
        selected = (preferred or fallback)
        if not selected:
            raise ValueError(f"No configured provider exposes image model: {requested_model}")
        provider_id, provider_data = selected[0]
        return provider_id, dict((provider_data or {}).get("provider") or {}), requested_model

    async def _run_openai_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._openai_image_provider(request)
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image job requires prompt")
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "1K"),
            adapter="openai_images",
            explicit_size=request.get("size"),
        )
        response_format = str(request.get("responseFormat") or request.get("response_format") or "b64_json")
        payload = _build_openai_image_payload(model=model, prompt=prompt, size=size, response_format=response_format)
        response = await self._request_json(
            "POST",
            f"{base_url}/images/generations",
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(response, job=job, provider=provider_id, model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": provider_id, "model": model, "size": size}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _run_volcengine_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        creds = self._volc_credentials()
        if not creds["apiKey"]:
            raise ValueError("Volcengine API key not found in jimeng_visual_generation MCP env or VOLC_API_KEY")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image job requires prompt")
        model = str(request.get("model") or creds["imageModel"])
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "2K"),
            adapter="volcengine_ark",
            explicit_size=request.get("size"),
        )
        payload = _build_volcengine_image_payload(
            model=model,
            prompt=prompt,
            size=size,
            response_format=str(request.get("responseFormat") or request.get("response_format") or "url"),
            seed=int(request.get("seed", -1)),
            image_urls=request.get("imageUrls") or request.get("image_urls"),
        )
        response = await self._request_json(
            "POST",
            f"{creds['baseUrl']}/images/generations",
            headers=self._bearer_headers(creds["apiKey"]),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(response, job=job, provider="volcengine_seedream", model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": "volcengine_seedream", "model": model, "size": size}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _submit_volcengine_video_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        creds = self._volc_credentials()
        if not creds["apiKey"]:
            raise ValueError("Volcengine API key not found in jimeng_visual_generation MCP env or VOLC_API_KEY")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt and not (request.get("imageUrls") or request.get("image_urls")):
            raise ValueError("video job requires prompt or imageUrls")
        duration = max(1, min(int(request.get("duration") or request.get("durationSeconds") or request.get("duration_seconds") or 5), 30))
        payload = _build_volcengine_video_payload(
            model=str(request.get("model") or creds["videoModel"]),
            prompt=prompt,
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "16:9"),
            resolution=resolve_video_resolution(preset=request.get("resolutionPreset"), explicit_resolution=request.get("resolution") or "720p"),
            duration=duration,
            seed=int(request.get("seed", -1)),
            image_urls=request.get("imageUrls") or request.get("image_urls"),
            generate_audio=bool(request.get("generateAudio", request.get("generate_audio", True))),
            watermark=bool(request.get("watermark", False)),
        )
        response = await self._request_json(
            "POST",
            f"{creds['baseUrl']}/contents/generations/tasks",
            headers=self._bearer_headers(creds["apiKey"]),
            json=payload,
            timeout=180,
        )
        task_id = str(response.get("id") or response.get("task_id") or response.get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError(f"Volcengine video response did not include a task id: {response}")
        job["status"] = "running"
        job["providerTaskId"] = task_id
        job["providerResponse"] = {"providerId": "volcengine_seedance", "taskId": task_id, "model": payload["model"]}
        return self._save_job(job)

    async def _poll_volcengine_video_job(self, job: dict[str, Any]) -> dict[str, Any]:
        creds = self._volc_credentials()
        task_id = str(job.get("providerTaskId") or "").strip()
        if not task_id:
            job["status"] = "failed"
            job["error"] = "Missing providerTaskId"
            return self._save_job(job)
        response = await self._request_json(
            "GET",
            f"{creds['baseUrl']}/contents/generations/tasks/{task_id}",
            headers=self._bearer_headers(creds["apiKey"]),
            timeout=60,
        )
        status = normalize_provider_status(response.get("status"), provider="volcengine_seedance")
        job["status"] = status
        job["providerResponse"] = {**dict(job.get("providerResponse") or {}), "lastStatus": response.get("status"), "taskId": task_id}
        if status == "succeeded":
            video_url = (((response.get("content") or {}) if isinstance(response, dict) else {}).get("video_url") or "")
            if not video_url:
                job["status"] = "failed"
                job["error"] = "Volcengine video task succeeded without content.video_url"
            else:
                artifact = await self._artifact_from_url(video_url, job=job, kind="video", provider="volcengine_seedance", mime_hint="video/mp4")
                job["artifacts"] = [artifact]
                job["completedAt"] = utc_now_iso()
        elif status == "failed":
            job["error"] = str((response.get("error") or {}).get("message") or response.get("error") or "Volcengine video task failed")
            job["completedAt"] = utc_now_iso()
        return self._save_job(job)

    async def _artifact_from_image_response(self, response: dict[str, Any], *, job: dict[str, Any], provider: str, model: str, mime_hint: str) -> dict[str, Any]:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"Image response did not contain data[]: {response}")
        first = dict(data[0] or {})
        if first.get("b64_json"):
            return self._artifact_from_b64(str(first["b64_json"]), job=job, kind="image", provider=provider, mime_type=mime_hint, extension=".png", metadata={"model": model})
        if first.get("url"):
            return await self._artifact_from_url(str(first["url"]), job=job, kind="image", provider=provider, mime_hint=mime_hint, metadata={"model": model})
        raise RuntimeError("Image response contained neither url nor b64_json")

    def _artifact_from_b64(self, payload: str, *, job: dict[str, Any], kind: str, provider: str, mime_type: str, extension: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        raw = payload.split(",", 1)[1] if payload.startswith("data:") and "," in payload else payload
        data = base64.b64decode(raw)
        path = self._output_path(job, kind, extension)
        path.write_bytes(data)
        return self._record_local_artifact(file_path=path, job=job, kind=kind, mime_type=mime_type, metadata={"provider": provider, "origin": "provider_result", **dict(metadata or {})})

    async def _artifact_from_url(self, url: str, *, job: dict[str, Any], kind: str, provider: str, mime_hint: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        headers = {"User-Agent": "V8-Agent-OS-CreativeMedia/1.0"}
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            request = client.build_request("GET", url, headers=headers)
            response = await client.send(request, stream=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or mime_hint
            extension = self._extension_for_url(url, content_type, kind)
            path = self._output_path(job, kind, extension)
            try:
                with open(path, "wb") as file:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            file.write(chunk)
            finally:
                await response.aclose()
        parsed = urlparse(url)
        return self._record_local_artifact(
            file_path=path,
            job=job,
            kind=kind,
            mime_type=content_type,
            metadata={
                "provider": provider,
                "origin": "provider_result",
                "sourceHost": parsed.netloc,
                "sourceUrlHash": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                **dict(metadata or {}),
            },
        )

    def _record_local_artifact(self, *, file_path: Path, job: dict[str, Any], kind: str, mime_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        artifact = artifact_store.record_artifact(
            artifact_kind=kind,
            mime_type=mime_type,
            title=file_path.name,
            source_path=str(file_path),
            metadata={
                **metadata,
                "creativeMediaJobId": job["jobId"],
                "modality": job["modality"],
                "projectId": job.get("projectId") or "",
                "workspaceId": job.get("workspaceId") or "",
                "workspacePath": job.get("workspacePath") or "",
                "pathPlane": "runtime",
                "storageClass": "runtime_artifact",
                "surfaceVisible": True,
            },
            source_component="creative_media_runtime",
            node="creative_media_runtime",
        )
        return artifact

    def _output_path(self, owner: dict[str, Any] | str, kind: str, extension: str) -> Path:
        if isinstance(owner, dict):
            owner_id = str(owner.get("jobId") or owner.get("renderJobId") or owner.get("planId") or "job")
            workspace_path = str(owner.get("workspacePath") or "").strip()
        else:
            owner_id = str(owner)
            workspace_path = ""
        if workspace_path:
            root = Path(workspace_path).expanduser() / "creative_media" / _safe_filename(owner_id, "job")
        else:
            root = storage.base_dir / "workspace" / "creative_media" / _safe_filename(owner_id, "job")
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{_safe_filename(kind, 'media')}-{uuid.uuid4().hex[:8]}{extension}"

    def _extension_for_url(self, url: str, content_type: str, kind: str) -> str:
        suffix = Path(urlparse(url).path).suffix
        if suffix and len(suffix) <= 8:
            return suffix
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            return guessed
        return {"image": ".png", "video": ".mp4", "audio": ".mp3"}.get(kind, ".bin")

    def _bearer_headers(self, api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def _request_json(self, method: str, url: str, *, headers: Optional[dict[str, str]] = None, json: Optional[dict[str, Any]] = None, timeout: float = 120.0) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, json=json)
            if response.status_code >= 400:
                raise RuntimeError(f"Provider request failed ({response.status_code}) at {url}: {response.text[:500]}")
            return response.json()


creative_media_runtime = runtime_registry.register(CreativeMediaRuntime())

__all__ = [
    "CreativeMediaRuntime",
    "creative_media_runtime",
    "_build_openai_image_payload",
    "_build_volcengine_image_payload",
    "_build_volcengine_video_payload",
    "normalize_provider_status",
]
