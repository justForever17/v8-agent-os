from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ComputerUseElement:
    element_id: str
    backend: str
    role: str
    name: str
    bounds: List[int]
    actions: List[str] = field(default_factory=list)
    confidence: float = 1.0
    path: List[str] = field(default_factory=list)
    automation_id: str = ""
    class_name: str = ""
    window_handle: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "elementId": self.element_id,
            "backend": self.backend,
            "role": self.role,
            "name": self.name,
            "bounds": list(self.bounds),
            "actions": list(self.actions),
            "confidence": self.confidence,
            "path": list(self.path),
            "automationId": self.automation_id,
            "className": self.class_name,
            "windowHandle": self.window_handle,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ComputerUseObservation:
    snapshot_id: str
    platform: str
    backend: str
    app: str
    window_title: str
    screen_hash: str
    tree_hash: str
    elements: List[ComputerUseElement] = field(default_factory=list)
    focused_element_id: Optional[str] = None
    screenshot_artifact: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "platform": self.platform,
            "backend": self.backend,
            "app": self.app,
            "windowTitle": self.window_title,
            "screenHash": self.screen_hash,
            "treeHash": self.tree_hash,
            "elements": [item.as_dict() for item in self.elements],
            "focusedElementId": self.focused_element_id,
            "screenshotArtifact": dict(self.screenshot_artifact or {}) if self.screenshot_artifact else None,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ComputerUseVerification:
    passed: bool
    status: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
    level: str = "verified"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status,
            "reason": self.reason,
            "details": dict(self.details),
            "level": self.level,
        }


@dataclass(slots=True)
class ComputerUseActionResult:
    action_id: str
    action_type: str
    status: str
    message: str
    target: Dict[str, Any] = field(default_factory=dict)
    observation: Optional[ComputerUseObservation] = None
    artifact: Optional[Dict[str, Any]] = None
    verification: Optional[ComputerUseVerification] = None
    attempt_count: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "actionId": self.action_id,
            "actionType": self.action_type,
            "status": self.status,
            "message": self.message,
            "target": dict(self.target),
            "observation": self.observation.as_dict() if self.observation else None,
            "artifact": dict(self.artifact or {}) if self.artifact else None,
            "verification": self.verification.as_dict() if self.verification else None,
            "attemptCount": self.attempt_count,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ComputerUseTraceVariable:
    name: str
    placeholder: str
    original_key: str
    required: bool = True
    source: str = "known_field"
    example_value: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "placeholder": self.placeholder,
            "originalKey": self.original_key,
            "required": self.required,
            "source": self.source,
            "exampleValue": self.example_value,
        }


@dataclass(slots=True)
class ComputerUseTraceTarget:
    window: Dict[str, Any] = field(default_factory=dict)
    selector: Dict[str, Any] = field(default_factory=dict)
    spatial_anchor: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "window": dict(self.window),
            "selector": dict(self.selector),
            "spatialAnchor": dict(self.spatial_anchor),
        }


@dataclass(slots=True)
class ComputerUseTraceRecovery:
    transient: bool = False
    fallback_order: List[str] = field(default_factory=list)
    performed: bool = False
    strategy: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "transient": self.transient,
            "fallbackOrder": list(self.fallback_order),
            "performed": self.performed,
            "strategy": self.strategy,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class ComputerUseTraceRisk:
    level: str
    high_risk_action: bool = False
    requires_pre_guard: bool = False
    requires_post_guard: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "highRiskAction": self.high_risk_action,
            "requiresPreGuard": self.requires_pre_guard,
            "requiresPostGuard": self.requires_post_guard,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class ComputerUseTraceTiming:
    wait_timeout_ms: int = 6000
    retry_limit: int = 1
    attempt_count: int = 1
    elapsed_ms: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "waitTimeoutMs": self.wait_timeout_ms,
            "retryLimit": self.retry_limit,
            "attemptCount": self.attempt_count,
            "elapsedMs": self.elapsed_ms,
        }


