from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from core.model_capability_matrix import normalize_capability_metadata
from core.model_budget_service import model_budget_service
from core.provider_runtime_profiles import runtime_readiness_for_provider
from core.provider_health_service import provider_health_service
from core.model_ref import make_model_ref, parse_model_ref
from core.prompt_cache_gateway import prompt_cache_profile_id_for_provider
from core.storage import storage


CHAT_CAPABILITY_CLASSES = [
    "chat_general",
    "chat_tool_calling",
    "chat_reasoning",
    "vision_multimodal",
]

DEFAULT_ROLE_MAP = {
    "default": "",
    "supervisor": "",
    "subagent": "",
    "summary": "",
    "extraction": "",
    "vision": "",
    "embedding": "",
    "reranker": "",
    "extensions_prefilter": "",
    "extensions_reranker": "",
    "channel": "",
    "automation": "",
    "computer_use_planner": "",
    "computer_use_visual_actor": "",
    "computer_use_visual_judge": "",
    "computer_use_candidate_reranker": "",
    "rpa_discovery": "",
}

DEFAULT_BINDINGS = {
    "agents": {},
}

DEFAULT_ROLE_PARAMETERS = {
    "supervisor": {"temperature": None},
    "subagent": {"temperature": None},
}


def normalize_config_temperature(value: Any) -> Optional[float]:
    """Normalize user-sourced temperature config.

    A configured value of 0 is treated as unset so accidental slider/input
    saves do not force provider requests into a degenerate sampling mode.
    Explicit runtime kwargs still bypass this helper.
    """
    if value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return max(min(parsed, 2.0), 0.05)

DEFAULT_GOVERNANCE = {
    "enabled": True,
    "stickyRunModel": True,
    "allowSameCapabilityFailover": True,
    "strictCapabilityMatch": True,
    "maxLocalRetries": 1,
    "maxProviderSwitches": 2,
    "defaultStreaming": True,
    "providerHealthWindowDays": 7,
    "providerFailureThreshold": 3,
    "providerErrorRateThreshold": 0.6,
    "budgets": {
        "enabled": True,
        "globalDailyCostLimit": 0.0,
        "globalDailyTokenLimit": 0,
        "runMaxCost": 0.0,
        "runMaxTokens": 0,
        "defaultProjectDailyCostLimit": 0.0,
        "defaultProjectDailyTokenLimit": 0,
        "projectBudgets": [],
    },
}

DEFAULT_ROUTING_POLICIES = {
    "chat": "supervisor",
    "subagent": "subagent",
    "channel": "channel",
    "automation": "automation",
    "summary": "summary",
    "memoryExtraction": "extraction",
    "visionAnalysis": "vision",
    "embedding": "embedding",
    "reranker": "reranker",
    "computerUsePlanner": "computer_use_planner",
    "computerUseVisualActor": "computer_use_visual_actor",
    "computerUseVisualJudge": "computer_use_visual_judge",
    "rpaDiscovery": "rpa_discovery",
}

ROLE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "default": {
        "label": "默认聊天",
        "description": "未显式指定角色时的基础聊天模型。",
        "group": "system",
        "capabilityClasses": CHAT_CAPABILITY_CLASSES,
    },
    "supervisor": {
        "label": "主理人模型",
        "description": "主会话编排、多智能体决策与工具调度。",
        "group": "system",
        "capabilityClasses": CHAT_CAPABILITY_CLASSES,
    },
    "subagent": {
        "label": "默认 Subagent 模型",
        "description": "本地子代理默认继承的模型绑定；单个 agent 显式绑定仍优先。",
        "group": "system",
        "capabilityClasses": CHAT_CAPABILITY_CLASSES,
    },
    "summary": {
        "label": "摘要模型",
        "description": "长会话压缩、摘要归纳和上下文收敛。",
        "group": "system",
        "capabilityClasses": ["chat_general", "chat_reasoning", "chat_tool_calling"],
    },
    "extraction": {
        "label": "记忆提取",
        "description": "偏好、知识与记忆条目的抽取模型。",
        "group": "system",
        "capabilityClasses": ["chat_general", "chat_reasoning", "chat_tool_calling"],
    },
    "vision": {
        "label": "视觉分析",
        "description": "图片理解、多模态分析与视觉问题解答。",
        "group": "system",
        "capabilityClasses": ["vision_multimodal"],
    },
    "embedding": {
        "label": "向量模型",
        "description": "RAG 的 embedding 与索引构建能力。",
        "group": "system",
        "capabilityClasses": ["embedding"],
    },
    "reranker": {
        "label": "重排模型",
        "description": "RAG 的二阶段相关性重排。",
        "group": "system",
        "capabilityClasses": ["reranker"],
    },
    "extensions_reranker": {
        "label": "扩展候选重排",
        "description": "Skills 与 MCP 工具候选的二阶段精排。",
        "group": "extension",
        "capabilityClasses": ["reranker"],
    },
    "extensions_prefilter": {
        "label": "扩展候选预筛",
        "description": "用廉价通用模型对 Skills、MCP 与 PluginHost 工具树做家族级预筛。",
        "group": "extension",
        "capabilityClasses": ["chat_general", "chat_tool_calling", "chat_reasoning"],
    },
    "channel": {
        "label": "渠道处理",
        "description": "飞书与其他外部渠道消息处理。",
        "group": "system",
        "capabilityClasses": CHAT_CAPABILITY_CLASSES,
    },
    "automation": {
        "label": "AutomationRuntime",
        "description": "动作钩子、定时任务和系统自动化触发的主执行模型。",
        "group": "system",
        "capabilityClasses": CHAT_CAPABILITY_CLASSES,
    },
    "computer_use_planner": {
        "label": "桌面规划",
        "description": "桌面操作前的策略规划与步骤拆解。",
        "group": "extension",
        "capabilityClasses": ["vision_multimodal", "chat_reasoning", "chat_tool_calling"],
    },
    "computer_use_visual_actor": {
        "label": "桌面视觉动作提案",
        "description": "根据截图、候选板与短目标提出下一步视觉动作；只提案，不直接执行。",
        "group": "extension",
        "capabilityClasses": ["vision_multimodal"],
    },
    "computer_use_visual_judge": {
        "label": "桌面视觉裁判",
        "description": "界面验真与高歧义点击场景的视觉仲裁。",
        "group": "extension",
        "capabilityClasses": ["vision_multimodal"],
    },
    "computer_use_candidate_reranker": {
        "label": "桌面候选重排",
        "description": "桌面多候选歧义场景的文本化候选精排。",
        "group": "extension",
        "capabilityClasses": ["reranker"],
    },
    "rpa_discovery": {
        "label": "流程发现",
        "description": "流程探索、变量抽取与自动化复用生成。",
        "group": "extension",
        "capabilityClasses": ["vision_multimodal", "chat_reasoning", "chat_tool_calling"],
    },
}

