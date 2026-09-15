"""Single directional output contract for Runtime, Agent and Human surfaces.

Runtime keeps the original provider/tool evidence.  Agent receives a bounded
action record with stable identifiers.  Human receives a short explanation
that cannot accidentally inherit runtime identifiers or raw payloads.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class RuntimeSurfaceRecord:
    source: str
    version: str
    recorded_at: str
    actor_id: str
    payload: Any
    raw_sha256: str

    @classmethod
    def create(cls, *, source: str, version: str, recorded_at: str, actor_id: str, payload: Any) -> "RuntimeSurfaceRecord":
        snapshot = deepcopy(payload)
        return cls(str(source), str(version), str(recorded_at), str(actor_id), snapshot, hashlib.sha256(_json(snapshot).encode()).hexdigest())

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "version": self.version, "recordedAt": self.recorded_at,
                "actorId": self.actor_id, "rawSha256": self.raw_sha256, "payload": deepcopy(self.payload)}


@dataclass(frozen=True, slots=True)
class AgentSurfaceRecord:
    status: str
    summary: str
    tool_call_id: str | None
    delegation_id: str | None
    detail_ref: str | None
    next_action: dict[str, Any] | None
    proof: dict[str, Any] | None
    omitted: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "summary": self.summary, "toolCallId": self.tool_call_id,
                "delegationId": self.delegation_id, "detailRef": self.detail_ref,
                "nextAction": self.next_action, "proof": self.proof, "omitted": self.omitted}


@dataclass(frozen=True, slots=True)
class HumanSurfaceRecord:
    status: str
    result: str
    risk: str | None
    next_step: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "result": self.result, "risk": self.risk, "nextStep": self.next_step}


def project_output_surfaces(
    *,
    runtime: RuntimeSurfaceRecord,
    status: str,
    summary: str,
    tool_call_id: str | None = None,
    delegation_id: str | None = None,
    detail_ref: str | None = None,
    next_action: dict[str, Any] | None = None,
    proof: dict[str, Any] | None = None,
    risk: str | None = None,
    next_step: str | None = None,
) -> tuple[RuntimeSurfaceRecord, AgentSurfaceRecord, HumanSurfaceRecord]:
    agent = AgentSurfaceRecord(
        status=str(status), summary=str(summary), tool_call_id=tool_call_id,
        delegation_id=delegation_id, detail_ref=detail_ref,
        next_action=next_action, proof=proof, omitted={"runtimePayload": True},
    )
    human = HumanSurfaceRecord(status=str(status), result=str(summary), risk=risk, next_step=next_step)
    return runtime, agent, human


__all__ = ["RuntimeSurfaceRecord", "AgentSurfaceRecord", "HumanSurfaceRecord", "project_output_surfaces"]
