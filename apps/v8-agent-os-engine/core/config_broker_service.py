from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import threading
import uuid
from copy import deepcopy
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

from core.database import db
from core.mcp_config_service import (
    list_mcp_server_configs,
    mcp_runtime_status_snapshot,
    request_mcp_inventory_refresh,
    validate_mcp_server_map,
)
from core.model_control_plane import (
    DEFAULT_GOVERNANCE,
    DEFAULT_ROLE_PARAMETERS,
    DEFAULT_ROUTING_POLICIES,
    model_control_plane,
)
from core.model_eligibility import evaluate_model_eligibility, model_category, model_kind
from core.model_ref import make_model_ref, parse_model_ref
from core.security.credentials import CredentialStoreError, credential_ref_store
from core.storage import storage
from core.time_truth import utc_now_iso
from erc.safety_guardian import safety_guardian


_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_API_STANDARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_CREATIVE_MEDIA_PREFERENCES_FILE = "creative_media/model_preferences.json"

_PROVIDER_PATCH_KEYS = {
    "name",
    "description",
    "icon",
    "base_url",
    "baseUrl",
    "api_standard",
    "apiStandard",
    "providerKind",
    "mediaModality",
    "type",
    "credential_mode",
    "credentialMode",
    "oauth_preset",
    "oauthPreset",
    "oauth_ref",
    "oauthRef",
    "local_backend_preset",
    "localBackendPreset",
    "logoAsset",
    "credentialRealm",
    "promptCachingProfileId",
    "is_enabled",
    "isEnabled",
    "channels",
    "defaultChannelId",
    "voice_app_id",
    "voice_resource_id",
    "voiceAppId",
    "voiceResourceId",
    "anthropicCompatible",
    "authContract",
    "timeoutMs",
    "proxy",
    "verifyTls",
}
_MODEL_PATCH_KEYS = {
    "name",
    "type",
    "contextWindow",
    "context_window",
    "maxTokens",
    "max_tokens",
    "capabilities",
    "capabilityClass",
    "capability_class",
    "capabilitySource",
    "capability_source",
    "sourceRefs",
    "parameterProfile",
    "parameter_profile",
    "mediaLimits",
    "media_limits",
    "logoAsset",
    "capabilityRegistry",
    "capability_registry",
    "pricing",
    "costPerInput",
    "costPerOutput",
    "rerank_api_flavor",
    "driftWarnings",
    "drift_warnings",
    "reasoningSurface",
    "reasoning_surface",
    "thinkingControl",
    "thinking_control",
    "reasoningEffortControl",
    "reasoning_effort_control",
    "promptCachingProfileId",
    "isEnabled",
    "runtimeReady",
    "operationKinds",
    "adapter",
    "rerankApiFlavor",
    "availability",
    "endpointBinding",
    "priority",
    "stabilityTier",
}
_SECRET_FIELD_NAMES = {
    "apikey",
    "api_key",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "cookie",
    "authorization",
    "credentialref",
    "credential_ref",
}
_MODEL_GOVERNANCE_KEYS = set(DEFAULT_GOVERNANCE)
_MODEL_BUDGET_KEYS = set(dict(DEFAULT_GOVERNANCE.get("budgets") or {}))
_MODEL_ROUTING_KEYS = {*DEFAULT_ROUTING_POLICIES, "channel"}
_MODEL_ROLE_PARAMETER_KEYS = {"temperature"}
_MODEL_BINDING_SOURCES = {"manual", "catalog_import", "reasoning_repair_probe"}
_MODEL_SNAPSHOT_SOURCE_KINDS = {
    "model",
    "model_record",
    "model_provider",
    "model_binding",
    "model_role",
    "model_role_bundle",
    "agent_model_role",
    "model_policy_bundle",
    "model_snapshot_restore",
}
_MODEL_CONTROL_PLANE_TARGET_KINDS = {
    "agent_model_role",
    "model",
    "model_binding",
    "model_policy_bundle",
    "model_provider",
    "model_record",
    "model_role",
    "model_role_bundle",
    "model_snapshot_restore",
}


def _get_model_connection_tester():
    from core.model_connection_tester import model_connection_tester

    return model_connection_tester


def _get_model_provider_catalog():
    from core.model_provider_catalog import model_provider_catalog

    return model_provider_catalog


def _build_catalog_connection_plan(**kwargs: Any) -> dict[str, Any]:
    from core.model_catalog_connection import build_catalog_model_connection_plan

    return build_catalog_model_connection_plan(**kwargs)


class ConfigBrokerError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return deepcopy(fallback)
    try:
        return json.loads(str(value))
    except Exception:
        return deepcopy(fallback)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _model_snapshot_authority_projection(config: dict[str, Any]) -> dict[str, Any]:
    """Return the durable, operator-owned portion of a model-domain snapshot.

    Runtime readiness and reasoning/thinking display controls are recomputed
    from the current model contracts. They are not durable user configuration,
    so they must not make a valid historical recovery look stale after a model
    capability profile evolves. Empty API-key placeholders are likewise not a
    credential or a model-routing fact.
    """

    projection = deepcopy(dict(config or {}))
    for provider_data in dict(projection.get("providers") or {}).values():
        if not isinstance(provider_data, dict):
            continue
        provider = dict(provider_data.get("provider") or {})
        if not str(provider.get("api_key") or provider.get("apiKey") or "").strip():
            provider.pop("api_key", None)
            provider.pop("apiKey", None)
        provider_data["provider"] = provider
        for model_data in dict(provider_data.get("models") or {}).values():
            if not isinstance(model_data, dict):
                continue
            for key in (
                "runtimeReady",
                "reasoningSurface",
                "thinkingControl",
                "reasoningEffortControl",
            ):
                model_data.pop(key, None)
    return projection


def _safe_refs(values: Iterable[Any] | None, *, limit: int = 12) -> list[str]:
    refs: list[str] = []
    for value in values or []:
        _reject_secret_fields(value, path="evidenceRefs")
        text = str(value or "").strip()
        if not text or text in refs:
            continue
        if re.search(
            r"(?i)(?:(?:authorization\s*:\s*)?(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passphrase|secret|cookie)\s*[:=])",
            text,
        ) or re.search(r"(?i)\b(?:sk|rk|pk|xox[abprs])-[-A-Za-z0-9_]{8,}\b", text):
            raise ConfigBrokerError(
                "证据引用包含凭据或敏感认证数据，已拒绝。",
                code="config_secret_in_evidence_ref",
                status_code=422,
            )
        refs.append(text[:500])
        if len(refs) >= limit:
            break
    return refs


def _reject_secret_fields(value: Any, *, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            raw_key = str(key or "").strip()
            normalized = raw_key.lower().replace("-", "_")
            separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw_key)
            tokens = {
                token.lower()
                for token in re.split(r"[^A-Za-z0-9]+", separated)
                if token
            }
            secret_tokens = {"secret", "password", "passphrase", "cookie", "authorization", "token"}
            if (
                normalized in _SECRET_FIELD_NAMES
                or normalized.endswith(("_apikey", "_token", "_secret", "_password"))
                or bool(tokens & secret_tokens)
                or {"api", "key"}.issubset(tokens)
            ):
                raise ConfigBrokerError(
                    f"{path}.{key} 不能携带凭据；请使用安全凭据卡。",
                    code="config_secret_in_patch",
                    status_code=422,
                )
            _reject_secret_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, path=f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
        parsed = urlparse(value.strip())
        if parsed.username is not None or parsed.password is not None:
            raise ConfigBrokerError(
                f"{path} 不能携带 URL 凭据。",
                code="config_secret_in_patch",
                status_code=422,
            )
        query_keys = [key for key, _item in parse_qsl(parsed.query, keep_blank_values=True)]
        query_keys.extend(
            component.split("=", 1)[0]
            for component in parsed.query.split("&")
            if component
        )
        for raw_key in query_keys:
            decoded = str(raw_key or "")
            for _index in range(3):
                next_value = unquote(decoded)
                if next_value == decoded:
                    break
                decoded = next_value
            separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", decoded)
            tokens = {
                token.lower()
                for token in re.split(r"[^A-Za-z0-9]+", separated)
                if token
            }
            if (
                tokens & {"secret", "password", "passphrase", "cookie", "authorization", "token", "signature", "sig"}
                or {"api", "key"}.issubset(tokens)
                or decoded.lower().replace("-", "_") in _SECRET_FIELD_NAMES
            ):
                raise ConfigBrokerError(
                    f"{path} 不能携带敏感 URL query。",
                    code="config_secret_in_patch",
                    status_code=422,
                )


def _safe_patch(value: Any, *, allowed: set[str], label: str) -> dict[str, Any]:
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict):
        raise ConfigBrokerError(f"{label} 必须是对象。", code="config_patch_invalid")
    _reject_secret_fields(value, path=label)
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ConfigBrokerError(
            f"{label} 包含未支持字段：{', '.join(unknown[:8])}。",
            code="config_patch_field_unknown",
        )
    return deepcopy(value)


def _canonical_provider_patch(value: Any, *, allow_oauth: bool = False) -> dict[str, Any]:
    patch = _safe_patch(value, allowed=_PROVIDER_PATCH_KEYS, label="providerConfig")
    aliases = {
        "baseUrl": "base_url",
        "apiStandard": "api_standard",
        "credentialMode": "credential_mode",
        "oauthPreset": "oauth_preset",
        "oauthRef": "oauth_ref",
        "localBackendPreset": "local_backend_preset",
        "isEnabled": "is_enabled",
        "voiceAppId": "voice_app_id",
        "voiceResourceId": "voice_resource_id",
    }
    for source, target in aliases.items():
        if source in patch:
            patch[target] = patch.pop(source)
    if "authContract" in patch:
        contract = patch["authContract"]
        if not isinstance(contract, dict):
            raise ConfigBrokerError("providerConfig.authContract 必须是对象。", code="config_patch_invalid")
        unknown = sorted(str(key) for key in contract if key not in {"type", "header", "scheme", "query", "preset", "path"})
        if unknown:
            raise ConfigBrokerError(
                f"providerConfig.authContract 包含未支持字段：{', '.join(unknown)}。",
                code="config_patch_field_unknown",
            )
        auth_type = str(contract.get("type") or "api_key").strip().lower()
        allowed_auth_types = {"api_key", "none", "oauth_file"} if allow_oauth else {"api_key", "none"}
        if auth_type not in allowed_auth_types:
            raise ConfigBrokerError(
                "providerConfig.authContract.type 不受支持。",
                code="provider_auth_contract_invalid",
            )
        contract["type"] = auth_type
    return patch


def _catalog_auth_contract(provider: dict[str, Any]) -> dict[str, str]:
    from core.model_catalog_connection import provider_auth_contract

    return provider_auth_contract(provider)


def _canonical_model_patch(value: Any) -> dict[str, Any]:
    patch = _safe_patch(value, allowed=_MODEL_PATCH_KEYS, label="modelConfig")
    aliases = {
        "context_window": "contextWindow",
        "max_tokens": "maxTokens",
        "capability_class": "capabilityClass",
        "capability_source": "capabilitySource",
        "parameter_profile": "parameterProfile",
        "media_limits": "mediaLimits",
        "capability_registry": "capabilityRegistry",
        "drift_warnings": "driftWarnings",
        "reasoning_surface": "reasoningSurface",
        "thinking_control": "thinkingControl",
        "reasoning_effort_control": "reasoningEffortControl",
    }
    for source, target in aliases.items():
        if source in patch:
            patch[target] = patch.pop(source)
    return patch


def _model_binding_target_id(source_ref: str, target_ref: str) -> str:
    return _json([str(source_ref or "").strip(), str(target_ref or "").strip()])


def _model_binding_target_refs(target_id: str) -> tuple[str, str]:
    values = _loads(target_id, [])
    if not isinstance(values, list) or len(values) != 2:
        return "", ""
    return str(values[0] or "").strip(), str(values[1] or "").strip()


def _model_role_bundle_target_id(roles: Iterable[Any]) -> str:
    return _json(sorted({str(role or "").strip() for role in roles if str(role or "").strip()}))


def _model_role_bundle_target_roles(target_id: str) -> list[str]:
    values = _loads(target_id, [])
    if not isinstance(values, list):
        return []
    return sorted({str(role or "").strip() for role in values if str(role or "").strip()})


def _model_reference_matches(value: Any, identity: tuple[str, str]) -> bool:
    """Accept canonical refs plus legacy bare model ids in persisted bindings."""

    text = str(value or "").strip()
    return parse_model_ref(text) == identity or text == identity[1]


def _model_dependencies(config: dict[str, Any], identity: tuple[str, str]) -> list[str]:
    references: list[str] = []
    for role_key, value in dict(config.get("roles") or {}).items():
        if _model_reference_matches(value, identity):
            references.append(f"role:{role_key}")
    for agent_id, binding in dict((config.get("bindings") or {}).get("agents") or {}).items():
        values = (
            (binding.get("model_id"), binding.get("modelId"))
            if isinstance(binding, dict)
            else (binding,)
        )
        if any(_model_reference_matches(value, identity) for value in values):
            references.append(f"agent:{agent_id}")
    return sorted(references)


def _provider_dependencies(config: dict[str, Any], provider_id: str) -> list[str]:
    provider = dict((config.get("providers") or {}).get(str(provider_id or "").strip()) or {})
    references: list[str] = []
    for model_id in dict(provider.get("models") or {}):
        references.extend(_model_dependencies(config, (str(provider_id or "").strip(), str(model_id))))
    return sorted(set(references))


def _binding_removed_model_identities(
    config: dict[str, Any],
    *,
    provider_id: str,
    model_id: str,
    source_provider_id: str,
    source_model_id: str,
    replace_provider_models: bool,
) -> list[tuple[str, str]]:
    providers = dict(config.get("providers") or {})
    target_identity = (provider_id, model_id)
    source_identity = (source_provider_id, source_model_id)
    removed: set[tuple[str, str]] = set()
    source_models = dict((providers.get(source_provider_id) or {}).get("models") or {})
    if source_identity != target_identity and source_model_id in source_models:
        removed.add(source_identity)
    if replace_provider_models:
        target_models = dict((providers.get(provider_id) or {}).get("models") or {})
        removed.update(
            (provider_id, str(existing_model_id))
            for existing_model_id in target_models
            if (provider_id, str(existing_model_id)) != target_identity
        )
    return sorted(removed)


def _binding_removal_dependencies(
    config: dict[str, Any],
    *,
    provider_id: str,
    model_id: str,
    source_provider_id: str,
    source_model_id: str,
    replace_provider_models: bool,
) -> list[str]:
    references: list[str] = []
    for identity in _binding_removed_model_identities(
        config,
        provider_id=provider_id,
        model_id=model_id,
        source_provider_id=source_provider_id,
        source_model_id=source_model_id,
        replace_provider_models=replace_provider_models,
    ):
        references.extend(_model_dependencies(config, identity))
    return sorted(set(references))


