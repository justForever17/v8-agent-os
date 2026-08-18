from __future__ import annotations

from typing import Any, Dict, Optional

from erc.kernel import erc_kernel


STOP_COMMANDS = {"pause", "cancel", "interrupt", "approval_rejected"}


class RuntimeControlInterruption(RuntimeError):
    def __init__(self, signal: Dict[str, Any]) -> None:
        self.signal = dict(signal or {})
        command = str(self.signal.get("command") or "control").strip() or "control"
        reason = str(self.signal.get("reason") or "").strip()
        message = f"run {command}"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)


def consume_stop_signal(run_id: str | None) -> Optional[Dict[str, Any]]:
    if not run_id:
        return None
    signal = erc_kernel.consume_control_signal(run_id)
    if not isinstance(signal, dict):
        return None
    command = str(signal.get("command") or "").strip().lower()
    if command not in STOP_COMMANDS:
        return None
    signal["command"] = command
    return signal


def control_status(signal: Dict[str, Any] | None) -> str:
    command = str((signal or {}).get("command") or "").strip().lower()
    if command in {"pause", "approval_rejected"}:
        return "paused"
    if command == "interrupt":
        return "interrupted"
    return "cancelled"


def control_payload(signal: Dict[str, Any] | None) -> Dict[str, Any]:
    signal = dict(signal or {})
    return {
        "command": signal.get("command"),
        "reason": signal.get("reason"),
        "status": control_status(signal),
        "payload": dict(signal.get("payload") or {}),
    }


def apply_control_signal(
    run_handle: Any,
    *,
    signal: Dict[str, Any] | None,
    runtime_kind: str,
    node: str,
    extras: Optional[Dict[str, Any]] = None,
    refresh_snapshot: bool = True,
) -> Dict[str, Any]:
    payload = control_payload(signal)
    payload.update(
        {
            "runId": getattr(run_handle, "run_id", None),
            "sessionId": getattr(run_handle, "session_id", None),
            "runtimeKind": runtime_kind,
        }
    )
    if extras:
        payload.update(dict(extras))
    run_handle.emit("run.controlled", payload)
    run_handle.transition(
        payload["status"],
        reason=str(payload.get("reason") or payload.get("command") or "run_controlled"),
        node=node,
    )
    if refresh_snapshot:
        run_handle.refresh_chat_snapshot()
    return payload


def raise_for_stop_signal(run_id: str | None) -> None:
    signal = consume_stop_signal(run_id)
    if signal is not None:
        raise RuntimeControlInterruption(signal)
