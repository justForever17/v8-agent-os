from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from core.storage import storage
from core.v8_agent_os_paths import CHECKPOINT_DB_PATH, STATE_DB_PATH, CONFIG_JSON_PATH


VALID_SESSION_LANE_POLICIES = {"queue", "reject", "interrupt_then_replace"}


@dataclass(slots=True)
class RuntimeStabilityConfig:
    strict_supervisor_durability: bool = True
    session_lane_policy: str = "queue"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "strictSupervisorDurability": bool(self.strict_supervisor_durability),
            "sessionLanePolicy": str(self.session_lane_policy or "queue"),
        }


class RuntimeStabilityService:
    def _normalize_policy(self, value: Any) -> str:
        normalized = str(value or "queue").strip().lower()
        if normalized not in VALID_SESSION_LANE_POLICIES:
            return "queue"
        return normalized

    def _coerce(self, payload: Dict[str, Any] | None = None) -> RuntimeStabilityConfig:
        raw = dict(payload or storage.get_runtime_stability_config() or {})
        return RuntimeStabilityConfig(
            strict_supervisor_durability=bool(raw.get("strictSupervisorDurability", raw.get("strict_supervisor_durability", True))),
            session_lane_policy=self._normalize_policy(raw.get("sessionLanePolicy", raw.get("session_lane_policy", "queue"))),
        )

    def get_config(self) -> RuntimeStabilityConfig:
        return self._coerce()

    def save_config(self, payload: Dict[str, Any]) -> RuntimeStabilityConfig:
        current = self.get_config().as_dict()
        current.update(dict(payload or {}))
        normalized = self._coerce(current)
        storage.save_runtime_stability_config(normalized.as_dict())
        return normalized

    def strict_supervisor_durability(self) -> bool:
        return self.get_config().strict_supervisor_durability

    def session_lane_policy(self) -> str:
        return self.get_config().session_lane_policy

    def build_payload(self) -> Dict[str, Any]:
        config = self.get_config()
        return {
            **config.as_dict(),
            "allowedSessionLanePolicies": sorted(VALID_SESSION_LANE_POLICIES),
            "paths": {
                "configPath": f"{CONFIG_JSON_PATH}#runtimeStability",
                "stateDbPath": str(STATE_DB_PATH),
                "checkpointDbPath": str(CHECKPOINT_DB_PATH),
            },
            "summaries": {
                "strictSupervisorDurability": "禁止 Supervisor 长任务静默回退到 MemorySaver，要求显式 durable checkpointer。",
                "sessionLanePolicy": {
                    "queue": "同一会话后来的任务排队等待，优先稳定。",
                    "reject": "同一会话忙碌时直接拒绝新任务，优先避免互踩。",
                    "interrupt_then_replace": "新任务会先打断当前任务，再抢占当前会话。",
                },
            },
        }


runtime_stability_service = RuntimeStabilityService()