MODULE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "key": "default_chat",
        "label": "默认聊天链路",
        "description": "Web 与渠道默认聊天的基础角色绑定。",
        "group": "system",
        "roles": ["default"],
        "pagePath": "/admin/model-hub",
        "pageLabel": "模型中心",
    },
    {
        "key": "supervisor_core",
        "label": "主理人主链",
        "description": "主会话编排、多智能体决策与工具调度。",
        "group": "system",
        "roles": ["supervisor"],
        "pagePath": "/admin/supervisor",
        "pageLabel": "主理人",
    },
    {
        "key": "subagent_lane",
        "label": "Subagent 执行班子",
        "description": "Planner / Delegation Broker 调度的本地子代理默认模型。",
        "group": "system",
        "roles": ["subagent"],
        "pagePath": "/admin/subagents",
        "pageLabel": "子代理",
    },
    {
        "key": "context_summary",
        "label": "上下文压缩",
        "description": "长会话摘要、压缩与上下文治理。",
        "group": "system",
        "roles": ["summary"],
        "pagePath": "/admin/context",
        "pageLabel": "上下文管理",
    },
    {
        "key": "memory_agent",
        "label": "记忆维护链路",
        "description": "记忆提取、偏好写回与知识维护。",
        "group": "system",
        "roles": ["extraction"],
        "pagePath": "/admin/memory",
        "pageLabel": "记忆管理",
    },
    {
        "key": "vision_stack",
        "label": "视觉理解链路",
        "description": "多模态理解、图片问答与视觉辅助决策。",
        "group": "system",
        "roles": ["vision"],
        "pagePath": "/admin/desktop-automation",
        "pageLabel": "桌面操作",
    },
    {
        "key": "retrieval_stack",
        "label": "检索增强链路",
        "description": "向量化与重排的组合检索链路。",
        "group": "system",
        "roles": ["embedding", "reranker"],
        "pagePath": "/admin/memory",
        "pageLabel": "记忆管理",
    },
    {
        "key": "extensions_prefilter",
        "label": "扩展候选预筛",
        "description": "对 Skills、MCP 与 PluginHost 候选树做 LLM 预筛。",
        "group": "extension",
        "roles": ["extensions_prefilter"],
        "pagePath": "/admin/extensions",
        "pageLabel": "扩展生态",
    },
    {
        "key": "plugin_host_runtime",
        "label": "插件宿主链路",
        "description": "OpenClaw sidecar/host 承接的外部插件与渠道消息处理。",
        "group": "system",
        "roles": ["channel"],
        "pagePath": "/admin/plugin-host",
        "pageLabel": "插件宿主",
    },
    {
        "key": "automation_runtime",
        "label": "AutomationRuntime 链路",
        "description": "动作钩子、定时任务与系统自动化触发的执行主链。",
        "group": "system",
        "roles": ["automation"],
        "pagePath": "/admin/automation",
        "pageLabel": "自动化运行时",
    },
    {
        "key": "computer_use_planner",
        "label": "桌面规划",
        "description": "未来桌面控制能力的规划角色，占位只读展示。",
        "group": "extension",
        "roles": ["computer_use_planner"],
        "pagePath": "/admin/desktop-automation",
        "pageLabel": "桌面操作",
    },
    {
        "key": "computer_use_visual_judge",
        "label": "桌面视觉裁判",
        "description": "桌面界面验真与高歧义点击场景的视觉裁判。",
        "group": "extension",
        "roles": ["computer_use_visual_judge"],
        "pagePath": "/admin/desktop-automation",
        "pageLabel": "桌面操作",
    },
    {
        "key": "computer_use_visual_actor",
        "label": "桌面视觉动作提案",
        "description": "从候选板与截图中提出视觉动作建议，执行前仍走 Safety 和验证链。",
        "group": "extension",
        "roles": ["computer_use_visual_actor"],
        "pagePath": "/admin/desktop-automation",
        "pageLabel": "桌面操作",
    },
    {
        "key": "computer_use_candidate_rerank",
        "label": "桌面候选重排",
        "description": "在多候选歧义场景中对文本化候选做二阶段精排。",
        "group": "extension",
        "roles": ["computer_use_candidate_reranker"],
        "pagePath": "/admin/desktop-automation",
        "pageLabel": "桌面操作",
    },
    {
        "key": "rpa_discovery",
        "label": "RPA 发现",
        "description": "流程探索、变量抽取与自动化复用生成角色，占位只读展示。",
        "group": "extension",
        "roles": ["rpa_discovery"],
        "pagePath": "/admin/rpa",
        "pageLabel": "RPA",
    },
]