@dataclass(slots=True)
class ComputerUseTracePrimitive:
    primitive_id: str
    category: str
    action: str
    affordances: List[str] = field(default_factory=list)
    requires_page_identity: bool = True
    requires_verification_contract: bool = True
    requires_recovery_policy: bool = True
    supports_rpa_promotion: bool = True
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.primitive_id,
            "category": self.category,
            "action": self.action,
            "affordances": list(self.affordances),
            "requiresPageIdentity": bool(self.requires_page_identity),
            "requiresVerificationContract": bool(self.requires_verification_contract),
            "requiresRecoveryPolicy": bool(self.requires_recovery_policy),
            "supportsRpaPromotion": bool(self.supports_rpa_promotion),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class ComputerUseTraceScene:
    page_identity: str = ""
    blocker_state: str = "none"
    transition_state: str = "unknown"
    affordances: List[str] = field(default_factory=list)
    confidence: str = "low"
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pageIdentity": self.page_identity,
            "blockerState": self.blocker_state,
            "transitionState": self.transition_state,
            "affordances": list(self.affordances),
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class ComputerUseTraceBudget:
    time_budget_ms: int = 0
    retry_budget: int = 0
    vision_budget: int = 0
    token_budget: int = 0
    fallback_budget: int = 0
    settle_budget_ms: int = 0
    elapsed_ms: int = 0
    attempts_used: int = 0
    vision_calls_used: int = 0
    token_usage: int = 0
    fallbacks_used: int = 0
    within_budget: bool = True
    exceeded: List[str] = field(default_factory=list)
    source: str = "default"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timeBudgetMs": self.time_budget_ms,
            "retryBudget": self.retry_budget,
            "visionBudget": self.vision_budget,
            "tokenBudget": self.token_budget,
            "fallbackBudget": self.fallback_budget,
            "settleBudgetMs": self.settle_budget_ms,
            "elapsedMs": self.elapsed_ms,
            "attemptsUsed": self.attempts_used,
            "visionCallsUsed": self.vision_calls_used,
            "tokenUsage": self.token_usage,
            "fallbacksUsed": self.fallbacks_used,
            "withinBudget": self.within_budget,
            "exceeded": list(self.exceeded),
            "source": self.source,
        }


@dataclass(slots=True)
class ComputerUseTraceStep:
    step_id: str
    app_id: str
    action: str
    intent: str
    phase: str = "action"
    target: ComputerUseTraceTarget = field(default_factory=ComputerUseTraceTarget)
    params: Dict[str, Any] = field(default_factory=dict)
    raw_params: Dict[str, Any] = field(default_factory=dict)
    variables: List[ComputerUseTraceVariable] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    recovery: ComputerUseTraceRecovery = field(default_factory=ComputerUseTraceRecovery)
    risk: ComputerUseTraceRisk = field(default_factory=lambda: ComputerUseTraceRisk(level="low"))
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    timing: ComputerUseTraceTiming = field(default_factory=ComputerUseTraceTiming)
    primitive: ComputerUseTracePrimitive = field(default_factory=lambda: ComputerUseTracePrimitive(
        primitive_id="custom.unknown",
        category="custom",
        action="unknown",
        supports_rpa_promotion=False,
    ))
    scene: ComputerUseTraceScene = field(default_factory=ComputerUseTraceScene)
    budget: ComputerUseTraceBudget = field(default_factory=ComputerUseTraceBudget)
    signals: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stepId": self.step_id,
            "appId": self.app_id,
            "action": self.action,
            "intent": self.intent,
            "phase": self.phase,
            "target": self.target.as_dict(),
            "params": dict(self.params),
            "rawParams": dict(self.raw_params),
            "variables": [item.as_dict() for item in self.variables],
            "verification": dict(self.verification),
            "recovery": self.recovery.as_dict(),
            "risk": self.risk.as_dict(),
            "artifacts": [dict(item) for item in self.artifacts],
            "timing": self.timing.as_dict(),
            "primitive": self.primitive.as_dict(),
            "scene": self.scene.as_dict(),
            "budget": self.budget.as_dict(),
            "signals": dict(self.signals),
            "metadata": dict(self.metadata),
        }
