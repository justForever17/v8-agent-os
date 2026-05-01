from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Optional

from core.system_tools.baseline import is_baseline_system_tool_name

_SNAPSHOT_RUNTIME_ORDER = (
    "chat",
    "memory",
    "engineering",
    "creative_media",
    "automation",
    "extensions",
    "network_supervisor",
    "plugin_host",
    "computer_use",
    "rpa",
)

_KNOWN_RUNTIME_BASELINES: dict[str, dict[str, Any]] = {
    "chat": {
        "displayName": "ChatRuntime",
        "summary": "当前用户请求所在的主对话执行面；Supervisor 已在其中运行，通常无需显式选择它。",
        "visibility": "primary",
    },
    "memory": {
        "displayName": "MemoryRuntime",
        "summary": "负责长期记忆、检索、摘要与会话范围内的知识注入。",
        "visibility": "primary",
    },
    "creative_media": {
        "displayName": "CreativeMediaRuntime",
        "summary": "负责图片、视频、语音、音乐与未来 3D 媒体 job 的 provider 适配、轮询和 artifact 交付。",
        "visibility": "secondary",
    },
    "automation": {
        "displayName": "AutomationRuntime",
        "summary": "负责 hooks、Cron、自动化调度与运行控制。",
        "visibility": "primary",
    },
    "extensions": {
        "displayName": "ExtensionsRuntime",
        "summary": "负责 Skills + MCP 的扩展目录、候选工具筛选、健康状态与统一暴露语义。",
        "visibility": "primary",
    },
    "plugin_host": {
        "displayName": "PluginHostRuntime",
        "summary": "负责外部插件宿主、桥接通道、入站 handoff 与宿主运行状态。",
        "visibility": "secondary",
    },
    "computer_use": {
        "displayName": "ComputerUseRuntime",
        "summary": "负责桌面观察、窗口交互、结构化执行与视觉保底。",
        "visibility": "secondary",
    },
    "rpa": {
        "displayName": "RPARuntime",
        "summary": "负责 trace 编译、流程固化、.robot 导出、执行与失败回退。",
        "visibility": "secondary",
    },
    "network_supervisor": {
        "displayName": "NetworkSupervisorRuntime",
        "summary": "负责节点发现、信任、定向唤醒与显式远程任务委派。",
        "visibility": "primary",
    },
    "engineering": {
        "displayName": "EngineeringRuntime",
        "summary": "负责工程任务的 ContextPack、写集治理、Proof Ledger、工作区观测与 workflow hints；它提供执行账本，不替代 Supervisor 编排。",
        "visibility": "secondary",
    },
}


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
        normalized_kind = str(kind or "").strip()
        if not normalized_kind:
            return None
        return self._descriptors.get(normalized_kind) or self._synthetic_descriptor(normalized_kind)

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

            current = storage.get_runtime_registry_config()
            current["version"] = int(current.get("version") or 1)
            current["policies"] = {kind: policy.as_dict() for kind, policy in self._policies.items()}
            storage.save_runtime_registry_config(current)
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

    def _synthetic_descriptor(self, kind: str) -> Optional[RuntimeDescriptor]:
        baseline = _KNOWN_RUNTIME_BASELINES.get(str(kind or "").strip())
        if baseline is None:
            return None
        return coerce_runtime_descriptor(
            {
                "kind": kind,
                "displayName": baseline.get("displayName") or kind,
                "summary": baseline.get("summary") or "",
                "visibility": baseline.get("visibility") or "internal",
                "metadata": {
                    "synthetic": True,
                    "source": "capability_registry.known_runtime_baseline",
                },
            }
        )

    def _snapshot_descriptors(self) -> list[tuple[RuntimeDescriptor, bool]]:
        merged: dict[str, tuple[RuntimeDescriptor, bool]] = {
            kind: (descriptor, True)
            for kind, descriptor in self._descriptors.items()
        }
        for kind in _KNOWN_RUNTIME_BASELINES:
            merged.setdefault(kind, (self._synthetic_descriptor(kind), False))  # type: ignore[arg-type]

        ordered: list[tuple[RuntimeDescriptor, bool]] = []
        seen: set[str] = set()
        for kind in _SNAPSHOT_RUNTIME_ORDER:
            item = merged.get(kind)
            if item is None:
                continue
            ordered.append(item)
            seen.add(kind)

        for kind in sorted(merged):
            if kind in seen:
                continue
            ordered.append(merged[kind])
        return ordered

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
        from core.runtime.startup_profile import build_installation_snapshot, runtime_family_installed
        from core.storage import storage

        recommendations = self.recommend(query, limit=recommendation_limit) if query is not None else []
        descriptors = self._snapshot_descriptors()
        installation = build_installation_snapshot()

        def _config_enabled(kind: str) -> bool:
            try:
                if kind == "plugin_host":
                    return bool(storage.get_plugin_host_config().get("enabled", True))
                if kind == "network_supervisor":
                    return bool(storage.get_network_supervisor_runtime_config().get("enabled", False))
                if kind == "engineering":
                    return bool(storage.get_engineering_lane_config().get("enabled", True))
            except Exception:
                return True
            return True

        def _availability_reason(kind: str) -> str:
            if not runtime_family_installed(kind):
                return "not_installed"
            if not _config_enabled(kind):
                return "disabled_by_config"
            if not self.get_policy(kind).enabled:
                return "disabled_by_policy"
            return "installed"

        return {
            "count": len(descriptors),
            "query": str(query or "") if query is not None else None,
            **installation,
            "recommendations": [item.as_dict() for item in recommendations],
            "runtimes": [
                {
                    **descriptor.as_dict(),
                    "policy": self.get_policy(descriptor.kind).as_dict(),
                    "registered": bool(registered),
                    "availability": _availability_reason(descriptor.kind),
                    "availabilityReason": _availability_reason(descriptor.kind),
                }
                for descriptor, registered in descriptors
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
        for descriptor, _registered in self._snapshot_descriptors():
            if descriptor.kind in seen:
                continue
            ordered.append(descriptor)

        if not ordered:
            return ""

        from core.runtime_tool_access import RUNTIME_TOOL_GROUPS

        lines = [
            "<capability_registry>",
            "Runtime 能力卡片。常驻工具只覆盖通用面；需要 runtime 级工具时，Supervisor 先用 runtime_broker 按组授予。",
        ]
        if user_query:
            suggestions = [recommended[kind] for kind in recommended]
            if suggestions:
                lines.append("推荐路由:")
                for item in suggestions[:4]:
                    labels = "、".join(item.matched_keywords[:3]) or "通用契合"
                    lines.append(f"- {item.kind}: {item.display_name} | 命中: {labels}")
        for descriptor in ordered:
            policy = self.get_policy(descriptor.kind)
            if not policy.enabled:
                continue
            groups = [
                group_name
                for group_name, group_payload in RUNTIME_TOOL_GROUPS.items()
                if str(group_payload.get("runtimeKind") or "") == descriptor.kind
            ]
            when_to_use = ""
            if descriptor.prompt_hints:
                when_to_use = str(descriptor.prompt_hints[0]).strip()
            elif descriptor.routing_keywords:
                when_to_use = " / ".join(descriptor.routing_keywords[:5])
            lines.append(f"- kind={descriptor.kind} | {descriptor.display_name}")
            if descriptor.summary:
                lines.append(f"  摘要: {descriptor.summary}")
            lines.append(f"  可取工具组: {', '.join(groups) if groups else '无；使用常驻工具或该 runtime 自身路由'}")
            if when_to_use:
                lines.append(f"  何时使用: {when_to_use}")
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