CAPABILITY_TAG_ORDER = [
    ("chat", "对话"),
    ("reasoning", "推理"),
    ("toolCalling", "工具"),
    ("vision", "视觉"),
    ("multimodal", "多模态"),
    ("streaming", "流式"),
    ("image", "图片"),
    ("video", "视频"),
    ("voice", "语音"),
    ("audio", "音频"),
    ("music", "音乐"),
    ("embedding", "向量"),
    ("rerank", "重排"),
    ("workflow", "工作流"),
    ("model3d", "3D"),
    ("computerUse", "桌面"),
]


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _optional_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _normalize_project_budget(item: Dict[str, Any]) -> Dict[str, Any]:
    project_id = str(item.get("projectId") or item.get("project_id") or "").strip()
    return {
        "projectId": project_id,
        "projectName": str(item.get("projectName") or item.get("project_name") or project_id or ""),
        "dailyCostLimit": _safe_float(item.get("dailyCostLimit"), 0.0),
        "dailyTokenLimit": max(_safe_int(item.get("dailyTokenLimit")) or 0, 0),
    }


def _infer_reasoning(model_id: str, display_name: str) -> bool:
    identifier = f"{model_id} {display_name}".lower()
    keywords = [
        "reason",
        "thinking",
        "thought",
        "deepseek-r1",
        "deepseek-reasoner",
        "glm-4.7",
        "glm-5",
        "qwen3",
        "qwen3.5",
        "qwen 3",
        "kimi",
        "kimi2.5",
        "kimi-2.5",
        "moonshot",
        "claude",
        "gemini",
        "doubao-seed",
        "seedance",
        "nemotron",
        "r1",
    ]
    return any(token in identifier for token in keywords)


def _infer_capability_class(model_type: str, capabilities: Dict[str, bool]) -> str:
    normalized_type = model_type.upper()
    if normalized_type == "EMBEDDING" or capabilities.get("embedding"):
        return "embedding"
    if normalized_type in {"RERANK", "RERANKER"} or capabilities.get("rerank"):
        return "reranker"
    if (
        normalized_type in {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}
        or capabilities.get("image")
        or capabilities.get("video")
        or capabilities.get("audio")
        or capabilities.get("voice")
        or capabilities.get("music")
        or capabilities.get("workflow")
        or capabilities.get("model3d")
    ):
        return "media_generation"
    if capabilities.get("vision") or capabilities.get("multimodal"):
        return "vision_multimodal"
    if capabilities.get("reasoning"):
        return "chat_reasoning"
    if capabilities.get("toolCalling"):
        return "chat_tool_calling"
    return "chat_general"


