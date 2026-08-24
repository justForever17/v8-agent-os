from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

from langchain_core.messages import BaseMessageChunk, message_chunk_to_message

from core.model_capability_matrix import (
    build_effective_capability_matrix,
    evaluate_capability_matrix,
    infer_runtime_capability_requirements,
)
from core.model_control_plane import model_control_plane
from core.llm_exceptions import (
    V8LLMCapabilityMismatchError,
    V8LLMError,
    build_llm_error_from_normalized,
)
from core.provider_runtime_profiles import runtime_readiness_for_provider
from core.model_budget_service import model_budget_service
from core.provider_compatibility import normalize_provider_error
from core.provider_health_service import provider_health_service
from erc.runtime_context import get_runtime_context
from erc.run_service import run_service

logger = logging.getLogger("v8chat.model_failover")

_RESPONSE_CONTRACT_ERROR_CODE = "response_contract_violation"
_LOCAL_RETRY_ERROR_CODES = {
    "rate_limit",
    "timeout",
    "provider_unavailable",
    _RESPONSE_CONTRACT_ERROR_CODE,
}
_FAILOVER_ERROR_CODES = _LOCAL_RETRY_ERROR_CODES | {"quota_exceeded"}


@dataclass(slots=True)
class FailoverCandidate:
    model_id: str
    provider_id: str
    provider_name: str
    capability_class: str
    reason: str
    priority: int
    effective_capability_match: bool = True
    api_standard: str = ""
    degrade_applied: bool = False
    degrade_reason: str = ""
    effective_capability_matrix: Dict[str, Any] | None = None


