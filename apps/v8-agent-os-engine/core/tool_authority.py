"""Canonical, fail-closed tool authority contract.

Discovery/registry data describes candidates.  This module is the only place
that turns a task policy into the per-invocation tool set.  Callers may still
apply domain guards (engineering capsule, runtime episode), but they must not
re-expand this decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable


def _present(mapping: dict[str, Any], *keys: str) -> tuple[bool, Any]:
    for key in keys:
        if key in mapping:
            return True, mapping[key]
    return False, None


def _name_values(value: Any) -> list[Any]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded, list):
                    return decoded
    return [value]


def _names(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = _name_values(value)
    else:
        try:
            raw = list(value)
        except TypeError:
            raw = [value]
    result: list[str] = []
    for item in raw:
        # The public delegation schema accepts both a scalar JSON array string
        # and JSON array strings inside list[str]. Keep both ingress forms here
        # so forbidden entries have the same meaning before and after resume.
        for entry in _name_values(item):
            name = str(entry or "").strip()
            if name and name not in result:
                result.append(name)
    return tuple(result)


def tool_ref_names(tool_ref: Any) -> frozenset[str]:
    metadata = getattr(tool_ref, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    values = (
        getattr(tool_ref, "name", ""),
        getattr(tool_ref, "__name__", ""),
        metadata.get("canonicalName"),
        metadata.get("canonical_name"),
        metadata.get("rawName"),
        metadata.get("raw_name"),
    )
    return frozenset(item for item in (str(value or "").strip() for value in values) if item)


@dataclass(frozen=True, slots=True)
class ToolAuthorityDecision:
    mode: str
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    explicit_allowlist: bool
    source: str
    policy_digest: str

    @property
    def allows_unlisted(self) -> bool:
        return self.mode == "default" and not self.explicit_allowlist

    def permits(self, names: Iterable[str], *, domain_guard: Callable[[str], bool] | None = None) -> bool:
        candidate = frozenset(str(item or "").strip() for item in names if str(item or "").strip())
        if not candidate or self.mode == "none":
            return False
        if self.mode == "allowlist" and not candidate.intersection(self.allowed):
            return False
        if candidate.intersection(self.forbidden):
            return False
        if domain_guard is not None and not all(domain_guard(name) for name in candidate):
            return False
        return True

    def as_receipt(self, visible_names: Iterable[str]) -> dict[str, Any]:
        names = sorted({str(item).strip() for item in visible_names if str(item).strip()})
        return {
            "mode": self.mode,
            "explicitAllowlist": self.explicit_allowlist,
            "source": self.source,
            "policyDigest": self.policy_digest,
            "visibleTools": names,
            "visibleToolSetDigest": hashlib.sha256(
                json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }


def resolve_tool_authority(task_brief: dict[str, Any] | None) -> ToolAuthorityDecision:
    task = dict(task_brief or {})
    _, raw_policy = _present(task, "toolPolicy", "tool_policy")
    nested = raw_policy if isinstance(raw_policy, dict) else {}
    mode_present, raw_mode = _present(nested, "mode")
    if not mode_present:
        mode_present, raw_mode = _present(task, "toolPolicyMode", "tool_policy_mode")
    allowed_present, raw_allowed = _present(nested, "allowedTools", "allowed_tools")
    source = "toolPolicy" if allowed_present or mode_present else "default"
    if not allowed_present:
        allowed_present, raw_allowed = _present(task, "allowedTools", "allowed_tools")
        if allowed_present:
            source = "task"
    forbidden_present, raw_forbidden = _present(nested, "forbiddenTools", "forbidden_tools")
    if not forbidden_present:
        forbidden_present, raw_forbidden = _present(task, "forbiddenTools", "forbidden_tools")
    no_tools_present, no_tools = _present(nested, "noTools", "no_tools")
    if not no_tools_present:
        no_tools_present, no_tools = _present(task, "noTools", "no_tools")
    mode = str(raw_mode or "").strip().lower()
    if bool(no_tools) or mode == "none":
        mode = "none"
    elif mode not in {"default", "allowlist"}:
        mode = "allowlist" if allowed_present else "default"
    # Presence, rather than truthiness, is intentional: [] is a deny-all
    # allowlist and must never fall through to a broader source.
    # An explicit ``mode=default`` is an inherited/default surface contract;
    # its canonical empty ``allowedTools`` field is metadata, not a grant.
    # Only an omitted mode with an allowlist, or mode=allowlist, is restrictive.
    if allowed_present and mode == "default" and not mode_present:
        mode = "allowlist"
    allowed = _names(raw_allowed) if allowed_present else ()
    forbidden = _names(raw_forbidden) if forbidden_present else ()
    canonical = {"mode": mode, "allowed": allowed, "forbidden": forbidden, "source": source}
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return ToolAuthorityDecision(mode, allowed, forbidden, allowed_present, source, digest)


def filter_authorized_tools(
    tools: Iterable[Any],
    task_brief: dict[str, Any] | None,
    *,
    domain_guard: Callable[[str], bool] | None = None,
) -> tuple[list[Any], ToolAuthorityDecision, dict[str, str]]:
    decision = resolve_tool_authority(task_brief)
    selected: list[Any] = []
    denied: dict[str, str] = {}
    seen: set[str] = set()
    for tool_ref in list(tools or []):
        names = tool_ref_names(tool_ref)
        identity = next(iter(sorted(names)), "")
        if not decision.permits(names, domain_guard=domain_guard):
            denied[identity or "unknown"] = "policy_denied"
            continue
        if names & seen:
            continue
        selected.append(tool_ref)
        seen.update(names)
    return selected, decision, denied


__all__ = ["ToolAuthorityDecision", "resolve_tool_authority", "filter_authorized_tools", "tool_ref_names"]