class ModelControlPlane:
    def _runtime_ready_for_provider(self, provider_meta: Dict[str, Any]) -> bool:
        runtime_ready, _reason = runtime_readiness_for_provider(
            provider_id=str(provider_meta.get("name") or provider_meta.get("provider_id") or ""),
            api_standard=provider_meta.get("api_standard") or provider_meta.get("apiStandard") or "openai",
            provider_config=provider_meta,
        )
        return runtime_ready

    def _normalize_capabilities(self, model_id: str, model_meta: Dict[str, Any]) -> Dict[str, bool]:
        model_type = str(model_meta.get("type") or "TEXT").upper()
        raw_caps = dict(model_meta.get("capabilities") or {})
        display_name = str(model_id)

        media_like = model_type in {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}
        chat_like = model_type in {"TEXT", "MULTIMODAL"} and not media_like
        multimodal = model_type == "MULTIMODAL"
        embedding = model_type == "EMBEDDING"
        rerank = model_type in {"RERANK", "RERANKER"}

        base_url = str(model_meta.get("base_url") or model_meta.get("baseUrl") or "").strip().lower()
        raw_chat = bool(raw_caps.get("chat", chat_like))
        if media_like:
            raw_chat = False
        normalized = {
            "chat": raw_chat,
            "reasoning": bool(raw_caps.get("reasoning", _infer_reasoning(model_id, display_name))),
            "toolCalling": bool(raw_caps.get("toolCalling", chat_like)),
            "vision": bool(raw_caps.get("vision", multimodal)),
            "multimodal": bool(raw_caps.get("multimodal", raw_caps.get("vision", multimodal))),
            "streaming": bool(raw_caps.get("streaming", chat_like)),
            "embedding": bool(raw_caps.get("embedding", embedding)),
            "rerank": bool(raw_caps.get("rerank", rerank)),
            "image": bool(raw_caps.get("image", media_like and model_type in {"MEDIA", "IMAGE"})),
            "video": bool(raw_caps.get("video", media_like and model_type in {"MEDIA", "VIDEO"})),
            "audio": bool(raw_caps.get("audio", media_like and model_type in {"MEDIA", "AUDIO", "VOICE"})),
            "voice": bool(raw_caps.get("voice", raw_caps.get("audio", media_like and model_type in {"VOICE", "AUDIO"}))),
            "music": bool(raw_caps.get("music", media_like and model_type == "MUSIC")),
            "workflow": bool(raw_caps.get("workflow", media_like and model_type in {"MEDIA", "WORKFLOW"})),
            "model3d": bool(raw_caps.get("model3d", media_like and model_type == "MODEL3D")),
            "computerUse": bool(raw_caps.get("computerUse", False)),
        }
        capability_class = str(model_meta.get("capabilityClass") or _infer_capability_class(model_type, normalized))
        api_standard = str(model_meta.get("api_standard") or model_meta.get("apiStandard") or "openai")
        runtime_ready = bool(model_meta.get("runtimeReady", True))
        oauth_preset = str(model_meta.get("oauth_preset") or model_meta.get("oauthPreset") or "").strip().lower()
        if api_standard in {"google", "gemini"} and oauth_preset == "geminicli":
            normalized["toolCalling"] = True
            normalized["supportsNativeTools"] = False
            normalized["supportsPromptEmulatedTools"] = True
            normalized["supportsNativeStructuredOutput"] = False
            normalized["supportsPromptFallbackStructuredOutput"] = True
        if api_standard == "anthropic" and base_url.startswith("https://api.deepseek.com/anthropic"):
            normalized["vision"] = False
            normalized["supportsMultimodal"] = False
        normalized.update(
            normalize_capability_metadata(
                normalized,
                capability_class=capability_class,
                api_standard=api_standard,
                runtime_ready=runtime_ready,
            )
        )
        return normalized

    def _normalize_roles(self, roles_in: Dict[str, Any]) -> Dict[str, str]:
        roles = {**DEFAULT_ROLE_MAP}
        for key, value in (roles_in or {}).items():
            if key in {"stt", "tts"}:
                continue
            roles[str(key)] = str(value or "")
        return roles

    def _normalize_routing(self, routing_in: Dict[str, Any]) -> Dict[str, str]:
        routing = {**DEFAULT_ROUTING_POLICIES}
        for key, value in (routing_in or {}).items():
            if key in {"stt", "tts"}:
                continue
            routing[str(key)] = str(value or "")
        return routing

    def _normalize_role_parameters(self, raw: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
        params: Dict[str, Dict[str, Optional[float]]] = deepcopy(DEFAULT_ROLE_PARAMETERS)
        for role_key, role_value in (raw or {}).items():
            if not isinstance(role_value, dict):
                continue
            incoming = dict(role_value)
            role_params = dict(params.get(str(role_key)) or {})
            if "temperature" in incoming:
                role_params["temperature"] = normalize_config_temperature(incoming.get("temperature"))
            params[str(role_key)] = role_params
        return params

    def normalize_config(self, raw_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw = deepcopy(raw_config or {})
        providers_in = raw.get("providers") or {}
        roles_in = raw.get("roles") or {}
        role_parameters_in = raw.get("roleParameters") or raw.get("role_parameters") or {}
        bindings_in = raw.get("bindings") or {}
        governance_in = raw.get("governance") or {}
        budgets_in = dict(governance_in.get("budgets") or {})
        routing_in = raw.get("routingPolicies") or {}

        providers: Dict[str, Any] = {}
        for provider_id, provider_data in providers_in.items():
            meta = dict(provider_data.get("provider") or {})
            models = dict(provider_data.get("models") or {})
            provider_api_standard = str(meta.get("api_standard") or meta.get("apiStandard") or "openai")
            provider_runtime_ready = self._runtime_ready_for_provider(meta)
            normalized_models: Dict[str, Any] = {}
            for model_id, model_meta_raw in models.items():
                model_meta = dict(model_meta_raw or {})
                model_meta.pop("name", None)
                model_meta.pop("temperature", None)
                model_meta["runtimeReady"] = provider_runtime_ready
                capabilities = self._normalize_capabilities(model_id, model_meta)
                capability_class = str(
                    model_meta.get("capabilityClass")
                    or _infer_capability_class(str(model_meta.get("type") or "TEXT"), capabilities)
                )
                capabilities.update(
                    normalize_capability_metadata(
                        capabilities,
                        capability_class=capability_class,
                        api_standard=provider_api_standard,
                        runtime_ready=provider_runtime_ready,
                    )
                )
                normalized_models[model_id] = {
                    **model_meta,
                    "type": str(model_meta.get("type") or "TEXT").upper(),
                    "contextWindow": _safe_int(model_meta.get("contextWindow")),
                    "maxTokens": _safe_int(model_meta.get("maxTokens")),
                    "priority": 50 if _safe_int(model_meta.get("priority")) is None else _safe_int(model_meta.get("priority")),
                    "stabilityTier": str(model_meta.get("stabilityTier") or "stable"),
                    "isEnabled": bool(model_meta.get("isEnabled", True)),
                    "runtimeReady": provider_runtime_ready,
                    "capabilities": capabilities,
                    "capabilityClass": capability_class,
                    "capabilitySource": model_meta.get("capabilitySource") or "manual",
                    "parameterProfile": model_meta.get("parameterProfile") or ("media_generation" if capability_class == "media_generation" else "chat"),
                    "mediaLimits": model_meta.get("mediaLimits") or {},
                    "promptCachingProfileId": model_meta.get("promptCachingProfileId")
                    or meta.get("promptCachingProfileId")
                    or prompt_cache_profile_id_for_provider(str(provider_id)),
                }

            providers[str(provider_id)] = {
                "provider": {
                    **meta,
                    "name": meta.get("name") or str(provider_id),
                    "description": meta.get("description") or "",
                    "base_url": meta.get("base_url") or meta.get("baseUrl") or "",
                    "api_key": meta.get("api_key") or meta.get("apiKey") or "",
                    "api_standard": meta.get("api_standard") or meta.get("apiStandard") or "openai",
                    "providerKind": meta.get("providerKind") or meta.get("provider_kind") or "chat",
                    "type": meta.get("type") or "API",
                    "icon": meta.get("icon") or None,
                    "logoAsset": meta.get("logoAsset") or None,
                    "promptCachingProfileId": meta.get("promptCachingProfileId")
                    or meta.get("prompt_caching_profile_id")
                    or prompt_cache_profile_id_for_provider(str(provider_id)),
                    "is_enabled": bool(meta.get("is_enabled", meta.get("isEnabled", True))),
                },
                "models": normalized_models,
            }

        config = {
            "version": _safe_int(raw.get("version")) or 2,
            "providers": providers,
            "roles": self._normalize_roles(roles_in),
            "roleParameters": self._normalize_role_parameters(role_parameters_in),
            "bindings": {
                **DEFAULT_BINDINGS,
                **bindings_in,
                "agents": dict((bindings_in or {}).get("agents") or {}),
            },
            "governance": {
                **DEFAULT_GOVERNANCE,
                **governance_in,
                "providerHealthWindowDays": DEFAULT_GOVERNANCE["providerHealthWindowDays"]
                if _safe_int(governance_in.get("providerHealthWindowDays")) is None
                else max(_safe_int(governance_in.get("providerHealthWindowDays")) or 1, 1),
                "providerFailureThreshold": DEFAULT_GOVERNANCE["providerFailureThreshold"]
                if _safe_int(governance_in.get("providerFailureThreshold")) is None
                else max(_safe_int(governance_in.get("providerFailureThreshold")) or 1, 1),
                "providerErrorRateThreshold": max(
                    min(_safe_float(governance_in.get("providerErrorRateThreshold"), DEFAULT_GOVERNANCE["providerErrorRateThreshold"]), 1.0),
                    0.0,
                ),
                "maxLocalRetries": DEFAULT_GOVERNANCE["maxLocalRetries"]
                if _safe_int(governance_in.get("maxLocalRetries")) is None
                else _safe_int(governance_in.get("maxLocalRetries")),
                "maxProviderSwitches": DEFAULT_GOVERNANCE["maxProviderSwitches"]
                if _safe_int(governance_in.get("maxProviderSwitches")) is None
                else _safe_int(governance_in.get("maxProviderSwitches")),
                "budgets": {
                    **DEFAULT_GOVERNANCE["budgets"],
                    **budgets_in,
                    "globalDailyCostLimit": _safe_float(
                        budgets_in.get("globalDailyCostLimit"),
                        DEFAULT_GOVERNANCE["budgets"]["globalDailyCostLimit"],
                    ),
                    "globalDailyTokenLimit": max(
                        _safe_int(
                            budgets_in.get("globalDailyTokenLimit"),
                            DEFAULT_GOVERNANCE["budgets"]["globalDailyTokenLimit"],
                        )
                        or 0,
                        0,
                    ),
                    "runMaxCost": _safe_float(
                        budgets_in.get("runMaxCost"),
                        DEFAULT_GOVERNANCE["budgets"]["runMaxCost"],
                    ),
                    "runMaxTokens": max(
                        _safe_int(budgets_in.get("runMaxTokens"), DEFAULT_GOVERNANCE["budgets"]["runMaxTokens"]) or 0,
                        0,
                    ),
                    "defaultProjectDailyCostLimit": _safe_float(
                        budgets_in.get("defaultProjectDailyCostLimit"),
                        DEFAULT_GOVERNANCE["budgets"]["defaultProjectDailyCostLimit"],
                    ),
                    "defaultProjectDailyTokenLimit": max(
                        _safe_int(
                            budgets_in.get("defaultProjectDailyTokenLimit"),
                            DEFAULT_GOVERNANCE["budgets"]["defaultProjectDailyTokenLimit"],
                        )
                        or 0,
                        0,
                    ),
                    "projectBudgets": [
                        normalized_budget
                        for item in (budgets_in.get("projectBudgets") or [])
                        if (normalized_budget := _normalize_project_budget(dict(item or {}))).get("projectId")
                    ],
                },
            },
            "routingPolicies": self._normalize_routing(routing_in),
        }
        return config

    def get_config(self) -> Dict[str, Any]:
        return self.normalize_config(storage.get_models_config())

    def save_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self.normalize_config(data)
        storage.save_models_config(normalized)
        return normalized

    def get_role_definitions(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        normalized = config or self.get_config()
        definitions = deepcopy(ROLE_DEFINITIONS)
        for role_key in (normalized.get("roles") or {}).keys():
            if role_key not in definitions:
                definitions[role_key] = {
                    "label": role_key.replace("_", " ").title(),
                    "description": "扩展角色，当前未声明固定能力约束。",
                    "group": "extension",
                    "capabilityClasses": [],
                }
        return definitions

    def get_role_model_id(self, role: str) -> str:
        resolved = self.resolve_model_for_role(role)
        return str(resolved.get("resolvedModelRef") or resolved.get("resolvedModelId") or "")

    def _parameter_role_key(self, role: str) -> str:
        normalized = str(role or "").strip()
        if normalized.startswith("agent:") or normalized.startswith("reviewer:"):
            return "subagent"
        return normalized

    def get_role_parameters(self, role: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[float]]:
        normalized = config or self.get_config()
        params = dict(normalized.get("roleParameters") or {})
        key = self._parameter_role_key(role)
        return dict(params.get(key) or {})

    def get_role_temperature(self, role: str, config: Optional[Dict[str, Any]] = None) -> Optional[float]:
        params = self.get_role_parameters(role, config)
        value = params.get("temperature")
        return normalize_config_temperature(value)

    def _build_model_record(
        self,
        *,
        provider_id: str,
        provider_data: Dict[str, Any],
        model_id: str,
        normalized: Dict[str, Any],
    ) -> Dict[str, Any]:
        model_meta = dict((provider_data.get("models") or {}).get(model_id) or {})
        provider_meta = dict(provider_data.get("provider") or {})
        model_ref = make_model_ref(provider_id, model_id)
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "model_ref": model_ref,
            "provider": provider_meta,
            "model": model_meta,
            "governance": dict(normalized.get("governance") or {}),
            "roles": dict(normalized.get("roles") or {}),
            "roleParameters": dict(normalized.get("roleParameters") or {}),
            "routingPolicies": dict(normalized.get("routingPolicies") or {}),
        }

    def _resolve_model_lookup(
        self,
        model_ref_or_id: str,
        config: Optional[Dict[str, Any]] = None,
        *,
        provider_id: str = "",
    ) -> Dict[str, Any]:
        normalized = config or self.get_config()
        raw = str(model_ref_or_id or "").strip()
        if not raw:
            return {"status": "empty", "record": None, "matches": []}

        parsed = parse_model_ref(raw)
        if parsed:
            provider_id, model_id = parsed
            provider_data = (normalized.get("providers") or {}).get(provider_id) or {}
            if model_id in (provider_data.get("models") or {}):
                return {
                    "status": "exact",
                    "record": self._build_model_record(
                        provider_id=provider_id,
                        provider_data=provider_data,
                        model_id=model_id,
                        normalized=normalized,
                    ),
                    "matches": [make_model_ref(provider_id, model_id)],
                }
            return {"status": "missing", "record": None, "matches": []}

        if provider_id:
            provider_data = (normalized.get("providers") or {}).get(provider_id) or {}
            models = provider_data.get("models") or {}
            if raw in models:
                return {
                    "status": "exact",
                    "record": self._build_model_record(
                        provider_id=provider_id,
                        provider_data=provider_data,
                        model_id=raw,
                        normalized=normalized,
                    ),
                    "matches": [make_model_ref(provider_id, raw)],
                }
            return {"status": "missing", "record": None, "matches": []}

        matches: List[Dict[str, Any]] = []
        for candidate_provider_id, provider_data in (normalized.get("providers") or {}).items():
            if raw in (provider_data.get("models") or {}):
                matches.append(
                    self._build_model_record(
                        provider_id=candidate_provider_id,
                        provider_data=provider_data,
                        model_id=raw,
                        normalized=normalized,
                    )
                )
        if len(matches) == 1:
            return {
                "status": "legacy_unique",
                "record": matches[0],
                "matches": [matches[0].get("model_ref")],
            }
        if len(matches) > 1:
            return {
                "status": "ambiguous",
                "record": None,
                "matches": [str(item.get("model_ref") or "") for item in matches],
            }
        return {"status": "missing", "record": None, "matches": []}

    def get_model_record(
        self,
        model_id: str,
        config: Optional[Dict[str, Any]] = None,
        *,
        provider_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        lookup = self._resolve_model_lookup(model_id, config, provider_id=provider_id)
        return lookup.get("record")

    def _is_model_compatible(self, role_definition: Dict[str, Any], model_record: Optional[Dict[str, Any]]) -> bool:
        if not model_record:
            return False
        allowed = list(role_definition.get("capabilityClasses") or [])
        if not allowed:
            return True
        capability_class = str((model_record.get("model") or {}).get("capabilityClass") or "")
        return capability_class in allowed

    def resolve_model_for_role(self, role: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized = config or self.get_config()
        roles = normalized.get("roles") or {}
        role_definition = self.get_role_definitions(normalized).get(role, ROLE_DEFINITIONS["default"])

        explicit_model_id = str(roles.get(role) or "")
        default_model_id = str(roles.get("default") or "")
        explicit_lookup = self._resolve_model_lookup(explicit_model_id, normalized) if explicit_model_id else {"status": "empty", "record": None, "matches": []}
        default_lookup = self._resolve_model_lookup(default_model_id, normalized) if default_model_id else {"status": "empty", "record": None, "matches": []}
        explicit_record = explicit_lookup.get("record")
        default_record = default_lookup.get("record")

        binding_state = "unbound"
        model_record: Optional[Dict[str, Any]] = None
        resolved_model_id = ""
        resolved_model_ref = ""

        if explicit_model_id and self._is_model_compatible(role_definition, explicit_record):
            model_record = explicit_record
            binding_state = "explicit"
            resolved_model_id = str(explicit_record.get("model_id") or explicit_model_id)
            resolved_model_ref = str(explicit_record.get("model_ref") or "")
        elif role != "default" and default_model_id and self._is_model_compatible(role_definition, default_record):
            model_record = default_record
            binding_state = "inherited_default" if explicit_model_id else "default"
            resolved_model_id = str(default_record.get("model_id") or default_model_id)
            resolved_model_ref = str(default_record.get("model_ref") or "")
        elif explicit_model_id:
            binding_state = "ambiguous" if explicit_lookup.get("status") == "ambiguous" else "invalid"

        resolved_model = dict((model_record or {}).get("model") or {})
        resolved_provider = dict((model_record or {}).get("provider") or {})
        return {
            "role": role,
            "roleDefinition": role_definition,
            "rawModelId": explicit_model_id,
            "bindingState": binding_state,
            "resolvedModelId": resolved_model_id,
            "resolvedModelRef": resolved_model_ref,
            "resolvedProviderId": (model_record or {}).get("provider_id") or "",
            "lookupStatus": explicit_lookup.get("status") if explicit_model_id else "",
            "lookupMatches": explicit_lookup.get("matches") or [],
            "resolvedModel": resolved_model,
            "resolvedProvider": resolved_provider,
        }

    def _build_resolved_roles(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        normalized = config or self.get_config()
        return {
            role_key: self.resolve_model_for_role(role_key, normalized)
            for role_key in self.get_role_definitions(normalized).keys()
        }

    def build_summary(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized = config or self.get_config()
        flat_models = self.list_models(normalized)
        assigned_roles = sum(1 for value in (normalized.get("roles") or {}).values() if value)
        return {
            "providers": len(normalized.get("providers") or {}),
            "enabledProviders": sum(
                1
                for provider in (normalized.get("providers") or {}).values()
                if (provider.get("provider") or {}).get("is_enabled", True)
            ),
            "models": len(flat_models),
            "reasoningModels": sum(1 for model in flat_models if model["capabilities"].get("reasoning")),
            "multimodalModels": sum(1 for model in flat_models if model["capabilities"].get("vision") or model["capabilities"].get("multimodal")),
            "rolesAssigned": assigned_roles,
            "capabilityClasses": self._count_capability_classes(flat_models),
        }

    def _count_capability_classes(self, models: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for model in models:
            capability_class = str(model.get("capabilityClass") or "chat_general")
            counts[capability_class] = counts.get(capability_class, 0) + 1
        return counts

    def list_models(self, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        normalized = config or self.get_config()
        resolved_roles = self._build_resolved_roles(normalized)
        assigned_roles_by_model: Dict[str, List[str]] = {}
        for role_key, resolution in resolved_roles.items():
            resolved_model_ref = str(resolution.get("resolvedModelRef") or "")
            if resolved_model_ref:
                assigned_roles_by_model.setdefault(resolved_model_ref, []).append(role_key)

        models: List[Dict[str, Any]] = []
        for provider_id, provider_data in (normalized.get("providers") or {}).items():
            provider_meta = provider_data.get("provider") or {}
            for model_id, model_meta in (provider_data.get("models") or {}).items():
                capabilities = dict(model_meta.get("capabilities") or {})
                model_ref = make_model_ref(provider_id, model_id)
                models.append(
                    {
                        "id": model_ref,
                        "modelRef": model_ref,
                        "providerId": provider_id,
                        "providerName": provider_meta.get("name") or provider_id,
                        "providerIcon": provider_meta.get("icon"),
                        "modelId": model_id,
                        "type": model_meta.get("type") or "TEXT",
                        "contextWindow": model_meta.get("contextWindow"),
                        "maxTokens": model_meta.get("maxTokens"),
                        "capabilitySource": model_meta.get("capabilitySource") or "manual",
                        "parameterProfile": model_meta.get("parameterProfile") or "chat",
                        "mediaLimits": model_meta.get("mediaLimits") or {},
                        "promptCachingProfileId": model_meta.get("promptCachingProfileId")
                        or provider_meta.get("promptCachingProfileId")
                        or prompt_cache_profile_id_for_provider(str(provider_id)),
                        "priority": model_meta.get("priority"),
                        "stabilityTier": model_meta.get("stabilityTier"),
                        "isEnabled": bool(model_meta.get("isEnabled", True)),
                        "capabilities": capabilities,
                        "capabilityClass": model_meta.get("capabilityClass") or "chat_general",
                        "capabilityTags": [
                            label for key, label in CAPABILITY_TAG_ORDER if capabilities.get(key)
                        ],
                        "assignedRoles": assigned_roles_by_model.get(model_ref, []),
                    }
                )
        return sorted(models, key=lambda item: (item["providerName"].lower(), item["modelId"].lower()))

    def get_role_cards(self, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        normalized = config or self.get_config()
        models = self.list_models(normalized)
        role_definitions = self.get_role_definitions(normalized)
        resolved_roles = self._build_resolved_roles(normalized)

        cards: List[Dict[str, Any]] = []
        for role_key, role_definition in role_definitions.items():
            compatible_models = [
                {
                    "modelId": model["modelId"],
                    "modelRef": model["modelRef"],
                    "providerName": model["providerName"],
                    "capabilityClass": model["capabilityClass"],
                    "capabilityTags": model["capabilityTags"],
                }
                for model in models
                if not role_definition.get("capabilityClasses")
                or model["capabilityClass"] in role_definition.get("capabilityClasses", [])
            ]
            resolution = resolved_roles.get(role_key, {})
            resolved_model = dict(resolution.get("resolvedModel") or {})
            resolved_provider = dict(resolution.get("resolvedProvider") or {})
            cards.append(
                {
                    "key": role_key,
                    "label": role_definition.get("label") or role_key,
                    "description": role_definition.get("description") or "",
                    "group": role_definition.get("group") or "system",
                    "capabilityClasses": list(role_definition.get("capabilityClasses") or []),
                    "rawModelId": resolution.get("rawModelId") or "",
                    "resolvedModelId": resolution.get("resolvedModelId") or "",
                    "resolvedModelRef": resolution.get("resolvedModelRef") or "",
                    "resolvedModelName": resolution.get("resolvedModelId") or "",
                    "resolvedProviderName": resolved_provider.get("name") or "",
                    "bindingState": resolution.get("bindingState") or "unbound",
                    "compatibleModels": compatible_models,
                }
            )
        return sorted(cards, key=lambda item: (item["group"], item["label"].lower()))

    def get_module_statuses(self, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        normalized = config or self.get_config()
        resolved_roles = self._build_resolved_roles(normalized)
        statuses: List[Dict[str, Any]] = []

        for definition in MODULE_DEFINITIONS:
            resolved_models = []
            for role_key in definition.get("roles", []):
                resolution = resolved_roles.get(role_key, {})
                role_definition = ROLE_DEFINITIONS.get(role_key, {})
                resolved_model = dict(resolution.get("resolvedModel") or {})
                resolved_provider = dict(resolution.get("resolvedProvider") or {})
                resolved_models.append(
                    {
                        "role": role_key,
                        "roleLabel": role_definition.get("label") or role_key,
                        "bindingState": resolution.get("bindingState") or "unbound",
                        "modelId": resolution.get("resolvedModelId") or "",
                        "modelRef": resolution.get("resolvedModelRef") or "",
                        "modelName": resolution.get("resolvedModelId") or "",
                        "providerName": resolved_provider.get("name") or "",
                    }
                )

            if not resolved_models or all(item["bindingState"] == "unbound" for item in resolved_models):
                status = "planned" if definition.get("group") == "extension" else "attention"
            elif any(item["bindingState"] == "invalid" for item in resolved_models):
                status = "attention"
            elif any(item["bindingState"] in {"default", "inherited_default"} for item in resolved_models):
                status = "fallback"
            else:
                status = "healthy"

            statuses.append(
                {
                    "key": definition["key"],
                    "label": definition["label"],
                    "description": definition["description"],
                    "group": definition["group"],
                    "status": status,
                    "pagePath": definition["pagePath"],
                    "pageLabel": definition["pageLabel"],
                    "resolvedModels": resolved_models,
                }
            )

        return statuses

    def get_provider_statuses(self, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        normalized = config or self.get_config()
        return provider_health_service.build_provider_statuses(
            normalized,
            self.list_models(normalized),
            self._build_resolved_roles(normalized),
        )

    def build_payload(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized = config or self.get_config()
        from core.model_failover_service import model_failover_service
        budget_summary = model_budget_service.build_budget_summary(normalized)

        return {
            "config": normalized,
            "summary": self.build_summary(normalized),
            "models": self.list_models(normalized),
            "roles": self.get_role_cards(normalized),
            "modules": self.get_module_statuses(normalized),
            "providersOverview": self.get_provider_statuses(normalized),
            "governanceSummary": {
                "budgets": {
                    "enabled": bool(budget_summary.get("enabled", True)),
                    "today": dict((budget_summary.get("global") or {}).get("usage") or {}),
                    "limits": {
                        "globalDailyCostLimit": _safe_float((budget_summary.get("global") or {}).get("dailyCostLimit")),
                        "globalDailyTokenLimit": _safe_int((budget_summary.get("global") or {}).get("dailyTokenLimit")) or 0,
                        "runMaxCost": _safe_float((budget_summary.get("run") or {}).get("maxCost")),
                        "runMaxTokens": _safe_int((budget_summary.get("run") or {}).get("maxTokens")) or 0,
                        "defaultProjectDailyCostLimit": _safe_float((budget_summary.get("projectDefaults") or {}).get("dailyCostLimit")),
                        "defaultProjectDailyTokenLimit": _safe_int((budget_summary.get("projectDefaults") or {}).get("dailyTokenLimit")) or 0,
                    },
                    "projectOverrides": len(list(budget_summary.get("projectBudgets") or [])),
                },
                "failover": model_failover_service.build_failover_summary(normalized),
            },
        }


model_control_plane = ModelControlPlane()
