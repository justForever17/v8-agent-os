from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.database import db
from core.json_safe import to_jsonable
from core.multimodal_payload_adapter import normalize_artifact_record


CanonicalNode = dict[str, Any]
CanonicalMessage = dict[str, Any]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def derive_text_fields(nodes: list[CanonicalNode]) -> tuple[str, str]:
    narrative_parts: list[str] = []
    reasoning_parts: list[str] = []
    for node in _as_list(nodes):
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind") or "").strip()
        if kind == "narrative":
            narrative_parts.append(str(node.get("content") or ""))
        elif kind == "execution" and str(node.get("executionType") or "").strip() == "reasoning":
            reasoning_parts.append(str(node.get("content") or ""))
    return "".join(narrative_parts), "".join(reasoning_parts)


def derive_tool_invocations(nodes: list[CanonicalNode]) -> list[dict[str, Any]]:
    invocations: list[dict[str, Any]] = []
    call_index: dict[str, dict[str, Any]] = {}
    for node in _as_list(nodes):
        if not isinstance(node, dict):
            continue
        if str(node.get("kind") or "").strip() != "execution":
            continue
        execution_type = str(node.get("executionType") or "").strip()
        tool_call_id = str(node.get("toolCallId") or "").strip()
        tool_name = str(node.get("toolName") or "").strip()
        if execution_type == "tool_call":
            invocation = {
                "toolCallId": tool_call_id or None,
                "toolName": tool_name or None,
                "args": to_jsonable(node.get("args")),
                "result": None,
            }
            invocations.append(invocation)
            if tool_call_id:
                call_index[tool_call_id] = invocation
        elif execution_type == "tool_result":
            invocation = call_index.get(tool_call_id)
            if invocation is not None:
                invocation["result"] = to_jsonable(node.get("result"))
            else:
                invocations.append(
                    {
                        "toolCallId": tool_call_id or None,
                        "toolName": tool_name or None,
                        "args": None,
                        "result": to_jsonable(node.get("result")),
                    }
                )
    return [item for item in invocations if isinstance(item, dict)]


