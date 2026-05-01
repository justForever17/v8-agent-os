from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
WAITING_RUN_STATUSES = {"waiting_input", "waiting_approval", "waiting_external_tool", "paused"}


@dataclass(slots=True)
class RunDescriptor:
    run_id: str
    session_id: str
    conversation_id: str
    user_id: str
    runtime_kind: str
    trigger_source: Optional[str] = None
    agent_id: Optional[str] = None
    workflow_id: Optional[str] = None
    thread_id: Optional[str] = None
    channel_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "queued"


@dataclass(slots=True)
class RuntimeSource:
    plane: str = "engine"
    component: str = "erc"
    node: str = "system"
    agent_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "plane": self.plane,
            "component": self.component,
            "node": self.node,
            "agent_id": self.agent_id,
        }


@dataclass(slots=True)
class ApprovalRequest:
    approval_id: str
    session_id: str
    run_id: str
    approval_kind: str
    request: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[str] = None


@dataclass(slots=True)
class RunControlSignal:
    command: str
    reason: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeCommand:
    topic: str
    run_id: Optional[str] = None
    approval_id: Optional[str] = None
    interaction_id: Optional[str] = None
    reason: Optional[str] = None
    response: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeEventsPayload:
    session_id: str
    latest_seq: int
    events: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "latestSeq": self.latest_seq,
            "events": list(self.events),
        }


@dataclass(slots=True)
class RuntimeSnapshotPayload:
    session_id: str
    latest_seq: int
    snapshot: Optional[Dict[str, Any]]
    runtime_timeline: List[Dict[str, Any]] = field(default_factory=list)
    todos: Optional[Dict[str, Any]] = None
    current_run: Optional[Dict[str, Any]] = None
    runtime_status: Optional[str] = None
    workflow: Optional[Dict[str, Any]] = None
    workflow_projection: Optional[Dict[str, Any]] = None
    approvals: List[Dict[str, Any]] = field(default_factory=list)
    ask_user_interactions: List[Dict[str, Any]] = field(default_factory=list)
    controls: Optional[Dict[str, Any]] = None
    recoverable: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    context_governance: Optional[Dict[str, Any]] = None
    context_governance_history: List[Dict[str, Any]] = field(default_factory=list)
    lane: Optional[Dict[str, Any]] = None
    liveness: Optional[Dict[str, Any]] = None
    recovery_class: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "latestSeq": self.latest_seq,
            "snapshot": self.snapshot,
            "runtimeTimeline": list(self.runtime_timeline),
            "todos": self.todos,
            "currentRun": self.current_run,
            "runtimeStatus": self.runtime_status,
            "workflow": self.workflow,
            "workflowProjection": self.workflow_projection,
            "approvals": list(self.approvals),
            "askUserInteractions": list(self.ask_user_interactions),
            "controls": self.controls,
            "recoverable": self.recoverable,
            "summary": self.summary,
            "source": self.source,
            "contextGovernance": self.context_governance,
            "contextGovernanceHistory": list(self.context_governance_history),
            "lane": self.lane,
            "liveness": self.liveness,
            "recoveryClass": self.recovery_class,
        }
