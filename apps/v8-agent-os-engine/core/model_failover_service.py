from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

from core.model_control_plane import model_control_plane
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.model_budget_service import model_budget_service
from core.provider_compatibility import normalize_provider_error
from core.provider_health_service import provider_health_service
from erc.runtime_context import get_runtime_context
from erc.run_service import run_service

logger = logging.getLogger("v8chat.model_failover")


@dataclass(slots=True)
class FailoverCandidate:
    model_id: str
    provider_id: str
    provider_name: str
    capability_class: str
    reason: str
    priority: int


class ModelFailoverService:
    def _sticky_enabled(self, config: Dict[str, Any]) -> bool:
        governance = dict((config or {}).get("governance") or {})
        return bool(governance.get("stickyRunModel", True))

    def _resolve_sticky_model(
        self,
        *,
        config: Dict[str, Any],
        run_id: str | None,
        role: str,
        preferred_model_id: str,
        capability_class: str,
    ) -> str:
        if not self._sticky_enabled(config) or not run_id or not role:
            return preferred_model_id

        run_record = run_service.get_run(run_id)
        if not run_record:
            return preferred_model_id

        metadata = dict(run_record.get("metadata") or {})
        sticky_models = dict(metadata.get("sticky_models") or {})
        sticky_model_id = str(sticky_models.get(role) or "")
        if not sticky_model_id:
            return preferred_model_id

        sticky_record = model_control_plane.get_model_record(sticky_model_id, config)
        if not sticky_record:
            return preferred_model_id

        sticky_capability_class = str((sticky_record.get("model") or {}).get("capabilityClass") or "")
        governance = dict((config or {}).get("governance") or {})
        if bool(governance.get("strictCapabilityMatch", True)) and capability_class and sticky_capability_class != capability_class:
            return preferred_model_id
        return sticky_model_id

    def _persist_sticky_choice(
        self,
        *,
        config: Dict[str, Any],
        run_id: str | None,
        role: str,
        model_id: str,
        capability_class: str,
    ) -> None:
        if not self._sticky_enabled(config) or not run_id or not role or not model_id:
            return

        run_record = run_service.get_run(run_id)
        if not run_record:
            return

        metadata = dict(run_record.get("metadata") or {})
        sticky_models = dict(metadata.get("sticky_models") or {})
        if sticky_models.get(role) == model_id:
            return

        sticky_models[role] = model_id
        run_service.update_metadata(
            run_id,
            {
                "sticky_models": sticky_models,
                "sticky_capability_classes": {
                    **dict(metadata.get("sticky_capability_classes") or {}),
                    role: capability_class,
                },
            },
        )

    def _infer_capability_class(self, preferred_model_id: str, role: str, config: Dict[str, Any]) -> str:
        if preferred_model_id:
            record = model_control_plane.get_model_record(preferred_model_id, config)
            if record:
                return str((record.get("model") or {}).get("capabilityClass") or "")
        resolution = model_control_plane.resolve_model_for_role(role or "default", config)
        resolved_model = dict(resolution.get("resolvedModel") or {})
        return str(resolved_model.get("capabilityClass") or "")

    def build_candidate_plan(
        self,
        *,
        config: Dict[str, Any],
        preferred_model_id: str,
        role: str,
    ) -> List[FailoverCandidate]:
        governance = dict((config or {}).get("governance") or {})
        max_provider_switches = int(governance.get("maxProviderSwitches") or 0)
        strict_match = bool(governance.get("strictCapabilityMatch", True))
        allow_failover = bool(governance.get("allowSameCapabilityFailover", True))
        capability_class = self._infer_capability_class(preferred_model_id, role, config)

        preferred_record = model_control_plane.get_model_record(preferred_model_id, config) if preferred_model_id else None
        preferred_provider_id = str((preferred_record or {}).get("provider_id") or "")
        if not preferred_provider_id:
            resolved_role = model_control_plane.resolve_model_for_role(role or "default", config)
            preferred_provider_id = str(resolved_role.get("resolvedProviderId") or "")
        models = model_control_plane.list_models(config)
        provider_states = {
            item["providerId"]: item
            for item in provider_health_service.build_provider_statuses(
                config,
                models,
                model_control_plane._build_resolved_roles(config),
            )
        }

        candidates: List[FailoverCandidate] = []
        cross_provider_count = 0
        for model in models:
            if not model.get("isEnabled", True):
                continue
            if strict_match and capability_class and model.get("capabilityClass") != capability_class:
                continue
            if model["modelId"] == preferred_model_id:
                candidates.insert(
                    0,
                    FailoverCandidate(
                        model_id=model["modelId"],
                        provider_id=model["providerId"],
                        provider_name=model["providerName"],
                        capability_class=model.get("capabilityClass") or "",
                        reason="preferred",
                        priority=int(model.get("priority") or 50),
                    ),
                )
                continue

            provider_state = provider_states.get(model["providerId"], {})
            if provider_state.get("circuitState") == "open":
                continue

            same_provider = model["providerId"] == preferred_provider_id
            if not allow_failover and not same_provider:
                continue
            if not same_provider:
                if cross_provider_count >= max_provider_switches:
                    continue
                cross_provider_count += 1

            candidates.append(
                FailoverCandidate(
                    model_id=model["modelId"],
                    provider_id=model["providerId"],
                    provider_name=model["providerName"],
                    capability_class=model.get("capabilityClass") or "",
                    reason="same_provider" if same_provider else "cross_provider",
                    priority=int(model.get("priority") or 50),
                )
            )

        def sort_key(item: FailoverCandidate) -> tuple[int, int, str]:
            reason_score = {"preferred": 0, "same_provider": 1, "cross_provider": 2}.get(item.reason, 9)
            return (reason_score, item.priority, item.model_id.lower())

        deduped: List[FailoverCandidate] = []
        seen: set[str] = set()
        for candidate in sorted(candidates, key=sort_key):
            if candidate.model_id in seen:
                continue
            seen.add(candidate.model_id)
            deduped.append(candidate)
        return deduped

    def invoke_with_failover(
        self,
        *,
        config: Dict[str, Any],
        base_llm_instance: Any,
        messages: Sequence[Any],
        tools: Sequence[Any] | None,
        role: str,
        preferred_model_id: str,
        build_model: Callable[[str], Any],
    ) -> Any:
        ctx = get_runtime_context()
        run_id = ctx.get("run_id")
        requested_capability_class = self._infer_capability_class(preferred_model_id, role, config)
        effective_preferred_model_id = self._resolve_sticky_model(
            config=config,
            run_id=run_id,
            role=role,
            preferred_model_id=preferred_model_id,
            capability_class=requested_capability_class,
        )
        capability_class = self._infer_capability_class(effective_preferred_model_id, role, config)
        model_budget_service.enforce_or_raise(
            config=config,
            run_id=run_id,
            project_id=ctx.get("project_id"),
            role=role,
            capability_class=capability_class,
            model_id=effective_preferred_model_id,
        )

        attempts: List[Dict[str, Any]] = []
        governance = dict((config or {}).get("governance") or {})
        max_local_retries = max(int(governance.get("maxLocalRetries") or 0), 0)
        candidates = self.build_candidate_plan(
            config=config,
            preferred_model_id=effective_preferred_model_id,
            role=role,
        )
        if not candidates:
            raise ModelGovernanceInterventionRequired(
                "当前没有可用的同类候选模型可用于切换。",
                approval_kind="model_review",
                question="当前角色没有可用的同类候选模型。是否要先调整模型配置后再继续？",
                details={
                    "role": role,
                    "preferredModelId": preferred_model_id,
                    "effectivePreferredModelId": effective_preferred_model_id,
                },
            )

        for index, candidate in enumerate(candidates):
            current_llm = (
                base_llm_instance
                if index == 0 and candidate.model_id == preferred_model_id and effective_preferred_model_id == preferred_model_id
                else build_model(candidate.model_id)
            )
            bound_llm = current_llm.bind_tools(tools) if tools else current_llm
            local_attempts = max_local_retries + 1 if index == 0 else 1
            for retry_index in range(local_attempts):
                try:
                    result = bound_llm.invoke(messages)
                    self._persist_sticky_choice(
                        config=config,
                        run_id=run_id,
                        role=role,
                        model_id=candidate.model_id,
                        capability_class=capability_class,
                    )
                    return result
                except Exception as exc:
                    normalized = normalize_provider_error(
                        exc,
                        provider=candidate.provider_name,
                        model=candidate.model_id,
                    )
                    attempt = {
                        "modelId": candidate.model_id,
                        "providerId": candidate.provider_id,
                        "reason": candidate.reason,
                        "retryIndex": retry_index,
                        "code": normalized["code"],
                        "message": normalized["message"],
                        "retryable": normalized["retryable"],
                    }
                    attempts.append(attempt)
                    logger.warning(
                        "[ModelFailover] role=%s model=%s attempt=%s code=%s message=%s",
                        role,
                        candidate.model_id,
                        retry_index + 1,
                        normalized["code"],
                        normalized["message"],
                    )
                    if not normalized["retryable"]:
                        break

        raise ModelGovernanceInterventionRequired(
            "主模型与同类候选模型均调用失败。",
            approval_kind="model_review",
            question="当前主模型和同类候选模型都调用失败。是否允许暂停本次运行，待你调整模型后再继续？",
            details={
                "role": role,
                "preferredModelId": preferred_model_id,
                "effectivePreferredModelId": effective_preferred_model_id,
                "capabilityClass": capability_class,
                "attempts": attempts,
            },
        )

    def build_failover_summary(self, config: Dict[str, Any]) -> Dict[str, Any]:
        governance = dict((config or {}).get("governance") or {})
        provider_statuses = provider_health_service.build_provider_statuses(
            config,
            model_control_plane.list_models(config),
            model_control_plane._build_resolved_roles(config),
        )
        return {
            "enabled": bool(governance.get("allowSameCapabilityFailover", True)),
            "stickyEnabled": self._sticky_enabled(config),
            "strictCapabilityMatch": bool(governance.get("strictCapabilityMatch", True)),
            "maxLocalRetries": int(governance.get("maxLocalRetries") or 0),
            "maxProviderSwitches": int(governance.get("maxProviderSwitches") or 0),
            "providersHealthy": sum(1 for item in provider_statuses if item.get("status") == "healthy"),
            "providersCircuitOpen": sum(1 for item in provider_statuses if item.get("circuitState") == "open"),
        }


model_failover_service = ModelFailoverService()
