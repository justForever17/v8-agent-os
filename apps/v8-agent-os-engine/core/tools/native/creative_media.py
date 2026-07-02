from __future__ import annotations

import json
import re
from typing import Any, Optional

from langchain_core.tools import tool

__all__ = [
    "_creative_media_artifact_summary",
    "_creative_media_job_summary",
    "_creative_media_job_detail",
    "_creative_media_asset_summary",
    "_creative_media_character_bible_summary",
    "_creative_media_recipe_summary",
    "_creative_media_work_order_summary",
    "_creative_media_edit_plan_summary",
    "_creative_media_render_summary",
    "_creative_media_quality_job_summary",
    "_creative_media_cost_entry_summary",
    "_creative_media_safety_event_summary",
    "creative_media_catalog",
    "creative_media_resolutions",
    "creative_media_create_job",
    "creative_media_get_job",
    "creative_media_list_jobs",
    "creative_media_job_artifacts",
    "creative_media_compile_recipe",
    "creative_media_compile_work_order",
    "creative_media_list_work_orders",
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
    "creative_media_create_quality_job",
    "creative_media_list_quality_jobs",
    "creative_media_get_quality_job",
    "creative_media_retry_job",
    "creative_media_cost_ledger",
    "creative_media_safety_events",
    "creative_media_production_pack",
    "creative_media_rank_models",
    "creative_media_reference_media_brief",
    "creative_media_sample_approval_packet",
    "creative_media_qa_check",
]

_AGENT_DETAIL_LIST_LIMIT = 6


def _agent_preview_text(value: Any, *, limit: int = 700) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _agent_compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        compact[key] = value
    return compact


def _agent_limited_list(values: Any, *, limit: int = 20) -> list[Any]:
    return list(values or [])[: max(0, int(limit))]


