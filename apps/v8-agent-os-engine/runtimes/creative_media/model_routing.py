from __future__ import annotations

from typing import Any, Iterable

from .comfyui_workflow import validate_comfyui_workflow


RUNTIME_ADAPTER_OPERATION_KINDS: dict[str, frozenset[str]] = {
    "openai_images": frozenset({"image.generate", "image.edit"}),
    "agnes_images": frozenset({"image.generate", "image.edit"}),
    "volcengine_ark": frozenset(
        {
            "image.generate",
            "image.edit",
            "video.text_to_video",
            "video.image_to_video",
            "video.first_last_frame",
            "video.reference_to_video",
        }
    ),
    "dashscope": frozenset(
        {
            "image.generate",
            "image.edit",
            "video.text_to_video",
            "video.image_to_video",
            "video.first_last_frame",
            "video.reference_to_video",
            "video.action_transfer",
        }
    ),
    "comfyui_workflow": frozenset({"video.action_transfer"}),
    "agnes_video": frozenset(
        {
            "video.text_to_video",
            "video.image_to_video",
            "video.first_last_frame",
        }
    ),
    "minimax_video": frozenset(
        {
            "video.text_to_video",
            "video.image_to_video",
            "video.first_last_frame",
            "video.reference_to_video",
        }
    ),
    "v8_audio_tts": frozenset({"voice.tts"}),
    "minimax_tts": frozenset({"voice.tts", "voice.design"}),
    "minimax_music": frozenset({"music.generate", "music.cover"}),
    "mureka_music": frozenset({"music.generate", "music.cover"}),
    "tencent_hunyuan_3d": frozenset({"model3d.generate"}),
}


READINESS_MESSAGES: dict[str, str] = {
    "provider_disabled": "The configured Provider is disabled.",
    "model_disabled": "The configured model is disabled.",
    "provider_base_url_missing": "The configured Provider has no base URL.",
    "provider_credential_missing": "The configured Provider credential is unavailable.",
    "operation_not_configured": "This operation is only a registry suggestion and is not enabled in the model configuration.",
    "adapter_not_configured": "The runtime adapter is only a registry suggestion and is not bound in the model configuration.",
    "adapter_missing": "The model configuration does not name a runtime adapter.",
    "adapter_catalog_only": "The configured adapter is catalog-only and cannot execute jobs.",
    "adapter_unsupported": "The configured adapter is not implemented by the Creative Media runtime.",
    "adapter_operation_mismatch": "The configured adapter does not implement this operation.",
    "endpoint_operation_mismatch": "The endpoint binding targets a different operation.",
    "media_wire_protocol_mismatch": "A chat wire protocol is bound to a media endpoint.",
    "comfyui_workflow_invalid": "The ComfyUI API workflow or its input/output bindings are missing or invalid.",
}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    return list(dict.fromkeys(str(item or "").strip() for item in values if str(item or "").strip()))


def configured_operation_kinds(
    *,
    provider_meta: dict[str, Any],
    model_data: dict[str, Any],
) -> tuple[list[str], bool]:
    """Return only the operation declaration supplied by the user's config."""

    media_limits = dict(model_data.get("mediaLimits") or {})
    for container, keys in (
        (model_data, ("operationKinds", "operations")),
        (media_limits, ("operationKinds",)),
        (provider_meta, ("operationKinds",)),
    ):
        for key in keys:
            if key in container:
                return _strings(container.get(key)), True
    return [], False


def configured_adapter(model_data: dict[str, Any]) -> tuple[str, bool]:
    """Read an adapter without replacing an explicit blank or catalog-only value."""

    endpoint_binding = dict(model_data.get("endpointBinding") or {})
    media_limits = dict(model_data.get("mediaLimits") or {})
    for container, keys in (
        (endpoint_binding, ("adapter",)),
        (model_data, ("adapter",)),
        (media_limits, ("adapter", "adapterProviderId")),
    ):
        for key in keys:
            if key in container:
                return str(container.get(key) or "").strip().lower(), True
    return "", False


def suggested_adapter_for_model(
    *,
    modality: str,
    model_id: str,
    provider_matrix: dict[str, Any],
) -> str:
    """Return an exact catalog suggestion; it never makes a candidate executable."""

    target = str(model_id or "").strip().rsplit("/", 1)[-1].lower()
    if not target:
        return ""
    matches: set[str] = set()
    for entry in list((provider_matrix.get("modalities") or {}).get(modality) or []):
        if not isinstance(entry, dict):
            continue
        model_ids = {str(item or "").strip().lower() for item in list(entry.get("modelIds") or [])}
        if target not in model_ids:
            continue
        adapter = str(entry.get("adapter") or "").strip().lower()
        if adapter:
            matches.add(adapter)
    return next(iter(matches)) if len(matches) == 1 else ""


def evaluate_candidate_readiness(
    *,
    provider_meta: dict[str, Any],
    model_data: dict[str, Any],
    endpoint_binding: dict[str, Any],
    operation_kind: str,
    adapter: str,
    operation_configured: bool,
    adapter_configured: bool,
) -> dict[str, Any]:
    reason_codes: list[str] = []

    if provider_meta.get("is_enabled", provider_meta.get("isEnabled", True)) is False:
        reason_codes.append("provider_disabled")
    if model_data.get("is_enabled", model_data.get("isEnabled", True)) is False:
        reason_codes.append("model_disabled")
    if not str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").strip():
        reason_codes.append("provider_base_url_missing")
    api_standard = str(
        endpoint_binding.get("apiStandard")
        or provider_meta.get("api_standard")
        or provider_meta.get("apiStandard")
        or ""
    ).strip().lower()
    if api_standard != "comfyui" and not str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip():
        reason_codes.append("provider_credential_missing")
    if not operation_configured:
        reason_codes.append("operation_not_configured")
    if not adapter_configured:
        reason_codes.append("adapter_not_configured")

    normalized_adapter = str(adapter or "").strip().lower()
    if not normalized_adapter:
        reason_codes.append("adapter_missing")
    elif normalized_adapter == "catalog_only":
        reason_codes.append("adapter_catalog_only")
    elif normalized_adapter not in RUNTIME_ADAPTER_OPERATION_KINDS:
        reason_codes.append("adapter_unsupported")
    elif operation_kind not in RUNTIME_ADAPTER_OPERATION_KINDS[normalized_adapter]:
        reason_codes.append("adapter_operation_mismatch")
    if normalized_adapter == "comfyui_workflow":
        try:
            validate_comfyui_workflow(dict(model_data.get("mediaLimits") or {}).get("comfyuiWorkflow"))
        except (TypeError, ValueError):
            reason_codes.append("comfyui_workflow_invalid")

    configured_binding = dict(model_data.get("endpointBinding") or {})
    binding_operation = str(configured_binding.get("operationKind") or configured_binding.get("operation_kind") or "").strip()
    if binding_operation and binding_operation != operation_kind:
        reason_codes.append("endpoint_operation_mismatch")
    if str(configured_binding.get("wireProtocol") or configured_binding.get("wire_protocol") or "").strip():
        reason_codes.append("media_wire_protocol_mismatch")

    reason_codes = list(dict.fromkeys(reason_codes))
    return {
        "executable": not reason_codes,
        "reasonCodes": reason_codes,
        "reasonMessages": [READINESS_MESSAGES[code] for code in reason_codes],
    }


__all__ = [
    "READINESS_MESSAGES",
    "RUNTIME_ADAPTER_OPERATION_KINDS",
    "configured_adapter",
    "configured_operation_kinds",
    "evaluate_candidate_readiness",
    "suggested_adapter_for_model",
]
