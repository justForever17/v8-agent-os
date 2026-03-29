from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Optional

from core.system_tools.baseline import is_baseline_system_tool_name


@dataclass(slots=True)
class RuntimeCapability:
    key: str
    label: str
    summary: str
    accepts: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    risk_level: str = "medium"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeDescriptor:
    kind: str
    display_name: str
    summary: str
    responsibilities: list[str] = field(default_factory=list)
    routing_keywords: list[str] = field(default_factory=list)
    accepted_inputs: list[str] = field(default_factory=list)
    produced_outputs: list[str] = field(default_factory=list)
    owned_steps: list[str] = field(default_factory=list)
    supports_pause: bool = False
    supports_resume: bool = False
    supports_approval: bool = False
    supports_repair: bool = False
    visibility: str = "internal"
    prompt_hints: list[str] = field(default_factory=list)
    capabilities: list[RuntimeCapability] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = [capability.as_dict() for capability in self.capabilities]
        return data


@dataclass(slots=True)
class RuntimePolicy:
    enabled: bool = True
    auto_route: bool = True
    expose_direct_tools: bool = True
    priority: int = 100
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CapabilityRouteSuggestion:
    kind: str
    display_name: str
    score: float
    matched_keywords: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    policy: RuntimePolicy = field(default_factory=RuntimePolicy)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": self.display_name,
            "score": round(float(self.score), 4),
            "matchedKeywords": list(self.matched_keywords),
            "matchedSignals": list(self.matched_signals),
            "policy": self.policy.as_dict(),
        }


def _coerce_capability(payload: RuntimeCapability | Dict[str, Any]) -> RuntimeCapability:
    if isinstance(payload, RuntimeCapability):
        return payload
    return RuntimeCapability(
        key=str(payload.get("key") or payload.get("id") or "unknown"),
        label=str(payload.get("label") or payload.get("name") or payload.get("key") or "Unknown"),
        summary=str(payload.get("summary") or ""),
        accepts=[str(item) for item in list(payload.get("accepts") or [])],
        outputs=[str(item) for item in list(payload.get("outputs") or [])],
        examples=[str(item) for item in list(payload.get("examples") or [])],
        risk_level=str(payload.get("risk_level") or payload.get("riskLevel") or "medium"),
    )


def _coerce_policy(payload: RuntimePolicy | Dict[str, Any] | None) -> RuntimePolicy:
    if isinstance(payload, RuntimePolicy):
        return payload
    payload = dict(payload or {})

    def _pick(*keys: str, default: Any):
        for key in keys:
            if key in payload:
                return payload.get(key)
        return default

    return RuntimePolicy(
        enabled=bool(_pick("enabled", default=True)),
        auto_route=bool(_pick("auto_route", "autoRoute", default=True)),
        expose_direct_tools=bool(_pick("expose_direct_tools", "exposeDirectTools", default=True)),
        priority=int(_pick("priority", default=100) or 100),
        notes=str(_pick("notes", default="") or ""),
    )


def coerce_runtime_descriptor(payload: RuntimeDescriptor | Dict[str, Any]) -> RuntimeDescriptor:
    if isinstance(payload, RuntimeDescriptor):
        return payload
    return RuntimeDescriptor(
        kind=str(payload.get("kind") or "unknown"),
        display_name=str(payload.get("display_name") or payload.get("displayName") or payload.get("kind") or "Unknown Runtime"),
        summary=str(payload.get("summary") or ""),
        responsibilities=[str(item) for item in list(payload.get("responsibilities") or [])],
        routing_keywords=[str(item) for item in list(payload.get("routing_keywords") or payload.get("routingKeywords") or [])],
        accepted_inputs=[str(item) for item in list(payload.get("accepted_inputs") or payload.get("acceptedInputs") or [])],
        produced_outputs=[str(item) for item in list(payload.get("produced_outputs") or payload.get("producedOutputs") or [])],
        owned_steps=[str(item) for item in list(payload.get("owned_steps") or payload.get("ownedSteps") or [])],
        supports_pause=bool(payload.get("supports_pause") or payload.get("supportsPause") or False),
        supports_resume=bool(payload.get("supports_resume") or payload.get("supportsResume") or False),
        supports_approval=bool(payload.get("supports_approval") or payload.get("supportsApproval") or False),
        supports_repair=bool(payload.get("supports_repair") or payload.get("supportsRepair") or False),
        visibility=str(payload.get("visibility") or "internal"),
        prompt_hints=[str(item) for item in list(payload.get("prompt_hints") or payload.get("promptHints") or [])],
        capabilities=[_coerce_capability(item) for item in list(payload.get("capabilities") or [])],
        metadata=dict(payload.get("metadata") or {}),
    )