def _agent_signal_flags(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flags: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            if value:
                flags[key] = True
        elif isinstance(value, (int, float)):
            if value:
                flags[key] = value
        elif isinstance(value, str):
            if value.strip():
                flags[key] = value.strip()
        elif isinstance(value, list):
            items = [item for item in value if item not in (None, "")]
            if items:
                flags[key] = items[:8]
        elif isinstance(value, dict):
            nested = _agent_signal_flags(value)
            if nested:
                flags[key] = nested
    return flags


def _agent_compact_signal_bundle(*payloads: Any) -> dict[str, Any]:
    bundle: dict[str, Any] = {}
    for payload in payloads:
        bundle.update(_agent_signal_flags(payload))
    return bundle


def _agent_count_nodes(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_agent_count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_agent_count_nodes(item) for item in value)
    return 1 if value not in (None, "") else 0


def _creative_media_artifact_summary(artifact: Any, *, detail: bool = False) -> dict[str, Any]:
    from runtimes.creative_media.tool_surface import artifact_summary

    return artifact_summary(artifact, detail=detail)


def _creative_media_job_summary(job: Any) -> dict[str, Any]:
    from runtimes.creative_media.tool_surface import job_summary

    return job_summary(job)


def _creative_media_job_detail(job: Any) -> dict[str, Any]:
    from runtimes.creative_media.tool_surface import job_detail

    return job_detail(job)


def _creative_media_asset_summary(asset: Any) -> dict[str, Any]:
    if not isinstance(asset, dict):
        return {}
    return _agent_compact_dict(
        {
            "assetId": asset.get("assetId"),
            "keyframeId": asset.get("keyframeId"),
            "characterBibleId": asset.get("characterBibleId"),
            "projectId": asset.get("projectId"),
            "workspaceId": asset.get("workspaceId"),
            "role": asset.get("role"),
            "modality": asset.get("modality"),
            "assetPlane": asset.get("assetPlane"),
            "artifactId": asset.get("artifactId"),
            "title": asset.get("title"),
            "label": asset.get("label"),
            "recipeId": asset.get("recipeId"),
            "createdAt": asset.get("createdAt"),
            "updatedAt": asset.get("updatedAt"),
        }
    )


def _creative_media_character_bible_summary(bible: Any) -> dict[str, Any]:
    if not isinstance(bible, dict):
        return {}
    return _agent_compact_dict(
        {
            "characterBibleId": bible.get("characterBibleId"),
            "projectId": bible.get("projectId"),
            "workspaceId": bible.get("workspaceId"),
            "name": bible.get("name"),
            "role": bible.get("role"),
            "modality": bible.get("modality"),
            "descriptionPreview": _agent_preview_text(
                bible.get("description") or bible.get("appearance") or bible.get("prompt"),
                limit=500,
            ),
            "referenceAssetIds": _agent_limited_list(bible.get("referenceAssetIds"), limit=8),
            "createdAt": bible.get("createdAt"),
            "updatedAt": bible.get("updatedAt"),
        }
    )


def _creative_media_recipe_summary(recipe: Any) -> dict[str, Any]:
    if not isinstance(recipe, dict):
        return {}
    provider_payload = dict(recipe.get("providerPayload") or {})
    return _agent_compact_dict(
        {
            "recipeId": recipe.get("recipeId"),
            "projectId": recipe.get("projectId"),
            "workspaceId": recipe.get("workspaceId"),
            "workspacePath": recipe.get("workspacePath"),
            "version": recipe.get("version"),
            "modality": recipe.get("modality"),
            "recipeKind": recipe.get("recipeKind"),
            "providerId": recipe.get("providerId") or provider_payload.get("providerId"),
            "model": recipe.get("model") or provider_payload.get("model"),
            "operationKind": recipe.get("operationKind") or provider_payload.get("operationKind"),
            "promptPreview": _agent_preview_text(recipe.get("prompt") or provider_payload.get("prompt"), limit=700),
            "negativePromptPreview": _agent_preview_text(recipe.get("negativePrompt") or provider_payload.get("negativePrompt"), limit=280),
            "assetRefs": _agent_limited_list(recipe.get("assetRefs"), limit=8),
            "createdAt": recipe.get("createdAt"),
            "updatedAt": recipe.get("updatedAt"),
        }
    )


def _creative_media_work_order_summary(work_order: Any) -> dict[str, Any]:
    if not isinstance(work_order, dict):
        return {}
    provider_plan = dict(work_order.get("providerPlan") or {})
    image_plan = dict(provider_plan.get("imageGeneration") or provider_plan.get("imageStoryboard") or {})
    video_plan = dict(provider_plan.get("videoGeneration") or {})
    image_primary = dict(image_plan.get("primary") or {})
    video_primary = dict(video_plan.get("primary") or {})
    return _agent_compact_dict(
        {
            "workOrderId": work_order.get("workOrderId"),
            "status": work_order.get("status"),
            "workOrderKind": work_order.get("workOrderKind"),
            "modality": work_order.get("modality"),
            "assetRole": work_order.get("assetRole"),
            "requestingRuntime": work_order.get("requestingRuntime"),
            "briefPreview": _agent_preview_text(work_order.get("brief"), limit=500),
            "recipeRefs": _agent_limited_list(work_order.get("recipeRefs"), limit=6),
            "artifactRefs": _agent_limited_list(work_order.get("artifactRefs"), limit=6),
            "shotCount": len(list(work_order.get("shotPlan") or [])),
            "storyboardAssetCount": len(list(work_order.get("storyboardAssets") or [])),
            "imageModel": image_primary.get("modelId"),
            "videoModel": video_primary.get("modelId"),
            "videoOperationKind": video_plan.get("operationKind"),
            "capabilityGaps": [
                item.get("capabilityGap")
                for item in (image_plan, video_plan)
                if isinstance(item, dict) and item.get("capabilityGap")
            ],
            "safetyStatus": work_order.get("safetyStatus"),
            "costEstimate": work_order.get("costEstimate"),
            "dryRunOnly": work_order.get("dryRunOnly"),
            "createdAt": work_order.get("createdAt"),
            "updatedAt": work_order.get("updatedAt"),
        }
    )


def _creative_media_edit_plan_summary(plan: Any, *, detail: bool = False) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    timeline = list(plan.get("timeline") or [])
    tracks = dict(plan.get("tracks") or {})
    subtitles = list(plan.get("subtitles") or [])
    payload = {
        "planId": plan.get("planId"),
        "projectId": plan.get("projectId"),
        "workspaceId": plan.get("workspaceId"),
        "recipeId": plan.get("recipeId"),
        "status": plan.get("status"),
        "timelineCount": len(timeline),
        "trackCounts": {key: len(list(value or [])) for key, value in tracks.items()},
        "subtitleCount": len(subtitles),
        "createdAt": plan.get("createdAt"),
        "updatedAt": plan.get("updatedAt"),
        "detailTool": "creative_media_get_edit_plan(plan_id=...)",
    }
    if detail:
        payload["workspacePath"] = plan.get("workspacePath")
        payload["output"] = plan.get("output")
        payload["qualityGates"] = plan.get("qualityGates")
        payload["timeline"] = [
            _agent_compact_dict(
                {
                    "clipId": item.get("clipId"),
                    "assetId": item.get("assetId"),
                    "artifactId": item.get("artifactId"),
                    "sourcePath": item.get("sourcePath"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "durationSeconds": item.get("durationSeconds"),
                    "role": item.get("role"),
                    "title": item.get("title"),
                }
            )
            for item in timeline[:_AGENT_DETAIL_LIST_LIMIT]
            if isinstance(item, dict)
        ]
    return _agent_compact_dict(payload)


def _creative_media_render_summary(render: Any, *, detail: bool = False) -> dict[str, Any]:
    if not isinstance(render, dict):
        return {}
    artifacts = list(render.get("artifacts") or [])
    execution = dict(render.get("execution") or {})
    payload = {
        "renderJobId": render.get("renderJobId"),
        "projectId": render.get("projectId"),
        "workspaceId": render.get("workspaceId"),
        "planId": render.get("planId"),
        "status": render.get("status"),
        "artifactCount": len(artifacts),
        "returnCode": execution.get("returnCode") or execution.get("returncode"),
        "error": _agent_preview_text(render.get("error"), limit=360),
        "createdAt": render.get("createdAt"),
        "updatedAt": render.get("updatedAt"),
        "completedAt": render.get("completedAt"),
        "detailTool": "creative_media_get_render(render_job_id=...)",
    }
    if detail:
        stderr_text = str(execution.get("stderr") or render.get("stderrTail") or "")
        payload["workspacePath"] = render.get("workspacePath")
        payload["artifacts"] = [_creative_media_artifact_summary(item, detail=True) for item in artifacts[:4] if isinstance(item, dict)]
        payload["outputPath"] = render.get("outputPath") or render.get("output")
        payload["stdoutPreview"] = _agent_preview_text(execution.get("stdout") or render.get("stdoutTail"), limit=500)
        payload["stderrPreview"] = _agent_preview_text(stderr_text, limit=700)
        payload["stderrOmittedChars"] = max(0, len(stderr_text) - 700)
        payload["inputs"] = _agent_limited_list(render.get("inputs"), limit=_AGENT_DETAIL_LIST_LIMIT)
    return _agent_compact_dict(payload)


def _creative_media_quality_job_summary(job: Any) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {}
    return _agent_compact_dict(
        {
            "qualityJobId": job.get("qualityJobId"),
            "jobId": job.get("jobId"),
            "renderJobId": job.get("renderJobId"),
            "status": job.get("status"),
            "checks": [
                _agent_compact_dict(
                    {
                        "name": item.get("name"),
                        "ok": item.get("ok"),
                        "reason": _agent_preview_text(item.get("reason"), limit=220),
                        "path": item.get("path"),
                    }
                )
                for item in list(job.get("checks") or [])[:8]
                if isinstance(item, dict)
            ],
            "summary": _agent_preview_text(job.get("summary") or job.get("reason"), limit=360),
            "createdAt": job.get("createdAt"),
            "updatedAt": job.get("updatedAt"),
        }
    )


def _creative_media_cost_entry_summary(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    return _agent_compact_dict(
        {
            "jobId": entry.get("jobId"),
            "providerId": entry.get("providerId"),
            "model": entry.get("model"),
            "modality": entry.get("modality"),
            "operationKind": entry.get("operationKind"),
            "currency": entry.get("currency"),
            "cost": entry.get("cost") or entry.get("amount"),
            "usage": _agent_compact_dict(
                {
                    "promptTokens": entry.get("promptTokens"),
                    "completionTokens": entry.get("completionTokens"),
                    "inputUnits": entry.get("inputUnits"),
                    "outputUnits": entry.get("outputUnits"),
                    "durationSeconds": entry.get("durationSeconds"),
                }
            ),
            "createdAt": entry.get("createdAt"),
        }
    )


def _creative_media_safety_event_summary(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    return _agent_compact_dict(
        {
            "eventId": event.get("eventId"),
            "jobId": event.get("jobId"),
            "modality": event.get("modality"),
            "operationKind": event.get("operationKind"),
            "providerId": event.get("providerId"),
            "action": event.get("action") or event.get("decision"),
            "status": event.get("status"),
            "reason": _agent_preview_text(event.get("reason"), limit=300),
            "promptPreview": _agent_preview_text(
                event.get("prompt") or event.get("originalPrompt") or event.get("rewrittenPrompt"),
                limit=500,
            ),
            "createdAt": event.get("createdAt"),
        }
    )


@tool
def creative_media_catalog(
    modality: Optional[str] = None,
    operation_kind: Optional[str] = None,
    detail_level: str = "summary",
    limit: int = 20,
    cursor: Optional[str] = None,
) -> str:
    """Return 多媒体创作 provider/model catalog and adapter capabilities.

    Use when choosing which image/video/audio/music/3D provider can satisfy a
    media task. Keep this as the internal capability lookup; use 多媒体创作 in
    user-facing wording and do not dump the full provider catalog into chat.
    """
    try:
        from runtimes.creative_media.runtime import creative_media_runtime
        from runtimes.creative_media.tool_surface import catalog_presenter

        catalog = creative_media_runtime.catalog()
        payload = catalog_presenter(
            catalog,
            modality=modality,
            operation_kind=operation_kind,
            detail_level=detail_level,
            limit=limit,
            cursor=cursor,
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia catalog: {str(e)}"


@tool
def creative_media_resolutions(detail_level: str = "summary") -> str:
    """Return 多媒体创作 resolution presets for image and video generation."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        normalized_detail = str(detail_level or "summary").strip().lower()
        payload = dict(creative_media_runtime.resolutions() or {})
        if normalized_detail not in {"matrix", "detail", "diagnostic", "full"}:
            image_presets = dict((payload.get("image") or {}).get("presets") or {})
            video_presets = dict((payload.get("video") or {}).get("presets") or {})
            payload = {
                "ok": True,
                "detailLevel": "summary",
                "summary": "常用创意媒体分辨率预设；完整矩阵按需读取。",
                "ratios": _agent_limited_list(payload.get("ratios"), limit=8),
                "imagePresets": {
                    key: value
                    for key, value in image_presets.items()
                    if key in {"1K", "2K"}
                },
                "videoPresets": {
                    key: value
                    for key, value in video_presets.items()
                    if key in {"720P", "1080P"}
                },
                "detailTool": "creative_media_resolutions(detail_level='matrix')",
            }
        else:
            payload["detailLevel"] = normalized_detail
        return json.dumps(_agent_compact_dict(payload), ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia resolutions: {str(e)}"


@tool
def creative_media_production_pack(request: dict[str, Any]) -> str:
    """Build or update a clean 多媒体创作 delivery pack.

    Use this as the production contract and final handoff checklist for complex media work. The pack has
    stable stages: brief, proposal, script, scene_plan, asset_manifest,
    edit_decisions, render_report, final_review. Keep sample approval,
    provider lock, artifact proof, and QA status here instead of scattering
    them across raw job outputs. A complex delivery is not complete until the
    subagent handoff preserves providerLock, sampleApproval, artifactProof, and qa.
    """
    try:
        from runtimes.creative_media.production_pack import build_production_pack, production_pack_markdown

        return production_pack_markdown(build_production_pack(dict(request or {})))
    except Exception as e:
        return f"Error building CreativeMediaProductionPack: {str(e)}"


@tool
def creative_media_rank_models(
    modality: Optional[str] = None,
    operation_kind: Optional[str] = None,
    goal: Optional[str] = None,
    limit: int = 8,
) -> str:
    """Return a clean Markdown ranking of 多媒体创作 model candidates.

    Use this before locking a provider/model. This is the agent-readable
    selector surface; it intentionally avoids dumping the full Model Hub catalog
    or provider JSON.
    """
    try:
        from runtimes.creative_media.production_pack import rank_candidates_markdown
        from runtimes.creative_media.runtime import creative_media_runtime

        candidates = creative_media_runtime.list_model_candidates()
        return rank_candidates_markdown(
            candidates,
            modality=modality,
            operation_kind=operation_kind,
            goal=goal,
            limit=limit,
        )
    except Exception as e:
        return f"Error ranking CreativeMedia models: {str(e)}"


@tool
def creative_media_reference_media_brief(request: dict[str, Any]) -> str:
    """Create a 多媒体创作 reference media preflight brief.

    Use before generation when the task has reference audio/image/video/files.
    The brief tracks audio transcript, visual style, shot structure, and reusable
    assets. Fill missing analysis by calling vision_media_analyzer or file read
    tools before batch generation. Missing analysis is a blocker for batch work,
    not a reason to skip the reference.
    """
    try:
        from runtimes.creative_media.production_pack import build_reference_media_pack, reference_media_markdown

        return reference_media_markdown(build_reference_media_pack(dict(request or {})))
    except Exception as e:
        return f"Error building CreativeMedia reference brief: {str(e)}"


@tool
def creative_media_sample_approval_packet(request: dict[str, Any]) -> str:
    """Prepare 多媒体创作 sample media approval input for ask_user.

    Use after producing sample images/video/audio/music/3D previews and before
    batch production. The returned Markdown tells the worker which question,
    media/artifacts, selection mode, and multi-step questions to pass into
    ask_user; it is not a separate approval system. Write the user decision back
    to ProductionPack.sampleApproval before continuing.
    """
    try:
        from runtimes.creative_media.production_pack import build_sample_approval_packet, sample_approval_markdown

        return sample_approval_markdown(build_sample_approval_packet(dict(request or {})))
    except Exception as e:
        return f"Error building CreativeMedia sample approval packet: {str(e)}"


@tool
def creative_media_qa_check(request: dict[str, Any]) -> str:
    """Run a local QA checklist over 多媒体创作 artifacts.

    Checks file existence, basic playability metadata when ffprobe is available,
    duration/resolution/audio-stream hints, subtitle file presence, and required
    artifact kinds. Returns Markdown for the agent; raw provider payloads stay
    out of the visible surface. Run this before claiming a complex media delivery
    is complete.
    """
    try:
        from runtimes.creative_media.production_pack import artifact_qa_markdown, run_artifact_qa

        return artifact_qa_markdown(run_artifact_qa(dict(request or {})))
    except Exception as e:
        return f"Error running CreativeMedia QA: {str(e)}"


@tool
async def creative_media_create_job(request: dict[str, Any]) -> str:
    """Create a real 多媒体创作 generation job.

    Use `request.modality` plus `request.operationKind`:
    images/videos use image/video operation kinds; `music.generate`/`music.cover`
    create music; `model3d.generate` creates 3D assets; `voice.tts` creates
    narration/voice-over audio artifacts; `voice.design` designs a reusable
    voice_id plus audition artifact. Creative Media voice jobs are media assets,
    not the chat `<voice>text</voice>` bubble TTS protocol. Useful fields include
    `prompt`, optional `providerId`/`modelId`, and modality-specific refs such as
    `imageUrl`, `audioUrl`, `resultFormat`, `voiceId`, or `previewText`.
    If the job is async, call `creative_media_get_job(job_id=...)` until it
    succeeds/fails, then call `creative_media_job_artifacts(job_id=...)`.
    """
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        job = await creative_media_runtime.create_job(dict(request or {}))
        return json.dumps({"job": _creative_media_job_detail(job)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia job: {str(e)}"


@tool
async def creative_media_get_job(job_id: str, refresh: bool = True) -> str:
    """Get or refresh a 多媒体创作 job.

    Use after `creative_media_create_job`. Keep `refresh=True` to poll supported
    async providers. When status is `succeeded`, read artifacts with
    `creative_media_job_artifacts(job_id=...)`; when failed/degraded, report the
    visible error, limitations, and next safe action instead of provider raw JSON.
    """
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        job = await creative_media_runtime.refresh_job(job_id) if refresh else creative_media_runtime.get_job(job_id, refresh=False)
        if not job:
            return f"Error: CreativeMedia job not found: {job_id}"
        return json.dumps({"job": _creative_media_job_detail(job)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia job: {str(e)}"


@tool
def creative_media_list_jobs(modality: Optional[str] = None, status: Optional[str] = None, limit: int = 20, detail_level: str = "summary") -> str:
    """List CreativeMediaRuntime jobs, optionally filtered by modality or status."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        jobs = creative_media_runtime.list_jobs(modality=modality, status=status)
        normalized_detail = str(detail_level or "summary").strip().lower()
        effective_limit = max(1, min(int(limit or 20), 50))
        if normalized_detail == "summary":
            effective_limit = min(effective_limit, 5)
        status_counts: dict[str, int] = {}
        for item in jobs:
            state = str((item or {}).get("status") or "unknown")
            status_counts[state] = status_counts.get(state, 0) + 1
        return json.dumps(
            {
                "ok": True,
                "summary": "Creative Media jobs listed; use detailTool for a single job.",
                "statusCounts": status_counts,
                "jobs": [_creative_media_job_summary(item) for item in jobs[:effective_limit]],
                "count": len(jobs),
                "limit": effective_limit,
                "hasMore": len(jobs) > effective_limit,
                "detailTool": "creative_media_get_job(job_id=...)",
                "omittedFields": [
                    "fullRequest",
                    "prompt",
                    "providerResponse",
                    "timeline",
                    "updatedAt",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia jobs: {str(e)}"


@tool
def creative_media_job_artifacts(job_id: str) -> str:
    """List deliverable artifact refs for a 多媒体创作 job.

    Use this after a job succeeds. Return artifact IDs, kind, file type, and
    download/detail refs to the Supervisor. Do not use provider URLs or
    provider raw JSON as the final deliverable.
    """
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        return json.dumps(
            {"artifacts": [_creative_media_artifact_summary(item) for item in creative_media_runtime.job_artifacts(job_id)]},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error reading CreativeMedia job artifacts: {str(e)}"


@tool
def creative_media_compile_recipe(request: dict[str, Any]) -> str:
    """Compile an image, video, voice, or music recipe without calling an LLM or media provider."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        recipe = creative_media_runtime.compile_recipe(dict(request or {}))
        return json.dumps({"recipe": _creative_media_recipe_summary(recipe)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error compiling CreativeMedia recipe: {str(e)}"


@tool
def creative_media_compile_work_order(request: dict[str, Any]) -> str:
    """Compile a CreativeAssetRequest into a dry-run work order for simple assets or storyboard-driven video."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        work_order = creative_media_runtime.compile_work_order(dict(request or {}))
        return json.dumps({"workOrder": _creative_media_work_order_summary(work_order)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error compiling CreativeMedia work order: {str(e)}"


@tool
def creative_media_list_work_orders(status: Optional[str] = None, requesting_runtime: Optional[str] = None, limit: int = 20) -> str:
    """List CreativeMedia work orders produced for upstream runtimes."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        work_orders = creative_media_runtime.list_work_orders(status=status, requesting_runtime=requesting_runtime)
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "workOrders": [_creative_media_work_order_summary(item) for item in work_orders[:effective_limit]],
                "count": len(work_orders),
                "limit": effective_limit,
                "hasMore": len(work_orders) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia work orders: {str(e)}"


@tool
def creative_media_get_recipe(recipe_id: str) -> str:
    """Read a compiled CreativeMedia recipe by recipe id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        recipe = creative_media_runtime.get_recipe(recipe_id)
        if not recipe:
            return f"Error: CreativeMedia recipe not found: {recipe_id}"
        return json.dumps({"recipe": _creative_media_recipe_summary(recipe)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia recipe: {str(e)}"


@tool
def creative_media_list_recipes(modality: Optional[str] = None, recipe_kind: Optional[str] = None, limit: int = 20) -> str:
    """List compiled CreativeMedia recipes, optionally filtered by modality or recipe kind."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        recipes = creative_media_runtime.list_recipes(modality=modality, recipe_kind=recipe_kind)
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "recipes": [_creative_media_recipe_summary(item) for item in recipes[:effective_limit]],
                "count": len(recipes),
                "limit": effective_limit,
                "hasMore": len(recipes) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia recipes: {str(e)}"


@tool
def creative_media_register_asset(request: dict[str, Any]) -> str:
    """Register an existing artifact/path as a CreativeMedia asset ledger entry without copying the file."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        asset = creative_media_runtime.register_asset(dict(request or {}))
        return json.dumps({"asset": _creative_media_asset_summary(asset)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error registering CreativeMedia asset: {str(e)}"


@tool
def creative_media_list_assets(modality: Optional[str] = None, role: Optional[str] = None, limit: int = 20) -> str:
    """List CreativeMedia asset ledger entries, optionally filtered by modality and role."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        assets = creative_media_runtime.list_assets(modality=modality, role=role)
        effective_limit = max(1, min(int(limit or 20), 5))
        return json.dumps(
            {
                "ok": True,
                "summary": "Creative Media assets listed; source paths and provider details stay in detail records/raw evidence.",
                "assets": [_creative_media_asset_summary(item) for item in assets[:effective_limit]],
                "count": len(assets),
                "limit": effective_limit,
                "hasMore": len(assets) > effective_limit,
                "detailTool": "Use related creative_media_get_* tools or rawRef for source paths and full ledger details.",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia assets: {str(e)}"


@tool
def creative_media_create_character_bible(request: dict[str, Any]) -> str:
    """Create or update a CreativeMedia character bible entry for character consistency."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        bible = creative_media_runtime.create_character_bible(dict(request or {}))
        return json.dumps({"characterBible": _creative_media_character_bible_summary(bible)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia character bible: {str(e)}"


@tool
def creative_media_get_character_bible(character_bible_id: str) -> str:
    """Read a CreativeMedia character bible by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        bible = creative_media_runtime.get_character_bible(character_bible_id)
        if not bible:
            return f"Error: CreativeMedia character bible not found: {character_bible_id}"
        return json.dumps({"characterBible": _creative_media_character_bible_summary(bible)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia character bible: {str(e)}"


@tool
def creative_media_list_character_bibles(limit: int = 20) -> str:
    """List CreativeMedia character bibles."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        bibles = creative_media_runtime.list_character_bibles()
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "characterBibles": [_creative_media_character_bible_summary(item) for item in bibles[:effective_limit]],
                "count": len(bibles),
                "limit": effective_limit,
                "hasMore": len(bibles) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia character bibles: {str(e)}"


@tool
def creative_media_register_keyframe(request: dict[str, Any]) -> str:
    """Register an artifact/path as a CreativeMedia keyframe without copying the file."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        keyframe = creative_media_runtime.register_keyframe(dict(request or {}))
        return json.dumps({"keyframe": _creative_media_asset_summary(keyframe)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error registering CreativeMedia keyframe: {str(e)}"


@tool
def creative_media_get_keyframe(keyframe_id: str) -> str:
    """Read a CreativeMedia keyframe by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        keyframe = creative_media_runtime.get_keyframe(keyframe_id)
        if not keyframe:
            return f"Error: CreativeMedia keyframe not found: {keyframe_id}"
        return json.dumps({"keyframe": _creative_media_asset_summary(keyframe)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia keyframe: {str(e)}"


@tool
def creative_media_list_keyframes(
    recipe_id: Optional[str] = None,
    role: Optional[str] = None,
    character_bible_id: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List CreativeMedia keyframes with optional recipe, role, or character bible filters."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        keyframes = creative_media_runtime.list_keyframes(
            recipe_id=recipe_id,
            role=role,
            character_bible_id=character_bible_id,
        )
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "keyframes": [_creative_media_asset_summary(item) for item in keyframes[:effective_limit]],
                "count": len(keyframes),
                "limit": effective_limit,
                "hasMore": len(keyframes) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia keyframes: {str(e)}"


@tool
def creative_media_create_edit_plan(request: dict[str, Any]) -> str:
    """Create a CreativeMedia P3 edit plan from registered video/audio assets and optional subtitles."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        plan = creative_media_runtime.create_edit_plan(dict(request or {}))
        return json.dumps({"editPlan": _creative_media_edit_plan_summary(plan, detail=True)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia edit plan: {str(e)}"


@tool
def creative_media_get_edit_plan(plan_id: str) -> str:
    """Read a CreativeMedia edit plan by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        plan = creative_media_runtime.get_edit_plan(plan_id)
        if not plan:
            return f"Error: CreativeMedia edit plan not found: {plan_id}"
        return json.dumps({"editPlan": _creative_media_edit_plan_summary(plan, detail=True)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia edit plan: {str(e)}"


@tool
def creative_media_list_edit_plans(recipe_id: Optional[str] = None, limit: int = 20) -> str:
    """List CreativeMedia edit plans, optionally filtered by recipe id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        plans = creative_media_runtime.list_edit_plans(recipe_id=recipe_id)
        effective_limit = max(1, min(int(limit or 20), 5))
        status_counts: dict[str, int] = {}
        for item in plans:
            state = str((item or {}).get("status") or "unknown")
            status_counts[state] = status_counts.get(state, 0) + 1
        return json.dumps(
            {
                "ok": True,
                "summary": "Creative Media edit plans listed; timeline, paths, and quality gates are available through detailTool.",
                "statusCounts": status_counts,
                "editPlans": [_creative_media_edit_plan_summary(item) for item in plans[:effective_limit]],
                "count": len(plans),
                "limit": effective_limit,
                "hasMore": len(plans) > effective_limit,
                "detailTool": "creative_media_get_edit_plan(plan_id=...)",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia edit plans: {str(e)}"


@tool
def creative_media_render_edit_plan(request: dict[str, Any]) -> str:
    """Render a CreativeMedia edit plan locally through ffmpeg and record output artifacts."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        render = creative_media_runtime.render_edit_plan(dict(request or {}))
        return json.dumps({"render": _creative_media_render_summary(render, detail=True)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error rendering CreativeMedia edit plan: {str(e)}"


@tool
def creative_media_get_render(render_job_id: str) -> str:
    """Read a CreativeMedia render job by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        render = creative_media_runtime.get_render(render_job_id)
        if not render:
            return f"Error: CreativeMedia render job not found: {render_job_id}"
        return json.dumps({"render": _creative_media_render_summary(render, detail=True)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia render job: {str(e)}"


@tool
def creative_media_list_renders(plan_id: Optional[str] = None, status: Optional[str] = None, limit: int = 20, detail_level: str = "summary") -> str:
    """List CreativeMedia render jobs, optionally filtered by edit plan or status."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        renders = creative_media_runtime.list_renders(plan_id=plan_id, status=status)
        normalized_detail = str(detail_level or "summary").strip().lower()
        effective_limit = max(1, min(int(limit or 20), 50))
        if normalized_detail == "summary":
            effective_limit = min(effective_limit, 5)
        status_counts: dict[str, int] = {}
        for item in renders:
            state = str((item or {}).get("status") or "unknown")
            status_counts[state] = status_counts.get(state, 0) + 1
        return json.dumps(
            {
                "ok": True,
                "summary": "Creative Media render jobs listed; use detailTool for artifacts and paths.",
                "statusCounts": status_counts,
                "renders": [_creative_media_render_summary(item) for item in renders[:effective_limit]],
                "count": len(renders),
                "limit": effective_limit,
                "hasMore": len(renders) > effective_limit,
                "detailTool": "creative_media_get_render(render_job_id=...)",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia render jobs: {str(e)}"


@tool
def creative_media_create_quality_job(request: dict[str, Any]) -> str:
    """Run lightweight deterministic quality checks for a CreativeMedia job or artifacts."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        quality_job = creative_media_runtime.create_quality_job(dict(request or {}))
        return json.dumps({"qualityJob": _creative_media_quality_job_summary(quality_job)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error creating CreativeMedia quality job: {str(e)}"


@tool
def creative_media_list_quality_jobs(status: Optional[str] = None, limit: int = 20) -> str:
    """List CreativeMedia quality jobs, optionally filtered by status."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        jobs = creative_media_runtime.list_quality_jobs(status=status)
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "qualityJobs": [_creative_media_quality_job_summary(item) for item in jobs[:effective_limit]],
                "count": len(jobs),
                "limit": effective_limit,
                "hasMore": len(jobs) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing CreativeMedia quality jobs: {str(e)}"


@tool
def creative_media_get_quality_job(quality_job_id: str) -> str:
    """Read a CreativeMedia quality job by id."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        quality_job = creative_media_runtime.get_quality_job(quality_job_id)
        if not quality_job:
            return f"Error: CreativeMedia quality job not found: {quality_job_id}"
        return json.dumps({"qualityJob": _creative_media_quality_job_summary(quality_job)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error reading CreativeMedia quality job: {str(e)}"


@tool
async def creative_media_retry_job(job_id: str, request: Optional[dict[str, Any]] = None) -> str:
    """Retry a CreativeMedia job within the same operationKind using runtime retry policy."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        job = await creative_media_runtime.retry_job(job_id, dict(request or {}))
        return json.dumps({"job": _creative_media_job_detail(job)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error retrying CreativeMedia job: {str(e)}"


@tool
def creative_media_cost_ledger(limit: int = 20) -> str:
    """List CreativeMedia provider cost and usage ledger entries."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        entries = creative_media_runtime.list_cost_ledger()
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "entries": [_creative_media_cost_entry_summary(item) for item in entries[:effective_limit]],
                "count": len(entries),
                "limit": effective_limit,
                "hasMore": len(entries) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error reading CreativeMedia cost ledger: {str(e)}"


@tool
def creative_media_safety_events(limit: int = 20) -> str:
    """List CreativeMedia prompt safety rewrite and provider policy events."""
    try:
        from runtimes.creative_media.runtime import creative_media_runtime

        events = creative_media_runtime.list_safety_events()
        effective_limit = max(1, min(int(limit or 20), 50))
        return json.dumps(
            {
                "events": [_creative_media_safety_event_summary(item) for item in events[:effective_limit]],
                "count": len(events),
                "limit": effective_limit,
                "hasMore": len(events) > effective_limit,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error reading CreativeMedia safety events: {str(e)}"
