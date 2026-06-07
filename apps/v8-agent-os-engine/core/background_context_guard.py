from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from core.context_governance import emit_context_prepared_event
from core.context_orchestrator import PreparedContext, context_orchestrator


@dataclass(frozen=True)
class BackgroundMaterial:
    title: str
    content: str
    kind: str = "material"


def _chunk_text(text: str, *, max_chars: int) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        if end < len(normalized):
            boundary = max(
                normalized.rfind("\n", start, end),
                normalized.rfind("。", start, end),
                normalized.rfind(".", start, end),
            )
            if boundary > start + max_chars // 3:
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _coerce_materials(items: Sequence[BackgroundMaterial | Mapping[str, Any]] | None) -> list[BackgroundMaterial]:
    materials: list[BackgroundMaterial] = []
    for index, item in enumerate(list(items or []), start=1):
        if isinstance(item, BackgroundMaterial):
            title = item.title
            content = item.content
            kind = item.kind
        elif isinstance(item, Mapping):
            title = str(item.get("title") or f"material {index}")
            content = str(item.get("content") or "")
            kind = str(item.get("kind") or "material")
        else:
            continue
        if str(content or "").strip():
            materials.append(BackgroundMaterial(title=str(title or f"material {index}"), content=content, kind=kind))
    return materials


def build_background_guard_messages(
    *,
    system_prompt: str,
    instruction: str,
    materials: Sequence[BackgroundMaterial | Mapping[str, Any]] | None = None,
    chunk_chars: int = 3200,
) -> list[BaseMessage]:
    """Build message history that lets ContextOrchestrator compact background material.

    Background agents often construct a single giant prompt. ContextOrchestrator
    can compact histories, not one huge "current" HumanMessage. This helper
    turns long background material into prior message chunks and keeps the
    actual instruction as the latest HumanMessage.
    """

    messages: list[BaseMessage] = []
    if str(system_prompt or "").strip():
        messages.append(SystemMessage(content=str(system_prompt).strip()))
    for material in _coerce_materials(materials):
        chunks = _chunk_text(material.content, max_chars=max(800, int(chunk_chars or 3200)))
        total = len(chunks)
        for part_index, chunk in enumerate(chunks, start=1):
            messages.append(
                HumanMessage(
                    content=(
                        f"[BACKGROUND MATERIAL: {material.title} | {material.kind} | {part_index}/{total}]\n"
                        f"{chunk}\n"
                        "[/BACKGROUND MATERIAL]"
                    )
                )
            )
    messages.append(HumanMessage(content=str(instruction or "").strip() or "Use the prepared background material."))
    return messages


def prepare_background_model_messages(
    *,
    system_prompt: str,
    instruction: str,
    materials: Sequence[BackgroundMaterial | Mapping[str, Any]] | None = None,
    runtime_kind: str,
    target_role: str,
    resolved_model_id: str | None = None,
    keep_recent_override: int = 1,
    component: str = "background",
    node: str = "background_context",
    emit_event: bool = True,
) -> PreparedContext:
    messages = build_background_guard_messages(
        system_prompt=system_prompt,
        instruction=instruction,
        materials=materials,
    )
    prepared = context_orchestrator.prepare(
        messages=messages,
        runtime_kind=runtime_kind,
        target_role=target_role,
        resolved_model_id=resolved_model_id,
        keep_recent_override=max(1, int(keep_recent_override or 1)),
    )
    if emit_event:
        emit_context_prepared_event(
            prepared.audit,
            component=component,
            node=node,
            agent_id=target_role,
        )
    return prepared

