from __future__ import annotations

import re
import uuid
from typing import Any


RUNTIME_CONTINUATION_KIND = "runtime_continuation_request"
RUNTIME_CONTINUATION_SCHEMA_VERSION = 1

_INPUT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
_REQUEST_ID_RE = re.compile(r"^continuation_[A-Za-z0-9]{12,64}$")
_INPUT_KINDS = {"text", "enum", "boolean", "number", "url", "file", "secret"}


class RuntimeContinuationContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def normalize_runtime_continuation_inputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RuntimeContinuationContractError(
            "runtime_continuation_inputs_required",
            "requiredInputs must be a non-empty array.",
        )
    if len(value) > 12:
        raise RuntimeContinuationContractError(
            "runtime_continuation_too_many_inputs",
            "requiredInputs may contain at most 12 entries.",
        )

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise RuntimeContinuationContractError(
                "runtime_continuation_input_invalid",
                "Every requiredInputs entry must be an object.",
            )
        input_id = _clean_text(raw.get("id"), limit=120)
        question = _clean_text(raw.get("question"), limit=1200)
        input_kind = _clean_text(raw.get("kind") or "text", limit=24).lower()
        if not _INPUT_ID_RE.fullmatch(input_id):
            raise RuntimeContinuationContractError(
                "runtime_continuation_input_id_invalid",
                "Each input id must start with a letter and contain only letters, digits, '.', '_' or '-'.",
            )
        if input_id in seen_ids:
            raise RuntimeContinuationContractError(
                "runtime_continuation_input_id_duplicate",
                f"Duplicate continuation input id: {input_id}",
            )
        if not question:
            raise RuntimeContinuationContractError(
                "runtime_continuation_question_required",
                f"Continuation input '{input_id}' is missing a question.",
            )
        if input_kind not in _INPUT_KINDS:
            raise RuntimeContinuationContractError(
                "runtime_continuation_input_kind_invalid",
                f"Unsupported continuation input kind: {input_kind}",
            )

        options = [
            _clean_text(option, limit=240)
            for option in list(raw.get("options") or [])
            if _clean_text(option, limit=240)
        ]
        options = list(dict.fromkeys(options))[:24]
        if input_kind == "enum" and not options:
            raise RuntimeContinuationContractError(
                "runtime_continuation_enum_options_required",
                f"Enum continuation input '{input_id}' requires options.",
            )
        if input_kind != "enum" and options:
            raise RuntimeContinuationContractError(
                "runtime_continuation_options_not_allowed",
                f"Continuation input '{input_id}' may only declare options when kind='enum'.",
            )

        item: dict[str, Any] = {
            "id": input_id,
            "kind": input_kind,
            "question": question,
        }
        reason = _clean_text(raw.get("reason"), limit=800)
        if reason:
            item["reason"] = reason
        if options:
            item["options"] = options
        normalized.append(item)
        seen_ids.add(input_id)
    return normalized


def build_runtime_continuation_request(
    *,
    required_inputs: Any,
    summary: Any,
    source: dict[str, Any],
) -> dict[str, Any]:
    normalized_inputs = normalize_runtime_continuation_inputs(required_inputs)
    compact_source = {
        key: _clean_text(source.get(key), limit=160)
        for key in (
            "sessionId",
            "runId",
            "runtimeEpisodeId",
            "taskBriefId",
            "delegationId",
            "agentId",
            "toolCallId",
        )
        if _clean_text(source.get(key), limit=160)
    }
    return {
        "kind": RUNTIME_CONTINUATION_KIND,
        "schemaVersion": RUNTIME_CONTINUATION_SCHEMA_VERSION,
        "requestId": f"continuation_{uuid.uuid4().hex}",
        "summary": _clean_text(summary, limit=1200)
        or "Runtime execution needs explicit input before it can continue.",
        "requiredInputs": normalized_inputs,
        "resumePolicy": "same_episode",
        "source": compact_source,
    }


