from __future__ import annotations

import asyncio
from concurrent.futures import Executor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable


class BackgroundModelTimeoutError(TimeoutError):
    def __init__(
        self,
        *,
        timeout_seconds: float,
        cancellation_requested: bool,
        cancellation_acknowledged: bool,
        stage: str,
    ) -> None:
        super().__init__(f"background model request timed out after {timeout_seconds:.3f}s")
        self.timeout_seconds = float(timeout_seconds)
        self.cancellation_requested = bool(cancellation_requested)
        self.cancellation_acknowledged = bool(cancellation_acknowledged)
        self.stage = str(stage or "provider_request")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "timeoutSeconds": self.timeout_seconds,
            "cancelRequested": self.cancellation_requested,
            "cancelAcknowledged": self.cancellation_acknowledged,
            "timeoutStage": self.stage,
        }


def invoke_background_model_with_timeout(
    model: Any,
    messages: Any,
    *,
    timeout_seconds: float,
    config: dict[str, Any] | None = None,
) -> Any:
    timeout_budget = max(float(timeout_seconds), 0.05)

    async def _invoke() -> Any:
        task = asyncio.create_task(model.ainvoke(messages, config=config))
        try:
            return await asyncio.wait_for(task, timeout=timeout_budget)
        except asyncio.TimeoutError as exc:
            if not task.done():
                task.cancel()
            cancellation_acknowledged = task.cancelled()
            if not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    cancellation_acknowledged = True
            raise BackgroundModelTimeoutError(
                timeout_seconds=timeout_budget,
                cancellation_requested=True,
                cancellation_acknowledged=cancellation_acknowledged or task.cancelled(),
                stage="provider_request",
            ) from exc

    return asyncio.run(_invoke())


def run_cancellable_background_call(
    executor: Executor,
    invoke: Callable[[], Any],
    *,
    timeout_seconds: float,
    cancellation_grace_seconds: float = 0.75,
) -> Any:
    timeout_budget = max(float(timeout_seconds), 0.05)
    future = executor.submit(invoke)
    try:
        return future.result(timeout=timeout_budget + max(float(cancellation_grace_seconds), 0.05))
    except BackgroundModelTimeoutError:
        raise
    except FuturesTimeoutError as exc:
        cancellation_acknowledged = future.cancel()
        raise BackgroundModelTimeoutError(
            timeout_seconds=timeout_budget,
            cancellation_requested=True,
            cancellation_acknowledged=cancellation_acknowledged,
            stage="executor_backstop",
        ) from exc
