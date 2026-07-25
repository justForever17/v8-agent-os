from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import tool

from erc.runtime_context import get_runtime_context


_MISSING = object()
_ID_FIELDS = {
    "artifactId",
    "candidateArtifactId",
    "characterBibleId",
    "editPlanId",
    "jobId",
    "keyframeId",
    "planId",
    "productionPackId",
    "providerAdapterId",
    "qualityJobId",
    "referenceArtifactId",
    "recipeId",
    "renderJobId",
}
_LIST_FIELDS = {
    "artifactIds",
    "artifactProof",
    "artifacts",
    "assetIds",
    "audioAssetIds",
    "detailRefs",
    "imageUrls",
    "layers",
    "media",
    "musicAssetIds",
    "proof",
    "questions",
    "referenceAssetIds",
    "references",
    "requiredKinds",
    "sampleArtifactRefs",
    "sourceRefs",
    "subtitleSegments",
    "videoAssetIds",
}
_BOOL_FIELDS = {
    "aigcWatermark",
    "autoRepair",
    "dryRun",
    "execute",
    "generateAudio",
    "promptOptimizer",
    "fastPretreatment",
    "isInstrumental",
    "preserveNativeAudio",
    "refresh",
    "subtitleEnable",
    "wait",
    "watermark",
}
_INT_FIELDS = {"fps", "height", "limit", "maxLayers", "maxRepairAttempts", "n", "sampleRate", "seed", "width"}
_DICT_FIELDS = {"canvas", "metadata", "pronunciationDict", "providerLock", "qa", "retryRequest", "sampleApproval", "stages", "voiceSetting"}
_FLOAT_FIELDS = {
    "costLimit",
    "defaultClipDurationSeconds",
    "durationSeconds",
    "pitch",
    "pollIntervalSeconds",
    "speed",
    "targetDurationSeconds",
    "timeoutSeconds",
    "volume",
}


@dataclass(frozen=True, slots=True)
class CreativeMediaActionSpec:
    facade: str
    action: str
    handler_module: Literal["creative", "psd", "plugin_manager", "contract"]
    handler_name: str
    required_fields: frozenset[str] = field(default_factory=frozenset)
    allowed_fields: frozenset[str] = field(default_factory=frozenset)
    any_of_fields: tuple[frozenset[str], ...] = ()
    mutating: bool = False
    output_kind: str = "summary"
    async_handler: bool = False
    pass_request: bool = False
    argument_map: tuple[tuple[str, str, Any], ...] = ()


_GENERATION_FIELDS = frozenset(
    {
        "aigcWatermark",
        "aspectRatio",
        "audioBase64",
        "audioFormat",
        "audioUrl",
        "brief",
        "costLimit",
        "coverFeatureId",
        "durationSeconds",
        "emotion",
        "firstFrame",
        "fps",
        "generateAudio",
        "goal",
        "hardRequirements",
        "imageUrl",
        "imageUrls",
        "isInstrumental",
        "lastFrame",
        "lyrics",
        "maskUrl",
        "modality",
        "modelId",
        "modelRef",
        "motionReferenceUrl",
        "n",
        "negativePrompt",
        "operationKind",
        "outputFormat",
        "pitch",
        "pollIntervalSeconds",
        "preserveNativeAudio",
        "previewText",
        "prompt",
        "promptOptimizer",
        "fastPretreatment",
        "pronunciationDict",
        "providerId",
        "providerAdapterId",
        "qualityTier",
        "qualityProfile",
        "referenceAssetIds",
        "referenceAudioUrl",
        "referenceImageUrl",
        "referenceVideoUrl",
        "resolution",
        "resultFormat",
        "sampleRate",
        "seed",
        "speed",
        "style",
        "subtitleEnable",
        "targetImageUrl",
        "timeoutSeconds",
        "title",
        "voiceDescription",
        "voiceId",
        "voicePrompt",
        "voiceSetting",
        "volume",
        "watermark",
    }
)
_RECIPE_FIELDS = _GENERATION_FIELDS | frozenset({"intent", "ratio", "size"})
_WORK_ORDER_FIELDS = _RECIPE_FIELDS | frozenset(
    {"assetRole", "referenceAssets", "requestingRuntime", "workOrderKind"}
)
_PRODUCTION_PACK_FIELDS = frozenset(
    {
        "artifactProof",
        "brief",
        "asset_manifest",
        "edit_decisions",
        "final_review",
        "goal",
        "packId",
        "productionPackId",
        "proof",
        "providerId",
        "providerLock",
        "providerLockReason",
        "modelId",
        "qa",
        "referenceMedia",
        "references",
        "proposal",
        "render_report",
        "sampleApproval",
        "sampleArtifactRefs",
        "sampleDecisionRef",
        "sampleStatus",
        "scene_plan",
        "script",
        "stages",
        "title",
    }
)
_REFERENCE_FIELDS = frozenset(
    {
        "artifacts",
        "audioTranscript",
        "goal",
        "media",
        "prompt",
        "references",
        "reusableAssets",
        "shotStructure",
        "transcript",
        "visualStyle",
    }
)
_SAMPLE_FIELDS = frozenset(
    {"artifacts", "media", "question", "questions", "sampleArtifactRefs", "selectionMode", "title"}
)
_ASSET_REGISTER_FIELDS = frozenset(
    {"artifactId", "metadata", "modality", "name", "role", "sourcePath", "title", "type"}
)
_CHARACTER_FIELDS = frozenset(
    {"artifactIds", "characterBibleId", "details", "name", "referenceAssetIds", "title"}
)
_KEYFRAME_FIELDS = frozenset(
    {"artifactId", "characterBibleId", "name", "recipeId", "role", "sourcePath", "title"}
)
_EDIT_PLAN_FIELDS = frozenset(
    {
        "assetIds",
        "audioAssetIds",
        "defaultClipDurationSeconds",
        "editIntent",
        "musicAssetIds",
        "parentPlanId",
        "recipeId",
        "subtitleSegments",
        "subtitleText",
        "title",
        "videoAssetIds",
    }
)
_RENDER_FIELDS = frozenset(
    {"editPlanId", "execute", "outputFormat", "outputPath", "planId", "renderProfile", "title"}
)
_QUALITY_FIELDS = frozenset(
    {
        "artifactIds",
        "artifacts",
        "autoRepair",
        "jobId",
        "maxRepairAttempts",
        "qualityProfile",
        "referenceArtifactId",
        "requiredKinds",
        "title",
    }
)
_PSD_COMPOSE_FIELDS = frozenset({"canvas", "dryRun", "layers", "name", "outputPath", "title"})


