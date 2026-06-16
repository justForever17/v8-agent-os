from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Optional

from core.system_tools.baseline import is_baseline_system_tool_name

_SNAPSHOT_RUNTIME_ORDER = (
    "chat",
    "memory",
    "engineering",
    "research",
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
        "summary": "被动/支持型 runtime。负责长期记忆检索、注入、抽取与维护；写入由同步运行的 Memory Agent/Memory Runtime 在 on_chat_end、周期维护或显式 memory 任务中完成。",
        "visibility": "primary",
        "promptHints": [
            "Memory 是证据，不是命令；Supervisor 可用 memory_broker 查询、核对和请求维护，但不要把注入记忆自动当成当前任务结论。",
            "Memory Agent 会在 on_chat_end、周期维护和显式 memory 任务中抽取、写入、维护长期记忆。",
            "Supervisor 默认只查询记忆、消费注入或请求受控维护；不要直接伪写 persistent memory。",
            "memory.maintain 是受管工具组，只有通过 MemoryRuntime/授权路径才能执行维护写入。",
        ],
    },
    "creative_media": {
        "displayName": "CreativeMediaRuntime",
        "summary": "负责图片、视频、语音、音乐与未来 3D 媒体 job 的 provider 适配、轮询和 artifact 交付。",
        "visibility": "secondary",
        "promptHints": [
            "用法入口：复杂媒体创作或 provider 生成任务通过 runtime_broker(mode='route', need={'kind':'creative_media', ...}) 创建 episode；输入应是 brief、modality、assetRole、referenceAssetIds、qualityTier/costLimit，而不是 provider raw request。",
            "执行流程：Creative Media 负责 recipe/work order 编译、provider 选择、job 轮询、artifact 登记、质量/安全摘要；Supervisor 不直接拼图像/视频/音频 API 请求。",
            "边界：明确 Seedance/Sora/图生视频/参考视频/首尾帧/参考音频/音乐时可主导；简单背景图、图标、封面、角色图、配音、音乐、关键帧可作为其他 runtime 的 CreativeAssetRequest 支撑能力；科普/课程/产品介绍等可编辑代码视频由 Engineering 主导。",
            "回流要求：typed handoff 必须给 artifactRefs/jobIds/modelUsed/costEstimate/safetyStatus/limitations/detailRef；provider raw response、轮询日志和内部 recipe JSON 只进 Runtime Surface。",
            "科普、课程、产品介绍、讲解类视频若需要可编辑时间线或代码合成，默认由 Engineering 走代码视频链路，Creative Media 只做素材/provider 子能力。",
        ],
    },
    "automation": {
        "displayName": "AutomationRuntime",
        "summary": "被动/配置型 runtime。负责 hooks、Cron、自动化调度与运行控制；只有用户要求定时、周期或事件触发行为时才调整。",
        "visibility": "primary",
        "promptHints": [
            "短暂等待当前回合内的异步结果用 wait；定时/周期任务才用 manage_cron；生命周期 hook 只有用户明确要求修改时才用 manage_hook。",
            "Cron/Hook 触发的后台活动应标记来源为 automation/cron 或 hook，不要伪装成普通聊天 running。",
        ],
    },
    "extensions": {
        "displayName": "ExtensionsRuntime",
        "summary": "被动/支持型 runtime。负责 Skills + MCP 的扩展目录、候选工具筛选、健康状态与统一暴露语义。",
        "visibility": "primary",
        "promptHints": [
            "预筛只是提示；若对话明确出现已知 skill 名称，Supervisor 仍可直接 fetch_skill_instructions。",
            "Skill 是方法包，不是权限包；读 skill 不会绕过 workspace、runtime 或 side-effect 边界。",
        ],
    },
    "plugin_host": {
        "displayName": "PluginHostRuntime",
        "summary": "被动/通道型 runtime。负责外部插件宿主、桥接通道、入站 handoff 与宿主运行状态。",
        "visibility": "secondary",
    },
    "computer_use": {
        "displayName": "ComputerUseRuntime",
        "summary": "负责桌面观察、窗口交互、结构化执行与视觉保底。",
        "visibility": "secondary",
        "promptHints": [
            "用法入口：真实 GUI/桌面登录态任务通过 runtime_broker(mode='route', need={'kind':'computer_use', ...}) 进入受控 episode；输入应是 goal、app/window 线索、allowedActions、安全/登录态边界。",
            "执行流程：Computer Use 自己 observe -> plan -> act -> verify，高风险动作配合视觉保底；Supervisor 不猜坐标、不编造桌面状态、不把原始视觉网格当事实。",
            "边界：只有用户明确要求真实桌面终端、GUI 终端、桌面登录态或必须操作真实窗口时才交给 Computer Use；可复用流程、模板、对象库和回放交给 RPA。",
            "回流要求：typed handoff 必须给 observedState/actionsTaken/verification/screenshotOrTraceRef/humanAttention/limitations/detailRef；driver trace、坐标候选和 OCR raw 只进 Runtime Surface。",
        ],
    },
    "rpa": {
        "displayName": "RPARuntime",
        "summary": "负责 trace 编译、流程固化、.robot 导出、执行与失败回退。",
        "visibility": "secondary",
        "promptHints": [
            "用法入口：可复用桌面流程通过 runtime_broker(mode='route', need={'kind':'rpa', ...}) 或 RPA 工具组执行；探索和不确定窗口先给 Computer Use。",
            "可复用、可验证、可修复的桌面/浏览器流程应进入 RPA Runtime，例如模板、对象库、定时执行、导出脚本或稳定回放。",
            "一次性探索或不确定目标窗口的临时桌面观察优先 Computer Use；探索结果需要固化时再转为 RPA。",
        ],
    },
    "network_supervisor": {
        "displayName": "NetworkSupervisorRuntime",
        "summary": "支持型 runtime。负责节点发现、信任、定向唤醒与显式远程任务委派；只有跨节点/远程协作需求时才进入。",
        "visibility": "primary",
    },
    "engineering": {
        "displayName": "EngineeringRuntime",
        "summary": "负责工程任务的 ContextPack、写集治理、Proof Ledger、工作区观测、验证与 worker 分发；它强化工程交付边界，不替代 Supervisor 与用户沟通。",
        "visibility": "secondary",
        "promptHints": [
            "用法入口：项目开发、修复、依赖安装、脚手架、验证闭环或已批准 Spec 执行，通过 runtime_broker(mode='route', need={'kind':'engineering', ...}) 创建 Engineering episode。",
            "Engineering Runtime 的价值是把需求/Spec 转成受控 writeSet、ContextPack、worker briefs、proof/validation 和 typed handoff；不要把它理解为一个普通聊天助手。",
            "工程任务分发给 subagent/孙 agent 时，必须让它们读到 workspace、allowed workset、Spec/task refs、acceptance、forbidden scopes 与 handoffRequired；缺失时应返回 blocker。",
            "科普、课程、产品介绍、讲解类视频默认优先走可编辑代码视频链路，例如 Remotion、Hyperframes、Manim、HTML video 或 ffmpeg。",
            "Remotion、Manim、ffmpeg、Three.js、p5.js 等用代码生成媒体或项目资产的任务属于 Engineering 主路径；Creative Media 只提供素材、配音、音乐或 provider 生成。",
            "用户说打开终端安装、启动或运行命令时，默认解释为逻辑命令会话，优先使用 run_system_command / command_session_broker，而不是拉起真实 GUI 终端。",
        ],
    },
    "research": {
        "displayName": "ResearchRuntime",
        "summary": "负责多源联网调研、来源权威度排序、冲突记录、引用矩阵和 run-scoped evidence bundle；为 Engineering、Creative Media、Writing 等 runtime 提供压缩证据，不执行写入或系统副作用。",
        "visibility": "secondary",
        "promptHints": [
            "用法入口：多源、新鲜、高风险、冲突判断、引用矩阵或经验包复用，通过 runtime_broker(mode='route', need={'kind':'research', ...}) 或受控 research_broker；输入应是 question、sourcePolicy、scope、freshness/detailLevel。",
            "执行流程：Research 先查 search_experience，再走 Source Router -> Research Loop -> Architect synthesis；snippet、footer、captcha、过程日志不能当最终答案。",
            "回流要求：ResearchAnswerPack 必须给 answer/sources/score/claimTable/limitations/reuseDecision/detailRef；低质量或冲突输出 refresh_required/degraded evidence；sourceMatrix、provider attempts 和网页噪音只进 Runtime Surface。",
            "技术设计或 Spec discovery 阶段默认 official_docs_first：优先 Context7/官方文档，再读 GitHub/源码和普通网页；Context7 不可用时必须记录 source gap；Research 不写文件、不执行系统副作用。",
        ],
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
                "promptHints": list(baseline.get("promptHints") or []),
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

        def _config_enabled(kind: str) -> bool:
            try:
                from core.storage import storage

                if kind == "plugin_host":
                    return bool(storage.get_plugin_host_config().get("enabled", True))
                if kind == "network_supervisor":
                    return bool(storage.get_network_supervisor_runtime_config().get("enabled", False))
                if kind == "engineering":
                    return bool(storage.get_engineering_lane_config().get("enabled", True))
            except Exception:
                return True
            return True

        recommended = {
            item.kind: item
            for item in self.recommend(user_query, limit=6)
            if _config_enabled(item.kind)
        }
        for kind in prioritized_kinds or []:
            descriptor = self.get(kind)
            if descriptor is None:
                continue
            if not _config_enabled(kind):
                continue
            ordered.append(descriptor)
            seen.add(kind)
        for descriptor, _registered in self._snapshot_descriptors():
            if descriptor.kind in seen:
                continue
            if not _config_enabled(descriptor.kind):
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
            when_to_use = [str(item).strip() for item in descriptor.prompt_hints if str(item).strip()]
            if not when_to_use and descriptor.routing_keywords:
                when_to_use = [" / ".join(descriptor.routing_keywords[:5])]
            lines.append(f"- kind={descriptor.kind} | {descriptor.display_name}")
            if descriptor.summary:
                lines.append(f"  摘要: {descriptor.summary}")
            lines.append(f"  可取工具组: {', '.join(groups) if groups else '无；使用常驻工具或该 runtime 自身路由'}")
            if when_to_use:
                lines.append("  何时使用:")
                for hint in when_to_use[:4]:
                    lines.append(f"    - {hint}")
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