class CapabilityRegistry:
    def __init__(self) -> None:
        self._descriptors: Dict[str, RuntimeDescriptor] = {}
        self._policies: Dict[str, RuntimePolicy] = {}
        self._policies_loaded = False

    def register(self, descriptor: RuntimeDescriptor | Dict[str, Any]) -> RuntimeDescriptor:
        normalized = coerce_runtime_descriptor(descriptor)
        self._descriptors[normalized.kind] = normalized
        self._ensure_policies_loaded()
        self._policies.setdefault(normalized.kind, RuntimePolicy())
        return normalized

    def get(self, kind: str) -> Optional[RuntimeDescriptor]:
        return self._descriptors.get(kind)

    def list(self) -> Iterable[RuntimeDescriptor]:
        return tuple(self._descriptors.values())

    def _ensure_policies_loaded(self) -> None:
        if self._policies_loaded:
            return
        self._policies_loaded = True
        try:
            from core.storage import storage

            raw = storage.get_runtime_registry_config()
        except Exception:
            raw = {}
        for kind, payload in dict(raw.get("policies") or {}).items():
            self._policies[str(kind)] = _coerce_policy(payload)

    def _persist_policies(self) -> None:
        self._ensure_policies_loaded()
        try:
            from core.storage import storage

            storage.save_runtime_registry_config(
                {
                    "version": 1,
                    "policies": {kind: policy.as_dict() for kind, policy in self._policies.items()},
                }
            )
        except Exception:
            return

    def reload_policies(self) -> None:
        self._policies = {}
        self._policies_loaded = False
        self._ensure_policies_loaded()
        for kind in self._descriptors:
            self._policies.setdefault(kind, RuntimePolicy())

    def get_policy(self, kind: str) -> RuntimePolicy:
        self._ensure_policies_loaded()
        return self._policies.setdefault(kind, RuntimePolicy())

    def set_policy(self, kind: str, payload: RuntimePolicy | Dict[str, Any]) -> RuntimePolicy:
        self._ensure_policies_loaded()
        incoming = dict(payload.as_dict() if isinstance(payload, RuntimePolicy) else payload)
        if "autoRoute" in incoming and "auto_route" not in incoming:
            incoming["auto_route"] = incoming.pop("autoRoute")
        if "exposeDirectTools" in incoming and "expose_direct_tools" not in incoming:
            incoming["expose_direct_tools"] = incoming.pop("exposeDirectTools")
        current = self.get_policy(kind).as_dict()
        current.update(incoming)
        normalized = _coerce_policy(current)
        self._policies[str(kind)] = normalized
        self._persist_policies()
        return normalized

    def reset_policy(self, kind: str) -> RuntimePolicy:
        self._ensure_policies_loaded()
        self._policies.pop(kind, None)
        self._persist_policies()
        return self.get_policy(kind)

    def is_enabled(self, kind: str) -> bool:
        return self.get_policy(kind).enabled

    def _descriptor_tool_names(self, descriptor: RuntimeDescriptor) -> set[str]:
        metadata = descriptor.metadata or {}
        names = {str(item) for item in list(metadata.get("managedToolNames") or []) if str(item).strip()}
        prefixes = [str(item) for item in list(metadata.get("managedToolPrefixes") or []) if str(item).strip()]
        for prefix in prefixes:
            if prefix.endswith("*"):
                prefix = prefix[:-1]
            if prefix and prefix.endswith("_"):
                names.add(prefix)
        return names

    def _matches_descriptor_tool(self, descriptor: RuntimeDescriptor, tool_name: str) -> bool:
        metadata = descriptor.metadata or {}
        exact_names = {str(item) for item in list(metadata.get("managedToolNames") or []) if str(item).strip()}
        if tool_name in exact_names:
            return True
        for prefix in list(metadata.get("managedToolPrefixes") or []):
            prefix_str = str(prefix or "").strip()
            if not prefix_str:
                continue
            if prefix_str.endswith("*"):
                prefix_str = prefix_str[:-1]
            if tool_name.startswith(prefix_str):
                return True
        return False

    def filter_direct_tools(self, tools: Iterable[Any]) -> list[Any]:
        filtered: list[Any] = []
        descriptors = tuple(self.list())
        for tool_ref in tools:
            tool_name = getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")).strip()
            if not tool_name:
                filtered.append(tool_ref)
                continue
            if is_baseline_system_tool_name(tool_name):
                filtered.append(tool_ref)
                continue
            blocked = False
            for descriptor in descriptors:
                policy = self.get_policy(descriptor.kind)
                if policy.enabled and policy.expose_direct_tools:
                    continue
                if self._matches_descriptor_tool(descriptor, tool_name):
                    blocked = True
                    break
            if not blocked:
                filtered.append(tool_ref)
        return filtered

    def recommend(self, user_query: str | None, *, limit: int = 5) -> list[CapabilityRouteSuggestion]:
        query = str(user_query or "").strip().lower()
        suggestions: list[CapabilityRouteSuggestion] = []
        for descriptor in self.list():
            policy = self.get_policy(descriptor.kind)
            if not policy.enabled or not policy.auto_route:
                continue
            score = max(0.0, 120.0 - float(policy.priority))
            matched_keywords: list[str] = []
            matched_signals: list[str] = []
            if descriptor.visibility == "primary":
                score += 12.0
                matched_signals.append("primary_runtime")
            if query:
                for keyword in descriptor.routing_keywords:
                    normalized_keyword = str(keyword or "").strip().lower()
                    if normalized_keyword and normalized_keyword in query:
                        matched_keywords.append(keyword)
                        score += 28.0
                capability_hits = 0
                for capability in descriptor.capabilities:
                    haystacks = [capability.label, capability.summary, *capability.examples]
                    if any(str(item or "").strip().lower() and str(item).strip().lower() in query for item in haystacks):
                        capability_hits += 1
                if capability_hits:
                    score += min(24.0, capability_hits * 12.0)
                    matched_signals.append("capability_match")
                responsibility_hits = 0
                for item in descriptor.responsibilities:
                    normalized_item = str(item or "").strip().lower()
                    if normalized_item and normalized_item in query:
                        responsibility_hits += 1
                if responsibility_hits:
                    score += min(16.0, responsibility_hits * 8.0)
                    matched_signals.append("responsibility_match")
            if not query or matched_keywords or matched_signals or descriptor.visibility == "primary":
                suggestions.append(
                    CapabilityRouteSuggestion(
                        kind=descriptor.kind,
                        display_name=descriptor.display_name,
                        score=score,
                        matched_keywords=matched_keywords[:6],
                        matched_signals=matched_signals[:4],
                        policy=policy,
                    )
                )
        suggestions.sort(key=lambda item: (-item.score, item.policy.priority, item.kind))
        return suggestions[: max(1, limit)]

    def snapshot(self, *, query: str | None = None, recommendation_limit: int = 5) -> Dict[str, Any]:
        recommendations = self.recommend(query, limit=recommendation_limit) if query is not None else []
        return {
            "count": len(self._descriptors),
            "query": str(query or "") if query is not None else None,
            "recommendations": [item.as_dict() for item in recommendations],
            "runtimes": [
                {
                    **descriptor.as_dict(),
                    "policy": self.get_policy(descriptor.kind).as_dict(),
                }
                for descriptor in self.list()
            ],
        }

    def build_supervisor_summary(
        self,
        *,
        user_query: str | None = None,
        prioritized_kinds: Optional[list[str]] = None,
    ) -> str:
        ordered: list[RuntimeDescriptor] = []
        seen: set[str] = set()
        recommended = {item.kind: item for item in self.recommend(user_query, limit=6)}
        for kind in prioritized_kinds or []:
            descriptor = self.get(kind)
            if descriptor is None:
                continue
            ordered.append(descriptor)
            seen.add(kind)
        for descriptor in self.list():
            if descriptor.kind in seen:
                continue
            ordered.append(descriptor)

        if not ordered:
            return ""

        lines = [
            "<capability_registry>",
            "Supervisor 不需要记住所有模块 prompt 细节。你应该优先根据下面这份 Runtime 能力卡片做路由和分工。",
        ]
        if user_query:
            suggestions = [recommended[kind] for kind in recommended]
            if suggestions:
                lines.append("当前查询的推荐路由:")
                for item in suggestions[:4]:
                    labels = "、".join(item.matched_keywords[:3]) or "通用契合"
                    lines.append(
                        f"- {item.display_name} ({item.kind}) score={round(item.score, 1)} | 命中: {labels}"
                    )
        for descriptor in ordered:
            policy = self.get_policy(descriptor.kind)
            if not policy.enabled:
                continue
            lines.append(f"- {descriptor.display_name} ({descriptor.kind})")
            if descriptor.summary:
                lines.append(f"  摘要: {descriptor.summary}")
            lines.append(
                f"  状态: enabled | auto_route={'yes' if policy.auto_route else 'no'} | direct_tools={'yes' if policy.expose_direct_tools else 'no'} | priority={policy.priority}"
            )
            if descriptor.routing_keywords:
                lines.append(f"  适用关键词: {', '.join(descriptor.routing_keywords[:8])}")
            if descriptor.capabilities:
                capability_labels = ", ".join(cap.label for cap in descriptor.capabilities[:4])
                lines.append(f"  代表能力: {capability_labels}")
            if descriptor.prompt_hints:
                lines.append(f"  路由提示: {'；'.join(descriptor.prompt_hints[:2])}")
        disabled = [
            descriptor.display_name
            for descriptor in ordered
            if not self.get_policy(descriptor.kind).enabled
        ]
        if disabled:
            lines.append(f"当前已禁用 Runtime: {', '.join(disabled)}")
        lines.append("</capability_registry>")
        return "\n".join(lines)


capability_registry = CapabilityRegistry()