def _spec(
    facade: str,
    action: str,
    module: Literal["creative", "psd", "plugin_manager", "contract"],
    handler: str,
    *,
    required: set[str] | frozenset[str] = frozenset(),
    allowed: set[str] | frozenset[str] = frozenset(),
    any_of: tuple[set[str] | frozenset[str], ...] = (),
    mutating: bool = False,
    output_kind: str = "summary",
    async_handler: bool = False,
    pass_request: bool = False,
    args: tuple[tuple[str, str, Any], ...] = (),
) -> CreativeMediaActionSpec:
    return CreativeMediaActionSpec(
        facade=facade,
        action=action,
        handler_module=module,
        handler_name=handler,
        required_fields=frozenset(required),
        allowed_fields=frozenset(allowed),
        any_of_fields=tuple(frozenset(group) for group in any_of),
        mutating=mutating,
        output_kind=output_kind,
        async_handler=async_handler,
        pass_request=pass_request,
        argument_map=args,
    )


CREATIVE_MEDIA_ACTION_REGISTRY: dict[str, dict[str, CreativeMediaActionSpec]] = {
    "capabilities": {
        "describe": _spec("capabilities", "describe", "contract", "describe"),
        "status": _spec("capabilities", "status", "plugin_manager", "status"),
        "catalog": _spec(
            "capabilities",
            "catalog",
            "creative",
            "creative_media_catalog",
            allowed={"cursor", "detailLevel", "limit", "modality", "operationKind"},
            output_kind="catalog",
            args=(
                ("modality", "modality", None),
                ("operation_kind", "operationKind", None),
                ("detail_level", "detailLevel", "summary"),
                ("limit", "limit", 20),
                ("cursor", "cursor", None),
            ),
        ),
        "resolutions": _spec(
            "capabilities",
            "resolutions",
            "creative",
            "creative_media_resolutions",
            allowed={"detailLevel"},
            args=(("detail_level", "detailLevel", "summary"),),
        ),
        "rank_models": _spec(
            "capabilities",
            "rank_models",
            "creative",
            "creative_media_rank_models",
            allowed={"goal", "limit", "modality", "operationKind"},
            args=(
                ("modality", "modality", None),
                ("operation_kind", "operationKind", None),
                ("goal", "goal", None),
                ("limit", "limit", 8),
            ),
        ),
    },
    "plan": {
        "compile_recipe": _spec(
            "plan", "compile_recipe", "creative", "creative_media_compile_recipe",
            required={"modality"}, allowed=_RECIPE_FIELDS, mutating=True, output_kind="recipe", pass_request=True,
        ),
        "compile_work_order": _spec(
            "plan", "compile_work_order", "creative", "creative_media_compile_work_order",
            required={"modality", "workOrderKind"}, allowed=_WORK_ORDER_FIELDS,
            mutating=True, output_kind="work_order", pass_request=True,
        ),
        "list_work_orders": _spec(
            "plan", "list_work_orders", "creative", "creative_media_list_work_orders",
            allowed={"limit", "requestingRuntime", "status"}, output_kind="work_order_list",
            args=(("status", "status", None), ("requesting_runtime", "requestingRuntime", None), ("limit", "limit", 20)),
        ),
        "production_pack": _spec(
            "plan", "production_pack", "creative", "creative_media_production_pack",
            required={"goal"}, allowed=_PRODUCTION_PACK_FIELDS, mutating=True, output_kind="production_pack", pass_request=True,
        ),
        "reference_brief": _spec(
            "plan", "reference_brief", "creative", "creative_media_reference_media_brief",
            allowed=_REFERENCE_FIELDS, any_of=({"media", "references", "artifacts"},), output_kind="reference_brief", pass_request=True,
        ),
        "sample_approval": _spec(
            "plan", "sample_approval", "creative", "creative_media_sample_approval_packet",
            allowed=_SAMPLE_FIELDS, any_of=({"media", "artifacts", "sampleArtifactRefs"},), output_kind="approval_packet", pass_request=True,
        ),
    },
    "assets": {
        "register_asset": _spec(
            "assets", "register_asset", "creative", "creative_media_register_asset",
            allowed=_ASSET_REGISTER_FIELDS, any_of=({"artifactId", "sourcePath"},), mutating=True, output_kind="asset", pass_request=True,
        ),
        "list_assets": _spec(
            "assets", "list_assets", "creative", "creative_media_list_assets",
            allowed={"limit", "modality", "role"}, output_kind="asset_list",
            args=(("modality", "modality", None), ("role", "role", None), ("limit", "limit", 20)),
        ),
        "get_recipe": _spec(
            "assets", "get_recipe", "creative", "creative_media_get_recipe",
            required={"recipeId"}, allowed={"recipeId"}, output_kind="recipe",
            args=(("recipe_id", "recipeId", _MISSING),),
        ),
        "list_recipes": _spec(
            "assets", "list_recipes", "creative", "creative_media_list_recipes",
            allowed={"limit", "modality", "recipeKind"}, output_kind="recipe_list",
            args=(("modality", "modality", None), ("recipe_kind", "recipeKind", None), ("limit", "limit", 20)),
        ),
        "create_character_bible": _spec(
            "assets", "create_character_bible", "creative", "creative_media_create_character_bible",
            required={"name"}, allowed=_CHARACTER_FIELDS, mutating=True, output_kind="character_bible", pass_request=True,
        ),
        "get_character_bible": _spec(
            "assets", "get_character_bible", "creative", "creative_media_get_character_bible",
            required={"characterBibleId"}, allowed={"characterBibleId"}, output_kind="character_bible",
            args=(("character_bible_id", "characterBibleId", _MISSING),),
        ),
        "list_character_bibles": _spec(
            "assets", "list_character_bibles", "creative", "creative_media_list_character_bibles",
            allowed={"limit"}, output_kind="character_bible_list", args=(("limit", "limit", 20),),
        ),
        "register_keyframe": _spec(
            "assets", "register_keyframe", "creative", "creative_media_register_keyframe",
            allowed=_KEYFRAME_FIELDS, any_of=({"artifactId", "sourcePath"},), mutating=True, output_kind="keyframe", pass_request=True,
        ),
        "get_keyframe": _spec(
            "assets", "get_keyframe", "creative", "creative_media_get_keyframe",
            required={"keyframeId"}, allowed={"keyframeId"}, output_kind="keyframe",
            args=(("keyframe_id", "keyframeId", _MISSING),),
        ),
        "list_keyframes": _spec(
            "assets", "list_keyframes", "creative", "creative_media_list_keyframes",
            allowed={"characterBibleId", "limit", "recipeId", "role"}, output_kind="keyframe_list",
            args=(
                ("recipe_id", "recipeId", None),
                ("role", "role", None),
                ("character_bible_id", "characterBibleId", None),
                ("limit", "limit", 20),
            ),
        ),
        "psd_inspect": _spec(
            "assets", "psd_inspect", "psd", "creative_media_psd_inspect",
            allowed={"artifactId", "maxLayers", "path"}, any_of=({"artifactId", "path"},), output_kind="psd_report",
            args=(("path", "path", ""), ("artifact_id", "artifactId", ""), ("max_layers", "maxLayers", 40)),
        ),
        "psd_compose_template": _spec(
            "assets", "psd_compose_template", "psd", "creative_media_psd_compose_template",
            required={"canvas", "layers"}, allowed=_PSD_COMPOSE_FIELDS, mutating=True, output_kind="psd_artifact", pass_request=True,
        ),
    },
    "jobs": {
        "create": _spec(
            "jobs", "create", "creative", "creative_media_create_job",
            required={"modality", "operationKind"}, allowed=_GENERATION_FIELDS,
            mutating=True, output_kind="job", async_handler=True, pass_request=True,
        ),
        "get": _spec(
            "jobs", "get", "creative", "creative_media_get_job",
            required={"jobId"}, allowed={"jobId", "refresh"}, output_kind="job",
            async_handler=True,
            args=(("job_id", "jobId", _MISSING), ("refresh", "refresh", True)),
        ),
        "list": _spec(
            "jobs", "list", "creative", "creative_media_list_jobs",
            allowed={"detailLevel", "limit", "modality", "status"}, output_kind="job_list",
            args=(
                ("modality", "modality", None),
                ("status", "status", None),
                ("limit", "limit", 20),
                ("detail_level", "detailLevel", "summary"),
            ),
        ),
        "artifacts": _spec(
            "jobs", "artifacts", "creative", "creative_media_job_artifacts",
            required={"jobId"}, allowed={"jobId"}, output_kind="artifact_list",
            args=(("job_id", "jobId", _MISSING),),
        ),
        "retry": _spec(
            "jobs", "retry", "creative", "creative_media_retry_job",
            required={"jobId"}, allowed={"jobId", "retryRequest"},
            mutating=True, output_kind="job", async_handler=True,
            args=(("job_id", "jobId", _MISSING), ("request", "retryRequest", {})),
        ),
    },
    "edit": {
        "create_plan": _spec(
            "edit", "create_plan", "creative", "creative_media_create_edit_plan",
            allowed=_EDIT_PLAN_FIELDS, any_of=({"assetIds", "videoAssetIds"},), mutating=True, output_kind="edit_plan", pass_request=True,
        ),
        "get_plan": _spec(
            "edit", "get_plan", "creative", "creative_media_get_edit_plan",
            required={"planId"}, allowed={"planId"}, output_kind="edit_plan",
            args=(("plan_id", "planId", _MISSING),),
        ),
        "list_plans": _spec(
            "edit", "list_plans", "creative", "creative_media_list_edit_plans",
            allowed={"limit", "recipeId"}, output_kind="edit_plan_list",
            args=(("recipe_id", "recipeId", None), ("limit", "limit", 20)),
        ),
        "render": _spec(
            "edit", "render", "creative", "creative_media_render_edit_plan",
            allowed=_RENDER_FIELDS, any_of=({"editPlanId", "planId"},), mutating=True, output_kind="render", pass_request=True,
        ),
        "get_render": _spec(
            "edit", "get_render", "creative", "creative_media_get_render",
            required={"renderJobId"}, allowed={"renderJobId"}, output_kind="render",
            args=(("render_job_id", "renderJobId", _MISSING),),
        ),
        "list_renders": _spec(
            "edit", "list_renders", "creative", "creative_media_list_renders",
            allowed={"detailLevel", "limit", "planId", "status"}, output_kind="render_list",
            args=(
                ("plan_id", "planId", None),
                ("status", "status", None),
                ("limit", "limit", 20),
                ("detail_level", "detailLevel", "summary"),
            ),
        ),
    },
    "quality": {
        "create_job": _spec(
            "quality", "create_job", "creative", "creative_media_create_quality_job",
            allowed=_QUALITY_FIELDS, any_of=({"jobId", "artifactIds", "artifacts"},), mutating=True, output_kind="quality_job", pass_request=True,
        ),
        "get_job": _spec(
            "quality", "get_job", "creative", "creative_media_get_quality_job",
            required={"qualityJobId"}, allowed={"qualityJobId"}, output_kind="quality_job",
            args=(("quality_job_id", "qualityJobId", _MISSING),),
        ),
        "list_jobs": _spec(
            "quality", "list_jobs", "creative", "creative_media_list_quality_jobs",
            allowed={"limit", "status"}, output_kind="quality_job_list",
            args=(("status", "status", None), ("limit", "limit", 20)),
        ),
        "qa_check": _spec(
            "quality", "qa_check", "creative", "creative_media_qa_check",
            allowed=_QUALITY_FIELDS, any_of=({"jobId", "artifactIds", "artifacts"},), output_kind="qa", pass_request=True,
        ),
        "cost_ledger": _spec(
            "quality", "cost_ledger", "creative", "creative_media_cost_ledger",
            allowed={"limit"}, output_kind="cost_ledger", args=(("limit", "limit", 20),),
        ),
        "safety_events": _spec(
            "quality", "safety_events", "creative", "creative_media_safety_events",
            allowed={"limit"}, output_kind="safety_events", args=(("limit", "limit", 20),),
        ),
        "alpha_inspect": _spec(
            "quality", "alpha_inspect", "psd", "creative_media_alpha_inspect",
            allowed={"artifactId", "expectedBackground", "path"}, any_of=({"artifactId", "path"},), output_kind="alpha_report",
            args=(
                ("path", "path", ""),
                ("artifact_id", "artifactId", ""),
                ("expected_background", "expectedBackground", "auto"),
            ),
        ),
        "image_compare": _spec(
            "quality", "image_compare", "psd", "creative_media_image_compare",
            allowed={
                "candidateArtifactId",
                "candidatePath",
                "qualityProfile",
                "referenceArtifactId",
                "referencePath",
            },
            any_of=({"referenceArtifactId", "referencePath"}, {"candidateArtifactId", "candidatePath"}),
            output_kind="image_comparison",
            args=(
                ("reference_path", "referencePath", ""),
                ("reference_artifact_id", "referenceArtifactId", ""),
                ("candidate_path", "candidatePath", ""),
                ("candidate_artifact_id", "candidateArtifactId", ""),
                ("quality_profile", "qualityProfile", "character_reference"),
            ),
        ),
        "psd_export_preview": _spec(
            "quality", "psd_export_preview", "psd", "creative_media_psd_export_preview",
            allowed={"artifactId", "dryRun", "outputPath", "path"}, any_of=({"artifactId", "path"},),
            mutating=True, output_kind="preview", args=(
                ("path", "path", ""),
                ("artifact_id", "artifactId", ""),
                ("output_path", "outputPath", ""),
                ("dry_run", "dryRun", False),
            ),
        ),
    },
}


