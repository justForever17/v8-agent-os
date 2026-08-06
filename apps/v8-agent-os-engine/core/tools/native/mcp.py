from __future__ import annotations

import json
import re
import warnings
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue

from core.mcp_config_service import (
    McpConfigValidationError,
    install_mcp_server_config,
    list_mcp_server_configs,
    mcp_runtime_status_snapshot,
    remove_mcp_server_config,
)
from core.config_broker_service import ConfigBrokerError, config_broker_service
from erc.runtime_context import get_runtime_context

__all__ = ["config_broker", "mcp_server_config"]


_SECRET_ARG_RE = re.compile(
    r"(?i)^(?:--?)?(?:api[-_]?key|access[-_]?token|token|secret|password|authorization|cookie)(?:=|:|$)"
)
_SECRET_ENV_ASSIGNMENT_RE = re.compile(r"(?i)^[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|COOKIE)\s*=")
_SAFE_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


ConfigBrokerMode = Literal[
    "models",
    "model_list",
    "inventory",
    "role_matrix",
    "recommend",
    "catalog_models",
    "catalog_discover",
    "catalog_connect_prepare",
    "catalog_provider_prepare",
    "catalog_provider_remove_prepare",
    "catalog_custom_provider_remove_prepare",
    "catalog_recover_prepare",
    "catalog_recover_finalize_prepare",
    "model_snapshot_recover_prepare",
    "model_provider_prepare",
    "model_binding_prepare",
    "model_default_prepare",
    "model_prepare",
    "role_prepare",
    "role_unbind_prepare",
    "model_record_prepare",
    "model_policy_prepare",
    "media_operation_prepare",
    "commit",
    "status",
    "rollback",
    "mcp_list",
    "mcp_status",
    "mcp_prepare_install",
    "mcp_prepare_remove",
]


class _StrictConfigBrokerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConfigBrokerAuthContract(_StrictConfigBrokerModel):
    type: str
    header: str | None = None
    scheme: str | None = None
    query: str | None = None
    preset: str | None = None
    path: str | None = None


class ConfigBrokerChannel(_StrictConfigBrokerModel):
    id: str
    label: str | None = None
    base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("baseUrl", "base_url"),
        serialization_alias="baseUrl",
    )
    api_standard: str | None = Field(
        default=None,
        validation_alias=AliasChoices("apiStandard", "api_standard"),
        serialization_alias="apiStandard",
    )
    wire_protocols: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("wireProtocols", "wire_protocols"),
        serialization_alias="wireProtocols",
    )
    default_wire_protocol: str | None = Field(
        default=None,
        validation_alias=AliasChoices("defaultWireProtocol", "default_wire_protocol"),
        serialization_alias="defaultWireProtocol",
    )
    auth_contract: ConfigBrokerAuthContract | None = Field(
        default=None,
        validation_alias=AliasChoices("authContract", "auth_contract"),
        serialization_alias="authContract",
    )
    auth: ConfigBrokerAuthContract | None = None


class ConfigBrokerSourceRef(_StrictConfigBrokerModel):
    source: str | None = None
    url: str | None = None
    title: str | None = None


class ConfigBrokerReasoningSurface(_StrictConfigBrokerModel):
    mode: str | None = None
    trust: str | None = None
    request_style: str | None = Field(
        default=None,
        validation_alias=AliasChoices("requestStyle", "request_style"),
        serialization_alias="requestStyle",
    )
    response_fields: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("responseFields", "response_fields"),
        serialization_alias="responseFields",
    )
    display_kind: str | None = Field(
        default=None,
        validation_alias=AliasChoices("displayKind", "display_kind"),
        serialization_alias="displayKind",
    )
    source_refs: list[str | ConfigBrokerSourceRef] | None = Field(
        default=None,
        validation_alias=AliasChoices("sourceRefs", "source_refs"),
        serialization_alias="sourceRefs",
    )
    notes: str | None = None
    source: str | None = None
    disabled: bool | None = None
    user_disabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("userDisabled", "user_disabled"),
        serialization_alias="userDisabled",
    )
    disable_reasoning_surface: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("disableReasoningSurface", "disable_reasoning_surface"),
        serialization_alias="disableReasoningSurface",
    )


class ConfigBrokerThinkingControl(_StrictConfigBrokerModel):
    supports_no_think: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("supportsNoThink", "supports_no_think"),
        serialization_alias="supportsNoThink",
    )
    disabled: bool | None = None
    no_think_disabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("noThinkDisabled", "no_think_disabled"),
        serialization_alias="noThinkDisabled",
    )
    thinking_disabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("thinkingDisabled", "thinking_disabled"),
        serialization_alias="thinkingDisabled",
    )
    request_style: str | None = Field(
        default=None,
        validation_alias=AliasChoices("requestStyle", "request_style"),
        serialization_alias="requestStyle",
    )
    source: str | None = None
    default_disabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("defaultDisabled", "default_disabled"),
        serialization_alias="defaultDisabled",
    )
    profile_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("profileId", "profile_id"),
        serialization_alias="profileId",
    )
    source_refs: list[str | ConfigBrokerSourceRef] | None = Field(
        default=None,
        validation_alias=AliasChoices("sourceRefs", "source_refs"),
        serialization_alias="sourceRefs",
    )
    wire_protocol: str | None = Field(
        default=None,
        validation_alias=AliasChoices("wireProtocol", "wire_protocol"),
        serialization_alias="wireProtocol",
    )