def merge_artifacts(base: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    def _fingerprint(artifact: dict[str, Any]) -> str:
        return str(
            artifact.get("id")
            or artifact.get("artifactId")
            or artifact.get("workspacePath")
            or artifact.get("sourcePath")
            or artifact.get("previewUrl")
            or artifact.get("externalUrl")
            or f"{artifact.get('kind') or 'artifact'}:{artifact.get('title') or artifact.get('displayLabel') or ''}"
        ).strip()

    for item in [*base, *incoming]:
        normalized = normalize_artifact_record(item) if isinstance(item, dict) else None
        if not normalized:
            continue
        fingerprint = _fingerprint(normalized)
        if not fingerprint:
            merged.append(dict(normalized))
            continue
        existing_index = seen.get(fingerprint)
        if existing_index is None:
            seen[fingerprint] = len(merged)
            merged.append(dict(normalized))
            continue
        merged[existing_index] = {**merged[existing_index], **normalized}
    return merged


def _artifact_nodes_for_message(message_id: str, artifacts: list[dict[str, Any]], metadata: dict[str, Any]) -> list[CanonicalNode]:
    timestamp = int(metadata.get("timestamp") or 0) or 0
    agent_name = metadata.get("agentName")
    agent_avatar = metadata.get("agentAvatar")
    agent_role_label = metadata.get("agentRoleLabel")
    nodes: list[CanonicalNode] = []
    for index, artifact in enumerate(artifacts):
        artifact_id = str(artifact.get("id") or artifact.get("artifactId") or f"{message_id}:artifact:{index}").strip()
        nodes.append(
            {
                "id": f"{message_id}:artifact:{artifact_id}",
                "kind": "artifact",
                "artifact": dict(artifact),
                "timestamp": timestamp,
                "agentName": agent_name,
                "agentAvatar": agent_avatar,
                "agentRoleLabel": agent_role_label,
            }
        )
    return nodes


def format_canonical_message(row: CanonicalMessage, runtime_artifacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    metadata = _as_dict(row.get("metadata"))
    base_nodes = [dict(node) for node in _as_list(row.get("nodes")) if isinstance(node, dict)]
    stored_artifacts = [dict(item) for item in _as_list(row.get("artifacts")) if isinstance(item, dict)]
    effective_artifacts = merge_artifacts(stored_artifacts, [dict(item) for item in _as_list(runtime_artifacts) if isinstance(item, dict)])
    nodes = base_nodes + _artifact_nodes_for_message(str(row.get("id") or ""), effective_artifacts, metadata)
    derived_content_text, derived_reasoning_text = derive_text_fields(base_nodes)
    content_text = derived_content_text or str(row.get("content_text") or "")
    reasoning_text = derived_reasoning_text or str(row.get("reasoning_text") or "")
    return {
        "id": row.get("id"),
        "role": row.get("role"),
        "runId": row.get("run_id"),
        "state": row.get("state"),
        "version": int(row.get("version") or 1),
        "content": content_text,
        "reasoningContent": reasoning_text or None,
        "timestamp": int(metadata.get("timestamp") or 0) or 0,
        "createdAt": row.get("created_at"),
        "agentName": metadata.get("agentName"),
        "agentAvatar": metadata.get("agentAvatar"),
        "agentRoleLabel": metadata.get("agentRoleLabel"),
        "agentId": metadata.get("agentId"),
        "images": _as_list(metadata.get("images")),
        "artifacts": effective_artifacts,
        "metadata": metadata,
        "nodes": nodes,
        "toolInvocations": derive_tool_invocations(base_nodes),
    }


def build_canonical_chat_messages(session_id: str) -> list[dict[str, Any]]:
    rows = db.get_chat_canonical_messages(session_id)
    if not rows:
        return []
    artifacts = db.list_runtime_artifacts(session_id=session_id, limit=1000)
    artifacts_by_message: dict[str, list[dict[str, Any]]] = {}
    artifacts_by_run: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        message_id = str(artifact.get("message_id") or artifact.get("messageId") or "").strip()
        run_id = str(artifact.get("run_id") or artifact.get("runId") or "").strip()
        if message_id:
            artifacts_by_message.setdefault(message_id, []).append(dict(artifact))
        elif run_id:
            artifacts_by_run.setdefault(run_id, []).append(dict(artifact))

    formatted: list[dict[str, Any]] = []
    for row in rows:
        message_id = str(row.get("id") or "").strip()
        run_id = str(row.get("run_id") or "").strip()
        runtime_artifacts = artifacts_by_message.get(message_id, [])
        if not runtime_artifacts and run_id:
            runtime_artifacts = artifacts_by_run.get(run_id, [])
        formatted.append(format_canonical_message(row, runtime_artifacts))
    return formatted


def export_legacy_message_payload(row: CanonicalMessage) -> dict[str, Any]:
    formatted = format_canonical_message(row)
    return {
        "id": formatted["id"],
        "session_id": row.get("session_id"),
        "role": formatted["role"],
        "content": formatted["content"],
        "reasoning_content": formatted["reasoningContent"],
        "tool_calls": formatted.get("toolInvocations") or None,
        "images": formatted.get("images") or None,
        "metadata": formatted.get("metadata") or {},
        "agent_id": formatted.get("agentId"),
        "agent_name": formatted.get("agentName"),
        "agent_avatar": formatted.get("agentAvatar"),
        "agent_role_label": formatted.get("agentRoleLabel"),
    }


INTERNAL_TOOL_ARG_MARKERS = (
    "ToolRuntime(",
    "AsyncCallbackManager",
    "PregelScratchpad",
    "__pregel_",
    "stream_writer=",
)


def _contains_internal_tool_arg(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in INTERNAL_TOOL_ARG_MARKERS)
    if isinstance(value, dict):
        return any(_contains_internal_tool_arg(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_internal_tool_arg(item) for item in value)
    return False


def validate_canonical_message_invariants(row: CanonicalMessage) -> list[str]:
    nodes = [dict(node) for node in _as_list(row.get("nodes")) if isinstance(node, dict)]
    derived_content_text, derived_reasoning_text = derive_text_fields(nodes)
    errors: list[str] = []
    if str(row.get("content_text") or "") != derived_content_text:
        errors.append("content_text_mismatch")
    if str(row.get("reasoning_text") or "") != (derived_reasoning_text or ""):
        errors.append("reasoning_text_mismatch")
    for node in nodes:
        if str(node.get("kind") or "").strip() != "execution":
            continue
        if str(node.get("executionType") or "").strip() != "tool_call":
            continue
        if _contains_internal_tool_arg(node.get("args")):
            errors.append("tool_args_contain_runtime_internal")
            break
    return errors


@dataclass(slots=True)
class CanonicalTranscriptMutation:
    message_id: str
    node_id: Optional[str]
    version: int
    state: str


class CanonicalTranscriptBuilder:
    def create_message(
        self,
        *,
        message_id: str,
        session_id: str,
        run_id: Optional[str],
        ordinal: int,
        role: str,
        state: str,
        metadata: Optional[dict[str, Any]] = None,
        nodes: Optional[list[CanonicalNode]] = None,
        content_text: Optional[str] = None,
        reasoning_text: Optional[str] = None,
    ) -> CanonicalMessage:
        seeded_nodes = [dict(node) for node in _as_list(nodes) if isinstance(node, dict)]
        derived_content_text, derived_reasoning_text = derive_text_fields(seeded_nodes)
        db.create_chat_canonical_message(
            message_id=message_id,
            session_id=session_id,
            run_id=run_id,
            ordinal=ordinal,
            role=role,
            state=state,
            nodes=seeded_nodes,
            artifacts=[],
            content_text=content_text if content_text is not None else derived_content_text,
            reasoning_text=reasoning_text if reasoning_text is not None else (derived_reasoning_text or None),
            metadata=metadata or {},
        )
        return db.get_chat_canonical_message(message_id) or {}

    def mutate_message(
        self,
        message_id: str,
        mutator: Callable[[list[CanonicalNode], dict[str, Any]], tuple[list[CanonicalNode], Optional[str]]],
        *,
        state: Optional[str] = None,
        metadata_updates: Optional[dict[str, Any]] = None,
        finalize: bool = False,
    ) -> CanonicalTranscriptMutation:
        existing = db.get_chat_canonical_message(message_id)
        if not existing:
            raise ValueError(f"Canonical message '{message_id}' not found")
        current_nodes = [dict(node) for node in _as_list(existing.get("nodes")) if isinstance(node, dict)]
        current_metadata = dict(_as_dict(existing.get("metadata")))
        if metadata_updates:
            current_metadata.update(metadata_updates)
        next_nodes, node_id = mutator(current_nodes, current_metadata)
        content_text, reasoning_text = derive_text_fields(next_nodes)
        updated = db.update_chat_canonical_message(
            message_id,
            state=state or existing.get("state") or "pending",
            nodes=next_nodes,
            artifacts=existing.get("artifacts") or [],
            content_text=content_text,
            reasoning_text=reasoning_text or None,
            metadata=current_metadata,
            finalized_at=datetime.now(timezone.utc).isoformat() if finalize else existing.get("finalized_at"),
        )
        version = int((updated or existing).get("version") or 1)
        return CanonicalTranscriptMutation(
            message_id=message_id,
            node_id=node_id,
            version=version,
            state=str((updated or existing).get("state") or state or "pending"),
        )

    def set_message_state(
        self,
        message_id: str,
        *,
        state: str,
        metadata_updates: Optional[dict[str, Any]] = None,
        finalize: bool = False,
    ) -> CanonicalTranscriptMutation:
        return self.mutate_message(
            message_id,
            lambda nodes, metadata: (nodes, None),
            state=state,
            metadata_updates=metadata_updates,
            finalize=finalize,
        )
