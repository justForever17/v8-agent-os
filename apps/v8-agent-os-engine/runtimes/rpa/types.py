from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class RPAScriptVariable:
    name: str
    var_type: str = "string"
    required: bool = True
    placeholder: str = ""
    source: str = "computer_use_trace"
    example_value: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.var_type,
            "required": self.required,
            "placeholder": self.placeholder or f"{{{{{self.name}}}}}",
            "source": self.source,
            "exampleValue": self.example_value,
        }


@dataclass(slots=True)
class RPAStepApproval:
    mode: str
    reason: str = ""
    required: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "required": self.required,
        }


@dataclass(slots=True)
class RPAStepAssessment:
    score: float
    status: str = "accepted"
    band: str = "medium"
    reasons: List[str] = field(default_factory=list)
    review_required: bool = False
    excluded: bool = False
    signals: Dict[str, Any] = field(default_factory=dict)
    trust_model: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status,
            "band": self.band,
            "reasons": list(self.reasons),
            "reviewRequired": self.review_required,
            "excluded": self.excluded,
            "signals": dict(self.signals),
            "trustModel": dict(self.trust_model),
        }


@dataclass(slots=True)
class RPAStepRobotSemantic:
    library: str = ""
    keyword: str = ""
    arguments: List[Any] = field(default_factory=list)
    fallback_keyword: str = ""
    locator: str = ""
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "library": self.library,
            "keyword": self.keyword,
            "arguments": list(self.arguments),
            "fallbackKeyword": self.fallback_keyword,
            "locator": self.locator,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class RPARobotLibrary:
    name: str
    required: bool = False
    alias: str = ""
    purpose: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "alias": self.alias,
            "purpose": self.purpose,
        }


@dataclass(slots=True)
class RPAScriptRobotOptions:
    tags: List[str] = field(default_factory=list)
    libraries: List[RPARobotLibrary] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_setup: Optional[str] = None
    task_teardown: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "tags": list(self.tags),
            "libraries": [item.as_dict() for item in self.libraries],
            "metadata": dict(self.metadata),
        }
        if self.task_setup:
            payload["taskSetup"] = self.task_setup
        if self.task_teardown:
            payload["taskTeardown"] = self.task_teardown
        return payload


@dataclass(slots=True)
class RPAScriptStep:
    step_id: str
    use: str
    intent: str
    params: Dict[str, Any] = field(default_factory=dict)
    target: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    recovery: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    timing: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    approval: Optional[RPAStepApproval] = None
    assessment: Optional[RPAStepAssessment] = None
    robot: Optional[RPAStepRobotSemantic] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "stepId": self.step_id,
            "use": self.use,
            "intent": self.intent,
            "params": dict(self.params),
            "target": dict(self.target),
            "verification": dict(self.verification),
            "recovery": dict(self.recovery),
            "risk": dict(self.risk),
            "timing": dict(self.timing),
            "artifacts": [dict(item) for item in self.artifacts],
            "approval": self.approval.as_dict() if self.approval is not None else {},
            "assessment": self.assessment.as_dict() if self.assessment is not None else {},
            "metadata": dict(self.metadata),
        }
        if self.robot is not None:
            payload["robot"] = self.robot.as_dict()
        return payload


@dataclass(slots=True)
class RPAScriptAssessment:
    score: float
    status: str = "accepted"
    band: str = "medium"
    reasons: List[str] = field(default_factory=list)
    accepted_steps: int = 0
    review_required_steps: int = 0
    excluded_steps: int = 0
    signals: Dict[str, Any] = field(default_factory=dict)
    trust_model: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status,
            "band": self.band,
            "reasons": list(self.reasons),
            "acceptedSteps": self.accepted_steps,
            "reviewRequiredSteps": self.review_required_steps,
            "excludedSteps": self.excluded_steps,
            "signals": dict(self.signals),
            "trustModel": dict(self.trust_model),
        }


@dataclass(slots=True)
class RPATemplateProfileBinding:
    app_id: str
    display_name: str = ""
    process_names: List[str] = field(default_factory=list)
    scenario_tags: List[str] = field(default_factory=list)
    title_patterns: List[str] = field(default_factory=list)
    class_names: List[str] = field(default_factory=list)
    transient_selectors: List[str] = field(default_factory=list)
    window_probe_selector_keys: List[str] = field(default_factory=list)
    high_risk_actions: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "appId": self.app_id,
            "displayName": self.display_name,
            "processNames": list(self.process_names),
            "scenarioTags": list(self.scenario_tags),
            "titlePatterns": list(self.title_patterns),
            "classNames": list(self.class_names),
            "transientSelectors": list(self.transient_selectors),
            "windowProbeSelectorKeys": list(self.window_probe_selector_keys),
            "highRiskActions": list(self.high_risk_actions),
        }


@dataclass(slots=True)
class RPATemplateCandidate:
    template_id: str
    name: str
    version: str = "0.1.0"
    kind: str = "rpa_template_candidate"
    app_id: str = "desktop"
    goal: str = ""
    variables: List[RPAScriptVariable] = field(default_factory=list)
    steps: List[RPAScriptStep] = field(default_factory=list)
    profile: Optional[RPATemplateProfileBinding] = None
    source: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    assessment: Optional[RPAScriptAssessment] = None
    robot: RPAScriptRobotOptions = field(default_factory=RPAScriptRobotOptions)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.template_id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "appId": self.app_id,
            "goal": self.goal,
            "variables": [item.as_dict() for item in self.variables],
            "steps": [item.as_dict() for item in self.steps],
            "profile": self.profile.as_dict() if self.profile else None,
            "source": dict(self.source),
            "metadata": dict(self.metadata),
            "assessment": self.assessment.as_dict() if self.assessment else None,
            "robot": self.robot.as_dict(),
        }


@dataclass(slots=True)
class RPAScript:
    script_id: str
    name: str
    version: str = "0.1.0"
    kind: str = "rpa_script"
    runtime: str = "robot_framework"
    app_id: str = "desktop"
    goal: str = ""
    variables: List[RPAScriptVariable] = field(default_factory=list)
    steps: List[RPAScriptStep] = field(default_factory=list)
    source: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    assessment: Optional[RPAScriptAssessment] = None
    robot: RPAScriptRobotOptions = field(default_factory=RPAScriptRobotOptions)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.script_id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "runtime": self.runtime,
            "appId": self.app_id,
            "goal": self.goal,
            "variables": [item.as_dict() for item in self.variables],
            "steps": [item.as_dict() for item in self.steps],
            "source": dict(self.source),
            "metadata": dict(self.metadata),
            "assessment": self.assessment.as_dict() if self.assessment else None,
            "robot": self.robot.as_dict(),
        }