def creative_media_action_contract() -> dict[str, Any]:
    return {
        facade: {
            action: {
                "requiredFields": sorted(spec.required_fields),
                "allowedFields": sorted(spec.allowed_fields),
                "anyOfFields": [sorted(group) for group in spec.any_of_fields],
                "mutating": spec.mutating,
                "outputKind": spec.output_kind,
                **(
                    {
                        "requiresPluginGrantWhen": "providerAdapterId is set",
                        "pluginBlockedError": "plugin_grant_required",
                    }
                    if "providerAdapterId" in spec.allowed_fields
                    else {}
                ),
            }
            for action, spec in actions.items()
        }
        for facade, actions in CREATIVE_MEDIA_ACTION_REGISTRY.items()
    }


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _validation_error(spec: CreativeMediaActionSpec, code: str, message: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "facade": spec.facade,
            "action": spec.action,
            "status": "invalid_request",
            "summary": message,
            "refs": [],
            "error": {"code": code, "message": message},
        },
        ensure_ascii=False,
    )


def _validate_request(spec: CreativeMediaActionSpec, request: Any) -> tuple[dict[str, Any] | None, str | None]:
    if request is None:
        payload: dict[str, Any] = {}
    elif isinstance(request, dict):
        payload = dict(request)
    else:
        return None, _validation_error(spec, "invalid_request_type", "request must be an object")

    unknown = sorted(set(payload) - set(spec.allowed_fields))
    if unknown:
        return None, _validation_error(
            spec,
            "unknown_fields",
            f"unsupported request fields for {spec.facade}.{spec.action}: {', '.join(unknown)}",
        )
    missing = sorted(field for field in spec.required_fields if _is_empty(payload.get(field)))
    if missing:
        return None, _validation_error(
            spec,
            "missing_required_fields",
            f"missing required fields for {spec.facade}.{spec.action}: {', '.join(missing)}",
        )
    for group in spec.any_of_fields:
        if not any(not _is_empty(payload.get(name)) for name in group):
            return None, _validation_error(
                spec,
                "missing_required_alternative",
                f"one of these fields is required for {spec.facade}.{spec.action}: {', '.join(sorted(group))}",
            )

    for name, value in payload.items():
        if value is None:
            continue
        if name in _ID_FIELDS and not isinstance(value, str):
            return None, _validation_error(spec, "invalid_field_type", f"{name} must be a string")
        if name in _LIST_FIELDS and not isinstance(value, list):
            return None, _validation_error(spec, "invalid_field_type", f"{name} must be a list")
        if name in _DICT_FIELDS and not isinstance(value, dict):
            return None, _validation_error(spec, "invalid_field_type", f"{name} must be an object")
        if name in _BOOL_FIELDS and not isinstance(value, bool):
            return None, _validation_error(spec, "invalid_field_type", f"{name} must be a boolean")
        if name in _INT_FIELDS and (not isinstance(value, int) or isinstance(value, bool)):
            return None, _validation_error(spec, "invalid_field_type", f"{name} must be an integer")
        if name in _FLOAT_FIELDS and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return None, _validation_error(spec, "invalid_field_type", f"{name} must be a number")
    if payload.get("limit") is not None and not 1 <= int(payload["limit"]) <= 100:
        return None, _validation_error(spec, "invalid_limit", "limit must be between 1 and 100")
    if payload.get("maxLayers") is not None and not 1 <= int(payload["maxLayers"]) <= 500:
        return None, _validation_error(spec, "invalid_max_layers", "maxLayers must be between 1 and 500")
    return _inject_runtime_scope(payload), None


