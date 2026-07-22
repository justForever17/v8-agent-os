from __future__ import annotations

from typing import Any, Dict, Iterable, List

from core.database import db
from core.local_visual_support import probe_local_multimodal_capability


def _safe_int(value: Any, default: int = 0) -> int:
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


class ProviderHealthService:
    def _pick_local_probe_model(self, provider_models: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not provider_models:
            return None
        prioritized = sorted(
            provider_models,
            key=lambda item: (
                0 if "computer_use_visual_judge" in list(item.get("assignedRoles") or []) else 1,
                0 if "vision" in list(item.get("assignedRoles") or []) else 1,
                0 if item.get("isEnabled", True) else 1,
                0 if str(item.get("capabilityClass") or "") == "vision_multimodal" else 1,
            ),
        )
        for item in prioritized:
            if not item.get("isEnabled", True):
                continue
            if str(item.get("capabilityClass") or "") == "vision_multimodal":
                return item
        return None

    def _governance(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return dict((config or {}).get("governance") or {})

    def _health_map(self, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        governance = self._governance(config)
        window_days = max(_safe_int(governance.get("providerHealthWindowDays"), 7), 1)
        failure_threshold = max(_safe_int(governance.get("providerFailureThreshold"), 3), 1)
        error_rate_threshold = _safe_float(governance.get("providerErrorRateThreshold"), 0.6)

        health_rows = db.get_provider_health_summary(days=window_days)
        mapped: Dict[str, Dict[str, Any]] = {}
        for row in health_rows:
            provider_id = str(row.get("provider_id") or "unknown")
            events = _safe_int(row.get("events"))
            errors = _safe_int(row.get("error_count"))
            error_rate = (errors / events) if events else 0.0
            circuit_state = "closed"
            if events >= failure_threshold and error_rate >= error_rate_threshold:
                circuit_state = "open"
            elif errors > 0:
                circuit_state = "half_open"
            mapped[provider_id] = {
                "events": events,
                "successCount": _safe_int(row.get("success_count")),
                "errorCount": errors,
                "errorRate": round(error_rate, 3),
                "avgLatencyMs": round(_safe_float(row.get("avg_latency_ms")), 2),
                "lastSeenAt": row.get("last_seen_at"),
                "circuitState": circuit_state,
            }
        return mapped

    def build_provider_statuses(
        self,
        config: Dict[str, Any],
        flat_models: Iterable[Dict[str, Any]],
        resolved_roles: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        models_by_provider: Dict[str, List[Dict[str, Any]]] = {}
        for model in flat_models:
            provider_id = str(model.get("providerId") or "")
            models_by_provider.setdefault(provider_id, []).append(model)

        assigned_roles_by_provider: Dict[str, List[str]] = {}
        for role_key, resolution in resolved_roles.items():
            provider_id = str(resolution.get("resolvedProviderId") or "")
            if provider_id:
                assigned_roles_by_provider.setdefault(provider_id, []).append(role_key)

        health_map = self._health_map(config)
        providers: List[Dict[str, Any]] = []
        for provider_id, provider_data in (config.get("providers") or {}).items():
            provider_meta = dict(provider_data.get("provider") or {})
            provider_models = models_by_provider.get(provider_id, [])
            enabled_models = [item for item in provider_models if item.get("isEnabled", True)]
            assigned_roles = assigned_roles_by_provider.get(provider_id, [])
            health = health_map.get(provider_id, {})

            is_enabled = bool(provider_meta.get("is_enabled", True))
            api_standard = str(provider_meta.get("api_standard") or "openai").lower()
            provider_type = str(provider_meta.get("type") or "API").upper()
            has_auth = bool(
                provider_meta.get("api_key")
                or provider_meta.get("credentialRef")
                or provider_meta.get("credential_ref")
                or provider_meta.get("oauth_ref")
            ) or provider_type == "LOCAL"
            has_base_url = bool(provider_meta.get("base_url")) or api_standard in {"openai", "anthropic", "google", "gemini"}
            local_capability_probe = None
            if provider_type == "LOCAL":
                probe_model = self._pick_local_probe_model(provider_models)
                if probe_model is not None:
                    local_capability_probe = probe_local_multimodal_capability(
                        model_id=str(probe_model.get("modelId") or ""),
                        provider_type=provider_type,
                        base_url=str(provider_meta.get("base_url") or ""),
                        api_key=str(provider_meta.get("api_key") or ""),
                    )

            if not is_enabled:
                status = "disabled"
                reason = "供应商已停用"
            elif not provider_models:
                status = "attention"
                reason = "已启用但尚未挂载模型"
            elif provider_type != "LOCAL" and not has_auth:
                status = "attention"
                reason = "缺少认证信息"
            elif not has_base_url:
                status = "attention"
                reason = "缺少基础地址"
            elif health.get("circuitState") == "open":
                status = "attention"
                reason = "最近错误率过高，熔断已打开"
            elif health.get("errorCount", 0) > 0:
                status = "attention"
                reason = "最近有失败事件，处于观察期"
            else:
                status = "healthy"
                reason = "可参与角色路由与后续 failover"

            if (
                provider_type == "LOCAL"
                and isinstance(local_capability_probe, dict)
                and local_capability_probe.get("status") == "unsupported"
            ):
                status = "attention"
                reason = "本地文本连接可用，但当前模型未启用图像输入能力。"

            providers.append(
                {
                    "providerId": provider_id,
                    "name": provider_meta.get("name") or provider_id,
                    "icon": provider_meta.get("icon"),
                    "status": status,
                    "reason": reason,
                    "assignedRoles": assigned_roles,
                    "models": len(provider_models),
                    "enabledModels": len(enabled_models),
                    "apiStandard": api_standard,
                    "type": provider_type,
                    "events": int(health.get("events") or 0),
                    "successCount": int(health.get("successCount") or 0),
                    "errorCount": int(health.get("errorCount") or 0),
                    "errorRate": float(health.get("errorRate") or 0.0),
                    "avgLatencyMs": float(health.get("avgLatencyMs") or 0.0),
                    "lastSeenAt": health.get("lastSeenAt"),
                    "circuitState": health.get("circuitState") or "closed",
                    "localCapabilityProbe": local_capability_probe,
                }
            )

        return sorted(providers, key=lambda item: item["name"].lower())


provider_health_service = ProviderHealthService()