def normalize_runtime_continuation_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeContinuationContractError(
            "runtime_continuation_request_invalid",
            "The continuation request must be an object.",
        )
    if str(value.get("kind") or "").strip() != RUNTIME_CONTINUATION_KIND:
        raise RuntimeContinuationContractError(
            "runtime_continuation_kind_invalid",
            "The continuation request kind is invalid.",
        )
    try:
        schema_version = int(value.get("schemaVersion"))
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != RUNTIME_CONTINUATION_SCHEMA_VERSION:
        raise RuntimeContinuationContractError(
            "runtime_continuation_schema_unsupported",
            "The continuation request schema version is unsupported.",
        )
    request_id = _clean_text(value.get("requestId"), limit=96)
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise RuntimeContinuationContractError(
            "runtime_continuation_request_id_invalid",
            "The continuation request id is invalid.",
        )
    resume_policy = _clean_text(value.get("resumePolicy"), limit=40)
    if resume_policy != "same_episode":
        raise RuntimeContinuationContractError(
            "runtime_continuation_resume_policy_invalid",
            "Only same_episode continuation is supported.",
        )
    required_inputs = normalize_runtime_continuation_inputs(value.get("requiredInputs"))
    source = dict(value.get("source") or {}) if isinstance(value.get("source"), dict) else {}
    return {
        "kind": RUNTIME_CONTINUATION_KIND,
        "schemaVersion": RUNTIME_CONTINUATION_SCHEMA_VERSION,
        "requestId": request_id,
        "summary": _clean_text(value.get("summary"), limit=1200)
        or "Runtime execution needs explicit input before it can continue.",
        "requiredInputs": required_inputs,
        "resumePolicy": "same_episode",
        "source": {
            key: _clean_text(source.get(key), limit=160)
            for key in (
                "sessionId",
                "runId",
                "runtimeEpisodeId",
                "taskBriefId",
                "delegationId",
                "agentId",
                "toolCallId",
            )
            if _clean_text(source.get(key), limit=160)
        },
    }


def validate_runtime_continuation_answers(
    request: dict[str, Any],
    answers: Any,
) -> dict[str, Any]:
    normalized_request = normalize_runtime_continuation_request(request)
    if not isinstance(answers, dict) or not answers:
        raise RuntimeContinuationContractError(
            "runtime_resume_inputs_required",
            "continuation_inputs must be a non-empty object.",
        )
    required_by_id = {
        str(item["id"]): item
        for item in normalized_request["requiredInputs"]
    }
    answer_keys = {str(key) for key in answers}
    required_keys = set(required_by_id)
    missing = sorted(required_keys - answer_keys)
    unknown = sorted(answer_keys - required_keys)
    if missing:
        raise RuntimeContinuationContractError(
            "runtime_resume_inputs_missing",
            f"Missing continuation inputs: {', '.join(missing)}",
        )
    if unknown:
        raise RuntimeContinuationContractError(
            "runtime_resume_inputs_unknown",
            f"Unknown continuation inputs: {', '.join(unknown)}",
        )

    normalized_answers: dict[str, Any] = {}
    for input_id, spec in required_by_id.items():
        value = answers.get(input_id)
        kind = str(spec.get("kind") or "text")
        if kind == "boolean":
            if not isinstance(value, bool):
                raise RuntimeContinuationContractError(
                    "runtime_resume_input_type_invalid",
                    f"Continuation input '{input_id}' must be a boolean.",
                )
            normalized_answers[input_id] = value
            continue
        if kind == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise RuntimeContinuationContractError(
                    "runtime_resume_input_type_invalid",
                    f"Continuation input '{input_id}' must be a number.",
                )
            normalized_answers[input_id] = value
            continue
        if not isinstance(value, str) or not value.strip():
            raise RuntimeContinuationContractError(
                "runtime_resume_input_type_invalid",
                f"Continuation input '{input_id}' must be a non-empty string.",
            )
        text = value.strip()
        if kind == "enum" and text not in set(spec.get("options") or []):
            raise RuntimeContinuationContractError(
                "runtime_resume_input_option_invalid",
                f"Continuation input '{input_id}' must be one of its declared options.",
            )
        normalized_answers[input_id] = text
    return normalized_answers


__all__ = [
    "RUNTIME_CONTINUATION_KIND",
    "RUNTIME_CONTINUATION_SCHEMA_VERSION",
    "RuntimeContinuationContractError",
    "build_runtime_continuation_request",
    "normalize_runtime_continuation_inputs",
    "normalize_runtime_continuation_request",
    "validate_runtime_continuation_answers",
]