def _inject_runtime_scope(payload: dict[str, Any]) -> dict[str, Any]:
    scoped = dict(payload)
    context = dict(get_runtime_context() or {})
    for target, candidates in (
        ("sessionId", ("session_id", "sessionId")),
        ("runId", ("run_id", "runId")),
        ("projectId", ("project_id", "projectId")),
        ("workspaceId", ("workspace_id", "workspaceId")),
        ("workspacePath", ("workspace_path", "workspacePath")),
    ):
        value = next((context.get(name) for name in candidates if context.get(name)), None)
        if value is not None:
            scoped[target] = value
    return scoped


def _resolve_handler(spec: CreativeMediaActionSpec) -> Any:
    if spec.handler_module == "creative":
        from core.tools.native import creative_media

        return getattr(creative_media, spec.handler_name)
    if spec.handler_module == "psd":
        from core.tools.native import creative_media_psd

        return getattr(creative_media_psd, spec.handler_name)
    raise RuntimeError(f"{spec.handler_module} is handled without a native tool")


def _handler_arguments(spec: CreativeMediaActionSpec, payload: dict[str, Any]) -> dict[str, Any]:
    if spec.pass_request:
        return {"request": payload}
    arguments: dict[str, Any] = {}
    for target, source, default in spec.argument_map:
        if source in payload:
            arguments[target] = payload[source]
        elif default is not _MISSING:
            arguments[target] = default
    return arguments


