from __future__ import annotations

from typing import Any, Iterable


def _unique_str_list(values: Iterable[Any] | None) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def _normalize_skill_entries(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in list(values or []):
        if not isinstance(value, dict):
            continue
        skill_name = str(value.get("skillName") or value.get("name") or "").strip()
        skill_root = str(value.get("skillRoot") or value.get("path") or "").strip()
        identity = skill_name or skill_root
        if not identity or identity in seen:
            continue
        seen.add(identity)
        entries.append(
            {
                "skillName": skill_name,
                "skillRoot": skill_root,
                "instructionPath": str(value.get("instructionPath") or "").strip(),
                "referencesDir": str(value.get("referencesDir") or "").strip(),
                "scriptsDir": str(value.get("scriptsDir") or "").strip(),
                "assetsDir": str(value.get("assetsDir") or "").strip(),
                "templatesDir": str(value.get("templatesDir") or "").strip(),
                "availableFiles": [
                    str(item).strip()
                    for item in list(value.get("availableFiles") or [])
                    if str(item).strip()
                ],
            }
        )
    return entries


def build_delegation_context(
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
    query: str | None = None,
    mode: str,
    source_runtime_kind: str | None = None,
    selected_skill_names: Iterable[Any] | None = None,
    selected_skill_entries: Iterable[Any] | None = None,
    selected_mcp_tools: Iterable[Any] | None = None,
    selected_plugin_host_tools: Iterable[Any] | None = None,
    selected_baseline_tools: Iterable[Any] | None = None,
    prompt_addition: str | None = None,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "agentId": str(agent_id or "").strip() or None,
        "agentName": str(agent_name or "").strip() or None,
        "query": str(query or "").strip(),
        "mode": str(mode or "").strip() or "serial",
        "sourceRuntimeKind": str(source_runtime_kind or "").strip() or None,
        "selectedSkillNames": _unique_str_list(selected_skill_names),
        "selectedSkillEntries": _normalize_skill_entries(selected_skill_entries),
        "selectedMcpTools": _unique_str_list(selected_mcp_tools),
        "selectedPluginHostTools": _unique_str_list(selected_plugin_host_tools),
        "selectedBaselineTools": _unique_str_list(selected_baseline_tools),
        "promptAddition": str(prompt_addition or "").strip(),
    }
    if invocation_id:
        payload["invocationId"] = str(invocation_id).strip()
    return payload


def latest_delegation_context(
    contexts: Iterable[Any] | None,
    *,
    agent_id: str | None = None,
) -> dict[str, Any]:
    target_agent_id = str(agent_id or "").strip()
    latest_match: dict[str, Any] | None = None
    latest_any: dict[str, Any] | None = None
    for item in list(contexts or []):
        if not isinstance(item, dict):
            continue
        normalized = build_delegation_context(
            agent_id=item.get("agentId"),
            agent_name=item.get("agentName"),
            query=item.get("query"),
            mode=item.get("mode") or "serial",
            source_runtime_kind=item.get("sourceRuntimeKind"),
            selected_skill_names=item.get("selectedSkillNames"),
            selected_skill_entries=item.get("selectedSkillEntries"),
            selected_mcp_tools=item.get("selectedMcpTools"),
            selected_plugin_host_tools=item.get("selectedPluginHostTools"),
            selected_baseline_tools=item.get("selectedBaselineTools"),
            prompt_addition=item.get("promptAddition"),
            invocation_id=item.get("invocationId"),
        )
        latest_any = normalized
        if target_agent_id and normalized.get("agentId") == target_agent_id:
            latest_match = normalized
    return latest_match or latest_any or build_delegation_context(mode="serial")
