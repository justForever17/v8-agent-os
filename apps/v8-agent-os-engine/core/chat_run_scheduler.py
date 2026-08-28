from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Coroutine
from typing import Any


logger = logging.getLogger(__name__)


class ChatRunScheduler:
    """Own one long-lived event loop for Supervisor chat runs."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._failure: BaseException | None = None
        self._lock = threading.Lock()
        self._futures: set[concurrent.futures.Future[Any]] = set()

    async def start(self) -> None:
        if self.is_ready():
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._failure = None
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name="chat-run-scheduler",
                    daemon=True,
                )
                self._thread.start()
        ready = await asyncio.to_thread(self._ready.wait, 5.0)
        if not ready or not self.is_ready():
            raise RuntimeError("Chat run scheduler did not become ready") from self._failure

    def submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        task_name: str = "chat-run",
    ) -> concurrent.futures.Future[Any]:
        loop = self._loop
        if not self.is_ready() or loop is None:
            coroutine.close()
            raise RuntimeError("chat_run_scheduler_unavailable")
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            coroutine.close()
            raise
        with self._lock:
            self._futures.add(future)

        def done(completed: concurrent.futures.Future[Any]) -> None:
            with self._lock:
                self._futures.discard(completed)
            try:
                completed.result()
            except concurrent.futures.CancelledError:
                return
            except Exception:
                logger.exception("Scheduled chat task '%s' failed", task_name)

        future.add_done_callback(done)
        return future

    async def run(self, coroutine: Coroutine[Any, Any, Any], *, task_name: str) -> Any:
        return await asyncio.wrap_future(self.submit(coroutine, task_name=task_name))

    async def stop(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        with self._lock:
            futures = list(self._futures)
        for future in futures:
            future.cancel()
        loop.call_soon_threadsafe(loop.stop)
        await asyncio.to_thread(thread.join, 5.0)
        if thread.is_alive():
            raise RuntimeError("Chat run scheduler did not stop within 5 seconds")
        self._thread = None
        self._loop = None
        self._ready.clear()
        self._failure = None
        with self._lock:
            self._futures.clear()

    def is_ready(self) -> bool:
        thread = self._thread
        loop = self._loop
        return bool(
            self._ready.is_set()
            and thread
            and thread.is_alive()
            and loop
            and loop.is_running()
            and self._failure is None
        )

    def readiness_status(self) -> dict[str, Any]:
        with self._lock:
            active = len(self._futures)
        return {
            "ready": self.is_ready(),
            "threadAlive": bool(self._thread and self._thread.is_alive()),
            "eventLoopRunning": bool(self._loop and self._loop.is_running()),
            "activeTasks": active,
            "failureType": type(self._failure).__name__ if self._failure else "",
        }

    def _run_loop(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            loop.run_forever()
        except BaseException as exc:
            self._failure = exc
            logger.exception("Chat run scheduler stopped unexpectedly")
        finally:
            self._ready.set()
            if loop is not None:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            if self._loop is loop:
                self._loop = None


chat_run_scheduler = ChatRunScheduler()