def _parse_raw(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _collect_refs(value: Any, *, limit: int = 24) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def append(item: Any) -> None:
        text = str(item or "").strip()
        if not text or len(text) > 500 or text in seen:
            return
        seen.add(text)
        refs.append(text)

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 5 or len(refs) >= limit:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized = str(key or "")
                if normalized.endswith("Id") and normalized not in {
                    "providerId",
                    "providerAdapterId",
                    "modelId",
                    "workspaceId",
                    "projectId",
                    "sessionId",
                    "runId",
                }:
                    append(nested)
                elif normalized.endswith("Ref") and isinstance(nested, str):
                    append(nested)
                elif normalized.endswith("Refs") or normalized in {"artifactIds", "sourceRefs"}:
                    if isinstance(nested, list):
                        for ref in nested:
                            if isinstance(ref, dict):
                                append(ref.get("artifactId") or ref.get("id") or ref.get("ref"))
                            else:
                                append(ref)
                visit(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item[:50]:
                visit(nested, depth + 1)

    visit(value)
    return refs[:limit]


def _record_internal_detail(spec: CreativeMediaActionSpec, raw: str) -> str | None:
    if not raw:
        return None
    try:
        from core.tool_surface import record_raw_observation

        return record_raw_observation(
            tool_name=f"creative_media_{spec.facade}.{spec.action}.internal",
            tool_call_id=None,
            runtime_kind="creative_media",
            surface="creative_media_facade_internal",
            raw_content=raw,
            budget_meta={"facade": spec.facade, "action": spec.action, "internalHandler": spec.handler_name},
        ) or None
    except Exception:
        return None


def _summary_from_raw(spec: CreativeMediaActionSpec, raw: str, parsed: Any) -> str:
    if isinstance(parsed, dict):
        for key in ("summary", "message", "result"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                return parsed[key].strip()[:600]
        for key in ("job", "recipe", "workOrder", "asset", "render", "editPlan", "qualityJob"):
            record = parsed.get(key)
            if not isinstance(record, dict):
                continue
            ident = next((record.get(name) for name in _ID_FIELDS if record.get(name)), None)
            state = record.get("status") or record.get("qualityStatus")
            bits = [spec.output_kind]
            if ident:
                bits.append(str(ident))
            if state:
                bits.append(str(state))
            return " | ".join(bits)[:600]
        count = parsed.get("count")
        if count is not None:
            return f"{spec.output_kind}: {count} item(s)"
    lines = [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    if lines:
        return " ".join(line.lstrip("#- ").strip() for line in lines[:2])[:600]
    return f"Creative Media {spec.facade}.{spec.action} completed"


def _envelope(spec: CreativeMediaActionSpec, raw: str) -> str:
    parsed = _parse_raw(raw)
    lower = str(raw or "").strip().lower()
    explicit_error: Any = parsed.get("error") if isinstance(parsed, dict) else None
    ok = not (
        (isinstance(parsed, dict) and parsed.get("ok") is False)
        or explicit_error not in (None, "", {}, [])
        or lower.startswith(("error ", "error:", "failed ", "request failed"))
    )
    status = "succeeded" if ok else "failed"
    if isinstance(parsed, dict):
        candidate = parsed.get("status")
        if not candidate:
            for key in ("job", "recipe", "workOrder", "asset", "render", "editPlan", "qualityJob"):
                record = parsed.get(key)
                if isinstance(record, dict) and record.get("status"):
                    candidate = record.get("status")
                    break
        if candidate:
            status = str(candidate)
    detail_ref = _record_internal_detail(spec, str(raw or ""))
    next_action = None
    if isinstance(parsed, dict):
        next_action = parsed.get("recommendedNextAction") or parsed.get("nextAction")
    payload: dict[str, Any] = {
        "ok": ok,
        "facade": spec.facade,
        "action": spec.action,
        "status": status,
        "summary": _summary_from_raw(spec, raw, parsed),
        "refs": _collect_refs(parsed),
    }
    if detail_ref:
        payload["detailRef"] = detail_ref
    if next_action:
        payload["nextAction"] = str(next_action)[:600]
    if not ok:
        if isinstance(explicit_error, dict):
            code = str(explicit_error.get("code") or "creative_media_action_failed")
            message = str(explicit_error.get("message") or explicit_error)
        else:
            code = "creative_media_action_failed"
            message = str(explicit_error or payload["summary"])
        payload["error"] = {"code": code, "message": message[:1000]}
    return json.dumps(payload, ensure_ascii=False)


def _contract_result() -> str:
    contract = creative_media_action_contract()
    return json.dumps(
        {
            "ok": True,
            "runtime": "creative_media",
            "summary": "Creative Media exposes six facade tools; choose an action and pass only its declared request fields.",
            "contract": contract,
            "facadeCount": len(contract),
            "actionCount": sum(len(actions) for actions in contract.values()),
        },
        ensure_ascii=False,
    )


def _plugin_status_result() -> str:
    from runtimes.plugin_manager.service import plugin_manager_service

    context = dict(get_runtime_context() or {})
    status = plugin_manager_service.status_summary(
        session_id=str(context.get("session_id") or context.get("sessionId") or "").strip() or None,
        run_id=str(context.get("run_id") or context.get("runId") or "").strip() or None,
    )
    relevant = [
        item for item in list(status.get("plugins") or [])
        if isinstance(item, dict) and item.get("pluginId") in {"aliyun-bailian", "hyperframes"}
    ]
    return json.dumps(
        {
            "ok": True,
            "status": "ready",
            "summary": f"Creative Media base capabilities are available; {len(relevant)} optional supplier plugin(s) were found.",
            "runtime": "creative_media",
            "baseCapabilities": [
                "image.generate",
                "image.edit",
                "video.text_to_video",
                "video.image_to_video",
                "video.first_last_frame",
                "video.reference_to_video",
                "voice.tts",
                "voice.design",
                "music.generate",
                "music.cover",
                "model3d.generate",
            ],
            "acceptedInputs": [
                "prompt",
                "image_reference",
                "video_reference",
                "audio_reference",
                "first_frame",
                "last_frame",
            ],
            "deliverables": ["image", "video", "audio", "music", "3D", "PSD", "recipe", "QA"],
            "optionalPlugins": relevant,
        },
        ensure_ascii=False,
    )


def _resolve_code_owned_provider_adapter(adapter_id: str) -> Any | None:
    """Resolve a grant-backed adapter through Plugin Manager without loading plugin code.

    Plugin Manager owns grant/digest/health validation. This facade only accepts
    adapters that are also present in the code-owned dispatcher table below.
    """
    try:
        from runtimes.plugin_manager.service import plugin_manager_service

        resolver = getattr(plugin_manager_service, "resolve_creative_media_adapter", None)
        if not callable(resolver):
            return None
        context = dict(get_runtime_context() or {})
        agent_id = str(context.get("agent_id") or context.get("agentId") or "supervisor").strip() or "supervisor"
        runtime_kind = str(context.get("runtime_kind") or context.get("runtimeKind") or "chat").strip()
        return resolver(
            adapter_id=adapter_id,
            session_id=str(context.get("session_id") or context.get("sessionId") or "").strip() or None,
            run_id=str(context.get("run_id") or context.get("runId") or "").strip() or None,
            grantee_type="supervisor" if agent_id == "supervisor" or runtime_kind in {"chat", "supervisor"} else "subagent",
            grantee_id=agent_id,
        )
    except Exception:
        return None


async def _dispatch_code_owned_provider_adapter(adapter_id: str, binding: Any, payload: dict[str, Any]) -> str | None:
    """Code-owned adapter dispatch hook.

    Signed plugin metadata alone can never inject a callable or fall back to a
    legacy supplier tool. Each branch below is compiled into the product.
    """
    del adapter_id
    binding_payload = dict(binding or {}) if isinstance(binding, dict) else {}
    handler_id = str(binding_payload.get("handlerId") or "")
    if handler_id == "creative_media.aliyun_bailian_dashscope":
        from core.tools.native.creative_media import creative_media_create_job
        from runtimes.creative_media.runtime import bind_creative_provider_credentials

        request = dict(payload or {})
        request.pop("providerAdapterId", None)
        request["providerId"] = "aliyun_bailian_dashscope"
        with bind_creative_provider_credentials(dict(binding_payload.get("credentials") or {})):
            return str(await creative_media_create_job.ainvoke({"request": request}))
    return None


def _plugin_grant_required(spec: CreativeMediaActionSpec, adapter_id: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "facade": spec.facade,
            "action": spec.action,
            "status": "blocked",
            "summary": f"Provider adapter '{adapter_id}' requires an installed, healthy, explicitly granted plugin.",
            "refs": [],
            "nextAction": f"Authorize the matching @plugin for providerAdapterId '{adapter_id}', then retry this action.",
            "error": {
                "code": "plugin_grant_required",
                "message": "No active code-owned provider adapter binding is available for this task.",
            },
        },
        ensure_ascii=False,
    )


def _execute_sync(facade: str, action: str, request: Any) -> str:
    spec = CREATIVE_MEDIA_ACTION_REGISTRY[facade][action]
    payload, error = _validate_request(spec, request)
    if error:
        return error
    try:
        if spec.handler_module == "contract":
            raw = _contract_result()
        elif spec.handler_module == "plugin_manager":
            raw = _plugin_status_result()
        else:
            raw = str(_resolve_handler(spec).invoke(_handler_arguments(spec, payload or {})))
        return _envelope(spec, raw)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return json.dumps(
            {
                "ok": False,
                "facade": spec.facade,
                "action": spec.action,
                "status": "failed",
                "summary": message,
                "refs": [],
                "error": {"code": "creative_media_action_failed", "message": message},
            },
            ensure_ascii=False,
        )


async def _execute_async(facade: str, action: str, request: Any) -> str:
    spec = CREATIVE_MEDIA_ACTION_REGISTRY[facade][action]
    payload, error = _validate_request(spec, request)
    if error:
        return error
    try:
        adapter_id = str((payload or {}).get("providerAdapterId") or "").strip()
        if facade == "jobs" and action == "create" and adapter_id:
            binding = _resolve_code_owned_provider_adapter(adapter_id)
            if binding is None:
                return _plugin_grant_required(spec, adapter_id)
            adapter_raw = await _dispatch_code_owned_provider_adapter(adapter_id, binding, dict(payload or {}))
            if adapter_raw is None:
                return _plugin_grant_required(spec, adapter_id)
            return _envelope(spec, str(adapter_raw))
        handler = _resolve_handler(spec)
        arguments = _handler_arguments(spec, payload or {})
        raw = str(await handler.ainvoke(arguments)) if spec.async_handler else str(handler.invoke(arguments))
        return _envelope(spec, raw)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        return json.dumps(
            {
                "ok": False,
                "facade": spec.facade,
                "action": spec.action,
                "status": "failed",
                "summary": message,
                "refs": [],
                "error": {"code": "creative_media_action_failed", "message": message},
            },
            ensure_ascii=False,
        )


@tool
def creative_media_capabilities(
    action: Literal["describe", "status", "catalog", "resolutions", "rank_models"] = "status",
    request: dict[str, Any] | None = None,
) -> str:
    """Inspect the 多媒体创作 six-facade action contract, base scope, model catalog, and optional plugin status.

    Call action='describe' when the required action or fields are unfamiliar. Request fields are action-specific;
    undeclared fields, including caller-supplied session/run/workspace ids, are rejected.
    """
    return _execute_sync("capabilities", action, request)


@tool
def creative_media_plan(
    action: Literal[
        "compile_recipe",
        "compile_work_order",
        "list_work_orders",
        "production_pack",
        "reference_brief",
        "sample_approval",
    ] = "compile_recipe",
    request: dict[str, Any] | None = None,
) -> str:
    """Plan governed 多媒体创作 production through recipes, work orders, provider locks, sample approval, artifact proof, and QA."""
    return _execute_sync("plan", action, request)


@tool
def creative_media_assets(
    action: Literal[
        "register_asset",
        "list_assets",
        "get_recipe",
        "list_recipes",
        "create_character_bible",
        "get_character_bible",
        "list_character_bibles",
        "register_keyframe",
        "get_keyframe",
        "list_keyframes",
        "psd_inspect",
        "psd_compose_template",
    ] = "list_assets",
    request: dict[str, Any] | None = None,
) -> str:
    """Manage assets, recipes, character bibles, keyframes, and governed PSD source structures."""
    return _execute_sync("assets", action, request)


@tool
async def creative_media_jobs(
    action: Literal["create", "get", "list", "artifacts", "retry"] = "list",
    request: dict[str, Any] | None = None,
) -> str:
    """Create, query, list, and retry provider-backed 多媒体创作 jobs through one governed job facade.

    action='create' requires request.modality and request.operationKind. Base operations include image/video
    generation, voice.tts, voice.design, music.generate, music.cover, and model3d.generate. Poll with action='get'
    and obtain deliverable artifact refs with action='artifacts'; provider raw JSON is never the deliverable.
    """
    return await _execute_async("jobs", action, request)


@tool
def creative_media_edit(
    action: Literal["create_plan", "get_plan", "list_plans", "render", "get_render", "list_renders"] = "list_plans",
    request: dict[str, Any] | None = None,
) -> str:
    """Create governed local edit plans and render/list their outputs; supplier-exclusive edits require a plugin."""
    return _execute_sync("edit", action, request)


@tool
def creative_media_quality(
    action: Literal[
        "create_job",
        "get_job",
        "list_jobs",
        "qa_check",
        "cost_ledger",
        "safety_events",
        "alpha_inspect",
        "image_compare",
        "psd_export_preview",
    ] = "qa_check",
    request: dict[str, Any] | None = None,
) -> str:
    """Run QA, alpha/PSD checks, and inspect bounded cost or safety evidence through one quality facade."""
    return _execute_sync("quality", action, request)


CREATIVE_MEDIA_FACADE_TOOLS = (
    creative_media_capabilities,
    creative_media_plan,
    creative_media_assets,
    creative_media_jobs,
    creative_media_edit,
    creative_media_quality,
)


__all__ = [
    "CREATIVE_MEDIA_ACTION_REGISTRY",
    "CREATIVE_MEDIA_FACADE_TOOLS",
    "creative_media_action_contract",
    "creative_media_capabilities",
    "creative_media_plan",
    "creative_media_assets",
    "creative_media_jobs",
    "creative_media_edit",
    "creative_media_quality",
]