class ConfigBrokerReasoningEffortControl(_StrictConfigBrokerModel):
    supports_reasoning_effort: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("supportsReasoningEffort", "supports_reasoning_effort"),
        serialization_alias="supportsReasoningEffort",
    )
    request_style: str | None = Field(
        default=None,
        validation_alias=AliasChoices("requestStyle", "request_style"),
        serialization_alias="requestStyle",
    )
    levels: list[str]
    default_level: str | None = Field(
        default=None,
        validation_alias=AliasChoices("defaultLevel", "default_level"),
        serialization_alias="defaultLevel",
    )
    selected_level: str | None = Field(
        default=None,
        validation_alias=AliasChoices("selectedLevel", "selected_level", "level"),
        serialization_alias="selectedLevel",
    )
    mandatory: bool | None = None
    budget_by_level: dict[str, int] | None = Field(
        default=None,
        validation_alias=AliasChoices("budgetByLevel", "budget_by_level"),
        serialization_alias="budgetByLevel",
    )
    request_aliases: dict[str, str] | None = Field(
        default=None,
        validation_alias=AliasChoices("requestAliases", "request_aliases"),
        serialization_alias="requestAliases",
    )
    source: str | None = None
    profile_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("profileId", "profile_id"),
        serialization_alias="profileId",
    )
    source_refs: list[str | ConfigBrokerSourceRef] | None = Field(
        default=None,
        validation_alias=AliasChoices("sourceRefs", "source_refs"),
        serialization_alias="sourceRefs",
    )
    wire_protocol: str | None = Field(
        default=None,
        validation_alias=AliasChoices("wireProtocol", "wire_protocol"),
        serialization_alias="wireProtocol",
    )
    provider_id: str | None = Field(default=None, alias="providerId")
    model_id: str | None = Field(default=None, alias="modelId")


class ConfigBrokerEndpointBinding(_StrictConfigBrokerModel):
    version: int | None = None
    route: str | None = None
    channel_id: str | None = Field(default=None, alias="channelId")
    wire_protocol: str | None = Field(default=None, alias="wireProtocol")
    endpoint_path: str | None = Field(default=None, alias="endpointPath")
    provider_model_id: str | None = Field(default=None, alias="providerModelId")
    operation_kind: str | None = Field(default=None, alias="operationKind")
    adapter: str | None = None
    auth_contract: ConfigBrokerAuthContract | None = Field(default=None, alias="authContract")
    provenance: dict[str, JsonValue] | None = None


class ConfigBrokerModelSettings(_StrictConfigBrokerModel):
    name: str | None = None
    type: str | None = None
    context_window: int | None = Field(
        default=None,
        validation_alias=AliasChoices("contextWindow", "context_window"),
        serialization_alias="contextWindow",
    )
    max_tokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("maxTokens", "max_tokens"),
        serialization_alias="maxTokens",
    )
    capabilities: dict[str, bool] | None = None
    capability_class: str | None = Field(
        default=None,
        validation_alias=AliasChoices("capabilityClass", "capability_class"),
        serialization_alias="capabilityClass",
    )
    capability_source: str | None = Field(
        default=None,
        validation_alias=AliasChoices("capabilitySource", "capability_source"),
        serialization_alias="capabilitySource",
    )
    source_refs: list[str | ConfigBrokerSourceRef] | None = Field(
        default=None,
        validation_alias=AliasChoices("sourceRefs", "source_refs"),
        serialization_alias="sourceRefs",
    )
    parameter_profile: str | dict[str, JsonValue] | None = Field(
        default=None,
        validation_alias=AliasChoices("parameterProfile", "parameter_profile"),
        serialization_alias="parameterProfile",
    )
    media_limits: dict[str, JsonValue] | None = Field(
        default=None,
        validation_alias=AliasChoices("mediaLimits", "media_limits"),
        serialization_alias="mediaLimits",
    )
    logo_asset: str | None = Field(default=None, alias="logoAsset")
    capability_registry: dict[str, JsonValue] | None = Field(
        default=None,
        validation_alias=AliasChoices("capabilityRegistry", "capability_registry"),
        serialization_alias="capabilityRegistry",
    )
    pricing: dict[str, JsonValue] | None = None
    cost_per_input: float | None = Field(default=None, alias="costPerInput")
    cost_per_output: float | None = Field(default=None, alias="costPerOutput")
    rerank_api_flavor: str | None = Field(
        default=None,
        validation_alias=AliasChoices("rerankApiFlavor", "rerank_api_flavor"),
        serialization_alias="rerankApiFlavor",
    )
    drift_warnings: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("driftWarnings", "drift_warnings"),
        serialization_alias="driftWarnings",
    )
    reasoning_surface: ConfigBrokerReasoningSurface | None = Field(
        default=None,
        validation_alias=AliasChoices("reasoningSurface", "reasoning_surface"),
        serialization_alias="reasoningSurface",
    )
    thinking_control: ConfigBrokerThinkingControl | None = Field(
        default=None,
        validation_alias=AliasChoices("thinkingControl", "thinking_control"),
        serialization_alias="thinkingControl",
    )
    reasoning_effort_control: ConfigBrokerReasoningEffortControl | None = Field(
        default=None,
        validation_alias=AliasChoices("reasoningEffortControl", "reasoning_effort_control"),
        serialization_alias="reasoningEffortControl",
    )
    prompt_caching_profile_id: str | None = Field(default=None, alias="promptCachingProfileId")
    is_enabled: bool | None = Field(default=None, alias="isEnabled")
    runtime_ready: bool | None = Field(default=None, alias="runtimeReady")
    operation_kinds: list[str] | None = Field(default=None, alias="operationKinds")
    adapter: str | None = None
    availability: str | dict[str, JsonValue] | None = None
    endpoint_binding: ConfigBrokerEndpointBinding | None = Field(default=None, alias="endpointBinding")
    priority: int | None = None
    stability_tier: str | None = Field(default=None, alias="stabilityTier")


