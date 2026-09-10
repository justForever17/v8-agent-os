"""Late image hydration for vision-capable agents; transcripts keep references only."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.model_capability_matrix import build_effective_capability_matrix
from core.tools.vision_image_inputs import prepare_ordered_images, ordered_image_content


def caller_accepts_direct_images(context: dict[str, Any]) -> bool:
    from core.storage import storage
    from core.model_control_plane import model_control_plane

    if storage.get_supervisor_config().get("compressedDirectImages") is not True:
        return False
    agent_id = str(context.get("agent_id") or context.get("subagent_id") or "")
    kind = str(context.get("runtime_kind") or "chat")
    if agent_id and agent_id != "supervisor":
        role = f"agent:{agent_id}"
    elif kind in {"chat", "supervisor"}:
        role = "supervisor"
    else:
        return False  # Runtime helper calls are not implicitly the Supervisor.
    resolved = model_control_plane.resolve_model_for_role(role)
    model = dict(resolved.get("resolvedModel") or {})
    provider = dict(resolved.get("resolvedProvider") or {})
    return bool(build_effective_capability_matrix(
        capability_class=str(model.get("capabilityClass") or ""),
        capabilities=model.get("capabilities") or {},
        api_standard=str(provider.get("api_standard") or "openai"),
    ).get("supports_multimodal"))


def direct_image_receipt(prepared, *, prompt: str, tool_call_id: str, context: dict) -> str:
    return json.dumps({
        "directImageInput": 1, "toolCallId": tool_call_id,
        "sessionId": context.get("session_id"), "runId": context.get("run_id"),
        "status": "prepared_not_analyzed", "prompt": prompt,
        "images": [item["source"] for item in prepared],
        "instruction": "The next model request attaches these numbered images. Assess them yourself; this receipt is not an analysis or proof of completion.",
    }, ensure_ascii=False)


def hydrate_direct_images(messages, *, context: dict, supports_multimodal: bool):
    """Only hydrate paired, current tool results; never trust paths in user prose.

    Re-read local sources under current workspace/session authority and compare
    their versions. No base64 enters durable messages or the tool event stream.
    Remote media retain the separate analyzer path (no duplicate download).
    """
    result = list(messages)
    calls: dict[str, Any] = {}
    start = len(result)
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage):
            calls = {str(c.get("id")): c for c in message.tool_calls}
            start = index + 1
            break
    for message in result[start:]:
        if not isinstance(message, ToolMessage) or message.name != "vision_media_analyzer":
            continue
        call = calls.get(message.tool_call_id) or {}
        if call.get("name") != "vision_media_analyzer":
            continue
        body = message.content
        raw_ref = (message.additional_kwargs.get("v8_tool_output_budget") or {}).get("rawRef")
        if raw_ref:
            from core.observability_db import observability_db
            record = observability_db.get_tool_observation_record(raw_ref) or {}
            metadata = record.get("metadata") or {}
            if (record.get("tool_call_id") != message.tool_call_id
                    or record.get("tool_name") != message.name
                    or metadata.get("sessionId") != context.get("session_id")):
                continue
            body = record.get("raw_body_text") or ""
        try:
            receipt = json.loads(body) if isinstance(body, str) else {}
        except (ValueError, TypeError):
            continue
        if not isinstance(receipt, dict) or receipt.get("directImageInput") != 1:
            continue
        if (receipt.get("toolCallId") != message.tool_call_id
                or receipt.get("sessionId") != context.get("session_id")
                or receipt.get("runId") != context.get("run_id")):
            continue
        sources = receipt.get("images") or []
        try:
            if not supports_multimodal:
                raise ValueError("current_model_has_no_image_input")
            if not sources or any(s.get("sourceKind") != "file" for s in sources):
                raise ValueError("direct_images_require_local_sources")
            prepared = prepare_ordered_images(
                [{"file_path": s["sourceRef"], "label": s.get("label")} for s in sources],
                runtime_context=context, remote_guard=lambda _url: None,
            )
            if any(a["source"]["sourceSha256"] != b["sourceSha256"] for a, b in zip(prepared, sources)):
                raise ValueError("image_changed_since_tool_read")
            content = ordered_image_content(
                prepared, prompt=str(receipt.get("prompt") or ""), api_standard="openai", provider_id="", model_id="",
            )
            result.append(HumanMessage(content=content, additional_kwargs={"v8_direct_image_input": True}))
        except (ValueError, KeyError, TypeError) as exc:
            # A model/config/file change must not become an invisible success.
            reason = getattr(exc, "code", None) or str(exc)
            result.append(HumanMessage(content=(
                f"Image input unavailable for tool {message.tool_call_id}: {reason}. "
                "No image was attached. Do not claim to have seen it; read the current image again or report the capability change."
            )))
    return result