def _valid_proxy(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ConfigBrokerError("providerConfig.proxy 必须是 HTTP(S) 地址。", code="provider_proxy_invalid")
    parsed = urlparse(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigBrokerError("providerConfig.proxy 必须是无凭据的 HTTP(S) 地址。", code="provider_proxy_invalid")
    return value.strip().rstrip("/")


def _valid_provider_endpoint(value: Any, *, code: str = "provider_base_url_invalid") -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ConfigBrokerError("Provider 地址必须是无凭据、无 query/fragment 的 HTTP(S) 地址。", code=code) from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigBrokerError("Provider 地址必须是无凭据、无 query/fragment 的 HTTP(S) 地址。", code=code)
    return raw.rstrip("/")


def _validate_provider_transport(
    provider: dict[str, Any],
    *,
    base_url: str,
    api_standard: str,
    channel_id: str,
    wire_protocol: str,
) -> tuple[str, str, str, str]:
    """Resolve the actual egress target before a credential card or Safety check."""

    if "verifyTls" in provider:
        raise ConfigBrokerError("不支持覆盖 TLS 证书校验。", code="provider_tls_override_forbidden")
    if "proxy" in provider:
        provider["proxy"] = _valid_proxy(provider.get("proxy"))
    channels = provider.get("channels")
    if channels is not None and not isinstance(channels, list):
        raise ConfigBrokerError("providerConfig.channels 必须是数组。", code="provider_channel_invalid")
    normalized_channels: dict[str, dict[str, Any]] = {}
    for raw in channels or []:
        if not isinstance(raw, dict):
            raise ConfigBrokerError("providerConfig.channels 项必须是对象。", code="provider_channel_invalid")
        key = str(raw.get("id") or "").strip().lower()
        endpoint = _valid_provider_endpoint(
            raw.get("baseUrl") or raw.get("base_url") or "",
            code="provider_channel_invalid",
        )
        standard = str(raw.get("apiStandard") or raw.get("api_standard") or api_standard).strip().lower()
        parsed = urlparse(endpoint)
        protocols = raw.get("wireProtocols")
        channel_auth = raw.get("authContract") or raw.get("auth")
        if channel_auth is not None:
            if not isinstance(channel_auth, dict) or str(channel_auth.get("type") or "").strip().lower() not in {
                "api_key",
                "none",
            }:
                raise ConfigBrokerError(
                    "providerConfig.channels.auth 只支持 api_key 或 none。",
                    code="provider_channel_invalid",
                )
        if (
            not key
            or key in normalized_channels
            or parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or not _API_STANDARD_RE.fullmatch(standard)
            or (protocols is not None and not isinstance(protocols, list))
        ):
            raise ConfigBrokerError("providerConfig.channels 包含无效通道。", code="provider_channel_invalid")
        wire_protocols = [str(item or "").strip() for item in protocols or []]
        if any(not item for item in wire_protocols) or len(set(wire_protocols)) != len(wire_protocols):
            raise ConfigBrokerError("providerConfig.channels.wireProtocols 无效。", code="provider_channel_invalid")
        normalized_channels[key] = {
            "endpoint": endpoint,
            "apiStandard": standard,
            "wireProtocols": wire_protocols,
            "defaultWireProtocol": str(raw.get("defaultWireProtocol") or "").strip(),
        }
    requested_channel = str(channel_id or "").strip().lower()
    default_channel = str(provider.get("defaultChannelId") or "").strip().lower()
    if default_channel and default_channel not in normalized_channels:
        raise ConfigBrokerError("providerConfig.defaultChannelId 未指向已配置通道。", code="provider_channel_invalid")
    selected_channel = requested_channel or default_channel
    # A legacy explicit channel id may label the provider base URL when no
    # channel catalog exists. Once channels are declared, selection is strict.
    if selected_channel and normalized_channels and selected_channel not in normalized_channels:
        raise ConfigBrokerError("请求的 Provider 通道不存在。", code="provider_channel_invalid")
    selected = normalized_channels.get(selected_channel) or {}
    effective_url = _valid_provider_endpoint(selected.get("endpoint") or base_url)
    effective_standard = str(selected.get("apiStandard") or api_standard).strip().lower()
    requested_wire = str(wire_protocol or "").strip()
    supported = list(selected.get("wireProtocols") or [])
    if requested_wire and supported and requested_wire not in supported:
        raise ConfigBrokerError("请求的 wireProtocol 不受所选通道支持。", code="provider_wire_protocol_invalid")
    effective_wire = requested_wire or str(selected.get("defaultWireProtocol") or "").strip()
    return effective_url, effective_standard, selected_channel, effective_wire


def _catalog_probe_url_in_scope(provider: dict[str, Any], target: dict[str, Any]) -> str:
    base_url = str(target.get("baseUrl") or target.get("url") or "").strip().rstrip("/")
    explicit = str(provider.get("modelsUrl") or provider.get("models_url") or "").strip()
    path = str(provider.get("modelsPath") or provider.get("models_path") or "/models").strip() or "/models"
    models_url = explicit or urljoin(f"{base_url}/", path.lstrip("/"))
    parsed = urlparse(models_url)
    authorized_bases = [base_url]
    credential_realm = str(provider.get("credentialRealm") or provider.get("credential_realm") or "").strip()
    if credential_realm:
        authorized_bases.extend(
            str(channel.get("baseUrl") or channel.get("base_url") or "").strip().rstrip("/")
            for channel in list(provider.get("channels") or [])
            if isinstance(channel, dict)
        )

    def _matches(allowed_url: str) -> bool:
        allowed = urlparse(allowed_url)
        allowed_path = allowed.path.rstrip("/")
        return bool(
            allowed.scheme.lower() in {"http", "https"}
            and parsed.scheme.lower() == allowed.scheme.lower()
            and (parsed.hostname or "").lower() == (allowed.hostname or "").lower()
            and parsed.port == allowed.port
            and (not allowed_path or parsed.path == allowed_path or parsed.path.startswith(f"{allowed_path}/"))
        )

    in_scope = bool(
        parsed.scheme.lower() in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and any(_matches(candidate) for candidate in authorized_bases if candidate)
    )
    if not in_scope:
        raise ConfigBrokerError("目录发现目标不在已授权供应商地址范围内。", code="catalog_discovery_target_out_of_scope", status_code=409)
    return models_url


def _catalog_discovery_message(reason: Any) -> tuple[str, str]:
    normalized = str(reason or "").strip()
    if normalized == "catalog_only_provider":
        return "catalog_discovery_not_supported", "该供应商不支持在线模型目录发现。"
    if normalized in {"credential_required", "missing_base_url"}:
        return "catalog_discovery_unavailable", "供应商目录发现当前不可用。"
    if normalized == "tls_or_network_error":
        return "catalog_discovery_network_failed", "供应商目录发现网络连接失败。"
    return "catalog_discovery_failed", "供应商目录发现失败。"


def _managed_catalog_digest(catalog: Any) -> str:
    state = catalog.managed_recovery_state()
    return str(dict(state or {}).get("managedDigest") or "").strip()


def _catalog_mutation_with_digest(
    method: Any,
    *args: Any,
    expected_current_digest: str,
    expected_backup_digest: str = "",
    expected_rejected_digest: str = "",
    expected_provider_digest: str = "",
    **kwargs: Any,
) -> Any:
    """Use Catalog's lock-held digest CAS whenever the deployed Catalog exposes it."""

    parameters = inspect.signature(method).parameters
    if "expected_current_digest" in parameters:
        kwargs["expected_current_digest"] = expected_current_digest
    elif "expected_managed_digest" in parameters:
        kwargs["expected_managed_digest"] = expected_current_digest
    if "expected_backup_digest" in parameters:
        kwargs["expected_backup_digest"] = expected_backup_digest
    if "expected_rejected_digest" in parameters:
        kwargs["expected_rejected_digest"] = expected_rejected_digest
    if "expected_provider_digest" in parameters:
        kwargs["expected_provider_digest"] = expected_provider_digest
    return method(*args, **kwargs)


def _provider_target_fingerprint(provider: dict[str, Any]) -> str:
    """Stable credential destination identity; excludes model and display fields."""

    raw_url = str(provider.get("base_url") or provider.get("baseUrl") or "").strip().rstrip("/")
    parsed = urlparse(raw_url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    host = parsed.hostname.lower()
    port = parsed.port
    if port and not ((parsed.scheme.lower() == "https" and port == 443) or (parsed.scheme.lower() == "http" and port == 80)):
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    auth_contract = dict(provider.get("authContract") or {})
    channels = []
    for channel in list(provider.get("channels") or []):
        if not isinstance(channel, dict):
            continue
        channel_url = str(channel.get("baseUrl") or channel.get("base_url") or "").strip().rstrip("/")
        parsed_channel = urlparse(channel_url)
        if (
            parsed_channel.scheme.lower() not in {"http", "https"}
            or not parsed_channel.hostname
            or parsed_channel.username is not None
            or parsed_channel.password is not None
            or parsed_channel.query
            or parsed_channel.fragment
        ):
            return ""
        channels.append(
            {
                "id": str(channel.get("id") or "").strip().lower(),
                "baseUrl": channel_url,
                "apiStandard": str(channel.get("apiStandard") or channel.get("api_standard") or "").strip().lower(),
                "wireProtocols": sorted(str(item).strip() for item in list(channel.get("wireProtocols") or []) if str(item).strip()),
                "authContract": {
                    str(key): str(value or "").strip()
                    for key, value in dict(channel.get("authContract") or channel.get("auth") or {}).items()
                    if str(key) in {"type", "header", "scheme", "query", "preset", "path"} and str(value or "").strip()
                },
            }
        )
    return _digest(
        {
            "origin": f"{parsed.scheme.lower()}://{host}{path}",
            "apiStandard": str(provider.get("api_standard") or provider.get("apiStandard") or "").strip().lower(),
            "credentialRealm": str(provider.get("credentialRealm") or provider.get("credential_realm") or "").strip().lower(),
            "type": str(provider.get("type") or provider.get("credential_mode") or provider.get("credentialMode") or "").strip().lower(),
            "proxy": str(provider.get("proxy") or "").strip().rstrip("/"),
            "authContract": {
                str(key): str(value or "").strip()
                for key, value in auth_contract.items()
                if str(key) in {"type", "header", "scheme", "query", "preset", "path"} and str(value or "").strip()
            },
            "channels": sorted(channels, key=lambda item: item["id"]),
        }
    )


def _credential_ref_is_reusable(reference: str, *, existing_provider: dict[str, Any], proposed_provider: dict[str, Any]) -> bool:
    normalized = str(reference or "").strip()
    if not normalized.startswith("cred:v8-model:"):
        return False
    try:
        if not credential_ref_store.status(normalized).configured:
            return False
    except CredentialStoreError:
        return False
    existing_fingerprint = _provider_target_fingerprint(existing_provider)
    proposed_fingerprint = _provider_target_fingerprint(proposed_provider)
    return bool(existing_fingerprint and existing_fingerprint == proposed_fingerprint)


def _session_owner(session_id: str, explicit_owner: str = "") -> str:
    owner = str(explicit_owner or "").strip()
    if owner and owner.lower() != "anonymous":
        return owner
    session = db.get_session(str(session_id or "").strip()) if session_id else None
    session_owner = str((session or {}).get("user_id") or (session or {}).get("userId") or "").strip()
    return session_owner or owner


class ConfigBrokerService:
    """Durable model/MCP configuration control plane.

    Doctor validates declared facts, Safety decides whether a credential may be
    sent to the exact target, and this service alone owns commit/rollback.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @staticmethod
    def _target_snapshot(target_kind: str, target_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if target_kind == "model_snapshot_restore":
            return _model_snapshot_authority_projection(config)
        if target_kind == "model_provider":
            providers = dict(config.get("providers") or {})
            return {
                "exists": target_id in providers,
                "value": deepcopy(providers.get(target_id)),
            }
        if target_kind == "model_binding":
            source_ref, target_ref = _model_binding_target_refs(target_id)
            identities = [parse_model_ref(value) for value in (source_ref, target_ref)]
            provider_ids = sorted({identity[0] for identity in identities if identity})
            providers = dict(config.get("providers") or {})
            return {
                "providers": {
                    provider_id: {
                        "exists": provider_id in providers,
                        "value": deepcopy(providers.get(provider_id)),
                    }
                    for provider_id in provider_ids
                }
            }
        if target_kind in {"model", "model_record"}:
            identity = parse_model_ref(target_id)
            if not identity:
                return {"providerExists": False, "modelExists": False}
            provider_id, model_id = identity
            providers = dict(config.get("providers") or {})
            provider_data = dict(providers.get(provider_id) or {})
            models = dict(provider_data.get("models") or {})
            return {
                "providerExists": provider_id in providers,
                "provider": dict(provider_data.get("provider") or {}),
                "providerModelIds": sorted(str(key) for key in models),
                "providerModels": deepcopy(models),
                "modelExists": model_id in models,
                "model": dict(models.get(model_id) or {}),
            }
        if target_kind == "model_role":
            roles = dict(config.get("roles") or {})
            return {"exists": target_id in roles, "value": roles.get(target_id)}
        if target_kind == "model_role_bundle":
            roles = dict(config.get("roles") or {})
            return {
                "roles": {
                    role: {"exists": role in roles, "value": roles.get(role)}
                    for role in _model_role_bundle_target_roles(target_id)
                }
            }
        if target_kind == "agent_model_role":
            agents = dict((config.get("bindings") or {}).get("agents") or {})
            return {"exists": target_id in agents, "value": deepcopy(agents.get(target_id))}
        if target_kind == "creative_media_operation":
            selections = [
                deepcopy(item)
                for item in list(config.get("selections") or [])
                if isinstance(item, dict) and str(item.get("operationKind") or "").strip() == target_id
            ]
            models = [
                deepcopy(item)
                for item in list(config.get("models") or [])
                if isinstance(item, dict) and str(item.get("operationKind") or "").strip() == target_id
            ]
            return {
                "exists": bool(selections or models),
                "selections": selections,
                "models": models,
            }
        if target_kind == "mcp":
            servers = dict(config.get("mcpServers") or {})
            return {"exists": target_id in servers, "value": deepcopy(servers.get(target_id))}
        if target_kind == "model_catalog_provider":
            providers = {
                str(item.get("id") or ""): deepcopy(item)
                for item in list(config.get("providers") or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            return {"exists": target_id in providers, "value": providers.get(target_id)}
        if target_kind == "model_catalog_custom_provider":
            providers = {
                str(item.get("id") or ""): deepcopy(item)
                for item in list(config.get("providers") or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            return {"exists": target_id in providers, "value": providers.get(target_id)}
        if target_kind == "model_policy_bundle":
            return {
                "governance": deepcopy(config.get("governance") or {}),
                "routingPolicies": deepcopy(config.get("routingPolicies") or {}),
                "roleParameters": deepcopy(config.get("roleParameters") or {}),
            }
        if target_kind == "model_catalog_recovery":
            return deepcopy(config)
        return {"unsupported": target_kind}

    @staticmethod
    def _model_record_snapshot(model_ref: str, config: dict[str, Any]) -> dict[str, Any]:
        """Snapshot a referenced model from the durable configuration view.

        ``mutate_config`` supplies a provider-call view with a materialized
        managed credential.  Role assignments protect the referenced model
        separately from the role field, so they must use the same secret-free
        revision as their prepare step.
        """
        return ConfigBrokerService._target_snapshot(
            "model_record",
            model_ref,
            model_control_plane._storage_safe_config(config),
        )

    @staticmethod
    def _target_config(target_kind: str) -> dict[str, Any]:
        if target_kind == "mcp":
            return deepcopy(storage.get_mcp_config() or {"mcpServers": {}})
        if target_kind == "creative_media_operation":
            return deepcopy(storage.read_json(_CREATIVE_MEDIA_PREFERENCES_FILE) or {})
        if target_kind == "model_catalog_provider":
            return deepcopy(_get_model_provider_catalog().load_managed())
        if target_kind == "model_catalog_custom_provider":
            return deepcopy(_get_model_provider_catalog().load_custom())
        if target_kind == "model_catalog_recovery":
            return deepcopy(_get_model_provider_catalog().managed_recovery_state())
        return model_control_plane.get_storage_safe_config()

    def _assert_target_revision(self, transaction: dict[str, Any]) -> dict[str, Any]:
        current_config = self._target_config(str(transaction.get("targetKind") or ""))
        return self._assert_target_revision_in_config(transaction, current_config)

    def _assert_target_revision_in_config(
        self,
        transaction: dict[str, Any],
        current_config: dict[str, Any],
    ) -> dict[str, Any]:
        validation = dict(transaction.get("validation") or {})
        expected = str(validation.get("targetBeforeDigest") or "").strip()
        target_kind = str(transaction.get("targetKind") or "")
        # ModelControlPlane mutation helpers deliberately materialize a managed
        # credential as ``api_key`` for the provider call.  The Broker plans
        # from the storage-safe revision, where that ephemeral field is absent.
        # Compare the same authority projection so a managed secret does not
        # create a false CAS conflict, while all durable provider/model fields
        # continue to participate in stale-write detection.
        snapshot_config = (
            model_control_plane._storage_safe_config(current_config)
            if target_kind in _MODEL_CONTROL_PLANE_TARGET_KINDS
            else current_config
        )
        current_snapshot = self._target_snapshot(
            target_kind,
            str(transaction.get("targetId") or ""),
            snapshot_config,
        )
        current_digest = _digest(current_snapshot)
        if expected and current_digest != expected:
            raise ConfigBrokerError(
                "目标配置已在计划后发生变化；请重新准备事务。",
                code="config_transaction_stale",
                status_code=409,
            )
        return current_snapshot

    def _cleanup_new_credential_refs(self, transaction: dict[str, Any]) -> list[str]:
        """Delete only credentials created for an uncommitted or reverted transaction.

        Successful deletions are removed from the durable pending list so a
        recovery retry is idempotent. A fail-once backend therefore leaves the
        transaction recoverable instead of permanently stranding a secret.
        """

        proposed = dict(transaction.get("proposed") or {})
        references = sorted(
            {
                str(item or "").strip()
                for item in list(proposed.get("newCredentialRefs") or [])
                if str(item or "").strip()
            }
        )
        remaining: list[str] = []
        errors: list[str] = []
        for reference in references:
            try:
                credential_ref_store.delete(reference)
            except Exception as exc:
                remaining.append(reference)
                errors.append(f"credential cleanup failed: {type(exc).__name__}")
        if references:
            proposed["newCredentialRefs"] = remaining
            self._update_transaction(
                str(transaction.get("transactionId") or ""),
                proposed_json=_json(proposed),
            )
            transaction["proposed"] = proposed
        return errors

    def reconcile_incomplete_transactions(self) -> dict[str, Any]:
        """Conservatively reconcile durable transactions left by an Engine crash.

        Known before/working revisions are restored automatically. An unknown
        target revision is never overwritten during startup; it remains
        `recovery_required` and can be retried explicitly through rollback.
        """

        with self._lock:
            with db.get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, owner_id FROM config_broker_transactions
                    WHERE state IN ('committing','verifying','rolling_back','recovery_required')
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                row_data = dict(row)
                transaction_id = str(row_data.get("id") or "")
                transaction = self.get_transaction(
                    transaction_id,
                    owner_id=str(row_data.get("owner_id") or ""),
                    include_private=True,
                )
                validation = dict(transaction.get("validation") or {})
                current_config = self._target_config(str(transaction.get("targetKind") or ""))
                current_target = self._target_snapshot(
                    str(transaction.get("targetKind") or ""),
                    str(transaction.get("targetId") or ""),
                    current_config,
                )
                current_digest = _digest(current_target)
                before_digest = str(validation.get("targetBeforeDigest") or "")
                working_digest = str(
                    validation.get("targetWorkingDigest")
                    or validation.get("targetPlannedDigest")
                    or validation.get("targetAfterDigest")
                    or ""
                )
                existing_error_code = str(dict(transaction.get("error") or {}).get("code") or "")
                if existing_error_code == "config_credential_cleanup_failed":
                    errors = self._cleanup_new_credential_refs(transaction)
                    if errors:
                        next_state = "recovery_required"
                        error_code = "config_credential_cleanup_failed"
                        error_message = "新建凭据清理仍未完成。"
                    else:
                        next_state = "rolled_back" if current_digest == before_digest else "conflict"
                        error_code = None if next_state == "rolled_back" else "config_transaction_stale"
                        error_message = None if next_state == "rolled_back" else "新建凭据已清理；较新的目标配置保持不变。"
                    rollback = {
                        "ok": not errors,
                        "conflict": next_state == "conflict",
                        "targetRestored": current_digest == before_digest,
                        "credentialCleanupPending": bool(errors),
                        "errors": errors,
                    }
                    self._update_transaction(
                        transaction_id,
                        state=next_state,
                        result_json=_json({"startupReconciliation": rollback}),
                        error_code=error_code,
                        error_message=error_message,
                        rolled_back_at=utc_now_iso() if next_state == "rolled_back" else None,
                    )
                    results.append({"transactionId": transaction_id, "state": next_state})
                    continue
                if current_digest == before_digest:
                    errors = self._cleanup_new_credential_refs(transaction)
                    next_state = "recovery_required" if errors else "rolled_back"
                    rollback = {
                        "ok": not errors,
                        "conflict": False,
                        "targetRestored": True,
                        "credentialCleanupPending": bool(errors),
                        "errors": errors,
                    }
                elif working_digest and current_digest == working_digest:
                    rollback = self._restore_snapshot(
                        transaction,
                        enforce_after_digest=True,
                        expected_current_digest=working_digest,
                    )
                    next_state = (
                        "rolled_back"
                        if rollback.get("ok")
                        else ("conflict" if rollback.get("conflict") else "recovery_required")
                    )
                else:
                    rollback = {
                        "ok": False,
                        "conflict": True,
                        "targetRestored": False,
                        "credentialCleanupPending": bool(
                            list((transaction.get("proposed") or {}).get("newCredentialRefs") or [])
                        ),
                        "errors": ["incomplete transaction target revision is unknown"],
                    }
                    next_state = "recovery_required"
                self._update_transaction(
                    transaction_id,
                    state=next_state,
                    result_json=_json({"startupReconciliation": rollback}),
                    error_code=None if next_state == "rolled_back" else "config_recovery_required",
                    error_message=None if next_state == "rolled_back" else "Engine 重启后目标版本无法自动确认，需要显式恢复。",
                    rolled_back_at=utc_now_iso() if next_state == "rolled_back" else None,
                )
                results.append({"transactionId": transaction_id, "state": next_state})
            return {
                "ok": all(item["state"] == "rolled_back" for item in results),
                "reconciled": len(results),
                "transactions": results,
            }

    def _insert_transaction(
        self,
        *,
        target_kind: str,
        target_id: str,
        operation: str,
        state: str,
        owner_id: str,
        session_id: str,
        run_id: str,
        before: dict[str, Any],
        proposed: dict[str, Any],
        validation: dict[str, Any] | None = None,
        error: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        transaction_id = f"cfg_txn_{uuid.uuid4().hex}"
        now = utc_now_iso()
        target_before = self._target_snapshot(target_kind, target_id, before)
        target_before_digest = _digest(target_before)
        validation_payload = {
            **dict(validation or {}),
            "targetBeforeDigest": target_before_digest,
        }
        plan_digest = _digest(
            {
                "targetKind": target_kind,
                "targetId": target_id,
                "operation": operation,
                "targetBeforeDigest": target_before_digest,
                "proposed": proposed,
            }
        )
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO config_broker_transactions
                (id,target_kind,target_id,operation,state,owner_id,session_id,run_id,plan_digest,
                 before_json,proposed_json,validation_json,error_code,error_message,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    transaction_id,
                    target_kind,
                    target_id,
                    operation,
                    state,
                    owner_id or None,
                    session_id or None,
                    run_id or None,
                    plan_digest,
                    _json(before),
                    _json(proposed),
                    _json(validation_payload),
                    (error or (None, None))[0],
                    (error or (None, None))[1],
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)

    def _update_transaction(self, transaction_id: str, **updates: Any) -> None:
        allowed = {
            "state",
            "proposed_json",
            "validation_json",
            "result_json",
            "error_code",
            "error_message",
            "committed_at",
            "rolled_back_at",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        values["updated_at"] = utc_now_iso()
        assignments = ",".join(f"{key}=?" for key in values)
        with db.get_connection() as conn:
            conn.execute(
                f"UPDATE config_broker_transactions SET {assignments} WHERE id=?",
                (*values.values(), transaction_id),
            )
            conn.commit()

    def get_transaction(
        self,
        transaction_id: str,
        *,
        owner_id: str = "",
        include_private: bool = False,
    ) -> dict[str, Any]:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM config_broker_transactions WHERE id=?", (str(transaction_id or "").strip(),)).fetchone()
        if not row:
            raise ConfigBrokerError("配置事务不存在。", code="config_transaction_not_found", status_code=404)
        item = dict(row)
        stored_owner = str(item.get("owner_id") or "").strip()
        if stored_owner and stored_owner != str(owner_id or "").strip():
            raise ConfigBrokerError("配置事务不属于当前用户。", code="config_transaction_owner_mismatch", status_code=403)
        payload = {
            "transactionId": item["id"],
            "targetKind": item["target_kind"],
            "targetId": item["target_id"],
            "operation": item["operation"],
            "state": item["state"],
            "planDigest": item["plan_digest"],
            "validation": _loads(item.get("validation_json"), {}),
            "result": _loads(item.get("result_json"), {}),
            "error": (
                {"code": item.get("error_code"), "message": item.get("error_message")}
                if item.get("error_code") or item.get("error_message")
                else None
            ),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
            "committedAt": item.get("committed_at"),
            "rolledBackAt": item.get("rolled_back_at"),
        }
        if include_private:
            payload["before"] = _loads(item.get("before_json"), {})
            payload["proposed"] = _loads(item.get("proposed_json"), {})
            payload["ownerId"] = stored_owner
            payload["sessionId"] = str(item.get("session_id") or "")
            payload["runId"] = str(item.get("run_id") or "")
        return payload

    def inventory(self, *, category: str = "", query: str = "", limit: int = 20, offset: int = 0) -> dict[str, Any]:
        config = model_control_plane.get_config()
        models = model_control_plane.list_models(config)
        provider_statuses = {
            str(item.get("providerId") or item.get("id") or ""): item
            for item in model_control_plane.get_provider_statuses(config)
        }
        normalized_category = str(category or "").strip().lower()
        category_aliases = {
            "llm": "text",
            "text_generation": "text",
            "multimodal": "vision",
            "image_understanding": "vision",
            "reranker": "rerank",
        }
        normalized_category = category_aliases.get(normalized_category, normalized_category)
        normalized_query = str(query or "").strip().lower()
        rows: list[dict[str, Any]] = []
        group_counts: dict[str, int] = {}
        for model in models:
            category_key = model_category(model)
            kind = model_kind(model)
            haystack = " ".join(
                [str(model.get("modelId") or ""), str(model.get("providerName") or ""), str(model.get("providerId") or "")]
            ).lower()
            if normalized_query and normalized_query not in haystack:
                continue
            provider_health = provider_statuses.get(str(model.get("providerId") or "")) or {}
            health_state = ""
            if str(provider_health.get("circuitState") or "") == "open":
                health_state = "circuit_open"
            elif int(provider_health.get("errorCount") or 0) > 0:
                health_state = "degraded"
            eligibility = evaluate_model_eligibility({**model, "healthStatus": health_state})
            group_counts[category_key] = group_counts.get(category_key, 0) + 1
            if normalized_category and normalized_category not in {
                category_key,
                kind,
                str(model.get("type") or "").lower(),
                str(model.get("capabilityClass") or "").lower(),
            }:
                continue
            rows.append(
                {
                    "modelRef": model.get("modelRef"),
                    "modelId": model.get("modelId"),
                    "providerId": model.get("providerId"),
                    "providerName": model.get("providerName"),
                    "type": model.get("type"),
                    "category": category_key,
                    "capabilityClass": model.get("capabilityClass"),
                    "status": eligibility.get("status"),
                    "statusLabel": eligibility.get("shortLabel"),
                    "contextWindow": model.get("contextWindow"),
                    "maxTokens": model.get("maxTokens"),
                    "defaultCategories": model.get("defaultCategories") or [],
                    "assignedRoles": model.get("assignedRoles") or [],
                    "providerHealth": provider_health.get("status") or provider_health.get("health") or None,
                    "requiredFacts": eligibility.get("requiredFacts") or [],
                    "warnings": [item.get("code") for item in eligibility.get("warnings") or []],
                }
            )
        rows.sort(
            key=lambda item: (
                0 if item.get("defaultCategories") else 1,
                0 if item.get("status") == "ready" else 1,
                str(item.get("providerName") or "").lower(),
                str(item.get("modelId") or "").lower(),
            )
        )
        bounded_limit = max(1, min(int(limit or 20), 50))
        bounded_offset = max(0, int(offset or 0))
        return {
            "ok": True,
            "mode": "inventory",
            "category": normalized_category or "all",
            "total": len(rows),
            "offset": bounded_offset,
            "limit": bounded_limit,
            "groups": [
                {"category": key, "count": group_counts[key]}
                for key in ("text", "vision", "embedding", "rerank", "media")
                if group_counts.get(key)
            ],
            "models": rows[bounded_offset : bounded_offset + bounded_limit],
            "summary": f"找到 {len(rows)} 个匹配模型；默认模型已置顶。",
        }

    def role_matrix(self) -> dict[str, Any]:
        config = model_control_plane.get_config()
        roles = []
        for card in model_control_plane.get_role_cards(config):
            roles.append(
                {
                    "role": card.get("key"),
                    "label": card.get("label"),
                    "group": card.get("group"),
                    "modelRef": card.get("resolvedModelRef"),
                    "model": card.get("resolvedModelName"),
                    "provider": card.get("resolvedProviderName"),
                    "binding": card.get("bindingState"),
                    "status": card.get("readiness"),
                    "reason": card.get("readinessReason"),
                }
            )
        models_by_ref = {str(item.get("modelRef") or ""): item for item in model_control_plane.list_models(config)}
        inherited = model_control_plane.resolve_model_for_role("subagent", config)
        agent_bindings = storage.get_agent_model_bindings()
        agents = []
        for agent in storage.get_all_agents():
            agent_id = str(agent.get("id") or "").strip()
            if not agent_id:
                continue
            explicit = str(agent_bindings.get(agent_id) or "").strip()
            explicit_record = model_control_plane.get_model_record(explicit, config) if explicit else None
            model_ref = str((explicit_record or {}).get("model_ref") or inherited.get("resolvedModelRef") or "")
            model_row = models_by_ref.get(model_ref) or {}
            eligibility = dict(model_row.get("eligibility") or evaluate_model_eligibility(model_row, role="subagent"))
            binding = "explicit" if explicit_record else ("invalid" if explicit else "inherited_subagent")
            agents.append(
                {
                    "role": f"agent:{agent_id}",
                    "agentId": agent_id,
                    "label": str(agent.get("name") or agent_id).strip() or agent_id,
                    "group": "subagent",
                    "modelRef": model_ref,
                    "model": model_row.get("modelId") or inherited.get("resolvedModelId") or "",
                    "provider": model_row.get("providerName") or "",
                    "binding": binding,
                    "status": "disabled" if agent.get("isEnabled") is False else eligibility.get("status") or "blocked",
                    "reason": eligibility.get("shortLabel") or "模型未就绪",
                }
            )
        agents.sort(key=lambda item: str(item.get("label") or "").lower())
        return {
            "ok": True,
            "mode": "role_matrix",
            "roles": roles,
            "agents": agents,
            "summary": f"当前有 {len(roles)} 个功能模型槽位和 {len(agents)} 个 Subagent 模型绑定。",
        }

    def _role_contract(self, role_key: str, config: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        definitions = model_control_plane.get_role_definitions(config)
        if role_key.startswith("agent:"):
            agent_id = role_key.removeprefix("agent:").strip()
            agent = storage.get_agent(agent_id) if agent_id else None
            if not agent:
                raise ConfigBrokerError("目标 Subagent 不存在。", code="model_agent_unknown", status_code=404)
            return dict(definitions["subagent"]), str(agent.get("name") or agent_id), agent_id
        if role_key not in definitions:
            raise ConfigBrokerError("未知模型角色。", code="model_role_unknown")
        return dict(definitions[role_key]), str(definitions[role_key].get("label") or role_key), ""

    def recommend(self, *, role: str, limit: int = 5) -> dict[str, Any]:
        config = model_control_plane.get_config()
        role_key = str(role or "").strip()
        role_definition, role_label, _agent_id = self._role_contract(role_key, config)
        candidates = []
        for model in model_control_plane.list_models(config):
            record = model_control_plane.get_model_record(str(model.get("modelRef") or ""), config)
            compatibility_record = {
                **dict(record or {}),
                "model": {
                    **dict((record or {}).get("model") or {}),
                    "capabilityClass": model.get("capabilityClass"),
                    "capabilities": dict(model.get("capabilities") or {}),
                    "type": model.get("type"),
                },
            }
            if not model_control_plane.is_model_compatible(role_definition, compatibility_record):
                continue
            eligibility = dict(model.get("eligibility") or {})
            if not eligibility.get("selectable"):
                continue
            score = 100
            if model.get("defaultCategories"):
                score += 20
            if role_key in list(model.get("assignedRoles") or []):
                score += 10
            if dict(model.get("capabilities") or {}).get("toolCalling") and role_key in {"supervisor", "subagent"}:
                score += 8
            candidates.append(
                {
                    "modelRef": model.get("modelRef"),
                    "modelId": model.get("modelId"),
                    "provider": model.get("providerName"),
                    "score": score,
                    "status": eligibility.get("shortLabel"),
                    "reason": "能力类别匹配且模型参数完整",
                }
            )
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["provider"]), str(item["modelId"])))
        return {
            "ok": True,
            "mode": "recommend",
            "role": role_key,
            "candidates": candidates[: max(1, min(int(limit or 5), 10))],
            "summary": f"为 {role_label} 找到 {len(candidates)} 个可用候选。",
        }

    def prepare_model_snapshot_recovery(
        self,
        *,
        source_transaction_id: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Prepare a full model-domain recovery from one durable Broker preimage.

        This intentionally accepts only a Config Broker transaction id, never a
        caller-provided configuration blob. It is an emergency repair path for a
        known bad whole-domain replacement while retaining normal transaction
        CAS, audit, and rollback behaviour.
        """

        source_id = str(source_transaction_id or "").strip()
        if not source_id:
            raise ConfigBrokerError("恢复需要来源配置事务。", code="model_snapshot_source_required")
        owner = _session_owner(session_id, owner_id)
        source = self.get_transaction(source_id, owner_id=owner, include_private=True)
        source_kind = str(source.get("targetKind") or "")
        if source_kind not in _MODEL_SNAPSHOT_SOURCE_KINDS or source.get("state") != "committed":
            raise ConfigBrokerError(
                "来源事务不是已提交的模型配置恢复点。",
                code="model_snapshot_source_invalid",
                status_code=409,
            )
        candidate = deepcopy(dict(source.get("before") or {}))
        providers = dict(candidate.get("providers") or {})
        if not providers:
            raise ConfigBrokerError(
                "来源恢复点不含有效模型 Provider。",
                code="model_snapshot_source_invalid",
                status_code=409,
            )
        for provider_data in providers.values():
            provider_meta = dict((dict(provider_data or {})).get("provider") or {})
            raw_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip()
            if raw_key and not raw_key.startswith("oauth:"):
                raise ConfigBrokerError(
                    "来源恢复点含未托管凭据，已拒绝恢复。",
                    code="model_snapshot_source_contains_secret",
                    status_code=409,
                )

        # Normalization verifies the stored model-domain shape without turning
        # a caller request into a writable arbitrary config object.
        candidate = model_control_plane._storage_safe_config(model_control_plane.normalize_config(candidate))
        current = model_control_plane.get_storage_safe_config()
        candidate_digest = _digest(_model_snapshot_authority_projection(candidate))
        already_current = candidate_digest == _digest(_model_snapshot_authority_projection(current))
        transaction = self._insert_transaction(
            target_kind="model_snapshot_restore",
            target_id=source_id,
            operation="restore_snapshot",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=current,
            proposed={
                "sourceTransactionId": source_id,
                "sourceSnapshotDigest": candidate_digest,
                "snapshot": candidate,
                "alreadyCurrent": already_current,
                "newCredentialRefs": [],
            },
            validation={
                "source": {
                    "transactionId": source_id,
                    "targetKind": source_kind,
                    "snapshotDigest": candidate_digest,
                }
            },
        )
        model_count = sum(
            len(dict((dict(provider_data or {})).get("models") or {}))
            for provider_data in providers.values()
        )
        return {
            "ok": True,
            "mode": "model_snapshot_recover_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": (
                f"当前模型配置已与 {len(providers)} 个 Provider、{model_count} 个模型的恢复快照一致；"
                "可提交只读核验记录。"
                if already_current
                else f"已准备恢复 {len(providers)} 个 Provider、{model_count} 个模型的已验证配置快照。"
            ),
            "nextAction": "提交配置事务。",
        }

    def prepare_model_provider_change(
        self,
        *,
        provider_id: str,
        operation: str,
        provider_config: dict[str, Any] | None,
        request_secret: bool,
        oauth_credential: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        normalized_operation = str(operation or "upsert").strip().lower()
        if not provider_key or not _PROVIDER_ID_RE.fullmatch(provider_key):
            raise ConfigBrokerError("providerId 格式无效。", code="provider_id_invalid")
        if normalized_operation not in {"upsert", "remove"}:
            raise ConfigBrokerError("Provider 操作无效。", code="provider_operation_invalid")
        before = model_control_plane.get_storage_safe_config()
        providers = dict(before.get("providers") or {})
        existing = dict(providers.get(provider_key) or {})
        existing_meta = dict(existing.get("provider") or {})
        if normalized_operation == "remove" and not existing:
            raise ConfigBrokerError("Provider 不存在。", code="provider_not_found", status_code=404)
        if normalized_operation == "remove":
            dependencies = _provider_dependencies(before, provider_key)
            if dependencies:
                raise ConfigBrokerError(
                    "Provider 仍被角色或 Subagent 使用；请先解除模型绑定。",
                    code="provider_still_bound",
                    status_code=409,
                )

        owner = _session_owner(session_id, owner_id)
        proposed: dict[str, Any] = {
            "providerId": provider_key,
            "operation": normalized_operation,
            "newCredentialRefs": [],
            "supersededCredentialRefs": [
                str(existing_meta.get("credentialRef") or "").strip()
            ] if normalized_operation == "remove" and str(existing_meta.get("credentialRef") or "").strip() else [],
        }
        needs_secret = False
        if normalized_operation == "upsert":
            patch = _canonical_provider_patch(provider_config, allow_oauth=True)
            provider = {**existing_meta, **patch}
            base_url = str(provider.get("base_url") or provider.get("baseUrl") or "").strip()
            api_standard = str(provider.get("api_standard") or provider.get("apiStandard") or "openai").strip().lower()
            if not _API_STANDARD_RE.fullmatch(api_standard):
                raise ConfigBrokerError("apiStandard 格式无效。", code="provider_api_standard_invalid")
            if base_url:
                normalized_url, normalized_standard, _channel_id, _wire_protocol = _validate_provider_transport(
                    provider,
                    base_url=_valid_provider_endpoint(base_url),
                    api_standard=api_standard,
                    channel_id="",
                    wire_protocol="",
                )
                provider["base_url"] = normalized_url
                provider["api_standard"] = normalized_standard

            oauth_reference = str(oauth_credential or "").strip()
            if oauth_reference and not oauth_reference.startswith("oauth:"):
                raise ConfigBrokerError("OAuth 凭据引用无效。", code="provider_oauth_reference_invalid")
            existing_ref = str(existing_meta.get("credentialRef") or "").strip()
            provider.pop("apiKey", None)
            if oauth_reference:
                provider["api_key"] = oauth_reference
                provider.pop("credentialRef", None)
                provider.pop("credentialSource", None)
            elif request_secret:
                provider.pop("api_key", None)
                provider.pop("credentialRef", None)
                provider.pop("credentialSource", None)
            else:
                reusable = _credential_ref_is_reusable(
                    existing_ref,
                    existing_provider=existing_meta,
                    proposed_provider=provider,
                )
                existing_oauth = str(existing_meta.get("api_key") or "").strip()
                same_target = bool(
                    _provider_target_fingerprint(existing_meta)
                    and _provider_target_fingerprint(existing_meta) == _provider_target_fingerprint(provider)
                )
                if reusable:
                    provider["credentialRef"] = existing_ref
                    provider["credentialSource"] = "os_credential_store"
                elif existing_oauth.startswith("oauth:") and same_target:
                    provider["api_key"] = existing_oauth
                else:
                    provider.pop("api_key", None)
                    provider.pop("credentialRef", None)
                    provider.pop("credentialSource", None)
            auth_type = str(dict(provider.get("authContract") or {}).get("type") or "api_key").strip().lower()
            provider_enabled = provider.get("is_enabled", provider.get("isEnabled", True)) is not False
            has_api_credential = bool(str(provider.get("credentialRef") or "").strip())
            oauth_reference = str(provider.get("api_key") or "").strip()
            has_oauth_credential = oauth_reference.startswith("oauth:") and bool(oauth_reference[6:].strip())
            if auth_type == "api_key" and provider_enabled and not has_api_credential:
                if not request_secret:
                    raise ConfigBrokerError(
                        "Provider 目标或鉴权合同已变化；请重新提交 API Key。",
                        code="model_provider_credential_required",
                        status_code=409,
                    )
                needs_secret = True
            elif auth_type == "oauth_file" and provider_enabled and not has_oauth_credential:
                raise ConfigBrokerError(
                    "Provider 目标或鉴权合同已变化；请重新完成 OAuth 授权。",
                    code="model_provider_oauth_credential_required",
                    status_code=409,
                )
            else:
                needs_secret = False
            if existing_ref and existing_ref != str(provider.get("credentialRef") or "").strip():
                proposed["supersededCredentialRefs"] = [existing_ref]
            proposed.update(
                {
                    "provider": provider,
                    "credentialTargetFingerprint": _provider_target_fingerprint(provider),
                }
            )

        if needs_secret and (not owner or not str(session_id or "").strip() or not str(run_id or "").strip()):
            raise ConfigBrokerError(
                "安全凭据卡只能从已归属用户的活动会话中创建。",
                code="config_ui_action_scope_required",
                status_code=409,
            )
        transaction = self._insert_transaction(
            target_kind="model_provider",
            target_id=provider_key,
            operation=normalized_operation,
            state="awaiting_secret" if needs_secret else "ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={"provider": {"validated": True, "containsSecrets": False}},
        )
        payload: dict[str, Any] = {
            "ok": True,
            "mode": "model_provider_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "providerId": provider_key,
            "operation": normalized_operation,
            "summary": "Provider 配置变更已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }
        if needs_secret:
            from core.ui_action_requests import ui_action_request_service

            provider = dict(proposed.get("provider") or {})
            parsed_url = urlparse(str(provider.get("base_url") or provider.get("baseUrl") or ""))
            action = ui_action_request_service.create(
                kind="secret_input",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                title=f"连接 {provider.get('name') or provider_key}",
                description=f"API Key 仅会提交给 {parsed_url.hostname or '已确认目标'}，不会进入配置事务、对话或日志。",
                target_label=str(provider.get("base_url") or provider.get("baseUrl") or ""),
                fields=[
                    {
                        "id": "apiKey",
                        "kind": "secret",
                        "label": "API Key",
                        "required": True,
                        "autocomplete": "off",
                        "binding": {"namespace": "model", "target": "provider", "targetName": "api_key"},
                    }
                ],
                handler_type="config_broker_secret",
                handler_ref=transaction["transactionId"],
            )
            payload["uiAction"] = action
            payload["nextAction"] = "等待安全凭据提交后自动完成事务。"
        return payload

    def prepare_model_binding(
        self,
        *,
        provider_id: str,
        model_id: str,
        model_config: dict[str, Any] | None,
        source_provider_id: str,
        source_model_id: str,
        source: str,
        replace_provider_models: bool,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        model_key = str(model_id or "").strip().strip("/")
        source_provider_key = str(source_provider_id or provider_key).strip()
        source_model_key = str(source_model_id or model_key).strip().strip("/")
        if not provider_key or not _PROVIDER_ID_RE.fullmatch(provider_key) or not model_key:
            raise ConfigBrokerError("providerId 和 modelId 必须有效。", code="model_binding_identity_invalid")
        if not source_provider_key or not source_model_key:
            raise ConfigBrokerError("来源模型标识无效。", code="model_binding_source_invalid")
        before = model_control_plane.get_storage_safe_config()
        if provider_key not in dict(before.get("providers") or {}):
            raise ConfigBrokerError("Provider 不存在。", code="provider_not_found", status_code=404)
        model_patch = _canonical_model_patch(model_config)
        source_label = str(source or "manual").strip().lower() or "manual"
        if source_label not in _MODEL_BINDING_SOURCES:
            raise ConfigBrokerError(
                "模型绑定来源无效。",
                code="model_binding_source_invalid",
            )
        source_ref = make_model_ref(source_provider_key, source_model_key)
        target_ref = make_model_ref(provider_key, model_key)
        dependencies = _binding_removal_dependencies(
            before,
            provider_id=provider_key,
            model_id=model_key,
            source_provider_id=source_provider_key,
            source_model_id=source_model_key,
            replace_provider_models=bool(replace_provider_models),
        )
        if dependencies:
            raise ConfigBrokerError(
                "模型移动或替换会使角色或 Subagent 的模型引用失效；请先解除绑定。",
                code="model_binding_still_bound",
                status_code=409,
            )
        target_id = _model_binding_target_id(source_ref, target_ref)
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_binding",
            target_id=target_id,
            operation="upsert",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={
                "providerId": provider_key,
                "modelId": model_key,
                "sourceProviderId": source_provider_key,
                "sourceModelId": source_model_key,
                "source": source_label,
                "model": model_patch,
                "replaceProviderModels": bool(replace_provider_models),
                "newCredentialRefs": [],
            },
            validation={
                "binding": {
                    "validated": True,
                    "containsSecrets": False,
                    "removalDependencyCount": 0,
                }
            },
        )
        return {
            "ok": True,
            "mode": "model_binding_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "modelRef": target_ref,
            "summary": "模型绑定变更已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_model_default(
        self,
        *,
        model_ref: str,
        category: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        config = model_control_plane.get_config()
        record = model_control_plane.get_model_record(str(model_ref or "").strip(), config)
        if not record:
            raise ConfigBrokerError("目标模型不存在。", code="default_model_not_found", status_code=404)
        inferred_category = model_control_plane.default_category_for_model_record(record)
        if not inferred_category:
            raise ConfigBrokerError(
                "媒体生成模型不能设为默认对话模型。",
                code="media_generation_models_do_not_support_default_binding",
            )
        target_category = str(category or inferred_category).strip() or inferred_category
        category_definition = model_control_plane._category_definition(target_category)
        if not model_control_plane.is_model_compatible(category_definition, record):
            raise ConfigBrokerError(
                "模型能力与默认类别不匹配。",
                code="default_model_category_mismatch",
            )
        role_key = str(category_definition.get("role") or "").strip()
        prepared = self.prepare_role_assignment(
            role=role_key,
            model_ref=str(record.get("model_ref") or model_ref),
            owner_id=owner_id,
            session_id=session_id,
            run_id=run_id,
        )
        return {**prepared, "mode": "model_default_prepare", "category": target_category, "role": role_key}

    def prepare_model(
        self,
        *,
        provider_id: str,
        model_id: str,
        provider_name: str,
        base_url: str,
        api_standard: str,
        channel_id: str = "",
        wire_protocol: str = "",
        endpoint_path: str = "",
        model_type: str,
        context_window: int | None,
        max_tokens: int | None,
        capabilities: dict[str, Any] | None,
        evidence_refs: Iterable[Any] | None,
        credential_required: bool | None,
        owner_id: str,
        session_id: str,
        run_id: str,
        provider_config: dict[str, Any] | None = None,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        model_key = str(model_id or "").strip().strip("/")
        if not provider_key or not _PROVIDER_ID_RE.fullmatch(provider_key):
            raise ConfigBrokerError("providerId 格式无效。", code="provider_id_invalid")
        if not model_key:
            raise ConfigBrokerError("modelId 不能为空。", code="model_id_required")
        safe_before = model_control_plane.get_storage_safe_config()
        existing_provider = dict((safe_before.get("providers") or {}).get(provider_key) or {})
        existing_meta = dict(existing_provider.get("provider") or {})
        existing_model = dict((existing_provider.get("models") or {}).get(model_key) or {})
        provider_extra = _canonical_provider_patch(provider_config)
        model_extra = _canonical_model_patch(model_config)
        transport_provider = {**existing_meta, **provider_extra}
        normalized_url = _valid_provider_endpoint(
            base_url
            or provider_extra.get("base_url")
            or existing_meta.get("base_url")
            or existing_meta.get("baseUrl")
            or ""
        )
        standard = str(
            api_standard
            or provider_extra.get("api_standard")
            or existing_meta.get("api_standard")
            or existing_meta.get("apiStandard")
            or "openai"
        ).strip().lower()
        if not _API_STANDARD_RE.fullmatch(standard) or standard == "catalog_only":
            raise ConfigBrokerError("apiStandard 格式无效。", code="provider_api_standard_invalid")
        (
            normalized_url,
            standard,
            normalized_channel_id,
            normalized_wire_protocol,
        ) = _validate_provider_transport(
            transport_provider,
            base_url=normalized_url,
            api_standard=standard,
            channel_id=channel_id,
            wire_protocol=wire_protocol,
        )
        normalized_url = _valid_provider_endpoint(normalized_url)
        effective_auth = dict(transport_provider.get("authContract") or {}) or {"type": "api_key"}
        auth_type = str(effective_auth.get("type") or "api_key").strip().lower()
        if auth_type not in {"api_key", "none"}:
            raise ConfigBrokerError(
                "手工模型配置只支持 api_key 或 none；OAuth 模型必须从受信 Model Hub 预置接入。",
                code="provider_auth_contract_invalid",
            )
        derived_credential_required = auth_type == "api_key"
        if credential_required is not None and bool(credential_required) != derived_credential_required:
            raise ConfigBrokerError(
                "credentialRequired 与已验证的 Provider 鉴权合同不一致。",
                code="provider_credential_contract_mismatch",
            )
        refs_explicitly_supplied = evidence_refs is not None or "sourceRefs" in model_extra
        refs = _safe_refs(
            [*(list(evidence_refs or [])), *(list(model_extra.get("sourceRefs") or []))]
            if refs_explicitly_supplied
            else list(existing_model.get("sourceRefs") or [])
        )
        source = "web_research" if refs else "agent_proposed"
        capabilities_explicitly_supplied = capabilities is not None or "capabilities" in model_extra
        raw_capabilities = (
            capabilities
            if capabilities is not None
            else model_extra.get("capabilities")
            if "capabilities" in model_extra
            else existing_model.get("capabilities")
            or {}
        )
        caps = {
            str(key): bool(value)
            for key, value in dict(raw_capabilities or {}).items()
        }
        type_explicitly_supplied = bool(str(model_type or "").strip()) or "type" in model_extra
        effective_model_type = str(
            model_type
            or model_extra.get("type")
            or existing_model.get("type")
            or ("MULTIMODAL" if caps.get("vision") or caps.get("multimodal") else "TEXT")
        ).strip().upper()
        effective_context_window = (
            int(context_window)
            if context_window is not None
            else model_extra.get("contextWindow", existing_model.get("contextWindow"))
        )
        effective_max_tokens = (
            int(max_tokens)
            if max_tokens is not None
            else model_extra.get("maxTokens", existing_model.get("maxTokens"))
        )
        fact_provenance = {
            **deepcopy(dict(existing_model.get("factProvenance") or {})),
        }
        for fact_key, fact_value, explicitly_supplied in (
            (
                "contextWindow",
                effective_context_window,
                context_window is not None or "contextWindow" in model_extra,
            ),
            (
                "maxTokens",
                effective_max_tokens,
                max_tokens is not None or "maxTokens" in model_extra,
            ),
        ):
            if not explicitly_supplied:
                continue
            if fact_value == existing_model.get(fact_key) and fact_key in fact_provenance:
                continue
            fact_provenance[fact_key] = {
                "source": "agent_proposed",
                "confidence": "unverified",
                **({"sourceRefs": refs} if refs else {}),
            }
        model_patch = {
            **model_extra,
            "type": effective_model_type,
            "contextWindow": effective_context_window,
            "maxTokens": effective_max_tokens,
            "capabilities": caps,
            "capabilitySource": str(
                model_extra.get("capabilitySource")
                or (source if capabilities_explicitly_supplied else existing_model.get("capabilitySource"))
                or source
            ),
            "sourceRefs": refs,
            "factProvenance": fact_provenance,
            "isEnabled": bool(
                model_extra["isEnabled"]
                if "isEnabled" in model_extra
                else existing_model.get("isEnabled", True)
            ),
            "runtimeReady": bool(
                model_extra["runtimeReady"]
                if "runtimeReady" in model_extra
                else existing_model.get("runtimeReady", True)
            ),
        }
        normalized_endpoint_path = str(endpoint_path or "").strip().replace("\\", "/").strip("/")
        parsed_endpoint_path = urlparse(normalized_endpoint_path)
        if parsed_endpoint_path.netloc or parsed_endpoint_path.query or parsed_endpoint_path.fragment or any(
            part == ".." for part in normalized_endpoint_path.split("/")
        ):
            raise ConfigBrokerError(
                "endpointPath 必须是无 query/fragment 的相对路径。",
                code="model_endpoint_path_invalid",
            )
        if normalized_channel_id or normalized_wire_protocol or normalized_endpoint_path:
            model_patch["endpointBinding"] = {
                **dict(model_patch.get("endpointBinding") or {}),
                "version": 2,
                "route": model_key,
                "channelId": normalized_channel_id or "default",
                "wireProtocol": normalized_wire_protocol,
                "endpointPath": normalized_endpoint_path,
                "providerModelId": model_key,
                "protocolSource": "config_broker_explicit",
                "provenance": {
                    "source": "config_broker_explicit",
                    "confidence": "authoritative",
                },
            }
        eligibility = evaluate_model_eligibility(model_patch)
        registration_required_facts = list(eligibility.get("requiredFacts") or [])
        if not existing_model:
            if not type_explicitly_supplied:
                registration_required_facts.append("type")
            if (
                (not capabilities_explicitly_supplied or not caps)
                and not str(model_extra.get("capabilityClass") or "").strip()
            ):
                registration_required_facts.append("capabilities")
        registration_required_facts = list(dict.fromkeys(registration_required_facts))
        safe_transport_provider = {
            key: value
            for key, value in transport_provider.items()
            if key not in {"api_key", "apiKey", "credentialRef", "credentialSource", "credential_ref", "credential_source"}
        }
        provider_patch = {
            **safe_transport_provider,
            "name": str(provider_name or provider_extra.get("name") or provider_key).strip() or provider_key,
            "base_url": normalized_url,
            "api_standard": standard,
            "is_enabled": bool(
                provider_extra["is_enabled"]
                if "is_enabled" in provider_extra
                else existing_meta.get("is_enabled", existing_meta.get("isEnabled", True))
            ),
            "authContract": effective_auth,
        }
        existing_ref = str(existing_meta.get("credentialRef") or "").strip()
        # Older connected providers may predate the explicit authContract
        # field. Their managed credential is still safe to reuse only when the
        # same normalized default API-key contract and exact target fingerprint
        # match; endpoint or auth drift remains non-reusable.
        existing_provider_for_fingerprint = {
            **existing_meta,
            "authContract": dict(existing_meta.get("authContract") or effective_auth),
        }
        credential_reusable = _credential_ref_is_reusable(
            existing_ref,
            existing_provider=existing_provider_for_fingerprint,
            proposed_provider=provider_patch,
        )
        if credential_reusable:
            provider_patch["credentialRef"] = existing_ref
            provider_patch["credentialSource"] = "os_credential_store"
        proposed = {
            "providerId": provider_key,
            "modelId": model_key,
            "provider": provider_patch,
            "model": model_patch,
            "source": source,
            "evidenceRefs": refs,
            "credentialRequired": derived_credential_required,
            "credentialPreviouslyConfigured": credential_reusable,
            "credentialReuseAuthorized": credential_reusable,
            "credentialTargetFingerprint": _provider_target_fingerprint(provider_patch),
            "newCredentialRefs": [],
        }
        owner = _session_owner(session_id, owner_id)
        if registration_required_facts:
            transaction = self._insert_transaction(
                target_kind="model",
                target_id=make_model_ref(provider_key, model_key),
                operation="upsert",
                state="blocked",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                before=safe_before,
                proposed=proposed,
                validation={"doctor": eligibility, "evidenceRefs": refs},
                error=("model_facts_incomplete", "模型参数不完整；请补全上下文窗口和最大输出 tokens。"),
            )
            return {
                "ok": False,
                "mode": "model_prepare",
                "state": "blocked",
                "transactionId": transaction["transactionId"],
                "summary": "模型配置计划已阻断：缺少可供运行时使用的必要模型参数。",
                "requiredFacts": registration_required_facts,
                "doctor": eligibility,
                "nextAction": "联网检索或由用户在 Model Hub 补全 contextWindow 与 maxTokens 后重新准备。",
            }
        needs_secret = bool(derived_credential_required and not credential_reusable)
        if needs_secret and (not owner or not str(session_id or "").strip() or not str(run_id or "").strip()):
            raise ConfigBrokerError(
                "安全凭据卡只能从已归属用户的活动会话中创建。",
                code="config_ui_action_scope_required",
                status_code=409,
            )
        transaction = self._insert_transaction(
            target_kind="model",
            target_id=make_model_ref(provider_key, model_key),
            operation="upsert",
            state="awaiting_secret" if needs_secret else "ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=safe_before,
            proposed=proposed,
            validation={"doctor": eligibility, "evidenceRefs": refs},
        )
        payload: dict[str, Any] = {
            "ok": True,
            "mode": "model_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": "模型配置计划已验证，等待安全提交。",
            "doctor": eligibility,
            "evidenceRefs": refs,
            "nextAction": "提交配置事务。",
        }
        if needs_secret:
            from core.ui_action_requests import ui_action_request_service

            action = ui_action_request_service.create(
                kind="secret_input",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                title=f"连接 {provider_patch['name']}",
                description=f"API Key 仅会提交给 {urlparse(normalized_url).hostname}，不会进入对话、工具参数或日志。",
                target_label=normalized_url,
                fields=[
                    {
                        "id": "apiKey",
                        "kind": "secret",
                        "label": "API Key",
                        "required": True,
                        "autocomplete": "off",
                        "binding": {"namespace": "model", "target": "provider", "targetName": "api_key"},
                    }
                ],
                handler_type="config_broker_secret",
                handler_ref=transaction["transactionId"],
            )
            payload["uiAction"] = action
            payload["nextAction"] = "等待用户在安全凭据卡中保存 API Key；保存后事务会自动验证并提交。"
        return payload

    def catalog_inventory(
        self,
        *,
        provider_id: str = "",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        catalog = _get_model_provider_catalog()
        payload = catalog.load()
        provider_key = str(provider_id or "").strip()
        query_text = str(query or "").strip().lower()
        rows: list[dict[str, Any]] = []
        for provider in list(payload.get("providers") or []):
            if not isinstance(provider, dict):
                continue
            current_provider_id = str(provider.get("id") or "").strip()
            if provider_key and current_provider_id != provider_key:
                continue
            models = []
            for model in list(provider.get("models") or []):
                if not isinstance(model, dict):
                    continue
                model_id = str(model.get("id") or model.get("modelId") or "").strip()
                if query_text and query_text not in f"{current_provider_id} {provider.get('name') or ''} {model_id}".lower():
                    continue
                models.append(
                    {
                        key: deepcopy(model.get(key))
                        for key in (
                            "id",
                            "type",
                            "contextWindow",
                            "maxOutputTokens",
                            "maxTokens",
                            "capabilities",
                            "parameterProfile",
                            "reasoningSurface",
                            "thinkingControl",
                            "reasoningEffortControl",
                            "mediaLimits",
                            "operationKinds",
                            "adapter",
                            "rerankApiFlavor",
                            "availability",
                            "sourceRefs",
                        )
                        if model.get(key) not in (None, "", [], {})
                    }
                )
            if query_text and not models and query_text not in f"{current_provider_id} {provider.get('name') or ''}".lower():
                continue
            rows.append(
                {
                    "providerId": current_provider_id,
                    "name": provider.get("name") or current_provider_id,
                    "apiStandard": provider.get("apiStandard"),
                    "providerKind": provider.get("providerKind"),
                    "baseUrl": provider.get("baseUrl"),
                    "defaultChannelId": provider.get("defaultChannelId"),
                    "channels": deepcopy(provider.get("channels") or []),
                    "credentialRequired": str((provider.get("auth") or {}).get("type") or "api_key") == "api_key",
                    "catalogConnectable": any(
                        bool((model.get("availability") or {}).get("catalogConnectable"))
                        for model in models
                    ),
                    "isCustom": bool(provider.get("isCustom")),
                    "isManaged": bool(provider.get("isManaged")),
                    "models": models,
                }
            )
        bounded_offset = max(0, int(offset or 0))
        bounded_limit = max(1, min(int(limit or 50), 100))
        return {
            "ok": True,
            "mode": "catalog_models",
            "total": len(rows),
            "offset": bounded_offset,
            "limit": bounded_limit,
            "providers": rows[bounded_offset : bounded_offset + bounded_limit],
            "managedCatalogStatus": payload.get("managedCatalogStatus") or {"state": "ok"},
            "summary": f"目录中有 {len(rows)} 个匹配供应商。",
        }

    def catalog_discover(
        self,
        *,
        provider_id: str,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        catalog = _get_model_provider_catalog()
        provider = catalog.get_provider(provider_key)
        if not provider:
            raise ConfigBrokerError("目录供应商不存在。", code="catalog_provider_not_found", status_code=404)
        safe_config = model_control_plane.get_storage_safe_config()
        existing = dict((safe_config.get("providers") or {}).get(provider_key) or {})
        existing_meta = dict(existing.get("provider") or {})
        reference = str(existing_meta.get("credentialRef") or "").strip()
        catalog_auth_contract = _catalog_auth_contract(provider)
        existing_auth_contract = dict(existing_meta.get("authContract") or {})
        if not catalog_auth_contract or existing_auth_contract != catalog_auth_contract:
            raise ConfigBrokerError(
                "供应商凭据传输合同与目录预置不完全一致。",
                code="catalog_discovery_auth_contract_mismatch",
                status_code=409,
            )
        if not _credential_ref_is_reusable(
            reference,
            existing_provider=existing_meta,
            proposed_provider=existing_meta,
        ):
            raise ConfigBrokerError(
                "该供应商没有可用于目录发现的有效受管凭据。",
                code="catalog_discovery_credential_required",
                status_code=409,
            )
        probe_provider = {
            **deepcopy(provider),
            "baseUrl": str(existing_meta.get("base_url") or ""),
            "apiStandard": str(existing_meta.get("api_standard") or provider.get("apiStandard") or ""),
        }
        try:
            resolver = getattr(catalog, "resolve_probe_target", None)
            if callable(resolver):
                probe_target = resolver(probe_provider, base_url=str(existing_meta.get("base_url") or ""))
            else:
                from core.model_provider_catalog import resolve_probe_target

                probe_target = resolve_probe_target(probe_provider, base_url=str(existing_meta.get("base_url") or ""))
            models_url = _catalog_probe_url_in_scope(probe_provider, dict(probe_target or {}))
        except (ConfigBrokerError, ValueError) as exc:
            if isinstance(exc, ConfigBrokerError):
                raise
            raise ConfigBrokerError("供应商目录发现目标无效。", code="catalog_discovery_target_invalid", status_code=409) from exc
        decision = safety_guardian.assess_http_request(
            "GET",
            models_url,
            headers={"Authorization": "Bearer [managed-credential]"},
            runtime_context={"source": "config_broker_catalog_discover", "credentialClass": "api_key"},
        )
        if decision.is_block() or decision.is_review():
            raise ConfigBrokerError("供应商目录发现目标未通过安全审查。", code="catalog_discovery_target_not_authorized", status_code=409)
        try:
            credential = credential_ref_store.resolve(reference)
        except CredentialStoreError as exc:
            raise ConfigBrokerError(
                "供应商凭据不可用。",
                code="catalog_discovery_credential_unavailable",
                status_code=409,
            ) from exc
        result = catalog.probe_provider_entry(
            probe_provider,
            credential=credential,
            base_url=str(probe_target.get("baseUrl") or ""),
        )
        if not result.get("ok"):
            code, message = _catalog_discovery_message(result.get("reason"))
            raise ConfigBrokerError(
                message,
                code=code,
                status_code=409,
            )
        query_text = str(query or "").strip().lower()
        matching_models = [
            deepcopy(item)
            for item in list(result.get("models") or [])
            if isinstance(item, dict)
            and (
                not query_text
                or query_text in str(item.get("modelId") or item.get("id") or "").lower()
            )
        ]
        bounded_offset = max(0, int(offset or 0))
        bounded_limit = max(1, min(int(limit or 50), 100))
        models = matching_models[bounded_offset : bounded_offset + bounded_limit]
        return {
            "ok": True,
            "mode": "catalog_discover",
            "providerId": provider_key,
            "source": result.get("source"),
            "models": models,
            "modelCount": len(models),
            "total": len(matching_models),
            "offset": bounded_offset,
            "limit": bounded_limit,
            "hasMore": bounded_offset + len(models) < len(matching_models),
            "summary": f"从 {provider.get('name') or provider_key} 发现 {len(models)} 个模型。",
        }

    def prepare_catalog_model(
        self,
        *,
        provider_id: str,
        model_id: str,
        channel_id: str = "",
        wire_protocol: str = "",
        discover_if_needed: bool = True,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        model_key = str(model_id or "").strip().strip("/")
        catalog = _get_model_provider_catalog()
        provider = catalog.get_provider(provider_key)
        if not provider:
            raise ConfigBrokerError("目录供应商不存在。", code="catalog_provider_not_found", status_code=404)
        if not model_key:
            raise ConfigBrokerError("modelId 不能为空。", code="model_id_required")

        model = catalog.normalize_model(provider, model_key)
        initial_eligibility = evaluate_model_eligibility(model)
        discovery: dict[str, Any] = {}
        if discover_if_needed and list(initial_eligibility.get("requiredFacts") or []):
            try:
                discovery = self.catalog_discover(provider_id=provider_key, query=model_key, limit=20)
            except ConfigBrokerError:
                discovery = {}
            discovered = next(
                (
                    dict(item)
                    for item in list(discovery.get("models") or [])
                    if str(item.get("modelId") or item.get("id") or "").strip() == model_key
                ),
                None,
            )
            if discovered:
                model = discovered

        safe_before = model_control_plane.get_storage_safe_config()
        existing_container = dict((safe_before.get("providers") or {}).get(provider_key) or {})
        existing_meta = dict(existing_container.get("provider") or {})
        existing_model = dict((existing_container.get("models") or {}).get(model_key) or {})
        existing_ref = str(existing_meta.get("credentialRef") or "").strip()
        existing_channels = existing_meta.get("channels") if isinstance(existing_meta.get("channels"), list) else None
        try:
            plan = _build_catalog_connection_plan(
                provider=provider,
                model=model,
                model_id=model_key,
                existing_provider=existing_meta,
                existing_model=existing_model,
                base_url=str(existing_meta.get("base_url") or ""),
                api_standard=str(existing_meta.get("api_standard") or ""),
                channel_id=channel_id,
                wire_protocol=wire_protocol,
                channels=existing_channels,
                default_channel_id=str(existing_meta.get("defaultChannelId") or ""),
                use_catalog_default_channel=True,
                source="config_broker_catalog",
            )
        except ValueError as exc:
            raise ConfigBrokerError(
                str(exc),
                code="catalog_connection_plan_invalid",
                status_code=409,
            ) from exc
        provider_patch = dict(plan.get("providerPatch") or {})
        existing_auth_view = {
            **existing_meta,
            # Existing records predate authContract persistence. A catalog
            # connect may migrate the exact provider using the catalog's
            # trusted non-secret transport contract.
            "authContract": dict(existing_meta.get("authContract") or provider_patch.get("authContract") or {}),
        }
        credential_reusable = _credential_ref_is_reusable(
            existing_ref,
            existing_provider=existing_auth_view,
            proposed_provider=provider_patch,
        )
        if credential_reusable:
            provider_patch["credentialRef"] = existing_ref
            provider_patch["credentialSource"] = "os_credential_store"
        model_patch = dict(plan.get("modelPatch") or {})
        # OAuth and catalog-managed runtimes may intentionally omit persisted
        # budgets because ModelControlPlane resolves them from the registry.
        # Doctor must validate the authoritative catalog facts, not that
        # storage-minimal patch.
        eligibility = evaluate_model_eligibility(model)
        registration_required_facts = list(eligibility.get("requiredFacts") or [])
        proposed = {
            "providerId": provider_key,
            "catalogModelId": model_key,
            "modelId": str(plan.get("modelId") or model_key),
            "provider": provider_patch,
            "model": model_patch,
            "source": "config_broker_catalog",
            "evidenceRefs": _safe_refs(
                [
                    provider.get("sourceUrl"),
                    *list((model.get("capabilityRegistry") or {}).get("sourceRefs") or []),
                    *list(model.get("sourceRefs") or []),
                ]
            ),
            "credentialRequired": bool(plan.get("credentialRequired")),
            "credentialPreviouslyConfigured": credential_reusable,
            "credentialReuseAuthorized": credential_reusable,
            "credentialTargetFingerprint": _provider_target_fingerprint(provider_patch),
            "replaceProviderModels": bool(plan.get("replaceProviderModels")),
            "newCredentialRefs": [],
        }
        owner = _session_owner(session_id, owner_id)
        if registration_required_facts:
            transaction = self._insert_transaction(
                target_kind="model",
                target_id=make_model_ref(provider_key, proposed["modelId"]),
                operation="catalog_connect",
                state="blocked",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                before=safe_before,
                proposed=proposed,
                validation={"doctor": eligibility, "discovery": {"used": bool(discovery)}},
                error=("model_facts_incomplete", "目录模型缺少运行所需的必要事实。"),
            )
            return {
                "ok": False,
                "mode": "catalog_connect_prepare",
                "state": "blocked",
                "transactionId": transaction["transactionId"],
                "requiredFacts": registration_required_facts,
                "summary": "目录模型仍缺少必要事实，未创建可提交配置。",
            }
        needs_secret = bool(plan.get("credentialRequired") and not credential_reusable)
        if needs_secret and (not owner or not str(session_id or "").strip() or not str(run_id or "").strip()):
            raise ConfigBrokerError(
                "安全凭据卡只能从已归属用户的活动会话中创建。",
                code="config_ui_action_scope_required",
                status_code=409,
            )
        transaction = self._insert_transaction(
            target_kind="model",
            target_id=make_model_ref(provider_key, proposed["modelId"]),
            operation="catalog_connect",
            state="awaiting_secret" if needs_secret else "ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=safe_before,
            proposed=proposed,
            validation={
                "doctor": eligibility,
                "catalog": {
                    "providerId": provider_key,
                    "catalogModelId": model_key,
                    "isManaged": bool(provider.get("isManaged")),
                    "isCustom": bool(provider.get("isCustom")),
                },
                "protocolAdvice": deepcopy(plan.get("protocolAdvice") or {}),
                "discovery": {"used": bool(discovery)},
            },
        )
        payload: dict[str, Any] = {
            "ok": True,
            "mode": "catalog_connect_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "providerId": provider_key,
            "modelId": proposed["modelId"],
            "credentialReused": credential_reusable,
            "summary": "目录模型接入计划已验证，等待安全提交。",
            "nextAction": "提交配置事务。",
        }
        if needs_secret:
            from core.ui_action_requests import ui_action_request_service

            parsed_url = urlparse(str(provider_patch.get("base_url") or ""))
            action = ui_action_request_service.create(
                kind="secret_input",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                title=f"连接 {provider_patch.get('name') or provider_key}",
                description=f"API Key 仅会提交给 {parsed_url.hostname or '已确认目标'}，不会进入对话、工具参数或日志。",
                target_label=str(provider_patch.get("base_url") or ""),
                fields=[
                    {
                        "id": "apiKey",
                        "kind": "secret",
                        "label": "API Key",
                        "required": True,
                        "autocomplete": "off",
                        "binding": {"namespace": "model", "target": "provider", "targetName": "api_key"},
                    }
                ],
                handler_type="config_broker_secret",
                handler_ref=transaction["transactionId"],
            )
            payload["uiAction"] = action
            payload["nextAction"] = "等待用户在安全凭据卡中保存 API Key；保存后事务会自动验证并提交。"
        return payload

    def prepare_catalog_provider(
        self,
        *,
        provider_preset: dict[str, Any],
        evidence_refs: Iterable[Any] | None,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        if not isinstance(provider_preset, dict):
            raise ConfigBrokerError("providerPreset 必须是对象。", code="catalog_provider_patch_invalid")
        _reject_secret_fields(provider_preset, path="providerPreset")
        catalog = _get_model_provider_catalog()
        try:
            validated = catalog.validate_managed_provider(provider_preset)
        except ValueError as exc:
            raise ConfigBrokerError(str(exc), code="catalog_provider_patch_invalid") from exc
        provider_key = str(validated.get("id") or "").strip()
        before = catalog.load_managed()
        managed_digest = _managed_catalog_digest(catalog)
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_catalog_provider",
            target_id=provider_key,
            operation="catalog_upsert",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={
                "providerId": provider_key,
                "provider": validated,
                "evidenceRefs": _safe_refs(evidence_refs),
                "newCredentialRefs": [],
            },
            validation={
                "catalog": {"validated": True, "containsSecrets": False, "managedDigest": managed_digest},
                "evidenceRefs": _safe_refs(evidence_refs),
            },
        )
        return {
            "ok": True,
            "mode": "catalog_provider_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "preview": {
                "providerId": provider_key,
                "modelIds": [str(item.get("id") or "") for item in list(validated.get("models") or [])],
                "channelIds": [str(item.get("id") or "") for item in list(validated.get("channels") or [])],
            },
            "summary": "Model Hub managed 预置更新已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_catalog_provider_removal(
        self,
        *,
        provider_id: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        catalog = _get_model_provider_catalog()
        before = catalog.load_managed()
        managed_digest = _managed_catalog_digest(catalog)
        existing = catalog.get_managed_provider(provider_key)
        if not existing:
            raise ConfigBrokerError(
                "该供应商没有可删除的 managed 预置。",
                code="catalog_managed_provider_not_found",
                status_code=404,
            )
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_catalog_provider",
            target_id=provider_key,
            operation="catalog_delete",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={
                "providerId": provider_key,
                "newCredentialRefs": [],
            },
            validation={"catalog": {"managedProviderExists": True, "managedDigest": managed_digest}},
        )
        return {
            "ok": True,
            "mode": "catalog_provider_remove_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "providerId": provider_key,
            "summary": "Model Hub managed 预置删除已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_custom_catalog_provider_removal(
        self,
        *,
        provider_id: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        catalog = _get_model_provider_catalog()
        before = catalog.load_custom()
        try:
            custom_digest = catalog.custom_digest()
        except ValueError as exc:
            raise ConfigBrokerError(
                "Custom Provider 目录损坏；修复前不会覆盖该文件。",
                code="catalog_custom_invalid",
                status_code=409,
            ) from exc
        providers = {
            str(item.get("id") or ""): dict(item)
            for item in list(before.get("providers") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        existing = providers.get(provider_key)
        if not existing or not bool(existing.get("isCustom")):
            raise ConfigBrokerError(
                "Custom Provider 不存在。",
                code="catalog_custom_provider_not_found",
                status_code=404,
            )
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_catalog_custom_provider",
            target_id=provider_key,
            operation="catalog_delete",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={"providerId": provider_key, "newCredentialRefs": []},
            validation={"catalog": {"customProviderExists": True, "customDigest": custom_digest}},
        )
        return {
            "ok": True,
            "mode": "catalog_custom_provider_remove_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "providerId": provider_key,
            "summary": "Custom Provider 删除已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_catalog_recovery(
        self,
        *,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        catalog = _get_model_provider_catalog()
        before = catalog.managed_recovery_state()
        if not before.get("managedExists"):
            raise ConfigBrokerError(
                "当前没有 managed 目录文件需要恢复。",
                code="catalog_recovery_not_required",
                status_code=409,
            )
        if not before.get("backupValid"):
            raise ConfigBrokerError(
                "没有可验证的 managed 目录备份。",
                code="catalog_recovery_backup_unavailable",
                status_code=409,
            )
        if before.get("rejectedExists"):
            raise ConfigBrokerError(
                "上一次目录恢复尚未完成撤销或确认。",
                code="catalog_recovery_pending",
                status_code=409,
            )
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_catalog_recovery",
            target_id="managed",
            operation="restore_backup",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={
                "managedDigest": before.get("managedDigest"),
                "backupDigest": before.get("backupDigest"),
                "newCredentialRefs": [],
            },
            validation={
                "recovery": {
                    "managedValid": bool(before.get("managedValid")),
                    "backupValid": True,
                }
            },
        )
        return {
            "ok": True,
            "mode": "catalog_recover_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "currentState": "valid" if before.get("managedValid") else "invalid",
            "summary": "managed 目录备份恢复已验证，原文件会被隔离并可精确撤销。",
            "nextAction": "提交配置事务。",
        }

    def prepare_catalog_recovery_finalize(
        self,
        *,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        catalog = _get_model_provider_catalog()
        before = catalog.managed_recovery_state()
        if not before.get("managedValid") or not before.get("rejectedExists"):
            raise ConfigBrokerError(
                "当前没有可最终确认的 managed 目录恢复结果。",
                code="catalog_recovery_finalize_not_ready",
                status_code=409,
            )
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_catalog_recovery",
            target_id="managed",
            operation="finalize_recovery",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={
                "managedDigest": before.get("managedDigest"),
                "rejectedDigest": before.get("rejectedDigest"),
                "newCredentialRefs": [],
            },
            validation={"recovery": {"managedValid": True, "rejectedExists": True}},
        )
        return {
            "ok": True,
            "mode": "catalog_recovery_finalize_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": "已准备确认 recovered managed 目录并清理隔离副本。",
            "nextAction": "提交配置事务。",
        }
    def prepare_model_policy(
        self,
        *,
        governance: dict[str, Any] | None,
        routing_policies: dict[str, Any] | None,
        role_parameters: dict[str, Any] | None,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        governance_patch = deepcopy(governance or {})
        routing_patch = deepcopy(routing_policies or {})
        parameters_patch = deepcopy(role_parameters or {})
        for label, value in (
            ("governance", governance_patch),
            ("routingPolicies", routing_patch),
            ("roleParameters", parameters_patch),
        ):
            if not isinstance(value, dict):
                raise ConfigBrokerError(f"{label} 必须是对象。", code="model_policy_patch_invalid")
            _reject_secret_fields(value, path=label)
        if not any((governance_patch, routing_patch, parameters_patch)):
            raise ConfigBrokerError("至少需要一个模型策略变更。", code="model_policy_patch_empty")

        unknown_governance = sorted(str(key) for key in governance_patch if key not in _MODEL_GOVERNANCE_KEYS)
        if unknown_governance:
            raise ConfigBrokerError(
                f"governance 包含未支持字段：{', '.join(unknown_governance)}。",
                code="model_policy_field_unknown",
            )
        for key, value in governance_patch.items():
            if key == "budgets":
                continue
            default = DEFAULT_GOVERNANCE[key]
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, int):
                valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            elif isinstance(default, float):
                valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0
            else:
                valid = False
            if not valid:
                raise ConfigBrokerError(
                    f"governance.{key} 必须是有限的非负有效值。",
                    code="model_policy_value_invalid",
                )
            if isinstance(default, float) and key == "providerErrorRateThreshold" and float(value) > 1:
                raise ConfigBrokerError("governance.providerErrorRateThreshold 必须在 0 到 1 之间。", code="model_policy_value_invalid")
        budgets_patch = governance_patch.get("budgets")
        if budgets_patch is not None:
            if not isinstance(budgets_patch, dict):
                raise ConfigBrokerError("governance.budgets 必须是对象。", code="model_policy_patch_invalid")
            unknown_budgets = sorted(str(key) for key in budgets_patch if key not in _MODEL_BUDGET_KEYS)
            if unknown_budgets:
                raise ConfigBrokerError(
                    f"governance.budgets 包含未支持字段：{', '.join(unknown_budgets)}。",
                    code="model_policy_field_unknown",
                )
            project_budgets = budgets_patch.get("projectBudgets")
            if project_budgets is not None and (
                not isinstance(project_budgets, list) or len(project_budgets) > 100
            ):
                raise ConfigBrokerError(
                    "governance.budgets.projectBudgets 必须是最多 100 项的数组。",
                    code="model_policy_patch_invalid",
                )
            for key, value in budgets_patch.items():
                if key == "projectBudgets":
                    continue
                default = DEFAULT_GOVERNANCE["budgets"][key]
                if isinstance(default, bool):
                    valid = isinstance(value, bool)
                elif isinstance(default, int):
                    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else:
                    valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0
                if not valid:
                    raise ConfigBrokerError(
                        f"governance.budgets.{key} 必须是有限的非负有效值。",
                        code="model_policy_value_invalid",
                    )
            project_ids: set[str] = set()
            for item in project_budgets or []:
                if not isinstance(item, dict):
                    raise ConfigBrokerError("governance.budgets.projectBudgets 项必须是对象。", code="model_policy_patch_invalid")
                project_id = str(item.get("projectId") or item.get("project_id") or "").strip()
                if not project_id or project_id in project_ids:
                    raise ConfigBrokerError("governance.budgets.projectBudgets.projectId 必须唯一。", code="model_policy_project_budget_invalid")
                project_ids.add(project_id)
                for key, value in item.items():
                    if key in {"projectId", "project_id"}:
                        continue
                    if key not in {"dailyCostLimit", "dailyTokenLimit"} or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
                        raise ConfigBrokerError("governance.budgets.projectBudgets 包含无效预算。", code="model_policy_project_budget_invalid")

        current = model_control_plane.get_storage_safe_config()
        role_definitions = model_control_plane.get_role_definitions(current)
        unknown_routes = sorted(str(key) for key in routing_patch if key not in _MODEL_ROUTING_KEYS)
        if unknown_routes:
            raise ConfigBrokerError(
                f"routingPolicies 包含未支持字段：{', '.join(unknown_routes)}。",
                code="model_policy_field_unknown",
            )
        for route_key, role_key in routing_patch.items():
            normalized_role = str(role_key or "").strip()
            if not normalized_role or normalized_role not in role_definitions:
                raise ConfigBrokerError(
                    f"routingPolicies.{route_key} 指向未知模型角色。",
                    code="model_policy_route_invalid",
                )
            routing_patch[route_key] = normalized_role

        for role_key, values in parameters_patch.items():
            if str(role_key) not in role_definitions or not isinstance(values, dict):
                raise ConfigBrokerError(
                    f"roleParameters.{role_key} 不是有效模型角色配置。",
                    code="model_policy_role_parameters_invalid",
                )
            unknown_parameters = sorted(str(key) for key in values if key not in _MODEL_ROLE_PARAMETER_KEYS)
            if unknown_parameters:
                raise ConfigBrokerError(
                    f"roleParameters.{role_key} 包含未支持字段：{', '.join(unknown_parameters)}。",
                    code="model_policy_field_unknown",
                )
            temperature = values.get("temperature")
            if temperature is not None:
                if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not math.isfinite(float(temperature)):
                    raise ConfigBrokerError(
                        f"roleParameters.{role_key}.temperature 必须是数字或 null。",
                        code="model_policy_role_parameters_invalid",
                    )
                numeric_temperature = float(temperature)
                if numeric_temperature < 0 or numeric_temperature > 2:
                    raise ConfigBrokerError(
                        f"roleParameters.{role_key}.temperature 必须在 0 到 2 之间。",
                        code="model_policy_role_parameters_invalid",
                    )

        preview = deepcopy(current)
        touched: list[str] = []
        if governance_patch:
            next_governance = {**dict(preview.get("governance") or {}), **governance_patch}
            if isinstance(budgets_patch, dict):
                next_governance["budgets"] = {
                    **dict((preview.get("governance") or {}).get("budgets") or {}),
                    **budgets_patch,
                }
            preview["governance"] = next_governance
            touched.append("governance")
        if routing_patch:
            preview["routingPolicies"] = {
                **dict(preview.get("routingPolicies") or {}),
                **routing_patch,
            }
            touched.append("routingPolicies")
        if parameters_patch:
            next_parameters = deepcopy(dict(preview.get("roleParameters") or {}))
            for role_key, values in parameters_patch.items():
                next_parameters[str(role_key)] = {
                    **dict(next_parameters.get(str(role_key)) or {}),
                    **values,
                }
            preview["roleParameters"] = next_parameters
            touched.append("roleParameters")
        normalized_preview = model_control_plane.normalize_config(preview)
        proposed = {
            key: deepcopy(normalized_preview.get(key) or {})
            for key in touched
        }
        proposed["newCredentialRefs"] = []
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_policy_bundle",
            target_id="models",
            operation="policy_update",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=current,
            proposed=proposed,
            validation={"policy": {"validated": True, "sections": touched}},
        )
        return {
            "ok": True,
            "mode": "model_policy_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "sections": touched,
            "summary": "模型路由与治理策略已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_model_record_change(
        self,
        *,
        model_ref: str,
        operation: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        identity = parse_model_ref(str(model_ref or "").strip())
        if not identity:
            raise ConfigBrokerError("目标模型引用无效。", code="model_ref_invalid")
        provider_id, model_id = identity
        normalized_operation = str(operation or "").strip().lower()
        if normalized_operation not in {"enable", "disable", "remove"}:
            raise ConfigBrokerError("模型变更只支持 enable、disable 或 remove。", code="model_operation_invalid")
        before = model_control_plane.get_storage_safe_config()
        canonical_ref = make_model_ref(provider_id, model_id)
        target = self._target_snapshot("model_record", canonical_ref, before)
        if not target.get("modelExists"):
            raise ConfigBrokerError("目标模型不存在。", code="model_not_found", status_code=404)
        if normalized_operation == "remove":
            references = _model_dependencies(before, identity)
            if references:
                raise ConfigBrokerError(
                    "模型仍被角色或 Subagent 使用；请先解除绑定。",
                    code="model_still_bound",
                    status_code=409,
                )
        proposed = {
            "providerId": provider_id,
            "modelId": model_id,
            "modelRef": canonical_ref,
            "operation": normalized_operation,
            **({"enabled": normalized_operation == "enable"} if normalized_operation != "remove" else {}),
            "newCredentialRefs": [],
        }
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_record",
            target_id=canonical_ref,
            operation=normalized_operation,
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={
                "modelRecord": {
                    "exists": True,
                    "bound": False,
                    "dependencyDigest": _digest({
                        "roles": dict(before.get("roles") or {}),
                        "agents": dict((before.get("bindings") or {}).get("agents") or {}),
                    }),
                }
            },
        )
        return {
            "ok": True,
            "mode": "model_record_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "modelRef": canonical_ref,
            "operation": normalized_operation,
            "summary": "模型记录变更已验证，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_role_unbind(
        self,
        *,
        role: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        config = model_control_plane.get_config()
        role_key = str(role or "").strip()
        _role_definition, role_label, agent_id = self._role_contract(role_key, config)
        before = model_control_plane.get_storage_safe_config()
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="agent_model_role" if agent_id else "model_role",
            target_id=agent_id or role_key,
            operation="unbind",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={
                "role": role_key,
                "modelRef": "",
                **({"agentId": agent_id} if agent_id else {}),
                "newCredentialRefs": [],
            },
            validation={"role": {"validated": True}},
        )
        return {
            "ok": True,
            "mode": "role_unbind_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": f"已准备解除 {role_label} 的显式模型绑定。",
            "nextAction": "提交配置事务。",
        }

    def prepare_role_bindings(
        self,
        *,
        updates: dict[str, Any],
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        if not isinstance(updates, dict) or not updates:
            raise ConfigBrokerError("至少需要一个模型角色变更。", code="model_role_bundle_empty")
        _reject_secret_fields(updates, path="roleBindings")

        before = model_control_plane.get_storage_safe_config()
        models_by_ref = {
            str(item.get("modelRef") or "").strip(): dict(item)
            for item in model_control_plane.list_models(before)
            if str(item.get("modelRef") or "").strip()
        }
        proposed_updates: dict[str, str] = {}
        role_validation: dict[str, Any] = {}
        assignment_model_digests: dict[str, str] = {}
        for raw_role, raw_model_ref in updates.items():
            role_key = str(raw_role or "").strip()
            if not role_key:
                raise ConfigBrokerError("模型角色不能为空。", code="model_role_unknown")
            if role_key in proposed_updates:
                raise ConfigBrokerError("模型角色不能重复。", code="model_role_bundle_duplicate")
            role_definition, _role_label, agent_id = self._role_contract(role_key, before)
            if agent_id:
                raise ConfigBrokerError(
                    "多角色事务只支持 V8OS 内部模型角色。",
                    code="model_role_bundle_agent_unsupported",
                )

            requested_ref = str(raw_model_ref or "").strip()
            if not requested_ref:
                proposed_updates[role_key] = ""
                role_validation[role_key] = {"validated": True, "operation": "unbind"}
                continue

            record = model_control_plane.get_model_record(requested_ref, before)
            if not record:
                raise ConfigBrokerError("目标模型不存在。", code="model_not_found", status_code=404)
            canonical_ref = str(record.get("model_ref") or requested_ref).strip()
            model_row = dict(models_by_ref.get(canonical_ref) or {})
            eligibility = dict(model_row.get("eligibility") or evaluate_model_eligibility(model_row, role=role_key))
            compatibility_record = record
            if not isinstance(record.get("model"), dict):
                compatibility_record = {
                    **record,
                    "model": {
                        "capabilityClass": model_row.get("capabilityClass"),
                        "capabilities": dict(model_row.get("capabilities") or {}),
                        "type": model_row.get("type"),
                    },
                }
            if not eligibility.get("selectable") or not model_control_plane.is_model_compatible(
                role_definition,
                compatibility_record,
            ):
                raise ConfigBrokerError(
                    "目标模型不满足该功能的运行条件。",
                    code="model_role_ineligible",
                    status_code=409,
                )
            proposed_updates[role_key] = canonical_ref
            role_validation[role_key] = {
                "validated": True,
                "operation": "assign",
                "doctor": eligibility,
            }
            assignment_model_digests[role_key] = _digest(
                self._model_record_snapshot(canonical_ref, before)
            )

        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="model_role_bundle",
            target_id=_model_role_bundle_target_id(proposed_updates),
            operation="update",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed={"updates": proposed_updates, "newCredentialRefs": []},
            validation={
                "roles": role_validation,
                "assignmentModelDigests": assignment_model_digests,
            },
        )
        return {
            "ok": True,
            "mode": "role_bundle_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "roles": sorted(proposed_updates),
            "summary": f"已验证 {len(proposed_updates)} 个模型角色变更，尚未写入。",
            "nextAction": "提交配置事务。",
        }

    def prepare_role_assignment(
        self,
        *,
        role: str,
        model_ref: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        config = model_control_plane.get_config()
        role_key = str(role or "").strip()
        role_definition, role_label, agent_id = self._role_contract(role_key, config)
        record = model_control_plane.get_model_record(str(model_ref or "").strip(), config)
        if not record:
            raise ConfigBrokerError("目标模型不存在。", code="model_not_found", status_code=404)
        model_row = next(
            (item for item in model_control_plane.list_models(config) if item.get("modelRef") == record.get("model_ref")),
            None,
        ) or {}
        eligibility = dict(model_row.get("eligibility") or evaluate_model_eligibility(model_row, role=role_key))
        compatibility_record = record
        if not isinstance(record.get("model"), dict):
            compatibility_record = {
                **record,
                "model": {
                    "capabilityClass": model_row.get("capabilityClass"),
                    "capabilities": dict(model_row.get("capabilities") or {}),
                    "type": model_row.get("type"),
                },
            }
        if not eligibility.get("selectable") or not model_control_plane.is_model_compatible(
            role_definition,
            compatibility_record,
        ):
            raise ConfigBrokerError("目标模型不满足该功能的运行条件。", code="model_role_ineligible", status_code=409)
        before = model_control_plane.get_storage_safe_config()
        proposed = {
            "role": role_key,
            "modelRef": str(record.get("model_ref") or model_ref),
            **({"agentId": agent_id} if agent_id else {}),
        }
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="agent_model_role" if agent_id else "model_role",
            target_id=agent_id or role_key,
            operation="assign",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={
                "doctor": eligibility,
                "assignmentModelDigest": _digest(
                    self._model_record_snapshot(str(record.get("model_ref") or model_ref), before)
                ),
            },
        )
        return {
            "ok": True,
            "mode": "role_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": f"已准备把 {role_label} 调整为 {record.get('model_id')}。",
            "nextAction": "提交配置事务。",
        }

    def prepare_media_operation(
        self,
        *,
        operation_kind: str,
        model_ref: str,
        enabled: bool,
        priority: int | None,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        operation = str(operation_kind or "").strip()
        requested_ref = str(model_ref or "").strip()
        if not operation:
            raise ConfigBrokerError("多媒体操作类型不能为空。", code="media_operation_kind_required")
        requested_identity = parse_model_ref(requested_ref)
        if not requested_identity:
            raise ConfigBrokerError("目标模型引用无效。", code="media_model_ref_invalid")

        from runtimes.creative_media.runtime import creative_media_runtime

        preferences = creative_media_runtime.get_model_preferences()
        candidates = [
            dict(item)
            for item in list(preferences.get("connectedOptions") or [])
            if isinstance(item, dict) and str(item.get("operationKind") or "").strip() == operation
        ]
        candidate = next(
            (
                item
                for item in candidates
                if parse_model_ref(str(item.get("modelRef") or "")) == requested_identity
            ),
            None,
        )
        if not candidate:
            raise ConfigBrokerError(
                "该模型没有为目标多媒体操作提供已配置候选。",
                code="media_operation_model_not_found",
                status_code=404,
            )
        readiness = dict(candidate.get("readiness") or {})
        if enabled and (not bool(candidate.get("available")) or not bool(readiness.get("executable"))):
            reasons = ", ".join(str(item) for item in list(readiness.get("reasonCodes") or []) if str(item))
            raise ConfigBrokerError(
                f"目标模型当前不能执行该多媒体操作{f'：{reasons}' if reasons else '。'}",
                code="media_operation_model_ineligible",
                status_code=409,
            )

        before = self._target_config("creative_media_operation")
        before_target = self._target_snapshot("creative_media_operation", operation, before)
        current_selection = next(iter(list(before_target.get("selections") or [])), {})
        normalized_priority = int(
            priority
            if priority is not None
            else current_selection.get("priority", candidate.get("priority", 100))
        )
        if normalized_priority < 1 or normalized_priority > 9999:
            raise ConfigBrokerError(
                "多媒体模型优先级必须在 1 到 9999 之间。",
                code="media_operation_priority_invalid",
            )
        normalized_ref = str(candidate.get("modelRef") or "").strip()
        proposed = {
            "operationKind": operation,
            "modelRef": normalized_ref,
            "enabled": bool(enabled),
            "priority": normalized_priority,
            "candidate": {
                key: deepcopy(candidate.get(key))
                for key in (
                    "candidateId",
                    "modality",
                    "operationKind",
                    "providerId",
                    "modelId",
                    "modelRef",
                    "adapter",
                )
            },
        }
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="creative_media_operation",
            target_id=operation,
            operation="select_model" if enabled else "disable_model",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={
                "doctor": {
                    "available": bool(candidate.get("available")),
                    "readiness": readiness,
                    "source": candidate.get("source"),
                }
            },
        )
        return {
            "ok": True,
            "mode": "media_operation_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": f"已准备把 {normalized_ref} {'启用为' if enabled else '停用于'} {operation}。",
            "nextAction": "提交配置事务。",
        }

    def _credentialize_mcp_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        safe = deepcopy(raw or {"mcpServers": {}})
        servers = dict(safe.get("mcpServers") or {})
        changed = False
        created_refs: list[str] = []
        try:
            for server_name, raw_server in servers.items():
                server = dict(raw_server or {})
                refs = dict(server.get("x-v8-credential-refs") or {})
                for target in ("env", "headers"):
                    values = dict(server.get(target) or {})
                    for key, value in list(values.items()):
                        if value in (None, ""):
                            continue
                        binding_id = f"legacy_{target}_{key}"
                        if binding_id in refs:
                            values.pop(key, None)
                            changed = True
                            continue
                        reference = credential_ref_store.put(str(value), namespace="plugin")
                        created_refs.append(reference)
                        refs[binding_id] = {
                            "secretRef": reference,
                            "target": "header" if target == "headers" else "env",
                            "targetName": str(key),
                        }
                        values.pop(key, None)
                        changed = True
                    if values:
                        server[target] = values
                    else:
                        server.pop(target, None)
                if refs:
                    server["x-v8-credential-refs"] = refs
                servers[str(server_name)] = server
            safe["mcpServers"] = servers
            if changed:
                storage.save_mcp_config(safe)
            return safe
        except Exception:
            for reference in created_refs:
                try:
                    credential_ref_store.delete(reference)
                except Exception:
                    pass
            raise

    def prepare_mcp(
        self,
        *,
        operation: str,
        name: str,
        server: dict[str, Any] | None,
        credential_requirements: list[dict[str, Any]] | None,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        server_name = str(name or "").strip()
        if not server_name:
            raise ConfigBrokerError("MCP server 名称不能为空。", code="mcp_name_required")
        try:
            before = self._credentialize_mcp_config(storage.get_mcp_config() or {"mcpServers": {}})
        except CredentialStoreError as exc:
            raise ConfigBrokerError(str(exc), code="credential_store_unavailable", status_code=503) from exc
        normalized_operation = str(operation or "install").strip().lower()
        if normalized_operation not in {"install", "remove"}:
            raise ConfigBrokerError("MCP 操作只支持 install/remove。", code="mcp_operation_invalid")
        proposed: dict[str, Any] = {"name": server_name, "operation": normalized_operation, "newCredentialRefs": []}
        requirements: list[dict[str, Any]] = []
        if normalized_operation == "install":
            normalized_server = validate_mcp_server_map({"mcpServers": {server_name: dict(server or {})}})[server_name]
            proposed["server"] = normalized_server
            for raw in credential_requirements or []:
                binding_id = str(raw.get("id") or raw.get("targetName") or "").strip()
                target = str(raw.get("target") or "env").strip().lower()
                target_name = str(raw.get("targetName") or "").strip()
                if not binding_id or target not in {"env", "header"} or not target_name:
                    raise ConfigBrokerError("MCP 凭据绑定必须声明 id、target 和 targetName。", code="mcp_credential_binding_invalid")
                requirements.append(
                    {
                        "id": binding_id,
                        "kind": "secret",
                        "label": str(raw.get("label") or target_name),
                        "required": bool(raw.get("required", True)),
                        "binding": {"namespace": "plugin", "target": target, "targetName": target_name},
                    }
                )
        owner = _session_owner(session_id, owner_id)
        if requirements and (not owner or not str(session_id or "").strip() or not str(run_id or "").strip()):
            raise ConfigBrokerError(
                "安全凭据卡只能从已归属用户的活动会话中创建。",
                code="config_ui_action_scope_required",
                status_code=409,
            )
        transaction = self._insert_transaction(
            target_kind="mcp",
            target_id=server_name,
            operation=normalized_operation,
            state="awaiting_secret" if requirements else "ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={"serverValidated": normalized_operation == "install"},
        )
        result: dict[str, Any] = {
            "ok": True,
            "mode": f"mcp_{normalized_operation}_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": f"MCP server `{server_name}` 的{('安装' if normalized_operation == 'install' else '移除')}计划已准备。",
            "nextAction": "提交配置事务。",
        }
        if requirements:
            from core.ui_action_requests import ui_action_request_service

            action = ui_action_request_service.create(
                kind="secret_input",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                title=f"配置 {server_name}",
                description="凭据只写入系统凭据库，不会进入 MCP 配置、对话或日志。",
                target_label=str((server or {}).get("url") or (server or {}).get("command") or server_name),
                fields=requirements,
                handler_type="config_broker_secret",
                handler_ref=transaction["transactionId"],
            )
            result["uiAction"] = action
            result["nextAction"] = "等待用户保存凭据；保存后事务会自动提交。"
        return result

    def attach_credentials_and_commit(
        self,
        transaction_id: str,
        *,
        bindings: list[dict[str, Any]],
        owner_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            transaction = self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)
            if transaction["state"] != "awaiting_secret":
                raise ConfigBrokerError("该配置事务当前不接受凭据。", code="config_transaction_not_awaiting_secret", status_code=409)
            proposed = dict(transaction.get("proposed") or {})
            refs = list(proposed.get("newCredentialRefs") or [])
            refs.extend(str(item.get("secretRef") or "") for item in bindings if item.get("secretRef"))
            proposed["newCredentialRefs"] = sorted({item for item in refs if item})
            if transaction["targetKind"] in {"model", "model_provider"}:
                provider_patch = dict(proposed.get("provider") or {})
                model_binding = next((item for item in bindings if str(item.get("target") or "") == "provider"), None)
                if not model_binding:
                    raise ConfigBrokerError("模型凭据绑定缺失。", code="model_credential_binding_missing")
                provider_patch["credentialRef"] = model_binding["secretRef"]
                provider_patch["credentialSource"] = "os_credential_store"
                proposed["provider"] = provider_patch
            else:
                server = dict(proposed.get("server") or {})
                server_refs = dict(server.get("x-v8-credential-refs") or {})
                for binding in bindings:
                    server_refs[str(binding.get("id") or binding.get("targetName") or uuid.uuid4().hex)] = {
                        "secretRef": binding.get("secretRef"),
                        "target": binding.get("target"),
                        "targetName": binding.get("targetName"),
                    }
                server["x-v8-credential-refs"] = server_refs
                proposed["server"] = server
            self._update_transaction(transaction_id, state="ready_to_commit", proposed_json=_json(proposed))
            return self.commit(transaction_id, owner_id=owner_id, user_confirmed_target=True)

    def _safety_check_model_target(self, proposed: dict[str, Any], *, user_confirmed_target: bool) -> dict[str, Any]:
        provider = dict(proposed.get("provider") or {})
        target = str(provider.get("base_url") or provider.get("baseUrl") or "").strip()
        has_managed_credential = bool(str(provider.get("credentialRef") or "").strip())
        decision = safety_guardian.assess_http_request(
            "POST",
            target,
            headers={"Authorization": "Bearer [managed-credential]"} if has_managed_credential else {},
            runtime_context={
                "source": "config_broker",
                "target": target,
                **({"credentialClass": "api_key"} if has_managed_credential else {}),
            },
        )
        payload = decision.to_payload()
        if decision.is_block():
            raise ConfigBrokerError(decision.reason, code=decision.risk_code or "safety_blocked", status_code=403)
        if decision.is_review() and not (user_confirmed_target and decision.allow_override):
            raise ConfigBrokerError(
                "目标需要用户确认后才能接收凭据。",
                code=decision.risk_code or "safety_review_required",
                status_code=409,
            )
        return {
            "verdict": decision.verdict,
            "riskCode": decision.risk_code,
            "userConfirmedExactTarget": bool(user_confirmed_target and decision.is_review()),
        }

    def _verify_committed_provider_static(
        self,
        provider_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        from core.model_provider_channels import validate_provider_channels

        provider_container = dict((config.get("providers") or {}).get(provider_id) or {})
        if not provider_container:
            return {
                "ok": False,
                "status": "missing_after_commit",
                "summary": "Provider 未能从控制面重新读取。",
                "verifier": "static_runtime_contract",
            }
        provider = dict(provider_container.get("provider") or {})
        try:
            validate_provider_channels(provider)
            base_url = str(provider.get("base_url") or provider.get("baseUrl") or "").strip()
            if base_url:
                _valid_provider_endpoint(base_url)
        except (ConfigBrokerError, ValueError) as exc:
            return {
                "ok": False,
                "status": "provider_transport_invalid",
                "summary": str(exc),
                "verifier": "static_runtime_contract",
            }
        if provider.get("is_enabled", provider.get("isEnabled", True)) is False:
            return {
                "ok": True,
                "status": "configured_disabled",
                "summary": "Provider 配置已写入并保持停用。",
                "verifier": "static_runtime_contract",
            }
        auth_contract = dict(provider.get("authContract") or {})
        credential_ref = str(provider.get("credentialRef") or provider.get("credential_ref") or "").strip()
        # Provider mutations persist an explicit authContract and enforce their
        # credential gate before commit. Older provider records may predate that
        # field and legitimately describe an unauthenticated local endpoint.
        auth_type = str(
            auth_contract.get("type")
            or ("api_key" if credential_ref else "none")
        ).strip().lower()
        if auth_type == "api_key":
            try:
                credential_ready = bool(
                    credential_ref and credential_ref_store.status(credential_ref).configured
                )
            except CredentialStoreError:
                credential_ready = False
            if not credential_ready:
                return {
                    "ok": False,
                    "status": "credential_missing",
                    "summary": "Provider 需要 API Key，但受管凭据不存在或不可读取。",
                    "verifier": "static_runtime_contract",
                }
        elif auth_type == "oauth_file":
            from core.oauth.credentials import resolve_provider_oauth_credential

            try:
                oauth_state = resolve_provider_oauth_credential(
                    provider_id=provider_id,
                    provider_config=provider,
                )
                credential_ready = bool(str(oauth_state.get("credential") or "").strip())
            except Exception:
                credential_ready = False
            if not credential_ready:
                return {
                    "ok": False,
                    "status": "credential_missing",
                    "summary": "Provider 需要 OAuth 授权，但授权文件不存在、不可读取或不含可用凭据。",
                    "verifier": "static_runtime_contract",
                }
        return {
            "ok": True,
            "status": "configured",
            "summary": "Provider 配置已按运行时静态合同重新读取。",
            "verifier": "static_runtime_contract",
        }

    def _verify_committed_model_static(
        self,
        provider_id: str,
        model_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        from core.model_endpoint_binding import build_model_endpoint_binding
        from core.model_provider_channels import validate_provider_channels

        record = model_control_plane.get_model_record(
            make_model_ref(provider_id, model_id),
            config,
        )
        if not record:
            return {
                "ok": False,
                "status": "missing_after_commit",
                "summary": "模型记录未能从控制面重新读取。",
                "verifier": "static_runtime_contract",
            }
        provider = dict(record.get("provider") or {})
        model = dict(record.get("model") or {})
        try:
            validate_provider_channels(provider)
            endpoint = build_model_endpoint_binding(
                provider_id,
                model_id,
                provider,
                model,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "status": "endpoint_contract_invalid",
                "summary": str(exc),
                "verifier": "static_runtime_contract",
            }
        if not endpoint.get("channelId"):
            return {
                "ok": False,
                "status": "endpoint_contract_missing",
                "summary": "模型缺少可解析的 Provider channel。",
                "verifier": "static_runtime_contract",
            }
        provider_check = self._verify_committed_provider_static(provider_id, config)
        if not provider_check.get("ok"):
            return provider_check
        if model.get("isEnabled") is False:
            return {
                "ok": True,
                "status": "configured_disabled",
                "summary": "模型配置已写入并保持停用。",
                "verifier": "static_runtime_contract",
            }
        capability_class = str(model.get("capabilityClass") or "").strip().lower()
        model_type = str(model.get("type") or "TEXT").strip().upper()
        if capability_class == "media_generation" or model_type in {
            "MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D",
        }:
            media_limits = dict(model.get("mediaLimits") or {})
            if not (
                endpoint.get("adapter")
                or media_limits.get("adapter")
                or model.get("parameterProfile")
            ):
                return {
                    "ok": False,
                    "status": "adapter_contract_missing",
                    "summary": "媒体模型缺少 adapter/parameter profile 合同。",
                    "verifier": "static_runtime_contract",
                }
        return {
            "ok": True,
            "status": "configured",
            "summary": "模型配置已按运行时静态合同重新读取。",
            "verifier": "static_runtime_contract",
        }

    def _verify_committed_model(self, proposed: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(proposed.get("providerId") or "")
        model_id = str(proposed.get("modelId") or "")
        model = dict(proposed.get("model") or {})
        record = model_control_plane.get_model_record(make_model_ref(provider_id, model_id))
        if not record:
            return {
                "ok": False,
                "status": "missing_after_commit",
                "summary": "模型记录未能从控制面重新读取。",
                "verifier": "static_runtime_contract",
            }
        provider_record = dict(record.get("provider") or {})
        model_record = dict(record.get("model") or {})
        if (
            provider_record.get("is_enabled") is False
            or provider_record.get("isEnabled") is False
            or model_record.get("isEnabled") is False
        ):
            return {
                "ok": True,
                "status": "configured_disabled",
                "summary": "模型配置已写入并保持停用；未发起真实 Provider 调用。",
                "verifier": "static_runtime_contract",
            }
        model_type = str(model.get("type") or "TEXT").strip().upper()
        capability_class = str(model.get("capabilityClass") or "").strip().lower()
        if model_type in {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D", "EMBEDDING", "RERANK", "RERANKER"} or capability_class in {
            "media_generation",
            "embedding",
            "rerank",
            "reranker",
        }:
            if proposed.get("credentialRequired"):
                credential_ref = str(
                    provider_record.get("credentialRef")
                    or provider_record.get("credential_ref")
                    or ""
                ).strip()
                try:
                    credential_ready = bool(
                        credential_ref and credential_ref_store.status(credential_ref).configured
                    )
                except CredentialStoreError:
                    credential_ready = False
                if not credential_ready:
                    return {
                        "ok": False,
                        "status": "credential_missing",
                        "summary": "Provider 需要 API Key，但受管凭据不存在或不可读取。",
                        "verifier": "static_runtime_contract",
                    }
            endpoint = dict((record.get("model") or {}).get("endpointBinding") or model.get("endpointBinding") or {})
            if capability_class == "media_generation" and not (
                endpoint.get("adapter")
                or dict(model.get("mediaLimits") or {}).get("adapter")
                or model.get("parameterProfile")
            ):
                return {
                    "ok": False,
                    "status": "adapter_contract_missing",
                    "summary": "媒体模型缺少 adapter/parameter profile 合同。",
                    "verifier": "static_runtime_contract",
                }
            return {
                "ok": True,
                "status": "configured",
                "summary": "模型配置已按运行时合同重新读取；真实媒体生成或检索调用需显式 live 验证。",
                "verifier": "static_runtime_contract",
            }
        probe = _get_model_connection_tester().test_model_connection(
            provider_id=provider_id,
            model_id=model_id,
            model_ref=make_model_ref(provider_id, model_id),
        )
        return {
            "ok": bool(probe.get("ok")),
            "status": probe.get("status"),
            "summary": probe.get("message") or probe.get("summary"),
            "verifier": "chat_connection_test",
        }

    def commit(self, transaction_id: str, *, owner_id: str = "", user_confirmed_target: bool = False) -> dict[str, Any]:
        with self._lock:
            transaction = self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)
            state = str(transaction.get("state") or "")
            if state == "committed":
                return {"ok": True, "mode": "commit", **self.get_transaction(transaction_id, owner_id=owner_id)}
            if state != "ready_to_commit":
                raise ConfigBrokerError("配置事务尚未达到可提交状态。", code="config_transaction_not_ready", status_code=409)
            try:
                self._assert_target_revision(transaction)
            except ConfigBrokerError as exc:
                if exc.code != "config_transaction_stale":
                    raise
                cleanup_errors = self._cleanup_new_credential_refs(transaction)
                next_state = "recovery_required" if cleanup_errors else "conflict"
                self._update_transaction(
                    transaction_id,
                    state=next_state,
                    error_code="config_credential_cleanup_failed" if cleanup_errors else exc.code,
                    error_message=(
                        "目标已变化，且新建凭据尚未清理完成。"
                        if cleanup_errors
                        else "目标配置已在计划后发生变化；事务未提交，也未覆盖新配置。"
                    ),
                    result_json=_json({"credentialCleanupErrors": cleanup_errors}),
                )
                return {
                    "ok": False,
                    "mode": "commit",
                    "state": next_state,
                    "transactionId": transaction_id,
                    "summary": (
                        "目标配置已变化；事务未写入，也未覆盖较新的配置。"
                        if not cleanup_errors
                        else "目标配置已变化；新建凭据清理需要重试。"
                    ),
                    "error": {
                        "code": "config_credential_cleanup_failed" if cleanup_errors else exc.code,
                        "message": str(exc),
                    },
                }
            proposed = dict(transaction.get("proposed") or {})
            validation = dict(transaction.get("validation") or {})
            target_mutated = False
            self._update_transaction(transaction_id, state="committing", error_code=None, error_message=None)

            def _capture_working_target(_config: dict[str, Any]) -> None:
                # Persisted, storage-safe state is authoritative. The return
                # value of ModelControlPlane.save_config may still contain a
                # materialized or empty credential field, which cannot serve as
                # an exact rollback revision.
                persisted_config = self._target_config(transaction["targetKind"])
                working_target = self._target_snapshot(
                    transaction["targetKind"],
                    transaction["targetId"],
                    persisted_config,
                )
                validation["targetWorkingDigest"] = _digest(working_target)
                self._update_transaction(transaction_id, validation_json=_json(validation))

            def _capture_planned_target(config: dict[str, Any]) -> None:
                planned_target = self._target_snapshot(
                    transaction["targetKind"],
                    transaction["targetId"],
                    config,
                )
                validation["targetPlannedDigest"] = _digest(planned_target)
                self._update_transaction(transaction_id, validation_json=_json(validation))

            try:
                if transaction["targetKind"] == "model_snapshot_restore":
                    candidate = deepcopy(dict(proposed.get("snapshot") or {}))
                    expected_snapshot_digest = str(proposed.get("sourceSnapshotDigest") or "")
                    if (
                        not candidate
                        or _digest(_model_snapshot_authority_projection(candidate)) != expected_snapshot_digest
                    ):
                        raise ConfigBrokerError(
                            "模型恢复快照校验失败。",
                            code="model_snapshot_digest_invalid",
                            status_code=409,
                        )
                    already_current = bool(proposed.get("alreadyCurrent"))
                    if already_current:
                        current_snapshot = self._target_snapshot(
                            "model_snapshot_restore",
                            transaction["targetId"],
                            self._target_config("model_snapshot_restore"),
                        )
                        if _digest(current_snapshot) != expected_snapshot_digest:
                            raise ConfigBrokerError(
                                "模型恢复核验期间配置发生变化。",
                                code="config_transaction_stale",
                                status_code=409,
                            )
                    else:
                        def _restore_model_snapshot(current: dict[str, Any]) -> dict[str, Any]:
                            self._assert_target_revision_in_config(transaction, current)
                            _capture_planned_target(candidate)
                            return deepcopy(candidate)

                        saved_model_config = model_control_plane.mutate_config(_restore_model_snapshot)
                        target_mutated = True
                        _capture_working_target(saved_model_config)
                        persisted_snapshot = self._target_snapshot(
                            "model_snapshot_restore",
                            transaction["targetId"],
                            self._target_config("model_snapshot_restore"),
                        )
                        if _digest(persisted_snapshot) != expected_snapshot_digest:
                            raise ConfigBrokerError(
                                "模型恢复后的配置投影不一致。",
                                code="model_snapshot_projection_mismatch",
                                status_code=409,
                            )
                    public_result = {
                        "sourceTransactionId": str(proposed.get("sourceTransactionId") or ""),
                        "recovered": True,
                        "alreadyCurrent": already_current,
                    }
                elif transaction["targetKind"] == "model_provider":
                    provider_id = str(proposed.get("providerId") or "").strip()
                    operation = str(proposed.get("operation") or transaction.get("operation") or "").strip()
                    provider_patch = dict(proposed.get("provider") or {})
                    if operation == "upsert" and provider_patch.get("credentialRef"):
                        validation["safety"] = self._safety_check_model_target(
                            {"provider": provider_patch},
                            user_confirmed_target=bool(user_confirmed_target),
                        )

                    def _change_provider(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        providers = dict(config.get("providers") or {})
                        if operation == "remove":
                            if provider_id not in providers:
                                raise ConfigBrokerError("Provider 不存在。", code="provider_not_found", status_code=404)
                            if _provider_dependencies(config, provider_id):
                                raise ConfigBrokerError(
                                    "Provider 在提交期间被角色或 Subagent 使用；删除已停止。",
                                    code="provider_still_bound",
                                    status_code=409,
                                )
                            providers.pop(provider_id, None)
                        else:
                            existing_provider = dict(providers.get(provider_id) or {})
                            providers[provider_id] = {
                                "provider": deepcopy(provider_patch),
                                "models": deepcopy(dict(existing_provider.get("models") or {})),
                            }
                        config["providers"] = providers
                        _capture_planned_target(config)
                        return config

                    saved_model_config = model_control_plane.mutate_config(_change_provider)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    projected = self._target_snapshot("model_provider", provider_id, saved_model_config)
                    if operation == "remove" and projected.get("exists"):
                        raise ConfigBrokerError("Provider 删除投影不一致。", code="provider_projection_mismatch")
                    if operation == "upsert":
                        projected_provider = dict((projected.get("value") or {}).get("provider") or {})
                        if not projected.get("exists") or any(
                            projected_provider.get(key) != value
                            for key, value in provider_patch.items()
                        ):
                            raise ConfigBrokerError("Provider 写入投影不一致。", code="provider_projection_mismatch")
                        self._update_transaction(transaction_id, state="verifying", validation_json=_json(validation))
                        provider_probe = self._verify_committed_provider_static(provider_id, saved_model_config)
                        model_probes = [
                            self._verify_committed_model_static(provider_id, str(model_id), saved_model_config)
                            for model_id in dict((projected.get("value") or {}).get("models") or {})
                        ]
                        validation["runtimeContract"] = {
                            "ok": bool(provider_probe.get("ok")) and all(bool(item.get("ok")) for item in model_probes),
                            "provider": provider_probe,
                            "models": model_probes,
                        }
                        if not validation["runtimeContract"]["ok"]:
                            raise ConfigBrokerError(
                                "Provider 运行时静态验证失败，已准备回滚。",
                                code="provider_runtime_validation_failed",
                                status_code=409,
                            )
                    public_result = {
                        "providerId": provider_id,
                        "removed": operation == "remove",
                    }
                elif transaction["targetKind"] == "model_binding":
                    provider_id = str(proposed.get("providerId") or "")
                    model_id = str(proposed.get("modelId") or "")
                    source_provider_id = str(proposed.get("sourceProviderId") or provider_id)
                    source_model_id = str(proposed.get("sourceModelId") or model_id)

                    def _binding_precondition(current: dict[str, Any]) -> None:
                        self._assert_target_revision_in_config(transaction, current)
                        if _binding_removal_dependencies(
                            current,
                            provider_id=provider_id,
                            model_id=model_id,
                            source_provider_id=source_provider_id,
                            source_model_id=source_model_id,
                            replace_provider_models=bool(proposed.get("replaceProviderModels")),
                        ):
                            raise ConfigBrokerError(
                                "模型在提交期间被角色或 Subagent 使用；移动或替换已停止。",
                                code="model_binding_still_bound",
                                status_code=409,
                            )
                    result = model_control_plane.upsert_model_record(
                        provider_id=provider_id,
                        model_id=model_id,
                        model_patch=dict(proposed.get("model") or {}),
                        source_provider_id=source_provider_id,
                        source_model_id=source_model_id,
                        source=str(proposed.get("source") or "manual"),
                        replace_provider_models=bool(proposed.get("replaceProviderModels")),
                        precondition=_binding_precondition,
                        before_persist=_capture_planned_target,
                    )
                    saved_model_config = dict(result.get("config") or {})
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    target_models = dict(
                        ((saved_model_config.get("providers") or {}).get(provider_id) or {}).get("models") or {}
                    )
                    if model_id not in target_models:
                        raise ConfigBrokerError("模型绑定写入投影不一致。", code="model_binding_projection_mismatch")
                    if (source_provider_id, source_model_id) != (provider_id, model_id):
                        source_models = dict(
                            ((saved_model_config.get("providers") or {}).get(source_provider_id) or {}).get("models") or {}
                        )
                        if source_model_id in source_models:
                            raise ConfigBrokerError("来源模型绑定未移除。", code="model_binding_projection_mismatch")
                    self._update_transaction(transaction_id, state="verifying", validation_json=_json(validation))
                    model_probe = self._verify_committed_model_static(
                        provider_id,
                        model_id,
                        saved_model_config,
                    )
                    validation["runtimeContract"] = model_probe
                    if not model_probe.get("ok"):
                        raise ConfigBrokerError(
                            "模型绑定运行时静态验证失败，已准备回滚。",
                            code="model_binding_runtime_validation_failed",
                            status_code=409,
                        )
                    public_result = {
                        "providerId": provider_id,
                        "modelId": model_id,
                        "modelRef": make_model_ref(provider_id, model_id),
                    }
                elif transaction["targetKind"] == "model":
                    validation["safety"] = self._safety_check_model_target(
                        proposed,
                        user_confirmed_target=bool(user_confirmed_target or proposed.get("credentialReuseAuthorized")),
                    )
                    result = model_control_plane.upsert_provider_model_records(
                        provider_id=str(proposed.get("providerId") or ""),
                        provider_patch=dict(proposed.get("provider") or {}),
                        model_id=str(proposed.get("modelId") or ""),
                        model_patch=dict(proposed.get("model") or {}),
                        source=str(proposed.get("source") or "agent_proposed"),
                        replace_provider_models=bool(proposed.get("replaceProviderModels")),
                        precondition=lambda current: self._assert_target_revision_in_config(transaction, current),
                        before_persist=_capture_planned_target,
                    )
                    target_mutated = True
                    _capture_working_target(dict(result.get("config") or {}))
                    self._update_transaction(transaction_id, state="verifying", validation_json=_json(validation))
                    probe = self._verify_committed_model(proposed)
                    validation["connection"] = {
                        "ok": bool(probe.get("ok")),
                        "status": probe.get("status"),
                        "summary": probe.get("summary"),
                        "verifier": probe.get("verifier"),
                    }
                    if not probe.get("ok"):
                        raise ConfigBrokerError("模型连接验证失败，已准备回滚。", code="model_connection_validation_failed", status_code=409)
                    public_result = {
                        "providerId": proposed.get("providerId"),
                        "modelId": proposed.get("modelId"),
                        "modelRef": make_model_ref(str(proposed.get("providerId") or ""), str(proposed.get("modelId") or "")),
                        "verified": True,
                    }
                elif transaction["targetKind"] == "model_record":
                    provider_id = str(proposed.get("providerId") or "")
                    model_id = str(proposed.get("modelId") or "")
                    operation = str(proposed.get("operation") or transaction.get("operation") or "")

                    def _change_model_record(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        providers = dict(config.get("providers") or {})
                        provider_data = dict(providers.get(provider_id) or {})
                        models = dict(provider_data.get("models") or {})
                        if model_id not in models:
                            raise ConfigBrokerError("目标模型不存在。", code="model_not_found", status_code=404)
                        if operation == "remove":
                            references = _model_dependencies(config, (provider_id, model_id))
                            if references:
                                raise ConfigBrokerError(
                                    "模型在提交期间被角色或 Subagent 绑定；删除已停止。",
                                    code="model_still_bound",
                                    status_code=409,
                                )
                            models.pop(model_id, None)
                        else:
                            models[model_id] = {
                                **dict(models.get(model_id) or {}),
                                "isEnabled": bool(proposed.get("enabled")),
                            }
                        providers[provider_id] = {**provider_data, "models": models}
                        config["providers"] = providers
                        return config

                    saved_model_config = model_control_plane.mutate_config(_change_model_record)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    after_record = self._target_snapshot(
                        "model_record",
                        make_model_ref(provider_id, model_id),
                        saved_model_config,
                    )
                    if operation == "remove" and after_record.get("modelExists"):
                        raise ConfigBrokerError("模型删除投影不一致。", code="model_record_projection_mismatch")
                    if operation != "remove" and (
                        not after_record.get("modelExists")
                        or bool((after_record.get("model") or {}).get("isEnabled")) != bool(proposed.get("enabled"))
                    ):
                        raise ConfigBrokerError("模型启停投影不一致。", code="model_record_projection_mismatch")
                    public_result = {
                        "modelRef": make_model_ref(provider_id, model_id),
                        "operation": operation,
                        **({"enabled": bool(proposed.get("enabled"))} if operation != "remove" else {}),
                    }
                elif transaction["targetKind"] == "model_role":
                    def _assign_role(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        if transaction.get("operation") != "unbind":
                            expected_model_digest = str(validation.get("assignmentModelDigest") or "")
                            current_model = self._model_record_snapshot(str(proposed.get("modelRef") or ""), config)
                            if not expected_model_digest or _digest(current_model) != expected_model_digest:
                                raise ConfigBrokerError("目标模型在角色绑定期间发生变化；请重新准备。", code="config_transaction_stale", status_code=409)
                        roles = dict(config.get("roles") or {})
                        roles[str(proposed.get("role") or "")] = (
                            ""
                            if transaction.get("operation") == "unbind"
                            else str(proposed.get("modelRef") or "")
                        )
                        config["roles"] = roles
                        return config

                    saved_model_config = model_control_plane.mutate_config(_assign_role)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    public_result = {
                        "role": proposed.get("role"),
                        "modelRef": proposed.get("modelRef"),
                        "unbound": transaction.get("operation") == "unbind",
                    }
                elif transaction["targetKind"] == "model_role_bundle":
                    role_updates = {
                        str(role or "").strip(): str(model_ref or "").strip()
                        for role, model_ref in dict(proposed.get("updates") or {}).items()
                        if str(role or "").strip()
                    }
                    assignment_model_digests = dict(validation.get("assignmentModelDigests") or {})

                    def _assign_role_bundle(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        for role_key, model_ref in role_updates.items():
                            if not model_ref:
                                continue
                            expected_model_digest = str(assignment_model_digests.get(role_key) or "")
                            current_model = self._model_record_snapshot(model_ref, config)
                            if not expected_model_digest or _digest(current_model) != expected_model_digest:
                                raise ConfigBrokerError(
                                    "目标模型在多角色绑定期间发生变化；请重新准备。",
                                    code="config_transaction_stale",
                                    status_code=409,
                                )
                        roles = dict(config.get("roles") or {})
                        roles.update(role_updates)
                        config["roles"] = roles
                        return config

                    saved_model_config = model_control_plane.mutate_config(_assign_role_bundle)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    saved_roles = dict(saved_model_config.get("roles") or {})
                    if any(
                        str(saved_roles.get(role_key) or "").strip() != model_ref
                        for role_key, model_ref in role_updates.items()
                    ):
                        raise ConfigBrokerError(
                            "多角色绑定提交后的投影不一致。",
                            code="model_role_bundle_projection_mismatch",
                        )
                    public_result = {
                        "roles": {
                            role_key: str(saved_roles.get(role_key) or "").strip()
                            for role_key in role_updates
                        },
                        "updated": True,
                    }
                elif transaction["targetKind"] == "agent_model_role":
                    def _assign_agent(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        if transaction.get("operation") != "unbind":
                            expected_model_digest = str(validation.get("assignmentModelDigest") or "")
                            current_model = self._model_record_snapshot(str(proposed.get("modelRef") or ""), config)
                            if not expected_model_digest or _digest(current_model) != expected_model_digest:
                                raise ConfigBrokerError("目标模型在 Agent 绑定期间发生变化；请重新准备。", code="config_transaction_stale", status_code=409)
                        bindings = dict(config.get("bindings") or {})
                        agents = dict(bindings.get("agents") or {})
                        agent_id = str(proposed.get("agentId") or "")
                        if transaction.get("operation") == "unbind":
                            agents.pop(agent_id, None)
                        else:
                            agents[agent_id] = {"model_id": str(proposed.get("modelRef") or "")}
                        bindings["agents"] = agents
                        config["bindings"] = bindings
                        return config

                    saved_model_config = model_control_plane.mutate_config(_assign_agent)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    public_result = {
                        "agentId": proposed.get("agentId"),
                        "modelRef": proposed.get("modelRef"),
                        "unbound": transaction.get("operation") == "unbind",
                    }
                elif transaction["targetKind"] == "model_policy_bundle":
                    sections = [
                        key
                        for key in ("governance", "routingPolicies", "roleParameters")
                        if key in proposed
                    ]

                    def _update_model_policy(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        for key in sections:
                            config[key] = deepcopy(proposed.get(key) or {})
                        return config

                    saved_model_config = model_control_plane.mutate_config(_update_model_policy)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    public_result = {"sections": sections, "updated": True}
                elif transaction["targetKind"] == "creative_media_operation":
                    operation_kind = str(proposed.get("operationKind") or "").strip()
                    model_ref = str(proposed.get("modelRef") or "").strip()
                    enabled = bool(proposed.get("enabled"))
                    priority = int(proposed.get("priority") or 100)
                    candidate = dict(proposed.get("candidate") or {})
                    now = utc_now_iso()

                    def _select_media_model(current: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, current)
                        selections = [
                            deepcopy(item)
                            for item in list(current.get("selections") or [])
                            if isinstance(item, dict) and str(item.get("operationKind") or "").strip() != operation_kind
                        ]
                        models = [
                            deepcopy(item)
                            for item in list(current.get("models") or [])
                            if isinstance(item, dict) and str(item.get("operationKind") or "").strip() != operation_kind
                        ]
                        selections.append(
                            {
                                "operationKind": operation_kind,
                                "modelRefs": [model_ref],
                                "enabled": enabled,
                                "priority": priority,
                                "updatedAt": now,
                            }
                        )
                        models.append(
                            {
                                **candidate,
                                "enabled": enabled,
                                "priority": priority,
                                "updatedAt": now,
                            }
                        )
                        return {
                            **current,
                            "version": 1,
                            "updatedAt": now,
                            "selections": selections,
                            "models": models,
                        }

                    saved_preferences = storage.mutate_json(
                        _CREATIVE_MEDIA_PREFERENCES_FILE,
                        _select_media_model,
                    )
                    target_mutated = True
                    _capture_working_target(saved_preferences)

                    from runtimes.creative_media.runtime import creative_media_runtime

                    projected = creative_media_runtime.get_model_preferences()
                    operation_row = next(
                        (
                            dict(item)
                            for item in list(projected.get("operationRows") or [])
                            if str(item.get("operationKind") or "").strip() == operation_kind
                        ),
                        {},
                    )
                    selected_refs = [str(item) for item in list(operation_row.get("selectedModelRefs") or [])]
                    execution = dict((projected.get("executionProjection") or {}).get(operation_kind) or {})
                    if model_ref not in selected_refs or bool(operation_row.get("enabled")) != enabled:
                        raise ConfigBrokerError(
                            "多媒体操作模型投影与提交值不一致。",
                            code="media_operation_projection_mismatch",
                            status_code=409,
                        )
                    if enabled and str(execution.get("status") or "") != "ready":
                        raise ConfigBrokerError(
                            "多媒体操作模型提交后仍不可执行。",
                            code="media_operation_not_ready_after_commit",
                            status_code=409,
                        )
                    validation["executionProjection"] = {
                        "status": execution.get("status"),
                        "configuredModelRefs": list(execution.get("configuredModelRefs") or []),
                    }
                    public_result = {
                        "operationKind": operation_kind,
                        "modelRef": model_ref,
                        "enabled": enabled,
                        "priority": priority,
                        "executionStatus": execution.get("status"),
                    }
                elif transaction["targetKind"] == "model_catalog_recovery":
                    catalog = _get_model_provider_catalog()
                    if transaction.get("operation") == "finalize_recovery":
                        recovery = _catalog_mutation_with_digest(
                            catalog.finalize_managed_recovery,
                            expected_current_digest=str(proposed.get("managedDigest") or ""),
                            expected_rejected_digest=str(proposed.get("rejectedDigest") or ""),
                        )
                    else:
                        recovery = _catalog_mutation_with_digest(
                            catalog.recover_managed_from_backup,
                            expected_current_digest=str(proposed.get("managedDigest") or ""),
                            expected_backup_digest=str(proposed.get("backupDigest") or ""),
                        )
                    target_mutated = True
                    _capture_working_target(recovery)
                    if not recovery.get("managedValid") or (
                        transaction.get("operation") != "finalize_recovery"
                        and str(recovery.get("managedDigest") or "") != str(proposed.get("backupDigest") or "")
                    ) or (transaction.get("operation") == "finalize_recovery" and recovery.get("rejectedExists")):
                        raise ConfigBrokerError(
                            "managed 目录备份恢复校验失败。",
                            code="catalog_recovery_projection_mismatch",
                            status_code=409,
                        )
                    validation["catalogRecovery"] = {
                        "managedValid": True,
                        "originalIsolated": bool(recovery.get("rejectedExists")),
                        "managedWorkingDigest": str(recovery.get("managedDigest") or ""),
                    }
                    public_result = {
                        "recovered": transaction.get("operation") != "finalize_recovery",
                        "finalized": transaction.get("operation") == "finalize_recovery",
                        "originalIsolated": bool(recovery.get("rejectedExists")),
                    }
                elif transaction["targetKind"] == "model_catalog_provider":
                    provider_id = str(proposed.get("providerId") or "").strip()
                    catalog = _get_model_provider_catalog()
                    expected_managed_digest = str((validation.get("catalog") or {}).get("managedDigest") or "")
                    if transaction.get("operation") == "catalog_delete":
                        if not _catalog_mutation_with_digest(
                            catalog.delete_managed_provider,
                            provider_id,
                            expected_current_digest=expected_managed_digest,
                        ):
                            raise ConfigBrokerError(
                                "managed 预置已不存在。",
                                code="catalog_managed_provider_not_found",
                                status_code=404,
                            )
                        target_mutated = True
                        managed_config = catalog.load_managed()
                        validation.setdefault("catalog", {})["managedWorkingDigest"] = _managed_catalog_digest(catalog)
                        _capture_working_target(managed_config)
                        if catalog.get_managed_provider(provider_id) is not None:
                            raise ConfigBrokerError(
                                "managed 预置删除投影不一致。",
                                code="catalog_projection_mismatch",
                                status_code=409,
                            )
                        validation["catalogProjection"] = {"isManaged": False, "deleted": True}
                        public_result = {"providerId": provider_id, "managed": False, "deleted": True}
                    else:
                        saved_provider = _catalog_mutation_with_digest(
                            catalog.upsert_managed_provider,
                            dict(proposed.get("provider") or {}),
                            expected_current_digest=expected_managed_digest,
                        )
                        target_mutated = True
                        managed_config = catalog.load_managed()
                        validation.setdefault("catalog", {})["managedWorkingDigest"] = _managed_catalog_digest(catalog)
                        _capture_working_target(managed_config)
                        effective_provider = catalog.get_provider(provider_id)
                        if not effective_provider or not bool(effective_provider.get("isManaged")):
                            raise ConfigBrokerError(
                                "managed 预置写入后未进入有效目录投影。",
                                code="catalog_projection_mismatch",
                                status_code=409,
                            )
                        validation["catalogProjection"] = {
                            "isManaged": True,
                            "effectiveModelIds": [
                                str(item.get("id") or "")
                                for item in list(effective_provider.get("models") or [])
                                if isinstance(item, dict)
                            ],
                        }
                        public_result = {
                            "providerId": provider_id,
                            "managed": True,
                            "patchedModelIds": [
                                str(item.get("id") or "")
                                for item in list(saved_provider.get("models") or [])
                                if isinstance(item, dict)
                            ],
                        }
                elif transaction["targetKind"] == "model_catalog_custom_provider":
                    provider_id = str(proposed.get("providerId") or "").strip()
                    catalog = _get_model_provider_catalog()
                    expected_custom_digest = str((validation.get("catalog") or {}).get("customDigest") or "")
                    if not _catalog_mutation_with_digest(
                        catalog.delete_custom_provider,
                        provider_id,
                        expected_current_digest=expected_custom_digest,
                        before_persist=_capture_planned_target,
                    ):
                        raise ConfigBrokerError(
                            "Custom Provider 不存在。",
                            code="catalog_custom_provider_not_found",
                            status_code=404,
                        )
                    target_mutated = True
                    custom_config = catalog.load_custom()
                    validation.setdefault("catalog", {})["customWorkingDigest"] = catalog.custom_digest()
                    _capture_working_target(custom_config)
                    if self._target_snapshot(
                        "model_catalog_custom_provider",
                        provider_id,
                        custom_config,
                    ).get("exists"):
                        raise ConfigBrokerError("Custom Provider 删除投影不一致。", code="catalog_projection_mismatch")
                    public_result = {"providerId": provider_id, "deleted": True}
                elif transaction["targetKind"] == "mcp":
                    server_name = str(proposed.get("name") or "")

                    def _mutate_mcp(current: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, current)
                        servers = dict(current.get("mcpServers") or {})
                        if transaction["operation"] == "install":
                            servers[server_name] = deepcopy(dict(proposed.get("server") or {}))
                        else:
                            servers.pop(server_name, None)
                        current["mcpServers"] = servers
                        return current

                    saved_mcp = storage.mutate_mcp_config(_mutate_mcp)
                    target_mutated = True
                    _capture_working_target(saved_mcp)
                    request_mcp_inventory_refresh(reason="config_broker_commit")
                    public_result = {
                        "status": "success",
                        "installedServers": [server_name] if transaction["operation"] == "install" else [],
                        "removedServer": server_name if transaction["operation"] == "remove" else None,
                        "serverCount": len(dict(saved_mcp.get("mcpServers") or {})),
                        "refreshRequested": True,
                    }
                    validation["runtimeRefresh"] = {"requested": bool(public_result.get("refreshRequested"))}
                else:
                    raise ConfigBrokerError("不支持的配置事务目标。", code="config_transaction_target_invalid")
                target_after = self._target_snapshot(
                    transaction["targetKind"],
                    transaction["targetId"],
                    self._target_config(transaction["targetKind"]),
                )
                if target_mutated:
                    validation.setdefault("targetWorkingDigest", _digest(target_after))
                validation["targetAfterDigest"] = _digest(target_after)
                committed_at = utc_now_iso()
                self._update_transaction(
                    transaction_id,
                    state="committed",
                    validation_json=_json(validation),
                    result_json=_json(public_result),
                    committed_at=committed_at,
                )
                return {
                    "ok": True,
                    "mode": "commit",
                    "state": "committed",
                    "transactionId": transaction_id,
                    "summary": "配置已校验、提交并记录恢复点。",
                    "result": public_result,
                    "validation": validation,
                }
            except Exception as exc:
                code = exc.code if isinstance(exc, ConfigBrokerError) else "config_commit_failed"
                message = str(exc)
                if (
                    transaction.get("targetKind") in {
                        "model_catalog_provider",
                        "model_catalog_custom_provider",
                        "model_catalog_recovery",
                    }
                    and not isinstance(exc, ConfigBrokerError)
                    and "digest" in message.lower()
                ):
                    code = "config_transaction_stale"
                if code == "config_transaction_stale" and not target_mutated:
                    cleanup_errors = self._cleanup_new_credential_refs(transaction)
                    next_state = "recovery_required" if cleanup_errors else "conflict"
                    self._update_transaction(
                        transaction_id,
                        state=next_state,
                        error_code="config_credential_cleanup_failed" if cleanup_errors else code,
                        error_message=(
                            "目标已变化，且新建凭据尚未清理完成。"
                            if cleanup_errors
                            else message
                        ),
                        result_json=_json({"credentialCleanupErrors": cleanup_errors}),
                    )
                    return {
                        "ok": False,
                        "mode": "commit",
                        "state": next_state,
                        "transactionId": transaction_id,
                        "summary": (
                            "目标配置已变化；事务未写入，也未覆盖较新的配置。"
                            if not cleanup_errors
                            else "目标配置已变化；新建凭据清理需要重试。"
                        ),
                        "error": {
                            "code": "config_credential_cleanup_failed" if cleanup_errors else code,
                            "message": message,
                        },
                    }
                self._update_transaction(transaction_id, state="rolling_back", error_code=code, error_message=message)
                expected_digest = str(
                    validation.get("targetWorkingDigest" if target_mutated else "targetBeforeDigest") or ""
                ).strip()
                rollback = self._restore_snapshot(
                    transaction,
                    enforce_after_digest=True,
                    expected_current_digest=expected_digest,
                )
                state = (
                    "rolled_back"
                    if rollback.get("ok")
                    else ("conflict" if rollback.get("conflict") else "recovery_required")
                )
                self._update_transaction(
                    transaction_id,
                    state=state,
                    result_json=_json({"rollback": rollback}),
                    rolled_back_at=utc_now_iso() if rollback.get("ok") else None,
                )
                return {
                    "ok": False,
                    "mode": "commit",
                    "state": state,
                    "transactionId": transaction_id,
                    "summary": "配置提交失败，已回滚到提交前状态。" if rollback.get("ok") else "配置提交失败且自动恢复未完成，需要人工检查。",
                    "error": {"code": code, "message": message},
                    "rollback": rollback,
                }

    def _restore_snapshot(
        self,
        transaction: dict[str, Any],
        *,
        enforce_after_digest: bool = False,
        expected_current_digest: str = "",
    ) -> dict[str, Any]:
        errors: list[str] = []
        conflict = False
        target_restored = False
        target_kind = str(transaction.get("targetKind") or "")
        proposed = dict(transaction.get("proposed") or {})
        target_id = str(transaction.get("targetId") or proposed.get("name") or "")
        validation = dict(transaction.get("validation") or {})
        expected_after = str(
            expected_current_digest or validation.get("targetAfterDigest") or ""
        ).strip()
        before_config = dict(transaction.get("before") or {})
        before_target = self._target_snapshot(target_kind, target_id, before_config)
        before_digest = _digest(before_target)
        current_config = self._target_config(target_kind)
        current_target = self._target_snapshot(target_kind, target_id, current_config)
        target_already_restored = _digest(current_target) == before_digest

        def _assert_restore_revision(current: dict[str, Any]) -> None:
            if not enforce_after_digest:
                return
            current_snapshot = self._target_snapshot(target_kind, target_id, current)
            if not expected_after or _digest(current_snapshot) != expected_after:
                raise ConfigBrokerError(
                    "目标配置在提交后再次变化；为避免覆盖新配置，自动撤销已停止。",
                    code="config_rollback_conflict",
                    status_code=409,
                )
        try:
            if target_already_restored:
                target_restored = True
                if target_kind == "mcp":
                    request_mcp_inventory_refresh(reason="config_broker_rollback")
            elif target_kind in {
                "model",
                "model_record",
                "model_provider",
                "model_binding",
                "model_role",
                "model_role_bundle",
                "agent_model_role",
                "model_snapshot_restore",
            }:
                def _restore_model_config(config: dict[str, Any]) -> dict[str, Any]:
                    _assert_restore_revision(config)
                    if target_kind == "model_snapshot_restore":
                        return deepcopy(before_config)
                    if target_kind in {"model", "model_record"}:
                        identity = parse_model_ref(target_id)
                        if not identity:
                            raise ValueError("invalid model target")
                        provider_id, model_id = identity
                        providers = dict(config.get("providers") or {})
                        if not before_target.get("providerExists"):
                            providers.pop(provider_id, None)
                        else:
                            current_provider = dict(providers.get(provider_id) or {})
                            providers[provider_id] = {
                                **current_provider,
                                "provider": deepcopy(before_target.get("provider") or {}),
                                "models": deepcopy(dict(before_target.get("providerModels") or {})),
                            }
                        config["providers"] = providers
                    elif target_kind == "model_provider":
                        providers = dict(config.get("providers") or {})
                        if before_target.get("exists"):
                            providers[target_id] = deepcopy(before_target.get("value") or {})
                        else:
                            providers.pop(target_id, None)
                        config["providers"] = providers
                    elif target_kind == "model_binding":
                        providers = dict(config.get("providers") or {})
                        for provider_id, snapshot in dict(before_target.get("providers") or {}).items():
                            if dict(snapshot or {}).get("exists"):
                                providers[provider_id] = deepcopy(dict(snapshot or {}).get("value") or {})
                            else:
                                providers.pop(provider_id, None)
                        config["providers"] = providers
                    elif target_kind == "model_role_bundle":
                        roles = dict(config.get("roles") or {})
                        for role_key, role_snapshot in dict(before_target.get("roles") or {}).items():
                            if dict(role_snapshot or {}).get("exists"):
                                roles[role_key] = dict(role_snapshot or {}).get("value")
                            else:
                                roles.pop(role_key, None)
                        config["roles"] = roles
                    elif target_kind == "model_role":
                        roles = dict(config.get("roles") or {})
                        if before_target.get("exists"):
                            roles[target_id] = before_target.get("value")
                        else:
                            roles.pop(target_id, None)
                        config["roles"] = roles
                    else:
                        bindings = dict(config.get("bindings") or {})
                        agents = dict(bindings.get("agents") or {})
                        if before_target.get("exists"):
                            agents[target_id] = deepcopy(before_target.get("value"))
                        else:
                            agents.pop(target_id, None)
                        bindings["agents"] = agents
                        config["bindings"] = bindings
                    return config

                model_control_plane.mutate_config(_restore_model_config)
                target_restored = True
            elif target_kind == "model_policy_bundle":
                def _restore_model_policy(config: dict[str, Any]) -> dict[str, Any]:
                    _assert_restore_revision(config)
                    config["governance"] = deepcopy(before_target.get("governance") or {})
                    config["routingPolicies"] = deepcopy(before_target.get("routingPolicies") or {})
                    config["roleParameters"] = deepcopy(before_target.get("roleParameters") or {})
                    return config

                model_control_plane.mutate_config(_restore_model_policy)
                target_restored = True
            elif target_kind == "mcp":
                def _restore_mcp_config(current: dict[str, Any]) -> dict[str, Any]:
                    _assert_restore_revision(current)
                    servers = dict(current.get("mcpServers") or {})
                    if before_target.get("exists"):
                        servers[target_id] = deepcopy(before_target.get("value") or {})
                    else:
                        servers.pop(target_id, None)
                    current["mcpServers"] = servers
                    return current

                storage.mutate_mcp_config(_restore_mcp_config)
                request_mcp_inventory_refresh(reason="config_broker_rollback")
                target_restored = True
            elif target_kind == "creative_media_operation":
                def _restore_media_operation(current: dict[str, Any]) -> dict[str, Any]:
                    _assert_restore_revision(current)
                    selections = [
                        deepcopy(item)
                        for item in list(current.get("selections") or [])
                        if isinstance(item, dict) and str(item.get("operationKind") or "").strip() != target_id
                    ]
                    models = [
                        deepcopy(item)
                        for item in list(current.get("models") or [])
                        if isinstance(item, dict) and str(item.get("operationKind") or "").strip() != target_id
                    ]
                    selections.extend(deepcopy(list(before_target.get("selections") or [])))
                    models.extend(deepcopy(list(before_target.get("models") or [])))
                    return {
                        **current,
                        "updatedAt": utc_now_iso(),
                        "selections": selections,
                        "models": models,
                    }

                storage.mutate_json(_CREATIVE_MEDIA_PREFERENCES_FILE, _restore_media_operation)
                target_restored = True
            elif target_kind == "model_catalog_recovery":
                current_recovery = _get_model_provider_catalog().managed_recovery_state()
                _assert_restore_revision(current_recovery)
                _catalog_mutation_with_digest(
                    _get_model_provider_catalog().rollback_managed_recovery,
                    expected_current_digest=str(
                        ((validation.get("catalogRecovery") or {}).get("managedWorkingDigest"))
                        or current_recovery.get("managedDigest")
                        or ""
                    ),
                    expected_rejected_digest=str(proposed.get("rejectedDigest") or ""),
                )
                target_restored = True
            elif target_kind == "model_catalog_provider":
                catalog = _get_model_provider_catalog()
                _assert_restore_revision(catalog.load_managed())
                _catalog_mutation_with_digest(
                    catalog.restore_managed_provider,
                    target_id,
                    deepcopy(before_target.get("value")) if before_target.get("exists") else None,
                    expected_current_digest=str(
                        ((validation.get("catalog") or {}).get("managedWorkingDigest"))
                        or expected_after
                        or ""
                    ),
                    expected_provider_digest=expected_after,
                )
                target_restored = True
            elif target_kind == "model_catalog_custom_provider":
                catalog = _get_model_provider_catalog()
                _assert_restore_revision(catalog.load_custom())
                _catalog_mutation_with_digest(
                    catalog.restore_custom_provider,
                    target_id,
                    deepcopy(before_target.get("value")) if before_target.get("exists") else None,
                    expected_current_digest=str(
                        ((validation.get("catalog") or {}).get("customWorkingDigest"))
                        or expected_after
                        or ""
                    ),
                )
                target_restored = True
            else:
                errors.append("unsupported snapshot target")
        except ConfigBrokerError as exc:
            if exc.code == "config_rollback_conflict":
                conflict = True
            errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
        if not errors and target_restored:
            verified_current = self._target_snapshot(
                target_kind,
                target_id,
                self._target_config(target_kind),
            )
            if _digest(verified_current) != before_digest:
                target_restored = False
                conflict = True
                errors.append("target changed while rollback was completing")
        if not errors and target_restored:
            errors.extend(self._cleanup_new_credential_refs(transaction))
        return {
            "ok": not errors,
            "conflict": conflict,
            "targetRestored": target_restored,
            "credentialCleanupPending": bool(
                list((transaction.get("proposed") or {}).get("newCredentialRefs") or [])
            ),
            **({"errorCode": "config_rollback_conflict"} if conflict else {}),
            "errors": errors,
        }

    def rollback(self, transaction_id: str, *, owner_id: str = "") -> dict[str, Any]:
        with self._lock:
            transaction = self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)
            if transaction["state"] == "rolled_back":
                return {"ok": True, "mode": "rollback", **self.get_transaction(transaction_id, owner_id=owner_id)}
            if transaction["state"] == "recovery_required" and dict(transaction.get("error") or {}).get("code") == "config_credential_cleanup_failed":
                cleanup_errors = self._cleanup_new_credential_refs(transaction)
                next_state = "recovery_required" if cleanup_errors else "conflict"
                self._update_transaction(
                    transaction_id,
                    state=next_state,
                    result_json=_json({"credentialCleanupErrors": cleanup_errors}),
                    error_code="config_credential_cleanup_failed" if cleanup_errors else "config_transaction_stale",
                    error_message=(
                        "新建凭据清理仍未完成。"
                        if cleanup_errors
                        else "新建凭据已清理；较新的目标配置保持不变。"
                    ),
                )
                return {
                    "ok": not cleanup_errors,
                    "mode": "rollback",
                    "state": next_state,
                    "transactionId": transaction_id,
                    "summary": (
                        "新建凭据清理仍需重试。"
                        if cleanup_errors
                        else "新建凭据已清理，未覆盖较新的目标配置。"
                    ),
                    "rollback": {
                        "ok": not cleanup_errors,
                        "conflict": True,
                        "targetRestored": False,
                        "credentialCleanupPending": bool(cleanup_errors),
                        "errors": cleanup_errors,
                    },
                }
            if transaction["state"] not in {"committed", "committing", "verifying", "rolling_back", "recovery_required"}:
                raise ConfigBrokerError("只有已提交事务可以执行精确撤销。", code="config_transaction_not_committed", status_code=409)
            validation = dict(transaction.get("validation") or {})
            expected_digest = str(
                validation.get("targetAfterDigest")
                or validation.get("targetWorkingDigest")
                or ""
            ).strip()
            self._update_transaction(transaction_id, state="rolling_back")
            rollback = self._restore_snapshot(
                transaction,
                enforce_after_digest=bool(expected_digest),
                expected_current_digest=expected_digest,
            )
            next_state = (
                "rolled_back"
                if rollback.get("ok")
                else ("conflict" if rollback.get("conflict") else "recovery_required")
            )
            self._update_transaction(
                transaction_id,
                state=next_state,
                result_json=_json({"rollback": rollback}),
                rolled_back_at=utc_now_iso() if rollback.get("ok") else None,
            )
            return {
                "ok": bool(rollback.get("ok")),
                "mode": "rollback",
                "state": next_state,
                "transactionId": transaction_id,
                "summary": (
                    "配置已恢复到事务前状态。"
                    if rollback.get("ok")
                    else (
                        "目标配置已在提交后变化；撤销已停止，未覆盖新配置。"
                        if rollback.get("conflict")
                        else "自动恢复未完成，需要人工检查。"
                    )
                ),
                "rollback": rollback,
            }

    def mcp_list(self) -> dict[str, Any]:
        payload = list_mcp_server_configs()
        return {"ok": True, "mode": "mcp_list", **payload, "summary": f"当前配置了 {payload.get('serverCount', 0)} 个 MCP server。"}

    def mcp_status(self) -> dict[str, Any]:
        payload = mcp_runtime_status_snapshot()
        return {"ok": not bool(payload.get("error")), "mode": "mcp_status", "status": payload, "summary": "已读取 MCP runtime 状态。" if not payload.get("error") else "MCP runtime 状态读取失败。"}


config_broker_service = ConfigBrokerService()


__all__ = ["ConfigBrokerError", "ConfigBrokerService", "config_broker_service"]