class ConfigBrokerProviderConfig(_StrictConfigBrokerModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("baseUrl", "base_url"),
        serialization_alias="baseUrl",
    )
    api_standard: str | None = Field(
        default=None,
        validation_alias=AliasChoices("apiStandard", "api_standard"),
        serialization_alias="apiStandard",
    )
    provider_kind: str | None = Field(default=None, alias="providerKind")
    media_modality: str | None = Field(default=None, alias="mediaModality")
    type: str | None = None
    credential_mode: str | None = Field(
        default=None,
        validation_alias=AliasChoices("credentialMode", "credential_mode"),
        serialization_alias="credentialMode",
    )
    oauth_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("oauthPreset", "oauth_preset"),
        serialization_alias="oauthPreset",
    )
    local_backend_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("localBackendPreset", "local_backend_preset"),
        serialization_alias="localBackendPreset",
    )
    logo_asset: str | None = Field(default=None, alias="logoAsset")
    credential_realm: str | None = Field(default=None, alias="credentialRealm")
    prompt_caching_profile_id: str | None = Field(default=None, alias="promptCachingProfileId")
    is_enabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("isEnabled", "is_enabled"),
        serialization_alias="isEnabled",
    )
    channels: list[ConfigBrokerChannel] | None = None
    default_channel_id: str | None = Field(default=None, alias="defaultChannelId")
    voice_app_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("voiceAppId", "voice_app_id"),
        serialization_alias="voiceAppId",
    )
    voice_resource_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("voiceResourceId", "voice_resource_id"),
        serialization_alias="voiceResourceId",
    )
    anthropic_compatible: bool | None = Field(default=None, alias="anthropicCompatible")
    auth_contract: ConfigBrokerAuthContract | None = Field(default=None, alias="authContract")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    proxy: str | None = None
    verify_tls: bool | None = Field(default=None, alias="verifyTls")


class ConfigBrokerCredentialHelp(_StrictConfigBrokerModel):
    label: str
    kind: str
    url: str


class ConfigBrokerCatalogRequest(_StrictConfigBrokerModel):
    submit_path: str | None = Field(default=None, alias="submitPath")


class ConfigBrokerCatalogModel(ConfigBrokerModelSettings):
    id: str
    capabilities: list[str] | None = None
    max_output_tokens: int | None = Field(default=None, alias="maxOutputTokens")
    metadata: dict[str, JsonValue] | None = None


class ConfigBrokerCapabilityEntry(_StrictConfigBrokerModel):
    type: str
    media_modality: str | None = Field(default=None, alias="mediaModality")
    provider_kind: str | None = Field(default=None, alias="providerKind")
    api_standard: str | None = Field(default=None, alias="apiStandard")
    base_url: str | None = Field(default=None, alias="baseUrl")
    adapter: str | None = None
    request: ConfigBrokerCatalogRequest | None = None
    models: list[ConfigBrokerCatalogModel] | None = None


