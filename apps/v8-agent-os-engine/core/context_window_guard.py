from __future__ import annotations

from typing import Any, Dict, Iterable

from core.llm_factory import llm_factory
from core.storage import storage


MIN_TEXT_CONTEXT_WINDOW_TOKENS = 262_144
DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS = 32_000

_NON_TEXT_MODEL_TYPES = {
    "IMAGE",
    "VIDEO",
    "VOICE",
    "MUSIC",
    "MODEL3D",
    "WORKFLOW",
    "EMBEDDING",
    "RERANK",
    "VECTOR",
}
_NON_TEXT_CAPABILITY_CLASSES = {
    "media_generation",
    "embedding",
    "reranker",
    "rerank",
    "workflow",
    "model3d",
}
_NON_TEXT_ROLES = {
    "embedding",
    "reranker",
    "extensions_reranker",
    "computer_use_candidate_reranker",
}


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _model_meta(model_ref: str) -> Dict[str, Any]:
    model_ref = str(model_ref or "").strip()
    if not model_ref:
        return {"is_found": False}
    try:
        return llm_factory.get_model_metadata(model_ref)
    except Exception:
        return {"is_found": False, "model_ref": model_ref}


def is_text_generation_model_ref(model_ref: str, *, role: str | None = None) -> bool:
    role_key = str(role or "").strip()
    if role_key in _NON_TEXT_ROLES:
        return False
    meta = _model_meta(model_ref)
    model_record = dict(meta.get("model_record") or {})
    model_type = str(model_record.get("type") or "").upper()
    capability_class = str(
        meta.get("capability_class") or model_record.get("capabilityClass") or ""
    ).strip().lower()
    if model_type in _NON_TEXT_MODEL_TYPES:
        return False
    if capability_class in _NON_TEXT_CAPABILITY_CLASSES:
        return False
    capabilities = dict(meta.get("capabilities") or model_record.get("capabilities") or {})
    if any(bool(capabilities.get(key)) for key in ("embedding", "rerank", "image", "video", "voice", "music", "workflow", "model3d")):
        if not any(bool(capabilities.get(key)) for key in ("chat", "text", "reasoning", "toolCalling", "vision", "multimodal")):
            return False
    return True


def _participant(
    *,
    role: str,
    runtime_kind: str,
    model_ref: str,
    fallback_context_window_tokens: int,
) -> Dict[str, Any] | None:
    model_ref = str(model_ref or "").strip()
    if not model_ref:
        return None
    if not is_text_generation_model_ref(model_ref, role=role):
        return None
    meta = _model_meta(model_ref)
    context_window = _coerce_int(meta.get("global_context_window"))
    source = "model_metadata" if context_window else "missing"
    reason = ""
    valid = True
    effective_for_min = context_window
    if context_window is None:
        valid = False
        reason = "missing_context_window"
        effective_for_min = int(fallback_context_window_tokens or DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS)
    elif context_window < MIN_TEXT_CONTEXT_WINDOW_TOKENS:
        valid = False
        reason = "below_min_context_window"
        effective_for_min = context_window
    return {
        "role": role,
        "runtimeKind": runtime_kind,
        "modelRef": model_ref,
        "contextWindowTokens": context_window,
        "effectiveForMinTokens": effective_for_min,
        "source": source,
        "valid": valid,
        "reason": reason,
    }


def _summary_model_ref() -> str:
    try:
        return str(storage.get_role_model_id("summary") or "").strip()
    except Exception:
        return ""


class ContextWindowGuard:
    def resolve(
        self,
        *,
        target_role: str,
        runtime_kind: str,
        model_ref: str | None,
        compression: Dict[str, Any] | None = None,
        extra_participants: Iterable[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        compression = dict(compression or {})
        fallback_window = _coerce_int(compression.get("default_context_window_tokens")) or DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS
        trigger_ratio = float(compression.get("trigger_ratio") or compression.get("hard_trigger_ratio") or 0.94)
        participants: list[Dict[str, Any]] = []
        target = _participant(
            role=str(target_role or "default").strip() or "default",
            runtime_kind=str(runtime_kind or "chat").strip() or "chat",
            model_ref=str(model_ref or "").strip(),
            fallback_context_window_tokens=fallback_window,
        )
        if target:
            participants.append(target)
        if compression.get("use_llm_summary", True):
            summary_ref = _summary_model_ref()
            summary = _participant(
                role="summary",
                runtime_kind="context_governance",
                model_ref=summary_ref,
                fallback_context_window_tokens=fallback_window,
            )
            if summary:
                participants.append(summary)
        for item in extra_participants or []:
            if not isinstance(item, dict):
                continue
            extra = _participant(
                role=str(item.get("role") or "runtime_text").strip() or "runtime_text",
                runtime_kind=str(item.get("runtimeKind") or runtime_kind or "runtime").strip() or "runtime",
                model_ref=str(item.get("modelRef") or item.get("model_ref") or "").strip(),
                fallback_context_window_tokens=fallback_window,
            )
            if extra:
                participants.append(extra)

        min_candidates = [
            _coerce_int(item.get("effectiveForMinTokens")) or fallback_window
            for item in participants
        ]
        effective_window = min(min_candidates) if min_candidates else fallback_window
        warnings: list[Dict[str, Any]] = []
        for item in participants:
            reason = str(item.get("reason") or "").strip()
            if reason:
                warnings.append(
                    {
                        "reason": reason,
                        "role": item.get("role"),
                        "runtimeKind": item.get("runtimeKind"),
                        "modelRef": item.get("modelRef"),
                        "contextWindowTokens": item.get("contextWindowTokens"),
                    }
                )
        max_summary_input = _coerce_int(compression.get("max_summary_input_tokens")) or 5000
        summary_budget = max(256, min(max_summary_input, int(effective_window * 0.90)))
        return {
            "effectiveContextWindowTokens": int(effective_window),
            "participants": participants,
            "triggerLimitTokens": max(1, int(effective_window * trigger_ratio)),
            "summaryInputBudgetTokens": summary_budget,
            "warnings": warnings,
            "minimumRequiredContextWindowTokens": MIN_TEXT_CONTEXT_WINDOW_TOKENS,
        }


context_window_guard = ContextWindowGuard()


def validate_text_role_model_window(role: str, model_ref: str) -> Dict[str, Any]:
    model_ref = str(model_ref or "").strip()
    if not model_ref or not is_text_generation_model_ref(model_ref, role=role):
        return {"ok": True, "reason": "not_text_generation"}
    participant = _participant(
        role=str(role or "").strip() or "default",
        runtime_kind="model_binding",
        model_ref=model_ref,
        fallback_context_window_tokens=DEFAULT_UNKNOWN_CONTEXT_WINDOW_TOKENS,
    )
    if not participant:
        return {"ok": True, "reason": "not_text_generation"}
    if participant.get("valid"):
        return {"ok": True, "participant": participant}
    reason = str(participant.get("reason") or "missing_context_window")
    return {
        "ok": False,
        "reason": reason,
        "participant": participant,
        "minimumRequiredContextWindowTokens": MIN_TEXT_CONTEXT_WINDOW_TOKENS,
        "message": (
            f"模型 {model_ref} 未配置上下文窗口，不能用于长上下文文本生成角色。"
            if reason == "missing_context_window"
            else f"模型 {model_ref} 上下文窗口低于 {MIN_TEXT_CONTEXT_WINDOW_TOKENS} tokens，不能用于长上下文文本生成角色。"
        ),
    }
