from __future__ import annotations

import asyncio

import pytest

from core.chat_run_scheduler import ChatRunScheduler


def test_chat_run_scheduler_reuses_one_event_loop_across_runs() -> None:
    scheduler = ChatRunScheduler()

    async def scenario() -> None:
        loop_ids: list[int] = []

        async def record_loop(value: str) -> str:
            loop_ids.append(id(asyncio.get_running_loop()))
            await asyncio.sleep(0)
            return value

        await scheduler.start()
        try:
            first = await asyncio.wrap_future(
                scheduler.submit(record_loop("first"), task_name="first"),
            )
            second = await asyncio.wrap_future(
                scheduler.submit(record_loop("second"), task_name="second"),
            )
            assert (first, second) == ("first", "second")
            assert len(set(loop_ids)) == 1
            assert scheduler.readiness_status()["activeTasks"] == 0
        finally:
            await scheduler.stop()

    asyncio.run(scenario())


def test_chat_run_scheduler_closes_coroutine_when_submission_fails(monkeypatch) -> None:
    scheduler = ChatRunScheduler()

    async def scenario() -> None:
        async def never_started() -> None:
            await asyncio.sleep(0)

        await scheduler.start()
        coroutine = never_started()
        try:
            monkeypatch.setattr(
                asyncio,
                "run_coroutine_threadsafe",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("submit failed")),
            )
            with pytest.raises(RuntimeError, match="submit failed"):
                scheduler.submit(coroutine, task_name="submission-failure")
            assert coroutine.cr_frame is None
        finally:
            await scheduler.stop()

    asyncio.run(scenario())