class ConfigBrokerProviderPreset(ConfigBrokerProviderConfig):
    id: str
    auth: ConfigBrokerAuthContract | None = None
    probe_strategy: str | None = Field(default=None, alias="probeStrategy")
    models_path: str | None = Field(default=None, alias="modelsPath")
    models_url: str | None = Field(default=None, alias="modelsUrl")
    probe_model_allowlist: list[str] | None = Field(default=None, alias="probeModelAllowlist")
    credential_help: ConfigBrokerCredentialHelp | None = Field(default=None, alias="credentialHelp")
    confidence: str | None = None
    last_checked_at: str | None = Field(default=None, alias="lastCheckedAt")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    single_active_model: bool | None = Field(default=None, alias="singleActiveModel")
    reasoning_surface: ConfigBrokerReasoningSurface | None = Field(default=None, alias="reasoningSurface")
    capability_entries: list[ConfigBrokerCapabilityEntry] | None = Field(default=None, alias="capabilityEntries")
    declared_capabilities: list[str] | None = Field(default=None, alias="declaredCapabilities")
    models: list[ConfigBrokerCatalogModel] | None = None
    metadata: dict[str, JsonValue] | None = None


class ConfigBrokerBudgetPolicy(_StrictConfigBrokerModel):
    enabled: bool | None = None
    global_daily_cost_limit: float | None = Field(default=None, alias="globalDailyCostLimit")
    global_daily_token_limit: int | None = Field(default=None, alias="globalDailyTokenLimit")
    run_max_cost: float | None = Field(default=None, alias="runMaxCost")
    run_max_tokens: int | None = Field(default=None, alias="runMaxTokens")
    default_project_daily_cost_limit: float | None = Field(default=None, alias="defaultProjectDailyCostLimit")
    default_project_daily_token_limit: int | None = Field(default=None, alias="defaultProjectDailyTokenLimit")
    project_budgets: list[dict[str, JsonValue]] | None = Field(default=None, alias="projectBudgets")


class ConfigBrokerGovernance(_StrictConfigBrokerModel):
    enabled: bool | None = None
    sticky_run_model: bool | None = Field(default=None, alias="stickyRunModel")
    allow_same_capability_failover: bool | None = Field(default=None, alias="allowSameCapabilityFailover")
    strict_capability_match: bool | None = Field(default=None, alias="strictCapabilityMatch")
    max_local_retries: int | None = Field(default=None, alias="maxLocalRetries")
    max_provider_switches: int | None = Field(default=None, alias="maxProviderSwitches")
    default_streaming: bool | None = Field(default=None, alias="defaultStreaming")
    provider_health_window_days: int | None = Field(default=None, alias="providerHealthWindowDays")
    provider_failure_threshold: int | None = Field(default=None, alias="providerFailureThreshold")
    provider_error_rate_threshold: float | None = Field(default=None, alias="providerErrorRateThreshold")
    budgets: ConfigBrokerBudgetPolicy | None = None


class ConfigBrokerRoleParameters(_StrictConfigBrokerModel):
    temperature: float | None = None


class ConfigBrokerCredentialRequirement(_StrictConfigBrokerModel):
    id: str
    target: Literal["env", "header"]
    target_name: str = Field(alias="targetName")
    label: str | None = None
    required: bool = True


