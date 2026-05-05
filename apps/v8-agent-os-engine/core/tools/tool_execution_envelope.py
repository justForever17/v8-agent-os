from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass
from typing import Any, Callable


TERMINAL_FAILURE_CLASSES = {
    "auth_failed",
    "blocked_by_safety",
    "network_timeout",
    "policy_reject",
    "provider_error",
    "unsupported_operation",
}


def classify_failure(error: Any, *, blocked: bool = False, fallback: str = "tool_error") -> str:
    if blocked:
        return "blocked_by_safety"
    text = str(error or "").strip().lower()
    if "timeout" in text or "timed_out" in text or "deadline" in text or "err_connection_timed_out" in text:
        return "network_timeout"
    if "unauthorized" in text or "forbidden" in text or "api key" in text or "invalid key" in text:
        return "auth_failed"
    if "policy" in text or "safety" in text or "blocked" in text:
        return "policy_reject"
    if "unsupported" in text or "not implemented" in text:
        return "unsupported_operation"
    if "provider" in text or "upstream" in text or "gateway" in text:
        return "provider_error"
    return fallback


@dataclass(slots=True)
class ToolExecutionEnvelope:
    tool_name: str
    family: str
    deadline_ms: int
    attempt: int = 1
    retry_limit: int = 1
    started_at: float = 0.0

    def __enter__(self) -> "ToolExecutionEnvelope":
        self.started_at = time.monotonic()
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def elapsed_ms(self) -> int:
        if not self.started_at:
            return 0
        return int((time.monotonic() - self.started_at) * 1000)

    def payload(
        self,
        *,
        ok: bool,
        failure_class: str = "",
        retryable: bool | None = None,
        recommended_next_action: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_failure = str(failure_class or "").strip()
        if retryable is None:
            retryable = bool(normalized_failure) and normalized_failure not in TERMINAL_FAILURE_CLASSES and self.attempt <= self.retry_limit
        payload = {
            "toolName": self.tool_name,
            "family": self.family,
            "deadlineMs": int(self.deadline_ms),
            "attempt": int(self.attempt),
            "retryLimit": int(self.retry_limit),
            "elapsedMs": self.elapsed_ms(),
            "failureClass": normalized_failure,
            "retryable": bool(retryable),
            "recommendedNextAction": recommended_next_action,
        }
        if extra:
            payload.update(extra)
        return payload

    def failure_payload(
        self,
        *,
        summary: str,
        failure_class: str,
        error: str = "",
        retryable: bool | None = None,
        recommended_next_action: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "kind": "tool_deadline_envelope",
            "summary": summary,
            "toolExecution": self.payload(
                ok=False,
                failure_class=failure_class,
                retryable=retryable,
                recommended_next_action=recommended_next_action,
                extra=extra,
            ),
            "error": str(error or "").strip(),
        }


def run_sync_with_deadline(
    func: Callable[[], Any],
    *,
    envelope: ToolExecutionEnvelope,
    timeout_summary: str,
    recommended_next_action: str,
) -> tuple[bool, Any | dict[str, Any]]:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"v8-tool-{envelope.tool_name}")
    future = executor.submit(func)
    try:
        return True, future.result(timeout=max(float(envelope.deadline_ms) / 1000.0, 0.1))
    except concurrent.futures.TimeoutError:
        future.cancel()
        return False, envelope.failure_payload(
            summary=timeout_summary,
            failure_class="deadline_exceeded",
            error=f"{envelope.tool_name} exceeded {envelope.deadline_ms}ms deadline",
            retryable=False,
            recommended_next_action=recommended_next_action,
        )
    except Exception as exc:
        failure_class = classify_failure(exc)
        return False, envelope.failure_payload(
            summary=f"{envelope.tool_name} failed before completing.",
            failure_class=failure_class,
            error=str(exc),
            retryable=failure_class not in TERMINAL_FAILURE_CLASSES,
            recommended_next_action=recommended_next_action,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