class ModelFailoverService:
    def _provider_api_standard(self, provider_meta: Dict[str, Any]) -> str:
        return str(provider_meta.get("api_standard") or provider_meta.get("apiStandard") or "openai").strip().lower()

    def _same_api_standard_failover_enabled(self, governance: Dict[str, Any]) -> bool:
        return bool(governance.get("sameApiStandardFailover", True))

    def _max_total_attempts(self, governance: Dict[str, Any], *, max_local_retries: int) -> int:
        raw = governance.get("maxTotalAttempts")
        if raw is not None:
            try:
                return max(min(int(raw), 10), 1)
            except (TypeError, ValueError):
                pass
        max_provider_switches = max(int(governance.get("maxProviderSwitches") or 0), 0)
        return max(min((max_local_retries + 1) + max_provider_switches + 2, 10), 1)

    def _max_failover_seconds(self, governance: Dict[str, Any]) -> float:
        raw = governance.get("maxFailoverSeconds")
        if raw is not None:
            try:
                return max(float(raw), 1.0)
            except (TypeError, ValueError):
                pass
        return 360.0

    def _can_retry_same_model(self, code: str) -> bool:
        return str(code or "") in _LOCAL_RETRY_ERROR_CODES

    def _can_try_next_candidate(self, code: str) -> bool:
        return str(code or "") in _FAILOVER_ERROR_CODES

    def _runtime_ready_for_provider(self, provider_meta: Dict[str, Any]) -> bool:
        runtime_ready, _reason = runtime_readiness_for_provider(
            provider_id=str(provider_meta.get("name") or provider_meta.get("provider_id") or ""),
            api_standard=provider_meta.get("api_standard") or provider_meta.get("apiStandard") or "openai",
            provider_config=provider_meta,
        )
        return runtime_ready

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
        capability_requirements: Dict[str, Any] | None = None,
    ) -> List[FailoverCandidate]:
        governance = dict((config or {}).get("governance") or {})
        max_provider_switches = int(governance.get("maxProviderSwitches") or 0)
        strict_match = bool(governance.get("strictCapabilityMatch", True))
        allow_failover = bool(governance.get("allowSameCapabilityFailover", True))
        capability_class = self._infer_capability_class(preferred_model_id, role, config)

        preferred_record = model_control_plane.get_model_record(preferred_model_id, config) if preferred_model_id else None
        preferred_provider_id = str((preferred_record or {}).get("provider_id") or "")
        preferred_api_standard = (
            self._provider_api_standard(dict((preferred_record or {}).get("provider") or {}))
            if preferred_record
            else ""
        )
        if not preferred_provider_id:
            resolved_role = model_control_plane.resolve_model_for_role(role or "default", config)
            preferred_provider_id = str(resolved_role.get("resolvedProviderId") or "")
            resolved_provider = dict(resolved_role.get("resolvedProvider") or {})
            if resolved_provider:
                preferred_api_standard = self._provider_api_standard(resolved_provider)
        same_api_standard_failover = self._same_api_standard_failover_enabled(governance)
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
            model_runtime_id = str(model.get("modelRef") or model["modelId"])
            model_record = model_control_plane.get_model_record(model_runtime_id, config)
            model_meta = dict((model_record or {}).get("model") or {})
            model_capabilities = dict(model_meta.get("capabilities") or {})
            provider_meta = dict((model_record or {}).get("provider") or {})
            api_standard = self._provider_api_standard(provider_meta)
            runtime_ready = self._runtime_ready_for_provider(provider_meta)
            if not runtime_ready:
                continue
            effective_capability_matrix = build_effective_capability_matrix(
                capability_class=str(model.get("capabilityClass") or model_meta.get("capabilityClass") or ""),
                capabilities=model_capabilities,
                api_standard=api_standard,
                runtime_ready=runtime_ready,
            )
            capability_gate = evaluate_capability_matrix(effective_capability_matrix, capability_requirements)
            if not capability_gate["effectiveCapabilityMatch"]:
                continue
            if str(model["modelId"]) == preferred_model_id or model_runtime_id == preferred_model_id:
                candidates.insert(
                    0,
                    FailoverCandidate(
                        model_id=model_runtime_id,
                        provider_id=model["providerId"],
                        provider_name=model["providerName"],
                        capability_class=model.get("capabilityClass") or "",
                        reason="preferred",
                        priority=int(model.get("priority") or 50),
                        effective_capability_match=True,
                        api_standard=api_standard,
                        degrade_applied=bool(capability_gate.get("degradeApplied")),
                        degrade_reason=str(capability_gate.get("degradeReason") or ""),
                        effective_capability_matrix=effective_capability_matrix,
                    ),
                )
                continue

            provider_state = provider_states.get(model["providerId"], {})
            if provider_state.get("circuitState") == "open":
                continue

            same_provider = model["providerId"] == preferred_provider_id
            if not allow_failover and not same_provider:
                continue
            if same_api_standard_failover and preferred_api_standard and api_standard != preferred_api_standard:
                continue
            if not same_provider:
                if cross_provider_count >= max_provider_switches:
                    continue
                cross_provider_count += 1

            candidates.append(
                FailoverCandidate(
                    model_id=model_runtime_id,
                    provider_id=model["providerId"],
                    provider_name=model["providerName"],
                    capability_class=model.get("capabilityClass") or "",
                    reason="same_provider" if same_provider else "cross_provider",
                    priority=int(model.get("priority") or 50),
                    effective_capability_match=True,
                    api_standard=api_standard,
                    degrade_applied=bool(capability_gate.get("degradeApplied")),
                    degrade_reason=str(capability_gate.get("degradeReason") or ""),
                    effective_capability_matrix=effective_capability_matrix,
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
        invocation_config: Dict[str, Any] | None = None,
        tool_choice: Any | None = None,
        result_validator: Callable[[Any], str | None] | None = None,
        stream_observer: Callable[[Any], None] | None = None,
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
        preferred_matrix = {}
        if hasattr(base_llm_instance, "effective_capability_matrix"):
            try:
                preferred_matrix = dict(base_llm_instance.effective_capability_matrix() or {})
            except Exception:
                preferred_matrix = {}
        if not preferred_matrix:
            preferred_record = model_control_plane.get_model_record(effective_preferred_model_id, config)
            preferred_model_meta = dict((preferred_record or {}).get("model") or {})
            preferred_provider_meta = dict((preferred_record or {}).get("provider") or {})
            preferred_runtime_ready = self._runtime_ready_for_provider(preferred_provider_meta)
            preferred_matrix = build_effective_capability_matrix(
                capability_class=str(preferred_model_meta.get("capabilityClass") or capability_class),
                capabilities=preferred_model_meta.get("capabilities") or {},
                api_standard=self._provider_api_standard(preferred_provider_meta),
                runtime_ready=preferred_runtime_ready,
            )
        capability_requirements = infer_runtime_capability_requirements(
            role=role,
            messages=messages,
            tools=tools,
            preferred_matrix=preferred_matrix,
            require_structured_output=False,
        )
        candidates = self.build_candidate_plan(
            config=config,
            preferred_model_id=effective_preferred_model_id,
            role=role,
            capability_requirements=capability_requirements,
        )
        if not candidates:
            raise V8LLMCapabilityMismatchError(
                code="model_capability_unavailable",
                message="Model Hub 中没有满足当前运行能力要求的已配置模型。",
                provider="model_hub",
                model=effective_preferred_model_id,
                retryable=False,
                user_action="请在 Model Hub 中配置并测试满足当前角色能力要求的模型，然后重新发送消息。",
                details={
                    "role": role,
                    "preferredModelId": preferred_model_id,
                    "effectivePreferredModelId": effective_preferred_model_id,
                    "effectiveCapabilityRequirements": capability_requirements,
                },
            )

        started_at = time.monotonic()
        max_total_attempts = self._max_total_attempts(governance, max_local_retries=max_local_retries)
        max_failover_seconds = self._max_failover_seconds(governance)
        total_attempts = 0
        caps_exhausted_reason = ""
        effective_preferred_record = model_control_plane.get_model_record(effective_preferred_model_id, config)
        effective_preferred_runtime_id = str((effective_preferred_record or {}).get("model_ref") or effective_preferred_model_id)

        for index, candidate in enumerate(candidates):
            if total_attempts >= max_total_attempts:
                caps_exhausted_reason = "max_total_attempts_exhausted"
                break
            if time.monotonic() - started_at >= max_failover_seconds:
                caps_exhausted_reason = "max_failover_seconds_exhausted"
                break
            current_llm = (
                base_llm_instance
                if index == 0
                and candidate.model_id in {preferred_model_id, effective_preferred_model_id, effective_preferred_runtime_id}
                else build_model(candidate.model_id)
            )
            bound_llm = (
                current_llm.bind_tools(tools, tool_choice=tool_choice)
                if tools and tool_choice is not None
                else current_llm.bind_tools(tools)
                if tools
                else current_llm
            )
            local_attempts = max_local_retries + 1 if index == 0 else 1
            for retry_index in range(local_attempts):
                if total_attempts >= max_total_attempts:
                    caps_exhausted_reason = "max_total_attempts_exhausted"
                    break
                if time.monotonic() - started_at >= max_failover_seconds:
                    caps_exhausted_reason = "max_failover_seconds_exhausted"
                    break
                total_attempts += 1
                try:
                    if stream_observer is None:
                        result = (
                            bound_llm.invoke(messages, config=invocation_config)
                            if invocation_config
                            else bound_llm.invoke(messages)
                        )
                    else:
                        stream = (
                            bound_llm.stream(messages, config=invocation_config)
                            if invocation_config
                            else bound_llm.stream(messages)
                        )
                        aggregate = None
                        for chunk in stream:
                            try:
                                stream_observer(chunk)
                            except Exception:
                                logger.debug("Subagent stream observer failed", exc_info=True)
                            aggregate = chunk if aggregate is None else aggregate + chunk
                        if aggregate is None:
                            raise RuntimeError("stream_completed_without_message")
                        result = (
                            message_chunk_to_message(aggregate)
                            if isinstance(aggregate, BaseMessageChunk)
                            else aggregate
                        )
                    validation_error = ""
                    if result_validator is not None:
                        try:
                            validation_error = str(result_validator(result) or "").strip()
                        except Exception as exc:
                            validation_error = (
                                f"response validator failed: {type(exc).__name__}: {exc}"
                            )
                    if validation_error:
                        attempts.append(
                            {
                                "modelId": candidate.model_id,
                                "providerId": candidate.provider_id,
                                "reason": candidate.reason,
                                "retryIndex": retry_index,
                                "attemptOrdinal": total_attempts,
                                "apiStandard": candidate.api_standard,
                                "effectiveCapabilityMatch": candidate.effective_capability_match,
                                "degradeApplied": candidate.degrade_applied,
                                "degradeReason": candidate.degrade_reason,
                                "effectiveCapabilityMatrix": candidate.effective_capability_matrix or {},
                                "code": _RESPONSE_CONTRACT_ERROR_CODE,
                                "message": validation_error,
                                "retryable": True,
                            }
                        )
                        logger.warning(
                            "[ModelFailover] role=%s model=%s attempt=%s code=%s message=%s",
                            role,
                            candidate.model_id,
                            retry_index + 1,
                            _RESPONSE_CONTRACT_ERROR_CODE,
                            validation_error,
                        )
                        continue
                    self._persist_sticky_choice(
                        config=config,
                        run_id=run_id,
                        role=role,
                        model_id=candidate.model_id,
                        capability_class=capability_class,
                    )
                    return result
                except Exception as exc:
                    if isinstance(exc, V8LLMError):
                        normalized = {
                            "code": exc.code,
                            "provider": exc.provider or candidate.provider_name,
                            "model": exc.model or candidate.model_id,
                            "retryable": exc.retryable,
                            "message": exc.message,
                            "userAction": exc.user_action,
                            "diagnostic": dict(exc.details or {}),
                        }
                    else:
                        normalized = normalize_provider_error(
                            exc,
                            provider=candidate.provider_name,
                            model=candidate.model_id,
                        )
                    attempt = {
                        "modelId": candidate.model_id,
                        "providerId": candidate.provider_id,
                        "providerName": normalized["provider"],
                        "reason": candidate.reason,
                        "retryIndex": retry_index,
                        "attemptOrdinal": total_attempts,
                        "apiStandard": candidate.api_standard,
                        "effectiveCapabilityMatch": candidate.effective_capability_match,
                        "degradeApplied": candidate.degrade_applied,
                        "degradeReason": candidate.degrade_reason,
                        "effectiveCapabilityMatrix": candidate.effective_capability_matrix or {},
                        "code": normalized["code"],
                        "message": normalized["message"],
                        "retryable": normalized["retryable"],
                        "userAction": normalized.get("userAction") or "",
                        "diagnostic": dict(normalized.get("diagnostic") or {}),
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
                    if not normalized["retryable"] or not self._can_retry_same_model(str(normalized["code"])):
                        break
            if caps_exhausted_reason:
                break
            if attempts:
                last_code = str(attempts[-1].get("code") or "")
                last_model = str(attempts[-1].get("modelId") or "")
                if last_model == candidate.model_id and not self._can_try_next_candidate(last_code):
                    break

        last_attempt = attempts[-1] if attempts else {}
        raise build_llm_error_from_normalized(
            {
                "code": str(last_attempt.get("code") or "model_invocation_failed"),
                "provider": str(last_attempt.get("providerName") or last_attempt.get("providerId") or "unknown"),
                "model": str(last_attempt.get("modelId") or effective_preferred_model_id),
                "retryable": bool(last_attempt.get("retryable")),
                "message": str(last_attempt.get("message") or "Model Hub 中已配置的模型调用失败。"),
                "userAction": str(last_attempt.get("userAction") or "请在 Model Hub 中运行连接测试，修正配置后重新发送消息。"),
            },
            details={
                "role": role,
                "preferredModelId": preferred_model_id,
                "effectivePreferredModelId": effective_preferred_model_id,
                "capabilityClass": capability_class,
                "effectiveCapabilityRequirements": capability_requirements,
                "attempts": attempts,
                "maxTotalAttempts": max_total_attempts,
                "maxFailoverSeconds": max_failover_seconds,
                "capsExhaustedReason": caps_exhausted_reason,
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
            "sameApiStandardFailover": self._same_api_standard_failover_enabled(governance),
            "maxLocalRetries": int(governance.get("maxLocalRetries") or 0),
            "maxProviderSwitches": int(governance.get("maxProviderSwitches") or 0),
            "maxTotalAttempts": self._max_total_attempts(
                governance,
                max_local_retries=max(int(governance.get("maxLocalRetries") or 0), 0),
            ),
            "maxFailoverSeconds": self._max_failover_seconds(governance),
            "providersHealthy": sum(1 for item in provider_statuses if item.get("status") == "healthy"),
            "providersCircuitOpen": sum(1 for item in provider_statuses if item.get("circuitState") == "open"),
        }


model_failover_service = ModelFailoverService()
