from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable, Optional

from core.database import db
from core.json_safe import to_jsonable
from core.multimodal_payload_adapter import normalize_artifact_record


CanonicalNode = dict[str, Any]
CanonicalMessage = dict[str, Any]


_INLINE_THINK_PATTERN = re.compile(
    r"<think\b[^>]*>([\s\S]*?)(?:</think>|$)",
    re.IGNORECASE,
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _split_inline_reasoning(value: Any) -> tuple[str, str]:
    raw = str(value or "")
    if "<think" not in raw.lower():
        return raw, ""
    reasoning_parts = [
        str(match.group(1) or "").strip()
        for match in _INLINE_THINK_PATTERN.finditer(raw)
        if str(match.group(1) or "").strip()
    ]
    visible = _INLINE_THINK_PATTERN.sub("", raw).strip()
    return visible, "\n".join(dict.fromkeys(reasoning_parts))


def _reasoning_fingerprint(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _inline_reasoning_node_id(narrative_node_id: Any) -> str:
    return f"{str(narrative_node_id or 'assistant-narrative')}:inline-reasoning"


def normalize_canonical_nodes(nodes: list[CanonicalNode], *, role: str) -> list[CanonicalNode]:
    """Normalize assistant inline reasoning without mutating the stored input.

    Canonical text nodes are the source of truth. Historical rows are projected
    through this function at read time, while active rows use it before writes.
    """

    source_nodes = [dict(node) for node in _as_list(nodes) if isinstance(node, dict)]
    if str(role or "").strip().lower() != "assistant":
        return source_nodes

    inline_reasoning_by_node_id = {
        _inline_reasoning_node_id(node.get("id")): _split_inline_reasoning(node.get("content"))[1]
        for node in source_nodes
        if str(node.get("kind") or "").strip() == "narrative"
        and _split_inline_reasoning(node.get("content"))[1]
    }
    explicit_reasoning = {
        _reasoning_fingerprint(node.get("content"))
        for node in source_nodes
        if str(node.get("kind") or "").strip() == "execution"
        and str(node.get("executionType") or "").strip() == "reasoning"
        and str(node.get("id") or "").strip() not in inline_reasoning_by_node_id
        and _reasoning_fingerprint(node.get("content"))
    }
    normalized: list[CanonicalNode] = []
    emitted_reasoning: set[str] = set()
    for node in source_nodes:
        kind = str(node.get("kind") or "").strip()
        execution_type = str(node.get("executionType") or "").strip()
        if kind == "execution" and execution_type == "reasoning":
            if str(node.get("id") or "").strip() in inline_reasoning_by_node_id:
                continue
            fingerprint = _reasoning_fingerprint(node.get("content"))
            if fingerprint and fingerprint in emitted_reasoning:
                continue
            if fingerprint:
                emitted_reasoning.add(fingerprint)
            normalized.append(node)
            continue
        if kind != "narrative":
            normalized.append(node)
            continue

        visible, inline_reasoning = _split_inline_reasoning(node.get("content"))
        fingerprint = _reasoning_fingerprint(inline_reasoning)
        if fingerprint and fingerprint not in explicit_reasoning and fingerprint not in emitted_reasoning:
            normalized.append(
                {
                    **{
                        key: node.get(key)
                        for key in (
                            "timestamp",
                            "agentName",
                            "agentAvatar",
                            "agentRoleLabel",
                            "finalized",
                            "partial",
                        )
                        if node.get(key) is not None
                    },
                    "id": _inline_reasoning_node_id(node.get("id")),
                    "kind": "execution",
                    "executionType": "reasoning",
                    "content": inline_reasoning,
                    "reasoningKind": "legacy_inline_think",
                    "reasoningUnverified": True,
                }
            )
            emitted_reasoning.add(fingerprint)
        normalized.append({**node, "content": visible})
    return normalized


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
    stored_nodes = [dict(node) for node in _as_list(row.get("nodes")) if isinstance(node, dict)]
    base_nodes = normalize_canonical_nodes(stored_nodes, role=str(row.get("role") or ""))
    stored_artifacts = [dict(item) for item in _as_list(row.get("artifacts")) if isinstance(item, dict)]
    effective_artifacts = merge_artifacts(stored_artifacts, [dict(item) for item in _as_list(runtime_artifacts) if isinstance(item, dict)])
    nodes = base_nodes + _artifact_nodes_for_message(str(row.get("id") or ""), effective_artifacts, metadata)
    derived_content_text, derived_reasoning_text = derive_text_fields(base_nodes)
    has_canonical_text_nodes = any(
        str(node.get("kind") or "").strip() == "narrative"
        or (
            str(node.get("kind") or "").strip() == "execution"
            and str(node.get("executionType") or "").strip() == "reasoning"
        )
        for node in base_nodes
    )
    content_text = derived_content_text if has_canonical_text_nodes else str(row.get("content_text") or "")
    reasoning_text = derived_reasoning_text if has_canonical_text_nodes else str(row.get("reasoning_text") or "")
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
    return format_canonical_chat_rows(session_id, rows)


def format_canonical_chat_rows(session_id: str, rows: list[CanonicalMessage]) -> list[dict[str, Any]]:
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


def _row_ordinal(row: CanonicalMessage) -> int:
    try:
        return int(row.get("ordinal") or 0)
    except Exception:
        return 0


def group_canonical_turn_rows(rows_asc: list[CanonicalMessage]) -> list[list[CanonicalMessage]]:
    groups: list[list[CanonicalMessage]] = []
    current: list[CanonicalMessage] = []
    current_key = ""

    for row in rows_asc:
        run_id = str(row.get("run_id") or "").strip()
        role = str(row.get("role") or "").strip()
        if run_id:
            key = f"run:{run_id}"
            if current and current_key != key:
                groups.append(current)
                current = []
            current_key = key
            current.append(row)
            continue

        if role == "user" and current:
            groups.append(current)
            current = []
        current_key = ""
        current.append(row)

    if current:
        groups.append(current)
    return groups


def select_canonical_turn_window_rows(
    rows_desc: list[CanonicalMessage],
    *,
    limit_turns: int = 1,
) -> tuple[list[CanonicalMessage], int]:
    rows_asc = sorted(rows_desc, key=lambda row: (_row_ordinal(row), str(row.get("created_at") or "")))
    groups = group_canonical_turn_rows(rows_asc)
    if not groups:
        return [], 0
    safe_limit = max(1, min(int(limit_turns or 1), 20))
    selected_groups = groups[-safe_limit:]
    selected_rows = [row for group in selected_groups for row in group]
    return selected_rows, len(selected_groups)


def build_canonical_chat_turn_window(
    session_id: str,
    *,
    before_ordinal: int | None = None,
    limit_turns: int = 1,
    scan_limit: int = 500,
) -> dict[str, Any]:
    safe_scan_limit = max(50, min(int(scan_limit or 500), 2000))
    rows_desc = db.get_chat_canonical_messages_before_ordinal(
        session_id,
        before_ordinal=before_ordinal,
        limit=safe_scan_limit,
    )
    selected_rows, loaded_turn_count = select_canonical_turn_window_rows(
        rows_desc,
        limit_turns=limit_turns,
    )
    if not selected_rows:
        return {
            "messages": [],
            "pageInfo": {
                "hasMore": False,
                "beforeCursor": None,
                "loadedTurnCount": 0,
            },
        }

    min_ordinal = min(_row_ordinal(row) for row in selected_rows)
    return {
        "messages": format_canonical_chat_rows(session_id, selected_rows),
        "pageInfo": {
            "hasMore": db.has_chat_canonical_message_before_ordinal(session_id, min_ordinal),
            "beforeCursor": str(min_ordinal),
            "loadedTurnCount": loaded_turn_count,
        },
    }


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
    stored_nodes = [dict(node) for node in _as_list(row.get("nodes")) if isinstance(node, dict)]
    nodes = normalize_canonical_nodes(stored_nodes, role=str(row.get("role") or ""))
    derived_content_text, derived_reasoning_text = derive_text_fields(nodes)
    errors: list[str] = []
    if str(row.get("content_text") or "") != derived_content_text:
        errors.append("content_text_mismatch")
    if str(row.get("reasoning_text") or "") != (derived_reasoning_text or ""):
        errors.append("reasoning_text_mismatch")
    if str(row.get("role") or "").strip().lower() == "assistant" and any(
        str(node.get("kind") or "").strip() == "narrative"
        and "<think" in str(node.get("content") or "").lower()
        for node in stored_nodes
    ):
        errors.append("assistant_narrative_contains_think")
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
        stored_nodes = [dict(node) for node in _as_list(nodes) if isinstance(node, dict)]
        seeded_nodes = normalize_canonical_nodes(stored_nodes, role=role)
        derived_content_text, derived_reasoning_text = derive_text_fields(seeded_nodes)
        has_canonical_text_nodes = any(
            str(node.get("kind") or "").strip() == "narrative"
            or (
                str(node.get("kind") or "").strip() == "execution"
                and str(node.get("executionType") or "").strip() == "reasoning"
            )
            for node in seeded_nodes
        )
        db.create_chat_canonical_message(
            message_id=message_id,
            session_id=session_id,
            run_id=run_id,
            ordinal=ordinal,
            role=role,
            state=state,
            nodes=seeded_nodes,
            artifacts=[],
            content_text=derived_content_text if has_canonical_text_nodes else (content_text or ""),
            reasoning_text=(derived_reasoning_text or None) if has_canonical_text_nodes else reasoning_text,
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
        next_nodes = normalize_canonical_nodes(next_nodes, role=str(existing.get("role") or ""))
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