class ConfigBrokerArgs(_StrictConfigBrokerModel):
    mode: ConfigBrokerMode
    category: str = ""
    query: str = ""
    limit: int = 20
    offset: int = 0
    provider_id: str = ""
    provider_name: str = ""
    model_id: str = ""
    model_ref: str = ""
    base_url: str = ""
    api_standard: str = ""
    channel_id: str = ""
    wire_protocol: str = ""
    endpoint_path: str = ""
    model_type: str = ""
    context_window: int | None = None
    max_tokens: int | None = None
    capabilities: dict[str, bool] | None = None
    evidence_refs: list[str] | None = None
    role: str = ""
    operation_kind: str = ""
    enabled: bool = True
    priority: int | None = None
    transaction_id: str = ""
    source_transaction_id: str = ""
    plan_digest: str = ""
    mcp_name: str = ""
    mcp_type: str = ""
    command: str = ""
    command_args: list[str] | str | None = None
    url: str = ""
    disabled: bool = False
    credential_requirements: list[ConfigBrokerCredentialRequirement] | None = None
    provider_config: ConfigBrokerProviderConfig | None = None
    model_settings: ConfigBrokerModelSettings | None = None
    provider_preset: ConfigBrokerProviderPreset | None = None
    discover_if_needed: bool = True
    provider_operation: Literal["upsert", "remove"] = "upsert"
    request_secret: bool = False
    source_provider_id: str = ""
    source_model_id: str = ""
    binding_source: str = "manual"
    replace_provider_models: bool = False
    record_operation: str = ""
    governance: ConfigBrokerGovernance | None = None
    routing_policies: dict[str, str] | None = None
    role_parameters: dict[str, ConfigBrokerRoleParameters] | None = None


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        return [line.strip() for line in text.splitlines() if line.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _reject_secret_bearing_args(values: list[str]) -> None:
    for value in values:
        normalized = str(value or "").strip()
        if _SECRET_ARG_RE.search(normalized) or _SECRET_ENV_ASSIGNMENT_RE.search(normalized):
            raise ConfigBrokerError(
                "commandArgs 不能携带凭据；请声明 credentialRequirements 并使用安全动作卡。",
                code="config_secret_in_command_args",
                status_code=422,
            )


def _coerce_mapping(value: Any) -> dict[str, str]:
    if value in (None, "", {}):
        return {}
    if isinstance(value, dict):
        return {str(key).strip(): str(item) for key, item in value.items() if str(key).strip()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return {str(key).strip(): str(item) for key, item in parsed.items() if str(key).strip()}
        except Exception:
            pass
        result: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or "=" not in stripped:
                continue
            key, item = stripped.split("=", 1)
            normalized_key = key.strip()
            if normalized_key:
                result[normalized_key] = item.strip()
        return result
    return {}


def _coerce_boolean_mapping(value: Any) -> dict[str, bool]:
    if value in (None, "", {}):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    if not isinstance(value, dict):
        return {}
    return {str(key).strip(): bool(item) for key, item in value.items() if str(key).strip()}


def _runtime_identity() -> tuple[str, str, str]:
    context = dict(get_runtime_context() or {})
    return (
        str(context.get("user_id") or context.get("userId") or "").strip(),
        str(context.get("session_id") or context.get("sessionId") or "").strip(),
        str(context.get("run_id") or context.get("runId") or "").strip(),
    )


def _config_payload(value: BaseModel | dict[str, Any] | None, model_type: type[BaseModel]) -> dict[str, Any] | None:
    if value is None:
        return None
    validated = value if isinstance(value, model_type) else model_type.model_validate(value)
    return validated.model_dump(by_alias=True, exclude_none=True)


def _config_list_payload(
    values: list[BaseModel | dict[str, Any]] | None,
    model_type: type[BaseModel],
) -> list[dict[str, Any]] | None:
    if values is None:
        return None
    return [
        item.model_dump(by_alias=True, exclude_none=True)
        if isinstance(item, model_type)
        else model_type.model_validate(item).model_dump(by_alias=True, exclude_none=True)
        for item in values
    ]


def _safe_config_error(exc: ConfigBrokerError) -> tuple[str, str]:
    code = str(getattr(exc, "code", "") or "").strip()
    if not _SAFE_ERROR_CODE_RE.fullmatch(code):
        code = "config_broker_blocked"
    return code, "配置控制面请求被阻断；异常原文未进入可见面。"


def _config_validation_error(_error: Any) -> str:
    return json.dumps(
        {
            "ok": False,
            "mode": "validation",
            "state": "blocked",
            "summary": "配置控制面参数无效；原始输入未进入可见错误面。",
            "error": {
                "code": "config_broker_input_invalid",
                "message": "配置控制面参数无效；请按工具 schema 修正后重试。",
            },
        },
        ensure_ascii=False,
    )


@tool(args_schema=ConfigBrokerArgs)
def config_broker(
    mode: ConfigBrokerMode,
    category: str = "",
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    provider_id: str = "",
    provider_name: str = "",
    model_id: str = "",
    model_ref: str = "",
    base_url: str = "",
    api_standard: str = "",
    channel_id: str = "",
    wire_protocol: str = "",
    endpoint_path: str = "",
    model_type: str = "",
    context_window: int | None = None,
    max_tokens: int | None = None,
    capabilities: dict[str, bool] | None = None,
    evidence_refs: list[str] | None = None,
    role: str = "",
    operation_kind: str = "",
    enabled: bool = True,
    priority: int | None = None,
    transaction_id: str = "",
    source_transaction_id: str = "",
    plan_digest: str = "",
    mcp_name: str = "",
    mcp_type: str = "",
    command: str = "",
    command_args: list[str] | str | None = None,
    url: str = "",
    disabled: bool = False,
    credential_requirements: list[ConfigBrokerCredentialRequirement] | None = None,
    provider_config: ConfigBrokerProviderConfig | None = None,
    model_settings: ConfigBrokerModelSettings | None = None,
    provider_preset: ConfigBrokerProviderPreset | None = None,
    discover_if_needed: bool = True,
    provider_operation: Literal["upsert", "remove"] = "upsert",
    request_secret: bool = False,
    source_provider_id: str = "",
    source_model_id: str = "",
    binding_source: str = "manual",
    replace_provider_models: bool = False,
    record_operation: str = "",
    governance: ConfigBrokerGovernance | None = None,
    routing_policies: dict[str, str] | None = None,
    role_parameters: dict[str, ConfigBrokerRoleParameters] | None = None,
) -> str:
    """Inspect and change model/MCP configuration through one recoverable control plane.

    Supervisor only. Use `models` to list models by category, `role_matrix` to
    inspect model consumers, and `recommend` before changing a role. Use
    `agent:<agent-id>` as the role when inspecting or updating one registered
    Subagent; grandchild agents inherit and have no independent model binding.
    `catalog_models` lists the effective Model Hub catalog. Use
    `catalog_discover` to query one provider with its already-bound exact
    credential, `catalog_connect_prepare` to connect a catalog model, and
    `catalog_provider_prepare` to update the secret-free managed preset
    overlay; `catalog_provider_remove_prepare` removes only that managed
    overlay, `catalog_custom_provider_remove_prepare` removes only a custom
    provider, and `catalog_recover_prepare` restores the last verified managed
    backup while retaining reversible original bytes. After the restored
    catalog is accepted, use `catalog_recover_finalize_prepare` to prepare
    removal of the isolated original. `model_snapshot_recover_prepare` is the
    emergency repair path for a known bad model-domain replacement: it accepts
    only a prior durable Config Broker transaction id, never an arbitrary
    configuration document. Updating or removing a preset does not
    connect or disconnect a runtime model. Use `model_provider_prepare` for a
    runtime Provider upsert/remove, `model_binding_prepare` for an explicit
    model binding, `model_default_prepare` for a category default, and
    `model_record_prepare` for enable/disable/remove,
    `role_unbind_prepare` for an explicit binding, and
    `model_policy_prepare` for validated governance, routing and role
    parameters.
    Use `model_prepare` with researched facts and evidence refs, or
    `media_operation_prepare` with an exact operation_kind and configured
    model_ref, then `commit` with the returned transaction_id and plan_digest.
    Credential requirements are derived from the validated Provider auth
    contract; callers cannot disable them with a separate flag.
    Evidence references remain unverified until a trusted catalog or explicit
    operator review supplies the corresponding fact provenance; web research
    is never promoted above a user's saved facts by itself.
    Never pass API keys, tokens, cookies, env values or authorization headers to
    this tool. OAuth credential references are also excluded from the Agent
    parameter surface; bind them through the controlled configuration UI.
    When a credential is required, `model_prepare` or
    `mcp_prepare_install` returns a one-time UI:// action card for the user.

    MCP modes are `mcp_list`, `mcp_status`, `mcp_prepare_install` and
    `mcp_prepare_remove`. Configuration commits are durable and expose
    `status`/`rollback`. Doctor validates model facts, Safety checks the exact
    credential target, and governed Config Broker paths commit or restore only
    through the transaction service.
    """

    normalized_mode = str(mode or "").strip().lower()
    normalized_provider_config = _config_payload(provider_config, ConfigBrokerProviderConfig)
    normalized_model_settings = _config_payload(model_settings, ConfigBrokerModelSettings)
    normalized_provider_preset = _config_payload(provider_preset, ConfigBrokerProviderPreset)
    normalized_governance = _config_payload(governance, ConfigBrokerGovernance)
    normalized_role_parameters = (
        {
            str(key): _config_payload(value, ConfigBrokerRoleParameters) or {}
            for key, value in role_parameters.items()
        }
        if role_parameters is not None
        else None
    )
    normalized_credential_requirements = _config_list_payload(
        credential_requirements,
        ConfigBrokerCredentialRequirement,
    )
    owner_id, session_id, run_id = _runtime_identity()
    try:
        if normalized_mode in {"models", "model_list", "inventory"}:
            payload = config_broker_service.inventory(category=category, query=query, limit=limit, offset=offset)
        elif normalized_mode == "role_matrix":
            payload = config_broker_service.role_matrix()
        elif normalized_mode == "recommend":
            payload = config_broker_service.recommend(role=role, limit=limit)
        elif normalized_mode == "catalog_models":
            payload = config_broker_service.catalog_inventory(
                provider_id=provider_id,
                query=query,
                limit=limit,
                offset=offset,
            )
        elif normalized_mode == "catalog_discover":
            payload = config_broker_service.catalog_discover(
                provider_id=provider_id,
                query=query,
                limit=limit,
                offset=offset,
            )
        elif normalized_mode == "catalog_connect_prepare":
            payload = config_broker_service.prepare_catalog_model(
                provider_id=provider_id,
                model_id=model_id,
                channel_id=channel_id,
                wire_protocol=wire_protocol,
                discover_if_needed=discover_if_needed,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "catalog_provider_prepare":
            payload = config_broker_service.prepare_catalog_provider(
                provider_preset=normalized_provider_preset or {},
                evidence_refs=evidence_refs,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "catalog_provider_remove_prepare":
            payload = config_broker_service.prepare_catalog_provider_removal(
                provider_id=provider_id,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "catalog_custom_provider_remove_prepare":
            payload = config_broker_service.prepare_custom_catalog_provider_removal(
                provider_id=provider_id,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "catalog_recover_prepare":
            payload = config_broker_service.prepare_catalog_recovery(
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "catalog_recover_finalize_prepare":
            payload = config_broker_service.prepare_catalog_recovery_finalize(
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_snapshot_recover_prepare":
            payload = config_broker_service.prepare_model_snapshot_recovery(
                source_transaction_id=source_transaction_id,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_provider_prepare":
            payload = config_broker_service.prepare_model_provider_change(
                provider_id=provider_id,
                operation=provider_operation,
                provider_config=normalized_provider_config,
                request_secret=request_secret,
                oauth_credential="",
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_binding_prepare":
            payload = config_broker_service.prepare_model_binding(
                provider_id=provider_id,
                model_id=model_id,
                model_config=normalized_model_settings,
                source_provider_id=source_provider_id,
                source_model_id=source_model_id,
                source=binding_source,
                replace_provider_models=replace_provider_models,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_default_prepare":
            payload = config_broker_service.prepare_model_default(
                model_ref=model_ref,
                category=category,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_prepare":
            payload = config_broker_service.prepare_model(
                provider_id=provider_id,
                model_id=model_id,
                provider_name=provider_name,
                base_url=base_url,
                api_standard=api_standard,
                channel_id=channel_id,
                wire_protocol=wire_protocol,
                endpoint_path=endpoint_path,
                model_type=model_type,
                context_window=context_window,
                max_tokens=max_tokens,
                capabilities=_coerce_boolean_mapping(capabilities),
                evidence_refs=evidence_refs,
                credential_required=None,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
                provider_config=normalized_provider_config,
                model_config=normalized_model_settings,
            )
        elif normalized_mode == "role_prepare":
            payload = config_broker_service.prepare_role_assignment(
                role=role,
                model_ref=model_ref,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "role_unbind_prepare":
            payload = config_broker_service.prepare_role_unbind(
                role=role,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_record_prepare":
            payload = config_broker_service.prepare_model_record_change(
                model_ref=model_ref,
                operation=record_operation,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "model_policy_prepare":
            payload = config_broker_service.prepare_model_policy(
                governance=normalized_governance,
                routing_policies=routing_policies,
                role_parameters=normalized_role_parameters,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "media_operation_prepare":
            payload = config_broker_service.prepare_media_operation(
                operation_kind=operation_kind,
                model_ref=model_ref,
                enabled=enabled,
                priority=priority,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "commit":
            transaction = config_broker_service.get_transaction(transaction_id, owner_id=owner_id)
            if not plan_digest or str(transaction.get("planDigest") or "") != str(plan_digest).strip():
                raise ConfigBrokerError("提交需要匹配当前事务的 planDigest。", code="config_plan_digest_mismatch", status_code=409)
            payload = config_broker_service.commit(transaction_id, owner_id=owner_id)
        elif normalized_mode == "status":
            payload = {"ok": True, "mode": "status", **config_broker_service.get_transaction(transaction_id, owner_id=owner_id)}
        elif normalized_mode == "rollback":
            payload = config_broker_service.rollback(transaction_id, owner_id=owner_id)
        elif normalized_mode == "mcp_list":
            payload = config_broker_service.mcp_list()
        elif normalized_mode == "mcp_status":
            payload = config_broker_service.mcp_status()
        elif normalized_mode == "mcp_prepare_install":
            server: dict[str, Any] = {"type": str(mcp_type or "").strip().lower(), "disabled": bool(disabled)}
            if command:
                server["command"] = str(command).strip()
            args = _coerce_string_list(command_args)
            _reject_secret_bearing_args(args)
            if args:
                server["args"] = args
            if url:
                server["url"] = str(url).strip()
            payload = config_broker_service.prepare_mcp(
                operation="install",
                name=mcp_name,
                server=server,
                credential_requirements=normalized_credential_requirements,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "mcp_prepare_remove":
            payload = config_broker_service.prepare_mcp(
                operation="remove",
                name=mcp_name,
                server=None,
                credential_requirements=None,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        else:
            raise ConfigBrokerError("不支持的 config_broker mode。", code="config_broker_mode_invalid")
        return json.dumps(payload, ensure_ascii=False)
    except ConfigBrokerError as exc:
        error_code, error_message = _safe_config_error(exc)
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "state": "blocked",
                "summary": error_message,
                "error": {"code": error_code, "message": error_message},
            },
            ensure_ascii=False,
        )
    except Exception:
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "state": "failed",
                "summary": "配置控制面执行失败。",
                "error": {
                    "code": "config_broker_failed",
                    "message": "配置控制面执行失败；异常原文未进入可见面。",
                },
            },
            ensure_ascii=False,
        )


config_broker.handle_validation_error = _config_validation_error


def _server_payload(
    *,
    name: str,
    type: str,
    command: str,
    args: Any,
    url: str,
    env: Any,
    headers: Any,
    disabled: bool,
) -> dict[str, Any]:
    server: dict[str, Any] = {
        "type": str(type or "").strip().lower(),
        "disabled": bool(disabled),
    }
    normalized_command = str(command or "").strip()
    normalized_url = str(url or "").strip()
    if normalized_command:
        server["command"] = normalized_command
    normalized_args = _coerce_string_list(args)
    if normalized_args:
        server["args"] = normalized_args
    if normalized_url:
        server["url"] = normalized_url
    normalized_env = _coerce_mapping(env)
    if normalized_env:
        server["env"] = normalized_env
    normalized_headers = _coerce_mapping(headers)
    if normalized_headers:
        server["headers"] = normalized_headers
    return {"mcpServers": {str(name or "").strip(): server}}


def _list_markdown() -> str:
    payload = list_mcp_server_configs()
    servers = payload.get("servers") or []
    if not servers:
        return "MCP server 配置为空。\n\n下一步：如用户要求安装 MCP server，调用 `mcp_server_config(mode='mcp_install', ...)`。"
    lines = [f"MCP server 配置共 {payload.get('serverCount', len(servers))} 个："]
    for server in servers:
        target = server.get("command") or server.get("url") or "未设置目标"
        disabled = "（已停用）" if server.get("disabled") else ""
        extras: list[str] = []
        if server.get("argsCount"):
            extras.append(f"args {server.get('argsCount')}")
        if server.get("envKeys"):
            extras.append("env: " + ", ".join(server.get("envKeys") or []))
        if server.get("headerKeys"):
            extras.append("headers: " + ", ".join(server.get("headerKeys") or []))
        suffix = f"；{'; '.join(extras)}" if extras else ""
        lines.append(f"- {server.get('name')}: {server.get('type') or 'unknown'} -> {target}{disabled}{suffix}")
    return "\n".join(lines)


def _status_markdown() -> str:
    payload = mcp_runtime_status_snapshot()
    if payload.get("error"):
        return f"MCP runtime 状态读取失败：{payload.get('error')}"
    health = payload.get("health") or {}
    startup = payload.get("startup") or {}
    status = payload.get("servers") or {}
    lines = [
        f"MCP runtime 状态：{health.get('status') or startup.get('startupState') or 'unknown'}",
        f"- 启动状态：{startup.get('startupState') or 'unknown'}",
        f"- server 数：{len(status) if isinstance(status, dict) else 0}",
    ]
    if isinstance(status, dict):
        for name, server in sorted(status.items()):
            if not isinstance(server, dict):
                continue
            lines.append(f"- {name}: {server.get('status') or server.get('state') or 'unknown'}")
    return "\n".join(lines)


@tool
def mcp_server_config(
    mode: str,
    name: str = "",
    type: str = "",
    command: str = "",
    command_args: Any = None,
    url: str = "",
    env: Any = None,
    headers: Any = None,
    disabled: bool = False,
) -> str:
    """Configure installed MCP servers through the governed Engine config service.

    Use this when the user explicitly asks to install, list, remove, or inspect an MCP server.
    Do not edit `~/.v8-agent-os/mcp.json` directly and do not call Admin login-only APIs.

    Modes:
    - `mcp_install`: add or replace one MCP server. Required: `name`, `type`.
      For `type='stdio'`, provide `command` and optional `command_args` / `env`.
      For `type='http'` or `type='sse'`, provide `url` and optional `headers`.
    - `mcp_list`: list configured MCP servers without exposing secret values.
    - `mcp_remove`: remove one server by `name`.
    - `mcp_status`: inspect current Extensions/MCP runtime status.

    `env` and `headers` may be JSON objects or newline `KEY=value` text. Installing a server only changes MCP config and requests an Extensions refresh; it does not grant permission to bypass runtime gates.
    """
    warnings.warn("mcp_server_config is deprecated; use config_broker", DeprecationWarning, stacklevel=2)
    normalized_mode = str(mode or "").strip().lower()
    try:
        if normalized_mode == "mcp_list":
            return _list_markdown()
        if normalized_mode == "mcp_status":
            return _status_markdown()
        if normalized_mode == "mcp_remove":
            result = remove_mcp_server_config(name)
            if result.get("alreadyAbsent"):
                return f"MCP server `{result.get('removedServer')}` 原本不存在；当前配置未变。"
            return (
                f"已移除 MCP server `{result.get('removedServer')}`。\n"
                f"- 当前 server 数：{result.get('serverCount')}\n"
                "- 已请求 Extensions Runtime 刷新。"
            )
        if normalized_mode == "mcp_install":
            payload = _server_payload(
                name=name,
                type=type,
                command=command,
                args=command_args,
                url=url,
                env=env,
                headers=headers,
                disabled=disabled,
            )
            result = install_mcp_server_config(payload)
            installed = ", ".join(result.get("installedServers") or [])
            replaced = result.get("replacedServers") or []
            lines = [
                f"已配置 MCP server：{installed or str(name or '').strip() or 'unknown'}。",
                f"- 当前 server 数：{result.get('serverCount')}",
                "- 已请求 Extensions Runtime 刷新。",
            ]
            if replaced:
                lines.append("- 注意：同名 server 已被替换：" + ", ".join(replaced))
            lines.append("下一步：可调用 `mcp_server_config(mode='mcp_status')` 查看连接状态。")
            return "\n".join(lines)
        return "mcp_server_config 参数错误：mode 必须是 mcp_install、mcp_list、mcp_remove 或 mcp_status。"
    except McpConfigValidationError as exc:
        return f"MCP server 配置无效：{exc.message}"
    except Exception as exc:
        return f"MCP server 配置失败：{str(exc)}"
