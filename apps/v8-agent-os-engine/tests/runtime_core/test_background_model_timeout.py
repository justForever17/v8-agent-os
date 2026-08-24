from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.background_model_timeout import (
    BackgroundModelTimeoutError,
    invoke_background_model_with_timeout,
    run_cancellable_background_call,
)


class _BlockingAsyncModel:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def ainvoke(self, _messages, config=None):  # noqa: ANN001
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def test_background_timeout_cancels_and_awaits_provider_request() -> None:
    model = _BlockingAsyncModel()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(BackgroundModelTimeoutError) as exc_info:
            run_cancellable_background_call(
                executor,
                lambda: invoke_background_model_with_timeout(
                    model,
                    [],
                    timeout_seconds=0.05,
                    config={"callbacks": []},
                ),
                timeout_seconds=0.05,
            )

    assert model.started.is_set()
    assert model.cancelled.is_set()
    assert exc_info.value.stage == "provider_request"
    assert exc_info.value.cancellation_requested is True
    assert exc_info.value.cancellation_acknowledged is True
