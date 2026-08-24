from __future__ import annotations

from typing import Any, Dict, Optional

from core.database import db
from core.realtime_protocol import build_runtime_event, utc_now_iso

from erc.snapshot_service import snapshot_service


class SessionRuntimeService:
    def subscribe(self, session_id: str, *, include_snapshot: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ack": {
                "v": 1,
                "kind": "ack",
                "topic": "session.subscribed",
                "event_id": f"evt_sub_{session_id}_{db.get_latest_runtime_seq(session_id)}",
                "session_id": session_id,
                "conversation_id": session_id,
                "ts": utc_now_iso(),
                "payload": {
                    "accepted": True,
                    "session_id": session_id,
                    "latest_seq": db.get_latest_runtime_seq(session_id),
                },
            }
        }
        if include_snapshot:
            snapshot_payload = snapshot_service.build_chat_projection_payload(session_id)
            if snapshot_payload:
                payload["snapshot_event"] = build_runtime_event(
                    kind="snapshot",
                    topic="session.snapshot.ready",
                    session_id=session_id,
                    conversation_id=session_id,
                    seq=snapshot_payload.get("latestSeq"),
                    payload=snapshot_payload,
                    source={
                        "plane": "engine",
                        "component": "session_runtime",
                        "node": "snapshot_service",
                        "agent_id": None,
                    },
                )
        return payload

    def get_runtime_events(
        self,
        session_id: str,
        *,
        after_seq: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return {
            "session_id": session_id,
            "events": db.get_runtime_events(session_id, after_seq=after_seq, limit=limit),
            "latestSeq": db.get_latest_runtime_seq(session_id),
        }

    def get_snapshot(self, session_id: str) -> Dict[str, Any]:
        return snapshot_service.build_chat_projection_payload(session_id)


session_runtime_service = SessionRuntimeService()
