from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from core.database import db
from core.delegation_broker import normalize_task_briefs
from core.delegation_result_contract import build_delegation_result_contract
from core.json_safe import to_jsonable
from core.runtime_episodes import build_handoff_ref, build_runtime_episode
from core.time_truth import utc_now_iso
from core.workspace_state_digest import build_workspace_state_digest_context
from erc.runtime_context import bind_runtime_context


def _preview(value: Any, *, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 40)].rstrip() + "\n...[episode output omitted]"


def _first_tool_message_content(command: Any) -> str:
    update = dict(getattr(command, "update", None) or {})
    for message in list(update.get("messages") or []):
        content = getattr(message, "content", None)
        if content:
            return str(content)
    return ""


def _schedule_runtime_episode_handoff_resume(episode: dict[str, Any]) -> dict[str, Any]:
    try:
        from erc.command_router import runtime_command_router

        return runtime_command_router.schedule_runtime_episode_handoff_resume(episode)
    except Exception as exc:
        return {
            "resume_mode": "chat",
            "resume_scheduled": False,
            "resume_error": f"{type(exc).__name__}: {exc}",
        }


class RuntimeEpisodeRunner:
    """Durable SQLite-backed runner for RuntimeEpisode queue items.

    This is intentionally small: it establishes the lease/heartbeat/handoff
    contract first, then individual runtimes can deepen their executors without
    changing Supervisor-facing routing semantics.
    """

    def __init__(self) -> None:
        self.worker_id = f"episode-runner:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task | None = None
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._thread_started = threading.Event()
        self._thread_loop: asyncio.AbstractEventLoop | None = None
        self._lease_seconds = 75
        self._poll_seconds = 0.8
        self._max_concurrent = max(1, int(os.getenv("V8_RUNTIME_EPISODE_CONCURRENCY", "4") or 4))
        self._agent_nodes_map_cache: dict[str, Any] | None = None
        self._agent_nodes_map_snapshot_hash: str = ""
        self._agent_nodes_map_snapshot_version: str = ""

    async def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._task and not self._task.done():
            return
        self._stop_event = threading.Event()
        self._thread_started.clear()
        self._thread = threading.Thread(
            target=self._run_loop_in_thread,
            name="runtime-episode-runner",
            daemon=True,
        )
        self._thread.start()
        await asyncio.to_thread(self._thread_started.wait, 2.0)
        print(f"[EpisodeRunner] Started worker {self.worker_id}.")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._thread_loop and self._thread_loop.is_running():
            self._thread_loop.call_soon_threadsafe(lambda: None)
        if self._thread and self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, 3.0)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print(f"[EpisodeRunner] Stopped worker {self.worker_id}.")

    def _run_loop_in_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._thread_loop = loop
        asyncio.set_event_loop(loop)
        self._thread_started.set()
        try:
            loop.run_until_complete(self._run_loop())
        finally:
            loop.close()
            if self._thread_loop is loop:
                self._thread_loop = None

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        active_tasks: set[asyncio.Task] = set()
        while not self._stop_event.is_set():
            try:
                completed_tasks = {task for task in active_tasks if task.done()}
                active_tasks.difference_update(completed_tasks)
                for task in completed_tasks:
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        print(f"[EpisodeRunner] Episode task error: {type(exc).__name__}: {exc}")

                claimed_any = False
                while len(active_tasks) < self._max_concurrent:
                    episode = db.claim_runtime_episode(
                        worker_id=self.worker_id,
                        lease_seconds=self._lease_seconds,
                        require_bound_run=True,
                    )
                    if not episode:
                        break
                    episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
                    task = asyncio.create_task(
                        self._execute_episode(episode),
                        name=f"runtime-episode:{episode_id or 'unknown'}",
                    )
                    active_tasks.add(task)
                    claimed_any = True
                if not claimed_any:
                    await asyncio.sleep(self._poll_seconds)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[EpisodeRunner] Loop error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(1.0)
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

    async def _execute_episode(self, episode: dict[str, Any]) -> None:
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        kind = str(episode.get("kind") or "unknown").strip()
        target_kind = str(episode.get("targetKind") or episode.get("target_kind") or "local_runtime").strip() or "local_runtime"
        session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip() or None
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip() or None
        self._emit("runtime.episode.started", episode=episode, session_id=session_id, run_id=run_id)
        try:
            self._heartbeat(episode_id, "executor starting")
            if self._should_dispatch_child_needs(episode):
                waiting = self._dispatch_child_needs(episode, session_id=session_id, run_id=run_id)
                self._emit("runtime.episode.waiting", episode=waiting, session_id=session_id, run_id=run_id)
                return
            if target_kind == "network_peer":
                handoff = await self._execute_network_peer_target(episode)
            elif target_kind == "external_worker":
                handoff = await self._execute_external_worker_target(episode)
            elif kind == "research":
                handoff = await self._execute_research(episode)
            elif kind == "engineering":
                handoff = await self._execute_engineering(episode)
            elif kind == "creative_media":
                handoff = await self._execute_creative_media(episode)
            elif kind == "computer_use":
                handoff = await self._execute_computer_use(episode)
            elif kind == "rpa":
                handoff = await self._execute_rpa(episode)
            elif kind == "delegation":
                handoff = await self._execute_delegation(episode)
            else:
                handoff = self._generic_handoff(episode, status="failed", summary=f"No executor registered for {kind}.")

            persisted_handoff = db.add_runtime_episode_handoff(
                episode_id=episode_id,
                handoff=handoff,
                session_id=session_id,
                run_id=run_id,
            )
            handoff_status = str(handoff.get("status") or "ready").strip().lower()
            if handoff_status in {"running", "waiting", "pending"}:
                waiting_state = "waiting_external" if target_kind in {"network_peer", "external_worker"} else "waiting"
                if self._handoff_has_child_episodes(handoff):
                    waiting_state = "waiting_child"
                waiting = db.complete_runtime_episode(
                    episode_id,
                    state=waiting_state,
                    result_ref=str(persisted_handoff.get("handoffId") or persisted_handoff.get("handoffRefId") or ""),
                    metadata={"handoff": persisted_handoff, "waitingReason": handoff_status},
                ) or {**episode, "state": waiting_state}
                self._emit(
                    "runtime.episode.waiting",
                    episode=waiting,
                    handoff=persisted_handoff,
                    session_id=session_id,
                    run_id=run_id,
                )
                return
            if handoff_status in {"failed", "blocked"} and self._can_retry(episode):
                retry_episode = db.retry_runtime_episode(
                    episode_id,
                    error_message=str(handoff.get("errorMessage") or handoff.get("compactSummary") or "episode failed; retry scheduled"),
                    delay_seconds=self._retry_delay_seconds(episode),
                ) or {**episode, "state": "queued"}
                self._emit(
                    "runtime.episode.retry_scheduled",
                    episode=retry_episode,
                    handoff=persisted_handoff,
                    session_id=session_id,
                    run_id=run_id,
                )
                return
            if handoff_status == "degraded":
                final_state = "degraded"
            elif handoff_status in {"cancelled", "canceled"}:
                final_state = "cancelled"
            else:
                final_state = "completed" if handoff_status not in {"failed", "blocked"} else "failed"
            recovery = self._build_recovery_bundle(episode, handoff, final_state=final_state)
            completed = db.complete_runtime_episode(
                episode_id,
                state=final_state,
                result_ref=str(persisted_handoff.get("handoffId") or persisted_handoff.get("handoffRefId") or ""),
                error_code=str(handoff.get("errorCode") or "") or None,
                error_message=str(handoff.get("errorMessage") or "") or None,
                metadata={"handoff": persisted_handoff, "recovery": recovery},
            )
            event_type = "runtime.episode.completed"
            if final_state == "failed":
                event_type = "runtime.episode.failed"
            elif final_state == "degraded":
                event_type = "runtime.episode.degraded"
            elif final_state == "cancelled":
                event_type = "runtime.episode.cancelled"
            self._emit(
                event_type,
                episode=completed or {**episode, "state": final_state},
                handoff=persisted_handoff,
                recovery=recovery,
                session_id=session_id,
                run_id=run_id,
            )
            self._maybe_schedule_chat_handoff_resume(completed or {**episode, "state": final_state})
            self._maybe_resume_parent_episode(completed or episode, session_id=session_id, run_id=run_id)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            if self._can_retry(episode):
                retry_episode = db.retry_runtime_episode(
                    episode_id,
                    error_message=error_message,
                    delay_seconds=self._retry_delay_seconds(episode),
                ) or {**episode, "state": "queued"}
                self._emit(
                    "runtime.episode.retry_scheduled",
                    episode=retry_episode,
                    error={"code": "episode_executor_error", "message": error_message, "recoverable": True},
                    session_id=session_id,
                    run_id=run_id,
                )
                return
            failed = db.complete_runtime_episode(
                episode_id,
                state="failed",
                error_code="episode_executor_error",
                error_message=error_message,
                metadata={"recoverable": True},
            )
            self._emit(
                "runtime.episode.failed",
                episode=failed or {**episode, "state": "failed"},
                error={"code": "episode_executor_error", "message": error_message, "recoverable": True},
                session_id=session_id,
                run_id=run_id,
            )
            self._maybe_schedule_chat_handoff_resume(failed or {**episode, "state": "failed"})
            self._maybe_resume_parent_episode(failed or {**episode, "state": "failed"}, session_id=session_id, run_id=run_id)

    def _heartbeat(self, episode_id: str, progress: str) -> None:
        db.heartbeat_runtime_episode(
            episode_id,
            worker_id=self.worker_id,
            progress=progress,
            lease_seconds=self._lease_seconds,
        )

    async def _await_with_heartbeat(self, episode_id: str, awaitable: Any, *, progress: str, interval_seconds: float = 20.0) -> Any:
        task = asyncio.create_task(awaitable)
        while not task.done():
            self._heartbeat(episode_id, progress)
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue
        return await task

    def _emit(
        self,
        topic: str,
        *,
        episode: dict[str, Any],
        session_id: str | None,
        run_id: str | None,
        **payload: Any,
    ) -> None:
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        event_payload = {"episode": to_jsonable(episode), **to_jsonable(payload)}
        if episode_id:
            db.add_runtime_episode_event_record(
                episode_id=episode_id,
                topic=topic,
                payload=event_payload,
                session_id=session_id,
                run_id=run_id,
                state=str(episode.get("state") or ""),
            )
        if session_id:
            db.add_runtime_event(
                {
                    "event_id": f"evt_{topic.replace('.', '_')}_{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "run_id": run_id,
                    "seq": db.get_next_runtime_seq(session_id),
                    "kind": "runtime_event",
                    "topic": topic,
                    "ts": utc_now_iso(),
                    "source": {"runtime": "episode_runner", "workerId": self.worker_id},
                    "payload": event_payload,
                }
            )

    def _generic_handoff(self, episode: dict[str, Any], *, status: str, summary: str) -> dict[str, Any]:
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or episode.get("id") or ""),
            kind=f"{episode.get('kind') or 'runtime'}_handoff",
            compact_summary=summary,
            status=status,
            confidence="low" if status != "ready" else "medium",
            consumer_hint="Route can be retried after configuring the matching runtime executor.",
            extra={"errorCode": "no_matching_runtime_executor"} if status == "failed" else None,
        )

    def _episode_attempt(self, episode: dict[str, Any]) -> int:
        try:
            return int(episode.get("attempt_count") or episode.get("attemptCount") or 0)
        except Exception:
            return 0

    def _retry_policy(self, episode: dict[str, Any]) -> dict[str, Any]:
        policy = episode.get("retryPolicy")
        return dict(policy) if isinstance(policy, dict) else {}

    def _can_retry(self, episode: dict[str, Any]) -> bool:
        policy = self._retry_policy(episode)
        max_attempts = int(policy.get("maxAttempts") or policy.get("max_attempts") or 1)
        if max_attempts <= 1:
            return False
        return self._episode_attempt(episode) < max_attempts

    def _retry_delay_seconds(self, episode: dict[str, Any]) -> int:
        policy = self._retry_policy(episode)
        try:
            value = policy["delaySeconds"] if "delaySeconds" in policy else policy.get("delay_seconds", 2)
            return max(0, int(value))
        except Exception:
            return 2

    def _build_recovery_bundle(self, episode: dict[str, Any], handoff: dict[str, Any], *, final_state: str) -> dict[str, Any]:
        failed = final_state == "failed"
        cancelled = final_state == "cancelled"
        incomplete = failed or cancelled
        compensation = episode.get("compensationPlan") if isinstance(episode.get("compensationPlan"), dict) else {}
        refs = list(handoff.get("refs") or [])
        return {
            "episodeId": episode.get("episodeId") or episode.get("id"),
            "kind": episode.get("kind"),
            "state": final_state,
            "done": refs if refs else ([handoff.get("artifactId")] if handoff.get("artifactId") else []),
            "notDone": [episode.get("reason") or (episode.get("need") or {}).get("reason") or "episode work"] if incomplete else [],
            "canRetry": failed and self._can_retry(episode),
            "canReplaceTarget": failed and bool(episode.get("targetKind") or episode.get("targetId")),
            "canContinueParent": not incomplete,
            "compensationPlan": compensation,
            "nextAction": (
                "report_cancelled" if cancelled else ("retry_or_replace_target" if failed else "merge_handoff_into_parent")
            ),
        }

    def _child_needs_from_episode(self, episode: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = dict(episode.get("inputs") or {})
        need = dict(episode.get("need") or {})
        raw = inputs.get("capabilityNeeds") or inputs.get("childNeeds") or need.get("capabilityNeeds") or need.get("childNeeds") or []
        return [dict(item) for item in list(raw or []) if isinstance(item, dict)]

    def _handoff_has_child_episodes(self, handoff: dict[str, Any]) -> bool:
        if not isinstance(handoff, dict):
            return False
        if handoff.get("childEpisodeIds"):
            return True
        nested = handoff.get("delegationHandoff")
        return isinstance(nested, dict) and bool(nested.get("childEpisodeIds"))

    def _is_optional_episode(self, episode: dict[str, Any]) -> bool:
        if not isinstance(episode, dict):
            return False
        inputs = dict(episode.get("inputs") or {})
        metadata = dict(episode.get("metadata") or {})
        return any(
            bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
            for source in (episode, inputs, metadata)
            if isinstance(source, dict)
        ) or str(inputs.get("dependencyMode") or metadata.get("dependencyMode") or "").strip().lower() in {
            "optional",
            "degraded_ok",
        }

    def _is_degraded_handoff(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        status = str(payload.get("status") or "").strip().lower()
        kind = str(payload.get("kind") or "").strip().lower()
        state = str(payload.get("delegationState") or payload.get("engineeringState") or "").strip().lower()
        return status == "degraded" or kind.endswith("_degraded") or state.endswith("_degraded")

    def _should_dispatch_child_needs(self, episode: dict[str, Any]) -> bool:
        metadata = dict(episode.get("metadata") or {})
        if metadata.get("childNeedsDispatched"):
            return False
        return bool(self._child_needs_from_episode(episode))

    def _dispatch_child_needs(
        self,
        episode: dict[str, Any],
        *,
        session_id: str | None,
        run_id: str | None,
    ) -> dict[str, Any]:
        parent_id = str(episode.get("episodeId") or episode.get("id") or "")
        child_ids: list[str] = []
        for index, child_need in enumerate(self._child_needs_from_episode(episode), start=1):
            child_kind = str(child_need.get("kind") or child_need.get("runtimeKind") or "delegation")
            child_need.setdefault("source", f"{episode.get('kind') or 'runtime'}_episode")
            child_need.setdefault("reason", child_need.get("reason") or f"Child capability requested by {parent_id}.")
            child_need.setdefault("parentEpisodeId", parent_id)
            child_need.setdefault("needId", f"{parent_id}:child:{index}:{child_kind}")
            child_episode = build_runtime_episode(
                need=child_need,
                kind=child_kind,
                state="queued",
                required_runtime_access=list(child_need.get("requiredRuntimeAccess") or []),
                parent_episode_id=parent_id,
                continuation_target="runtime_episode_runner",
                extra={
                    "rootEpisodeId": episode.get("rootEpisodeId") or episode.get("root_episode_id") or parent_id,
                    "idempotencyKey": child_need.get("idempotencyKey") or f"{parent_id}:child:{index}:{child_kind}",
                    "retryPolicy": child_need.get("retryPolicy") or {"maxAttempts": 1},
                    "targetKind": child_need.get("targetKind") or "local_runtime",
                    "targetId": child_need.get("targetId") or child_kind,
                },
            )
            persisted = db.upsert_runtime_episode_record(child_episode, session_id=session_id, run_id=run_id, enqueue=True)
            child_ids.append(str(persisted.get("episodeId") or child_episode.get("episodeId")))
            self._emit(
                "capability.need.detected",
                episode=persisted,
                parentEpisodeId=parent_id,
                session_id=session_id,
                run_id=run_id,
            )
            self._emit("runtime.episode.queued", episode=persisted, session_id=session_id, run_id=run_id)
        metadata = {**dict(episode.get("metadata") or {}), "childNeedsDispatched": True, "childEpisodeIds": child_ids}
        waiting = db.complete_runtime_episode(
            parent_id,
            state="waiting_child",
            metadata={"childNeedsDispatched": True, "childEpisodeIds": child_ids, "resumeReason": "waiting_child_handoffs"},
        ) or {**episode, "state": "waiting_child", "metadata": metadata}
        return waiting

    def _maybe_resume_parent_episode(
        self,
        episode: dict[str, Any],
        *,
        session_id: str | None,
        run_id: str | None,
    ) -> None:
        parent_id = str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip()
        if not parent_id:
            return
        parent = db.get_runtime_episode(parent_id)
        if not parent or str(parent.get("state") or "") not in {"waiting_child", "waiting"}:
            return
        children = db.list_runtime_episodes(parent_episode_id=parent_id, limit=1000)
        if not children:
            return
        terminal = {"completed", "failed", "cancelled", "merged", "degraded"}
        if any(str(child.get("state") or "") not in terminal for child in children):
            return
        completed_children = [child for child in children if str(child.get("state") or "") in {"completed", "merged"}]
        failed_children = [child for child in children if str(child.get("state") or "") in {"failed", "cancelled", "degraded"}]
        child_handoffs: list[dict[str, Any]] = []
        for child in [*completed_children, *failed_children]:
            for handoff in db.list_runtime_episode_handoffs(str(child.get("episodeId") or child.get("id") or "")):
                payload = dict(handoff.get("payload") or {})
                if payload:
                    child_handoffs.append(payload)
        resume_token = {
            "resumedFrom": "child_handoffs",
            "childEpisodeIds": [child.get("episodeId") for child in children],
            "handoffIds": [item.get("handoffId") or item.get("handoffRefId") for item in child_handoffs],
            "childHandoffs": child_handoffs,
            "handoffBundle": child_handoffs,
            "failedChildCount": len(failed_children),
        }
        if failed_children:
            def _is_budget_boundary_handoff(payload: dict[str, Any]) -> bool:
                if not isinstance(payload, dict):
                    return False
                if payload.get("budgetBlockedChildDelegations"):
                    return True
                results = payload.get("results")
                if not isinstance(results, list) or not results:
                    return False
                return all(
                    str(item.get("error") or "").strip() == "child_delegation_not_allowed"
                    or str(item.get("dispatchStatus") or "").strip() == "dispatch_missing_child_budget"
                    for item in results
                    if isinstance(item, dict)
                )

            budget_boundary_only = bool(child_handoffs) and all(
                _is_budget_boundary_handoff(item)
                for item in child_handoffs
                if isinstance(item, dict)
            )
            optional_or_degraded_only = bool(failed_children) and all(
                self._is_optional_episode(child)
                for child in failed_children
            )
            degraded_handoff_only = bool(child_handoffs) and all(
                self._is_degraded_handoff(item) or _is_budget_boundary_handoff(item)
                for item in child_handoffs
                if isinstance(item, dict)
            )
            if budget_boundary_only or optional_or_degraded_only or degraded_handoff_only:
                resume_token["failedChildCount"] = 0
                if budget_boundary_only:
                    resume_token["budgetBoundaryChildCount"] = len(failed_children)
                if optional_or_degraded_only or degraded_handoff_only:
                    resume_token["degradedChildCount"] = len(failed_children)
                resumed = db.resume_runtime_episode(parent_id, resume_token=resume_token) or parent
                self._emit(
                    "runtime.episode.resumed",
                    episode=resumed,
                    session_id=session_id,
                    run_id=run_id,
                    handoffBundle=child_handoffs,
                    resumeToken=resume_token,
                    warning={
                        "code": "child_lane_degraded" if optional_or_degraded_only or degraded_handoff_only else "child_delegation_budget_boundary",
                        "message": (
                            "Optional child lane degraded; parent episode can continue with degraded synthesis."
                            if optional_or_degraded_only or degraded_handoff_only
                            else "Child delegation reached its recursion budget; parent episode can continue with a warning."
                        ),
                    },
                )
                return
            updated = db.complete_runtime_episode(
                parent_id,
                state="failed",
                error_code="child_episode_failed",
                error_message=f"{len(failed_children)} child episode(s) failed.",
                metadata={"resumeToken": resume_token, "childHandoffs": child_handoffs, "recoverable": True},
            ) or parent
            self._emit("runtime.episode.failed", episode=updated, session_id=session_id, run_id=run_id, resumeToken=resume_token)
            self._maybe_resume_parent_episode(updated, session_id=session_id, run_id=run_id)
            return
        resumed = db.resume_runtime_episode(parent_id, resume_token=resume_token) or parent
        self._emit(
            "runtime.episode.resumed",
            episode=resumed,
            session_id=session_id,
            run_id=run_id,
            handoffBundle=child_handoffs,
            resumeToken=resume_token,
        )

    def _maybe_schedule_chat_handoff_resume(self, episode: dict[str, Any]) -> None:
        parent_id = str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip()
        if parent_id:
            return
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip()
        session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip()
        state = str(episode.get("state") or "").strip()
        if not episode_id or not run_id or not session_id:
            return
        if state not in {"completed", "degraded", "failed", "cancelled"}:
            return
        result = _schedule_runtime_episode_handoff_resume(episode)
        self._emit(
            "runtime.episode.handoff_resume_scheduled"
            if bool(result.get("resume_scheduled"))
            else "runtime.episode.handoff_resume_not_scheduled",
            episode=episode,
            session_id=session_id,
            run_id=run_id,
            resume=result,
        )

    def _load_child_handoffs_from_resume_token(self, resume_token: dict[str, Any]) -> list[dict[str, Any]]:
        child_handoffs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_child_id in list(resume_token.get("childEpisodeIds") or []):
            child_id = str(raw_child_id or "").strip()
            if not child_id:
                continue
            for handoff in db.list_runtime_episode_handoffs(child_id):
                payload = dict(handoff.get("payload") or {})
                if not payload:
                    continue
                handoff_id = str(payload.get("handoffId") or payload.get("handoffRefId") or handoff.get("handoffId") or "")
                dedupe_key = handoff_id or f"{child_id}:{len(child_handoffs)}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                child_handoffs.append(payload)
        return child_handoffs

    async def _execute_research(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "research: experience lookup")
        need = dict(episode.get("need") or {})
        inputs = dict(episode.get("inputs") or {})
        task_briefs = [dict(item) for item in list(inputs.get("taskBriefs") or inputs.get("tasks") or []) if isinstance(item, dict)]
        brief_query = ""
        for brief in task_briefs:
            for key in ("routeQuery", "query", "question", "goal", "title"):
                value = str(brief.get(key) or "").strip()
                if value:
                    brief_query = value
                    break
            if brief_query:
                break
        query = str(inputs.get("query") or inputs.get("question") or need.get("query") or brief_query or need.get("reason") or "research request").strip()
        state = {
            "current_route_context": {
                "runtimeToolGrants": [{"group": "research.core", "runtimeKind": "research"}],
            }
        }
        from core.native_tools import research_broker

        search_result = research_broker.func(
            mode="search_experience",
            query=query,
            state=state,
            tool_call_id=f"episode:{episode.get('episodeId')}:search_experience",
        )
        search_visible = _first_tool_message_content(search_result)
        run_mode = str(inputs.get("mode") or need.get("mode") or "").strip().lower()
        research_blob = json.dumps(
            {
                "inputs": inputs,
                "need": need,
                "taskBriefs": task_briefs,
            },
            ensure_ascii=False,
            default=str,
        ).lower()
        if not run_mode and any(
            marker in research_blob
            for marker in (
                "full_read",
                "multi_source",
                "evidence_bundle",
                "claim_table",
                "claimtable",
                "sourcematrix",
                "source_matrix",
                "architect",
                "citations",
                "source_quality",
            )
        ):
            run_mode = "run"
        if run_mode == "run":
            self._heartbeat(str(episode.get("episodeId")), "research: live run")
            run_result = research_broker.func(
                mode="run",
                question=query,
                query=query,
                state=state,
                tool_call_id=f"episode:{episode.get('episodeId')}:research_run",
            )
            visible = _first_tool_message_content(run_result) or search_visible
        else:
            self._heartbeat(str(episode.get("episodeId")), "research: plan")
            plan_result = research_broker.func(
                mode="plan",
                question=query,
                query=query,
                state=state,
                tool_call_id=f"episode:{episode.get('episodeId')}:research_plan",
            )
            visible = _first_tool_message_content(plan_result) or search_visible
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="research",
                compact_summary=_preview(visible or f"Research plan prepared for: {query}"),
                status="degraded",
                confidence="low",
                consumer_hint="This is a research plan, not source-backed evidence. Route a research run before using it as evidence.",
                extra={
                    "query": query,
                    "researchRefs": [],
                    "runMode": "plan",
                    "researchState": "plan_only",
                    "degradedReason": "research_plan_only_no_evidence",
                    "recommendedNextAction": "route_research_run",
                },
            )
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or ""),
            kind="research",
            compact_summary=_preview(visible or f"Research routed for: {query}"),
            status="ready",
            confidence="medium",
            consumer_hint="Use this research handoff as evidence refs input for Engineering/Creative episodes.",
            extra={"query": query, "researchRefs": [f"episode:{episode.get('episodeId')}"], "runMode": run_mode or "plan"},
        )

    async def _execute_engineering(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "engineering: workspace digest")
        need = dict(episode.get("need") or {})
        inputs = dict(episode.get("inputs") or {})
        resume_token = dict(episode.get("resumeToken") or episode.get("resume_token") or {})
        child_handoffs = list(resume_token.get("handoffBundle") or resume_token.get("childHandoffs") or [])
        if not child_handoffs and str(resume_token.get("resumedFrom") or "") == "child_handoffs":
            child_handoffs = self._load_child_handoffs_from_resume_token(resume_token)
        if not child_handoffs and str(resume_token.get("resumedFrom") or "") == "child_handoffs":
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="engineering",
                compact_summary="Engineering could not resume because completed child handoffs were not available.",
                status="failed",
                confidence="low",
                consumer_hint="Retry the parent episode after child handoff recovery; do not dispatch new child work blindly.",
                extra={
                    "engineeringState": "recoverable_failed",
                    "recoverable": True,
                    "errorCode": "child_handoff_missing",
                    "resumeToken": resume_token,
                },
            )
        if child_handoffs:
            ready_count = len(child_handoffs)
            skill_validation = self._validate_skill_artifact_if_requested(episode, need=need, inputs=inputs)
            if skill_validation and not skill_validation.get("ok"):
                return build_handoff_ref(
                    producer_episode_id=str(episode.get("episodeId") or ""),
                    kind="engineering",
                    compact_summary="Engineering recoverable_failed after child handoff: skill artifact validation failed.",
                    status="failed",
                    confidence="low",
                    consumer_hint="Repair the generated skill artifact before marking the episode completed.",
                    extra={
                        "engineeringState": "recoverable_failed",
                        "errorCode": "skill_artifact_validation_failed",
                        "skillArtifactValidation": skill_validation,
                        "childHandoffs": child_handoffs,
                    },
                )
            visible_evidence_summary = self._delegation_handoff_visible_evidence_summary({"childHandoffs": child_handoffs})
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="engineering",
                compact_summary=(
                    f"Engineering handoff_ready after {ready_count} child delegation handoff(s).\n"
                    f"{visible_evidence_summary or _preview('; '.join(str(item.get('compactSummary') or item.get('summary') or item.get('kind') or '') for item in child_handoffs), limit=900)}"
                ),
                status="ready",
                confidence="medium",
                consumer_hint="Merge child delegation handoffs into Supervisor route context and continue orchestration.",
                extra={
                    "engineeringState": "skill_artifact_ready" if skill_validation and skill_validation.get("ok") else "handoff_ready",
                    "childHandoffs": child_handoffs,
                    "handoffRefs": [item.get("handoffId") or item.get("handoffRefId") for item in child_handoffs if isinstance(item, dict)],
                    "visibleEvidenceSummary": visible_evidence_summary,
                    **({"skillArtifactValidation": skill_validation} if skill_validation else {}),
                },
            )
        session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip() or None
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip() or None
        workspace_path = inputs.get("workspacePath") or need.get("workspacePath")
        digest_text, _digest_refs = build_workspace_state_digest_context(
            state={
                "run_id": run_id,
                "workspace_path": str(workspace_path) if workspace_path else None,
            },
            session_id=session_id,
        )
        context_summary = ""
        try:
            from runtimes.engineering.service import engineering_lane_service

            pack = engineering_lane_service.build_context_pack(
                user_query=str(inputs.get("task") or need.get("reason") or "engineering episode"),
                mode=str(inputs.get("mode") or "force"),
                session_id=session_id,
                run_id=run_id,
                workspace_path=str(workspace_path) if workspace_path else None,
                task_brief=inputs.get("taskBrief") if isinstance(inputs.get("taskBrief"), dict) else None,
            )
            trigger = dict(pack.get("trigger") or {})
            repo = dict((pack.get("repo") or pack.get("repoBrief") or {}))
            context_summary = (
                f"Engineering context ready: active={trigger.get('active')} "
                f"workspaceMode={trigger.get('workspaceMode')} repoDetected={repo.get('repoDetected')}"
            )
        except Exception as exc:
            context_summary = f"Engineering context initialized with workspace digest; context pack warning: {type(exc).__name__}: {exc}"
        worker_briefs = normalize_task_briefs(
            inputs.get("workerBriefs")
            or inputs.get("worker_briefs")
            or inputs.get("taskBriefs")
            or inputs.get("task_briefs")
            or inputs.get("tasks")
            or need.get("workerBriefs")
            or need.get("taskBriefs")
            or need.get("tasks")
        )
        plan_only = self._is_engineering_plan_only_request(need=need, inputs=inputs, worker_briefs=worker_briefs)
        blocked_tool_intent = inputs.get("blockedToolIntent") or need.get("blockedToolIntent")
        if not worker_briefs and isinstance(blocked_tool_intent, dict):
            tool_name = str(blocked_tool_intent.get("tool") or blocked_tool_intent.get("name") or "engineering action").strip()
            worker_briefs = normalize_task_briefs(
                [
                    {
                        "title": f"Handle blocked {tool_name}",
                        "goal": (
                            f"Execute the routed engineering work for blocked Supervisor tool `{tool_name}`. "
                            "Return touched files, proof, and any blockers."
                        ),
                        "context": {
                            "blockedToolIntent": blocked_tool_intent,
                            "workspacePath": str(workspace_path) if workspace_path else "",
                        },
                        "runtimeAccess": ["memory.read"],
                        "executionLaneHint": "subagent",
                        "acceptanceContract": "Return result summary, touched files, validation proof, and remaining risks.",
                    }
                ]
            )
        if worker_briefs and not plan_only:
            worker_briefs = self._prepare_engineering_worker_briefs_for_delegation(
                worker_briefs,
                need=need,
                inputs=inputs,
            )
            self._heartbeat(str(episode.get("episodeId")), "engineering: delegate executable work")
            delegation_episode = {
                **episode,
                "kind": "delegation",
                "inputs": {
                    **inputs,
                    "workerBriefs": worker_briefs,
                    "tasks": worker_briefs,
                    "targetCount": int(inputs.get("targetCount") or len(worker_briefs) or 1),
                    "workspacePath": str(workspace_path) if workspace_path else inputs.get("workspacePath"),
                    "proofExpectations": inputs.get("proofExpectations") or need.get("proofExpectations") or [],
                },
                "need": {
                    **need,
                    "kind": "delegation",
                    "reason": need.get("reason") or inputs.get("task") or "engineering execution requires delegated worker(s)",
                },
            }
            delegation_handoff = await self._execute_delegation(delegation_episode)
            visible_evidence_summary = self._delegation_handoff_visible_evidence_summary(delegation_handoff)
            skill_validation = self._validate_skill_artifact_if_requested(episode, need=need, inputs=inputs)
            if skill_validation and not skill_validation.get("ok"):
                return build_handoff_ref(
                    producer_episode_id=str(episode.get("episodeId") or ""),
                    kind="engineering",
                    compact_summary="Engineering degraded after delegated execution: skill artifact validation failed.",
                    status="degraded",
                    confidence="low",
                    consumer_hint="Repair the generated skill artifact before marking the episode completed.",
                    extra={
                        "engineeringState": "recoverable_failed",
                        "errorCode": "skill_artifact_validation_failed",
                        "recoverable": True,
                        "degraded": True,
                        "degradedReason": "skill_artifact_validation_failed",
                        "skillArtifactValidation": skill_validation,
                        "delegationHandoff": delegation_handoff,
                    },
                )
            delegation_status = str(delegation_handoff.get("status") or "ready").strip().lower()
            status = "waiting" if delegation_status in {"waiting", "pending", "running"} else delegation_status
            if status not in {"failed", "blocked", "waiting", "degraded"}:
                status = "ready"
            if (
                status in {"ready", "degraded"}
                and self._engineering_requires_write_evidence(need=need, inputs=inputs, worker_briefs=worker_briefs)
                and not self._delegation_handoff_has_write_evidence(delegation_handoff)
            ):
                return build_handoff_ref(
                    producer_episode_id=str(episode.get("episodeId") or ""),
                    kind="engineering",
                    compact_summary=(
                        "Engineering degraded after delegated execution: "
                        "delegation reported ready without concrete touched files, patch, artifact, or proof."
                    ),
                    status="degraded",
                    confidence="low",
                    consumer_hint=(
                        "Retry Engineering with a complete implementation brief or narrow the contract; "
                        "do not treat directory creation or empty verification as completed project delivery."
                    ),
                    extra={
                        "engineeringState": "recoverable_failed",
                        "errorCode": "engineering_missing_write_evidence",
                        "recoverable": True,
                        "degraded": True,
                        "degradedReason": "engineering_missing_write_evidence",
                        "delegationHandoff": delegation_handoff,
                        "writeEvidenceRequired": True,
                    },
                )
            missing_expected_artifacts = self._engineering_missing_expected_artifacts(
                workspace_path=str(workspace_path or ""),
                worker_briefs=worker_briefs,
            )
            if status in {"ready", "degraded"} and missing_expected_artifacts:
                return build_handoff_ref(
                    producer_episode_id=str(episode.get("episodeId") or ""),
                    kind="engineering",
                    compact_summary=(
                        "Engineering degraded after delegated execution: "
                        f"{len(missing_expected_artifacts)} expected artifact(s) are still missing."
                    ),
                    status="degraded",
                    confidence="low",
                    consumer_hint=(
                        "Retry only the missing Spec tasks or repair the expected outputs; "
                        "do not accept the project as complete while required artifacts are absent."
                    ),
                    extra={
                        "engineeringState": "recoverable_failed",
                        "errorCode": "engineering_expected_artifacts_missing",
                        "recoverable": True,
                        "degraded": True,
                        "degradedReason": "engineering_expected_artifacts_missing",
                        "missingExpectedArtifacts": missing_expected_artifacts,
                        "delegationHandoff": delegation_handoff,
                    },
                )
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="engineering",
                compact_summary=(
                    f"Engineering execution_started through {len(worker_briefs)} delegated worker(s).\n"
                    f"{visible_evidence_summary or _preview(delegation_handoff.get('compactSummary') or delegation_handoff.get('summary') or context_summary, limit=700)}"
                ),
                status=status,
                confidence=str(delegation_handoff.get("confidence") or "medium"),
                consumer_hint="Merge this engineering handoff into Supervisor route context before continuing.",
                extra={
                    "engineeringState": "skill_artifact_ready" if skill_validation and skill_validation.get("ok") else "execution_started",
                    "delegationHandoff": delegation_handoff,
                    "workspaceDigestRef": f"workspace_digest:{episode.get('episodeId')}",
                    "proofExpectations": inputs.get("proofExpectations") or need.get("proofExpectations") or [],
                    "consumedRefs": inputs.get("handoffRefs") or need.get("handoffRefs") or [],
                    "visibleEvidenceSummary": visible_evidence_summary,
                    **({"skillArtifactValidation": skill_validation} if skill_validation else {}),
                },
            )
        reason = str(inputs.get("task") or need.get("reason") or "engineering episode").strip()
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or ""),
            kind="engineering",
            compact_summary=(
                (
                    "Engineering work_plan_ready for plan-only request.\n"
                    if plan_only
                    else "Engineering work_plan_ready, but no executable worker brief/task was available yet.\n"
                )
                +
                f"Reason: {reason}\n{_preview(context_summary or digest_text, limit=700)}"
            ),
            status="ready" if plan_only else "failed",
            confidence="medium",
            consumer_hint=(
                "Use this plan-only handoff as the engineering planning result; no file write was requested."
                if plan_only
                else "Provide workerBriefs/taskBriefs or reroute with blockedToolIntent; Supervisor should not batch-write directly."
            ),
            extra={
                "engineeringState": "work_plan_ready" if plan_only else "recoverable_failed",
                "deliverableKind": "plan_only" if plan_only else inputs.get("deliverableKind") or need.get("deliverableKind"),
                "writeRequired": False if plan_only else inputs.get("writeRequired") if "writeRequired" in inputs else need.get("writeRequired"),
                **({} if plan_only else {
                    "recoverable": True,
                    "errorCode": "engineering_missing_executable_tasks",
                }),
                "workspaceDigestRef": f"workspace_digest:{episode.get('episodeId')}",
                "proofExpectations": inputs.get("proofExpectations") or need.get("proofExpectations") or [],
                "consumedRefs": inputs.get("handoffRefs") or need.get("handoffRefs") or [],
            },
        )

    @staticmethod
    def _engineering_requires_write_evidence(
        *,
        need: dict[str, Any],
        inputs: dict[str, Any],
        worker_briefs: list[dict[str, Any]],
    ) -> bool:
        explicit = RuntimeEpisodeRunner._engineering_contract_value(
            need=need,
            inputs=inputs,
            worker_briefs=worker_briefs,
            key="writeRequired",
        )
        if explicit is False and RuntimeEpisodeRunner._any_engineering_worker_brief_requires_write(worker_briefs):
            return True
        if explicit is False:
            return False
        if explicit is True:
            return True
        deliverable_kind = str(
            RuntimeEpisodeRunner._engineering_contract_value(
                need=need,
                inputs=inputs,
                worker_briefs=worker_briefs,
                key="deliverableKind",
            )
            or ""
        ).strip().lower()
        if deliverable_kind == "plan_only":
            return False
        if deliverable_kind in {"patch", "proof", "artifact", "implementation"}:
            return True
        combined = " ".join(
            [
                str(inputs.get("userRequest") or ""),
                str(inputs.get("task") or ""),
                str(need.get("reason") or ""),
                str(inputs.get("deliverableKind") or need.get("deliverableKind") or ""),
                json.dumps(worker_briefs, ensure_ascii=False, default=str),
            ]
        ).lower()
        artifact_markers = (
            "create",
            "build",
            "implement",
            "write",
            "generate",
            "project",
            "file",
            "artifact",
            "patch",
            "创建",
            "生成",
            "实现",
            "写",
            "项目",
            "文件",
            "产物",
            "交付",
            "index.html",
            ".html",
            ".js",
            ".css",
            ".md",
            "canvas",
            "readme",
            "design",
        )
        if any(marker in combined for marker in artifact_markers):
            return True
        for brief in worker_briefs:
            caps = " ".join(str(item or "") for item in list(brief.get("requiredCapabilities") or []))
            scope = " ".join(str(item or "") for item in list(brief.get("behaviorScope") or []))
            if "workspace_mutation" in caps and "implementation" in scope:
                return True
        return False

    @staticmethod
    def _any_engineering_worker_brief_requires_write(worker_briefs: list[dict[str, Any]] | None) -> bool:
        write_deliverables = {"artifact", "patch", "implementation", "skill_artifact", "project_artifact"}
        for brief in list(worker_briefs or []):
            if not isinstance(brief, dict):
                continue
            sources = [brief]
            capsule = brief.get("engineeringTaskCapsule") or brief.get("engineering_task_capsule")
            if isinstance(capsule, dict):
                sources.append(capsule)
            context = brief.get("context")
            if isinstance(context, dict):
                sources.append(context)
                contract = context.get("engineeringExecutionContract") or context.get("engineering_execution_contract")
                if isinstance(contract, dict):
                    sources.append(contract)
                handoff = context.get("handoffContract") or context.get("handoff_contract")
                if isinstance(handoff, dict):
                    sources.append(handoff)
            for source in sources:
                write_required = source.get("writeRequired") if "writeRequired" in source else source.get("write_required")
                if write_required is True:
                    return True
                deliverable = str(source.get("deliverableKind") or source.get("deliverable_kind") or "").strip().lower()
                if deliverable in write_deliverables:
                    return True
        return False

    @staticmethod
    def _handoff_value_items(value: Any, *, limit: int = 8) -> list[str]:
        items: list[str] = []

        def _add(text: Any) -> None:
            rendered = _preview(text, limit=220).strip()
            if rendered and rendered not in items:
                items.append(rendered)

        def _walk(candidate: Any) -> None:
            if len(items) >= limit:
                return
            if isinstance(candidate, str):
                _add(candidate)
                return
            if isinstance(candidate, (int, float, bool)):
                _add(candidate)
                return
            if isinstance(candidate, dict):
                preferred_parts: list[str] = []
                for key in (
                    "path",
                    "file",
                    "filePath",
                    "relativePath",
                    "ref",
                    "id",
                    "command",
                    "status",
                    "summary",
                    "message",
                    "name",
                    "title",
                ):
                    item = candidate.get(key)
                    if item is None or item == "":
                        continue
                    preferred_parts.append(str(item))
                if preferred_parts:
                    _add(" | ".join(preferred_parts))
                elif len(candidate) <= 4 and all(not isinstance(item, (dict, list)) for item in candidate.values()):
                    _add("; ".join(f"{key}={value}" for key, value in candidate.items()))
                else:
                    for item in candidate.values():
                        _walk(item)
                        if len(items) >= limit:
                            break
                return
            if isinstance(candidate, list):
                for item in candidate:
                    _walk(item)
                    if len(items) >= limit:
                        break

        _walk(value)
        return items

    @classmethod
    def _collect_handoff_values(cls, value: Any, keys: set[str], *, limit: int = 8) -> list[str]:
        found: list[str] = []

        def _add_many(values: list[str]) -> None:
            for item in values:
                if item and item not in found:
                    found.append(item)
                if len(found) >= limit:
                    break

        def _walk(candidate: Any) -> None:
            if len(found) >= limit:
                return
            if isinstance(candidate, dict):
                for key, item in candidate.items():
                    normalized = str(key or "").replace("_", "").lower()
                    if normalized in keys:
                        _add_many(cls._handoff_value_items(item, limit=max(1, limit - len(found))))
                    _walk(item)
                    if len(found) >= limit:
                        break
            elif isinstance(candidate, list):
                for item in candidate:
                    _walk(item)
                    if len(found) >= limit:
                        break

        _walk(value)
        return found

    @classmethod
    def _delegation_handoff_visible_evidence_summary(cls, handoff: dict[str, Any]) -> str:
        if not isinstance(handoff, dict):
            return ""
        lines: list[str] = []
        summary = _preview(handoff.get("compactSummary") or handoff.get("summary") or "", limit=360)
        if summary:
            lines.append(f"- Summary: {summary}")
        changed = cls._collect_handoff_values(
            handoff,
            {
                "touchedfiles",
                "changedfiles",
                "createdfiles",
                "modifiedfiles",
                "fileinventory",
                "patches",
                "patchrefs",
            },
            limit=8,
        )
        if changed:
            lines.append(f"- Changed files / patches: {'; '.join(changed)}")
        commands = cls._collect_handoff_values(
            handoff,
            {
                "commandsrun",
                "testresults",
                "verification",
                "validations",
                "skillartifactvalidation",
            },
            limit=8,
        )
        if commands:
            lines.append(f"- Commands / tests: {'; '.join(commands)}")
        artifacts = cls._collect_handoff_values(
            handoff,
            {
                "artifacts",
                "artifactrefs",
                "validatedroot",
                "skillroot",
            },
            limit=8,
        )
        if artifacts:
            lines.append(f"- Artifacts: {'; '.join(artifacts)}")
        proof = cls._collect_handoff_values(
            handoff,
            {
                "proofrefs",
                "proof",
                "evidence",
                "sourcerefs",
            },
            limit=8,
        )
        if proof:
            lines.append(f"- Proof / evidence refs: {'; '.join(proof)}")
        blockers = cls._collect_handoff_values(
            handoff,
            {
                "blockers",
                "residualrisks",
                "degradedreason",
                "error",
                "errors",
            },
            limit=6,
        )
        if blockers:
            lines.append(f"- Blockers / risks: {'; '.join(blockers)}")
        return _preview("\n".join(lines), limit=1200)

    @staticmethod
    def _delegation_handoff_has_write_evidence(handoff: dict[str, Any]) -> bool:
        def _walk(value: Any) -> list[Any]:
            if isinstance(value, dict):
                out: list[Any] = []
                for item in value.values():
                    out.extend(_walk(item))
                return out
            if isinstance(value, list):
                out = []
                for item in value:
                    out.extend(_walk(item))
                return out
            return [value]

        evidence_keys = {
            "touchedFiles",
            "touched_files",
            "changedFiles",
            "changed_files",
            "createdFiles",
            "created_files",
            "modifiedFiles",
            "modified_files",
            "fileInventory",
            "file_inventory",
            "patches",
            "patchRefs",
            "proofRefs",
            "artifactRefs",
            "artifacts",
        }

        def _has_non_empty_evidence(value: Any) -> bool:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in evidence_keys:
                        if isinstance(item, (list, tuple, set, dict)):
                            return bool(item)
                        if isinstance(item, str):
                            return bool(item.strip())
                    if _has_non_empty_evidence(item):
                        return True
            elif isinstance(value, list):
                return any(_has_non_empty_evidence(item) for item in value)
            return False

        if _has_non_empty_evidence(handoff):
            return True
        text = "\n".join(str(item or "") for item in _walk(handoff) if isinstance(item, str)).lower()
        empty_dir_markers = ("0 个文件", "0 files", "empty directory", "目录为空")
        if any(marker in text for marker in empty_dir_markers):
            return False
        write_verbs = ("created", "wrote", "modified", "updated", "patched", "新增", "写入", "修改", "更新", "创建")
        file_markers = (".html", ".js", ".css", ".md", ".json", ".ts", ".tsx", ".py", "index.html", "readme", "design")
        return any(verb in text for verb in write_verbs) and any(marker in text for marker in file_markers)

    @staticmethod
    def _engineering_missing_expected_artifacts(
        *,
        workspace_path: str,
        worker_briefs: list[dict[str, Any]] | None,
    ) -> list[str]:
        workspace = Path(str(workspace_path or "")).expanduser()
        if not str(workspace_path or "").strip():
            return []
        try:
            workspace = workspace.resolve()
        except Exception:
            return []
        expected: list[str] = []
        for brief in list(worker_briefs or []):
            if not isinstance(brief, dict):
                continue
            sources: list[dict[str, Any]] = [brief]
            capsule = brief.get("engineeringTaskCapsule") or brief.get("engineering_task_capsule")
            if isinstance(capsule, dict):
                sources.append(capsule)
            context = brief.get("context")
            if isinstance(context, dict):
                contract = context.get("engineeringExecutionContract") or context.get("engineering_execution_contract")
                if isinstance(contract, dict):
                    sources.append(contract)
            for source in sources:
                values = source.get("expectedArtifacts") or source.get("expected_artifacts")
                if isinstance(values, str):
                    values = [values]
                for value in list(values or []):
                    normalized = str(value or "").strip().strip("`'\"")
                    if (
                        not normalized
                        or normalized.startswith(("spec://", "http://", "https://"))
                        or any(marker in normalized for marker in ("<", ">", "\r", "\n"))
                    ):
                        continue
                    if normalized not in expected:
                        expected.append(normalized)
        missing: list[str] = []
        for value in expected:
            candidate = Path(value)
            resolved = candidate if candidate.is_absolute() else workspace / candidate
            try:
                resolved = resolved.resolve()
                resolved.relative_to(workspace)
            except Exception:
                continue
            if not resolved.exists():
                missing.append(value)
        return missing

    @staticmethod
    def _engineering_expected_artifact_values(worker_briefs: list[dict[str, Any]] | None) -> list[str]:
        expected: list[str] = []
        for brief in list(worker_briefs or []):
            if not isinstance(brief, dict):
                continue
            sources: list[dict[str, Any]] = [brief]
            capsule = brief.get("engineeringTaskCapsule") or brief.get("engineering_task_capsule")
            if isinstance(capsule, dict):
                sources.append(capsule)
            context = brief.get("context")
            if isinstance(context, dict):
                contract = context.get("engineeringExecutionContract") or context.get("engineering_execution_contract")
                if isinstance(contract, dict):
                    sources.append(contract)
            for source in sources:
                values = source.get("expectedArtifacts") or source.get("expected_artifacts")
                if isinstance(values, str):
                    values = [values]
                for value in list(values or []):
                    normalized = str(value or "").strip().strip("`'\"")
                    if normalized and normalized not in expected:
                        expected.append(normalized)
        return expected

    @staticmethod
    def _engineering_brief_is_research_like(brief: dict[str, Any] | None) -> bool:
        if not isinstance(brief, dict):
            return False
        context = brief.get("context") if isinstance(brief.get("context"), dict) else {}
        capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), dict) else {}
        text = json.dumps(
            {
                "goal": brief.get("goal"),
                "title": brief.get("title"),
                "familyHint": brief.get("familyHint"),
                "preferredAgentId": brief.get("preferredAgentId"),
                "executionLaneHint": brief.get("executionLaneHint"),
                "runtimeLane": context.get("runtimeLane") or capsule.get("runtimeLane"),
                "taskExcerpt": context.get("taskExcerpt"),
            },
            ensure_ascii=False,
        ).lower()
        return any(marker in text for marker in ("research", "web-research", "调研", "证据", "来源"))

    @classmethod
    def _engineering_unready_expected_artifacts(
        cls,
        *,
        workspace_path: str,
        worker_briefs: list[dict[str, Any]] | None,
    ) -> list[str]:
        workspace = Path(str(workspace_path or "")).expanduser()
        if not str(workspace_path or "").strip():
            return []
        try:
            workspace = workspace.resolve()
        except Exception:
            return []
        briefs = list(worker_briefs or [])
        research_like = any(cls._engineering_brief_is_research_like(brief) for brief in briefs if isinstance(brief, dict))
        if not research_like:
            return []
        placeholder_markers = (
            "待 phase",
            "待执行",
            "待填充",
            "待补充",
            "占位",
            "placeholder",
            "todo:",
            "tbd",
        )
        unready: list[str] = []
        for value in cls._engineering_expected_artifact_values(briefs):
            normalized = str(value or "").strip().strip("`'\"")
            if (
                not normalized
                or normalized.startswith(("spec://", "http://", "https://"))
                or any(marker in normalized for marker in ("<", ">", "\r", "\n"))
            ):
                continue
            candidate = Path(normalized)
            resolved = candidate if candidate.is_absolute() else workspace / candidate
            try:
                resolved = resolved.resolve()
                resolved.relative_to(workspace)
            except Exception:
                continue
            if not resolved.exists() or resolved.is_dir() or resolved.suffix.lower() not in {".md", ".txt"}:
                continue
            try:
                content = resolved.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            normalized_content = content.strip().lower()
            if not normalized_content:
                unready.append(normalized)
                continue
            if any(marker in normalized_content for marker in placeholder_markers):
                unready.append(normalized)
        return unready

    @classmethod
    def _delegation_summary_with_expected_artifact_guard(
        cls,
        summary: dict[str, Any],
        *,
        branch: dict[str, Any],
        workspace_path: str | None,
    ) -> dict[str, Any]:
        if not workspace_path or not isinstance(summary, dict):
            return dict(summary or {})
        task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
        if not task_brief:
            return dict(summary or {})
        missing = cls._engineering_missing_expected_artifacts(
            workspace_path=str(workspace_path),
            worker_briefs=[task_brief],
        )
        unready = cls._engineering_unready_expected_artifacts(
            workspace_path=str(workspace_path),
            worker_briefs=[task_brief],
        )
        if not missing and not unready:
            return dict(summary or {})
        guarded = dict(summary or {})
        original_status = guarded.get("status")
        guarded["status"] = "blocked"
        guarded["error"] = "expected_artifact_not_ready"
        guarded["originalStatus"] = original_status
        if missing:
            guarded["missingExpectedArtifacts"] = missing
        if unready:
            guarded["unreadyExpectedArtifacts"] = unready
        guarded["localSelfCheck"] = (
            "The worker returned before its declared evidence/artifact was ready. "
            "Downstream dependent tasks are blocked so the parent episode cannot treat local worker text as a global success."
        )
        return guarded

    @staticmethod
    def _engineering_contract_value(
        *,
        need: dict[str, Any],
        inputs: dict[str, Any],
        worker_briefs: list[dict[str, Any]] | None,
        key: str,
    ) -> Any:
        sources: list[Any] = [inputs, need]
        for brief in list(worker_briefs or []):
            if not isinstance(brief, dict):
                continue
            sources.append(brief)
            capsule = brief.get("engineeringTaskCapsule") or brief.get("engineering_task_capsule")
            if isinstance(capsule, dict):
                sources.append(capsule)
            context = brief.get("context")
            if isinstance(context, dict):
                sources.append(context)
        for source in sources:
            if isinstance(source, dict) and key in source:
                return source.get(key)
        return None

    @staticmethod
    def _is_engineering_plan_only_request(
        *,
        need: dict[str, Any],
        inputs: dict[str, Any],
        worker_briefs: list[dict[str, Any]] | None = None,
    ) -> bool:
        deliverable_kind = str(
            RuntimeEpisodeRunner._engineering_contract_value(
                need=need,
                inputs=inputs,
                worker_briefs=worker_briefs,
                key="deliverableKind",
            )
            or ""
        ).strip().lower()
        if deliverable_kind == "plan_only":
            return True
        explicit_write = RuntimeEpisodeRunner._engineering_contract_value(
            need=need,
            inputs=inputs,
            worker_briefs=worker_briefs,
            key="writeRequired",
        )
        if explicit_write is False and RuntimeEpisodeRunner._any_engineering_worker_brief_requires_write(worker_briefs):
            return False
        if explicit_write is False:
            return True
        if explicit_write is True:
            return False
        natural_request_parts = [
            str(inputs.get("userRequest") or ""),
            str(inputs.get("task") or ""),
            str(need.get("reason") or ""),
        ]
        for brief in list(worker_briefs or []):
            if not isinstance(brief, dict):
                continue
            natural_request_parts.append(str(brief.get("goal") or ""))
            context = brief.get("context")
            if isinstance(context, dict):
                natural_request_parts.append(str(context.get("userRequest") or ""))
        blob = "\n".join(part for part in natural_request_parts if part).lower()
        return any(
            marker in blob
            for marker in (
                "plan_only",
                "只输出计划",
                "只要计划",
                "输出执行计划",
                "不需要真实写文件",
                "无需真实写文件",
                "不用真实写文件",
                "不写文件",
                "不保存",
                "不创建",
                "do not write files",
                "no file writes",
                "plan only",
            )
        )

    def _prepare_engineering_worker_briefs_for_delegation(
        self,
        worker_briefs: list[dict[str, Any]],
        *,
        need: dict[str, Any],
        inputs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Normalize Engineering-owned briefs before handing them to subagents.

        The owning runtime is already Engineering here. A child delegation item
        should select an actual worker family, not the Engineering runtime lane
        itself.
        """

        blob = json.dumps({"need": need, "inputs": inputs, "workerBriefs": worker_briefs}, ensure_ascii=False).lower()
        skill_artifact = bool(inputs.get("validateSkillArtifact") or need.get("validateSkillArtifact"))
        skill_artifact = skill_artifact or any(
            marker in blob
            for marker in (
                "skill_artifact_validation",
                "skillartifactvalidator",
                "huashu-nuwa",
                "skill-creator",
                ".agents/skills",
            )
        )
        normalized: list[dict[str, Any]] = []
        for brief in worker_briefs:
            item = dict(brief)
            lane = str(item.get("executionLaneHint") or "").strip().lower()
            if lane == "engineering":
                item["executionLaneHint"] = "subagent"
            deliverable_kind = str(item.get("deliverableKind") or item.get("deliverable_kind") or "").strip().lower()
            context = dict(item.get("context") or {}) if isinstance(item.get("context"), dict) else {}
            expected_outputs = item.get("expectedOutputs") or item.get("expected_outputs") or context.get("expectedOutputs")
            write_required = item.get("writeRequired") if "writeRequired" in item else item.get("write_required")
            validate_skill_artifact = bool(item.get("validateSkillArtifact") or item.get("validate_skill_artifact"))
            artifact_write_required = bool(write_required is True) or deliverable_kind in {
                "artifact",
                "patch",
                "implementation",
                "skill_artifact",
                "project_artifact",
            }
            if skill_artifact:
                family = str(item.get("familyHint") or "").strip().lower()
                if not family or family == "engineering":
                    item["familyHint"] = "writing"
                item.setdefault("preferredAgentId", "skill-workflow-curator")
                task_text = "\n".join(
                    str(value or "")
                    for value in (
                        item.get("taskBriefId"),
                        item.get("title"),
                        item.get("goal"),
                        expected_outputs,
                        item.get("acceptanceContract"),
                    )
                ).lower()
                validate_skill_artifact = validate_skill_artifact or (
                    "skill.md" in task_text
                    and any(marker in task_text for marker in ("构建", "组装", "写入", "质量验证", "build", "assemble", "validate"))
                )
                artifact_write_required = artifact_write_required or validate_skill_artifact
            if expected_outputs:
                context.setdefault("expectedOutputs", expected_outputs)
                expected_text = json.dumps(expected_outputs, ensure_ascii=False, default=str).lower()
                artifact_write_required = artifact_write_required or bool(
                    re.search(r"(?i)(?:skill\.md|[\\/\w.-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml))", expected_text)
                )
            if artifact_write_required:
                item["writeRequired"] = True
                if validate_skill_artifact:
                    item["validateSkillArtifact"] = True
                context.setdefault(
                    "artifactWriteDiscipline",
                    "Use write_native_file for substantive artifact contents. Do not use run_system_command, shell redirection, echo, New-Item, Set-Content, or Out-File to create or populate Markdown/source/skill files; shell commands are only for directories, listing, and verification.",
                )
                context.setdefault(
                    "artifactAcceptanceGuard",
                    "Expected output files must contain complete, source-backed content. Empty placeholders or one-line stubs are invalid; return a blocker/degraded result if evidence is missing.",
                )
                item["context"] = context
            normalized.append(item)
        return normalized

    @staticmethod
    def _delegation_send_arg(item: Any) -> dict[str, Any]:
        arg = getattr(item, "arg", None)
        return dict(arg) if isinstance(arg, dict) else {}

    @classmethod
    def _delegation_send_branch(cls, item: Any) -> dict[str, Any]:
        arg = cls._delegation_send_arg(item)
        return dict(arg.get("parallel_branch") or {}) if isinstance(arg.get("parallel_branch"), dict) else {}

    @classmethod
    def _delegation_send_task_id(cls, item: Any) -> str:
        branch = cls._delegation_send_branch(item)
        task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
        return str(
            branch.get("taskBriefId")
            or branch.get("taskId")
            or task_brief.get("taskBriefId")
            or task_brief.get("taskId")
            or ""
        ).strip()

    @classmethod
    def _delegation_send_dependencies(cls, item: Any) -> list[str]:
        branch = cls._delegation_send_branch(item)
        task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
        context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
        deps: list[str] = []
        for source in (
            branch.get("dependency"),
            branch.get("dependencies"),
            branch.get("dependsOn"),
            task_brief.get("dependency"),
            task_brief.get("dependencies"),
            task_brief.get("dependsOn"),
            context.get("dependency"),
            context.get("dependencies"),
            context.get("dependsOn"),
        ):
            values = source if isinstance(source, list) else [source]
            for value in values:
                text = str(value or "").strip()
                if text and text not in deps:
                    deps.append(text)
        return deps

    @staticmethod
    def _delegation_summary_succeeded(summary: dict[str, Any]) -> bool:
        status = str(summary.get("status") or "").strip().lower()
        if status in {"ok", "ready", "success", "completed", "done"}:
            return True
        if status in {"degraded", "blocked", "error", "failed", "cancelled"}:
            return False
        return bool(summary.get("artifacts") or summary.get("proofRefs") or summary.get("changedFiles"))

    @classmethod
    def _dependency_results_for_delegation(
        cls,
        deps: list[str],
        completed_by_task_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for dep in deps:
            summary = completed_by_task_id.get(dep)
            if not summary:
                continue
            results.append(
                {
                    "taskBriefId": dep,
                    "status": summary.get("status"),
                    "agentId": summary.get("agentId"),
                    "agentName": summary.get("agentName"),
                    "summary": cls._delegation_handoff_visible_evidence_summary(summary)[:1600],
                    "artifacts": list(summary.get("artifacts") or [])[:8] if isinstance(summary.get("artifacts"), list) else [],
                    "proofRefs": list(summary.get("proofRefs") or [])[:8] if isinstance(summary.get("proofRefs"), list) else [],
                    "blockers": list(summary.get("blockers") or [])[:6] if isinstance(summary.get("blockers"), list) else [],
                    "error": summary.get("error"),
                }
            )
        return results

    @staticmethod
    def _blocked_dependency_summary(*, branch: dict[str, Any], task_id: str, deps: list[str], reason: str, failed: list[str]) -> dict[str, Any]:
        return {
            "invocationId": branch.get("invocationId"),
            "taskBriefId": task_id or branch.get("taskBriefId"),
            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
            "taskGoal": branch.get("reason"),
            "agentId": branch.get("agentId"),
            "agentName": branch.get("agentName") or branch.get("agentId"),
            "delegationId": branch.get("delegationId"),
            "lane": branch.get("lane") or "subagent",
            "targetId": branch.get("agentId"),
            "targetLabel": branch.get("agentName") or branch.get("agentId"),
            "branchIndex": branch.get("branchIndex"),
            "status": "blocked",
            "error": reason,
            "dependency": deps,
            "blockedDependencies": failed,
            "completedAt": utc_now_iso(),
        }

    @staticmethod
    def _inject_dependency_results_into_send_arg(arg: dict[str, Any], dependency_results: list[dict[str, Any]]) -> dict[str, Any]:
        if not dependency_results:
            return dict(arg)
        updated = dict(arg)
        branch = dict(updated.get("parallel_branch") or {})
        task_brief = dict(branch.get("taskBrief") or {}) if isinstance(branch.get("taskBrief"), dict) else {}
        context = dict(task_brief.get("context") or {}) if isinstance(task_brief.get("context"), dict) else {}
        context["dependencyResults"] = dependency_results
        task_brief["context"] = context
        branch["taskBrief"] = task_brief
        branch["dependencyResults"] = dependency_results
        updated["parallel_branch"] = branch
        route_context = dict(updated.get("current_route_context") or {})
        route_context["dependencyResults"] = dependency_results
        updated["current_route_context"] = route_context
        return updated

    def _validate_skill_artifact_if_requested(
        self,
        episode: dict[str, Any],
        *,
        need: dict[str, Any],
        inputs: dict[str, Any],
    ) -> dict[str, Any] | None:
        blob = json.dumps({"need": need, "inputs": inputs}, ensure_ascii=False).lower()
        requested = bool(inputs.get("validateSkillArtifact") or need.get("validateSkillArtifact"))
        requested = requested or any(
            marker in blob
            for marker in (
                "skill_artifact_validation",
                "skillartifactvalidator",
                "skill.md",
                ".agents/skills",
                "huashu-nuwa",
                "skill-creator",
            )
        )
        if not requested:
            return None
        try:
            from runtimes.extensions.skills.artifact_validator import SkillArtifactValidator

            candidate_roots = self._candidate_skill_artifact_roots(need=need, inputs=inputs)
            if not candidate_roots:
                return {
                    "ok": False,
                    "status": "skill_artifact_target_missing",
                    "findings": ["未能从 Engineering episode 输入中定位生成 skill 目录。"],
                }
            require_huashu = "huashu-nuwa" in blob or bool(inputs.get("requireHuashuResearch") or need.get("requireHuashuResearch"))
            results = [
                SkillArtifactValidator.validate(
                    root,
                    require_huashu_research=require_huashu,
                    require_source_markers=True,
                ).as_dict()
                for root in candidate_roots
            ]
            passing = [item for item in results if item.get("ok")]
            if passing:
                return {
                    "ok": True,
                    "status": "skill_artifact_ready",
                    "validatedRoot": passing[0].get("skillRoot"),
                    "results": results,
                }
            return {
                "ok": False,
                "status": "skill_artifact_invalid",
                "results": results,
                "findings": [finding for item in results for finding in list(item.get("findings") or [])],
            }
        except Exception as exc:  # noqa: BLE001 - keep runtime failure recoverable.
            return {
                "ok": False,
                "status": "skill_artifact_validator_error",
                "findings": [f"{type(exc).__name__}: {exc}"],
            }

    def _candidate_skill_artifact_roots(self, *, need: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
        values: list[str] = []

        def extract_skill_path_candidates(text: str) -> list[str]:
            stripped = text.strip().strip("\"'`")
            candidates: list[str] = []
            if re.match(r"^(?:[A-Za-z]:[\\/]|\.agents[\\/])", stripped) or stripped.lower().endswith("skill.md"):
                candidates.append(stripped)
            patterns = [
                r"[A-Za-z]:[\\/](?:(?![\r\n\"'<>|，。；;,]).)*?\.agents[\\/]+skills[\\/]+(?:(?![\r\n\"'<>|，。；;,]).)+",
                r"(?<!\S)\.agents[\\/]+skills[\\/]+(?:(?![\r\n\"'<>|，。；;,]).)+",
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    raw = match.group(0).strip().strip("\"'`()（）[]【】")
                    if raw:
                        candidates.append(raw)
            return candidates

        def collect(value: Any) -> None:
            if isinstance(value, str):
                values.extend(extract_skill_path_candidates(value))
                return
            if isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)

        collect(inputs)
        collect(need)
        workspace_path = str(inputs.get("workspacePath") or need.get("workspacePath") or "").strip()
        roots: list[Path] = []

        def as_skill_root(path: Path) -> Path:
            if path.name.lower() == "skill.md":
                path = path.parent
            parts = list(path.parts)
            lowered = [part.lower() for part in parts]
            for index, part in enumerate(lowered):
                if part == ".agents" and index + 2 < len(parts) and lowered[index + 1] == "skills":
                    return Path(*parts[: index + 3])
            return path

        for raw in values:
            candidate = Path(str(raw).strip().strip("\"'`"))
            candidate = as_skill_root(candidate)
            if not candidate.is_absolute() and workspace_path:
                candidate = Path(workspace_path) / candidate
            if candidate.exists() and candidate.is_file() and candidate.name.lower() == "skill.md":
                candidate = candidate.parent
            if candidate.exists() and candidate.is_file():
                continue
            explicit_skill_root = ".agents" in str(candidate).lower()
            if explicit_skill_root or (candidate.exists() and candidate.is_dir() and (candidate / "SKILL.md").exists()):
                roots.append(candidate)
        if not roots and workspace_path:
            skills_root = Path(workspace_path) / ".agents" / "skills"
            if skills_root.exists():
                children = [
                    child
                    for child in skills_root.iterdir()
                    if child.is_dir() and (child / "SKILL.md").exists()
                ]
                children.sort(key=lambda item: (item / "SKILL.md").stat().st_mtime if (item / "SKILL.md").exists() else 0, reverse=True)
                roots.extend(children[:3])
        deduped: list[str] = []
        seen: set[str] = set()
        for root in roots:
            normalized = str(root.resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    async def _execute_creative_media(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "creative_media: compile")
        need = dict(episode.get("need") or {})
        inputs = dict(episode.get("inputs") or {})
        request = dict(inputs.get("request") or {})
        request.setdefault("modality", inputs.get("modality") or need.get("modality") or "image")
        request.setdefault("prompt", inputs.get("prompt") or need.get("reason") or "Create supporting visual asset.")
        try:
            from runtimes.creative_media.runtime import creative_media_runtime

            should_compile_work_order = bool(
                request.get("workOrderKind")
                or request.get("work_order_kind")
                or request.get("assetRole")
                or request.get("asset_role")
                or request.get("requestingRuntime")
                or request.get("referenceAssetIds")
                or str(request.get("intent") or "").strip() in {"simple_asset", "storyboard_to_video"}
            )
            if should_compile_work_order:
                work_order = creative_media_runtime.compile_work_order(
                    {
                        **request,
                        "requestingRuntime": request.get("requestingRuntime") or need.get("requestingRuntime") or "runtime_episode",
                    }
                )
                work_order_id = str(work_order.get("workOrderId") or "")
                summary = f"Creative Media work order planned: {work_order.get('workOrderKind') or work_order_id}"
                return build_handoff_ref(
                    producer_episode_id=str(episode.get("episodeId") or ""),
                    kind="creative_media",
                    compact_summary=summary,
                    status="ready",
                    confidence="medium",
                    consumer_hint="Use workOrderId/artifactRefs/providerPlan to request or reference generated media.",
                    extra={
                        "workOrderId": work_order_id,
                        "workOrderKind": work_order.get("workOrderKind"),
                        "recipeRefs": work_order.get("recipeRefs") or [],
                        "artifactRefs": work_order.get("artifactRefs") or [],
                        "handoffStage": "planned",
                        "requiresContinuation": not bool(work_order.get("artifactRefs")),
                        "recommendedNextAction": (
                            "Create the required provider jobs with creative_media_create_job, poll queued jobs with "
                            "creative_media_get_job, then pass the resulting artifact refs to Engineering."
                        ),
                        "providerPlan": work_order.get("providerPlan"),
                        "qualityChecks": work_order.get("qualityChecks") or [],
                        "costEstimate": work_order.get("costEstimate") or {},
                        "safetyStatus": work_order.get("safetyStatus") or {},
                    },
                )

            recipe = creative_media_runtime.compile_recipe(request)
            recipe_id = str(recipe.get("recipeId") or recipe.get("id") or "")
            summary = f"Creative Media recipe compiled: {recipe_id or request.get('modality')}"
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="creative_media",
                compact_summary=summary,
                status="ready",
                confidence="medium",
                consumer_hint="Pass recipeRefs/assetRefs back to Engineering or Supervisor for UI/media integration.",
                extra={
                    "recipeRefs": [recipe_id] if recipe_id else [],
                    "providerStatus": recipe.get("providerStatus"),
                    "handoffStage": "compiled",
                    "requiresContinuation": True,
                    "recommendedNextAction": (
                        "Create the required provider jobs with creative_media_create_job, poll queued jobs with "
                        "creative_media_get_job, then pass the resulting artifact refs to Engineering."
                    ),
                },
            )
        except Exception as exc:
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="creative_media",
                compact_summary=f"Creative Media episode failed during recipe compile: {type(exc).__name__}: {exc}",
                status="failed",
                confidence="low",
                consumer_hint="Check Creative Media config/provider availability before retry.",
                extra={"errorCode": "creative_media_compile_failed", "errorMessage": str(exc)},
            )

    async def _execute_computer_use(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "computer_use: observe")
        need = dict(episode.get("need") or {})
        inputs = dict(episode.get("inputs") or {})
        if not bool(inputs.get("observe", True)):
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="computer_use",
                compact_summary="Computer Use episode routed; no observation requested.",
                status="ready",
                confidence="medium",
                consumer_hint="Request observe=true to collect observationRefs/screenshotRefs.",
            )
        from runtimes.computer_use.runtime import computer_use_runtime

        result = computer_use_runtime.observe(
            session_id=str(episode.get("session_id") or "") or None,
            run_id=str(episode.get("run_id") or "") or None,
            goal=str(inputs.get("goal") or need.get("reason") or "runtime episode observe"),
            app_id=inputs.get("appId") or inputs.get("app_id"),
            include_screenshot=bool(inputs.get("includeScreenshot", False)),
            invocation_metadata={"trigger_source": "runtime_episode_runner"},
        )
        observation = dict(result.get("observation") or {})
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or ""),
            kind="computer_use",
            compact_summary=_preview(result.get("message") or observation.get("windowTitle") or "Computer Use observation completed."),
            status="ready" if result.get("ok", True) else "failed",
            confidence=str(observation.get("confidence") or "medium"),
            consumer_hint="Use observationRefs/traceRefs to continue desktop/browser task.",
            extra={"observationRefs": [f"computer_use_observation:{episode.get('episodeId')}"]},
        )

    async def _execute_rpa(self, episode: dict[str, Any]) -> dict[str, Any]:
        episode_id = str(episode.get("episodeId") or "")
        self._heartbeat(episode_id, "rpa: route")
        inputs = dict(episode.get("inputs") or {})
        from runtimes.rpa.runtime import rpa_runtime

        action = str(inputs.get("action") or inputs.get("mode") or inputs.get("executionMode") or "prepare").strip().lower()
        variables = inputs.get("variables") if isinstance(inputs.get("variables"), dict) else {}
        timeout_ms = int(inputs.get("timeoutMs") or inputs.get("timeout_ms") or 600000)
        session_id = str(episode.get("session_id") or episode.get("sessionId") or inputs.get("sessionId") or "").strip() or None
        run_id = str(episode.get("run_id") or episode.get("runId") or inputs.get("runId") or "").strip() or None
        user_id = str(inputs.get("userId") or inputs.get("user_id") or "system")
        project_id = str(inputs.get("projectId") or inputs.get("project_id") or "").strip() or None
        workspace_id = str(inputs.get("workspaceId") or inputs.get("workspace_id") or "").strip() or None
        workspace_path = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip() or None
        cwd = str(inputs.get("cwd") or "").strip() or None
        output_dir = inputs.get("outputDir") or inputs.get("output_dir")
        refs: list[str] = []
        trace_refs: list[str] = []
        robot_refs: list[str] = []
        run_refs: list[str] = []
        verification: dict[str, Any] = {}
        risk: dict[str, Any] = {}
        prepared: dict[str, Any] = {}
        result: dict[str, Any] = {}
        status = "ready"
        confidence = "medium"

        def _robot_file_from(payload: dict[str, Any]) -> str:
            export = payload.get("export") if isinstance(payload.get("export"), dict) else {}
            return str(payload.get("robotFile") or export.get("path") or payload.get("path") or "").strip()

        try:
            if inputs.get("traceRunIds") or inputs.get("runIds"):
                run_ids = [str(item).strip() for item in list(inputs.get("traceRunIds") or inputs.get("runIds") or []) if str(item).strip()]
                if not run_ids:
                    raise ValueError("RPA trace compile episode 缺少 traceRunIds/runIds。")
                self._heartbeat(episode_id, "rpa: compile traces")
                draft = await self._await_with_heartbeat(
                    episode_id,
                    asyncio.to_thread(rpa_runtime.compile_traces_to_draft, run_ids, save=bool(inputs.get("save", True))),
                    progress="rpa: compiling traces",
                )
                draft_id = str(draft.get("id") or "")
                refs.append(f"rpa_draft:{draft_id}" if draft_id else "rpa_draft")
                trace_refs.extend([f"trace:{item}" for item in run_ids])
                summary = f"RPA traces compiled into draft {draft_id or '<unsaved>'}."
            elif inputs.get("traceRunId") or inputs.get("traceId"):
                trace_run_id = str(inputs.get("traceRunId") or inputs.get("traceId") or "").strip()
                self._heartbeat(episode_id, "rpa: compile trace")
                draft = await self._await_with_heartbeat(
                    episode_id,
                    asyncio.to_thread(rpa_runtime.compile_trace_to_draft, trace_run_id, save=bool(inputs.get("save", True))),
                    progress="rpa: compiling trace",
                )
                draft_id = str(draft.get("id") or "")
                refs.append(f"rpa_draft:{draft_id}" if draft_id else "rpa_draft")
                trace_refs.append(f"trace:{trace_run_id}")
                summary = f"RPA trace compiled into draft {draft_id or '<unsaved>'}."
            elif inputs.get("draftId") or inputs.get("scriptId") or inputs.get("templateId"):
                script_id = str(inputs.get("draftId") or inputs.get("scriptId") or inputs.get("templateId") or "").strip()
                if action in {"run", "execute", "run_draft", "execute_draft", "live"} or inputs.get("execute") is True:
                    self._heartbeat(episode_id, f"rpa: execute draft {script_id}")
                    result = await self._await_with_heartbeat(
                        episode_id,
                        asyncio.to_thread(
                            rpa_runtime.run_draft,
                            script_id=script_id,
                            variables=variables,
                            output_dir=output_dir,
                            timeout_ms=timeout_ms,
                            cwd=cwd,
                            session_id=session_id,
                            run_id=run_id,
                            user_id=user_id,
                            project_id=project_id,
                            workspace_id=workspace_id,
                            workspace_path=workspace_path,
                            trigger_source="runtime_episode_runner",
                            non_chat_run=True,
                        ),
                        progress=f"rpa: running draft {script_id}",
                    )
                    status = "failed" if str(result.get("status") or "").lower() in {"failed", "fallback_failed", "blocked"} else "ready"
                    prepared = dict(result)
                    summary = f"RPA draft executed: {script_id} ({result.get('status') or 'completed'})."
                else:
                    self._heartbeat(episode_id, f"rpa: prepare draft {script_id}")
                    prepared = await asyncio.to_thread(
                        rpa_runtime.prepare_draft_run,
                        script_id=script_id,
                        variables=variables,
                        output_dir=output_dir,
                    )
                    summary = f"RPA draft prepared: {script_id}."
                refs.append(f"rpa_draft:{script_id}")
                robot_file = _robot_file_from(prepared)
                if robot_file:
                    robot_refs.append(robot_file)
            elif inputs.get("robotFile"):
                robot_file = str(inputs.get("robotFile") or "").strip()
                if action in {"run", "execute", "run_existing", "execute_existing", "live"} or inputs.get("execute") is True:
                    self._heartbeat(episode_id, f"rpa: execute robot {robot_file}")
                    result = await self._await_with_heartbeat(
                        episode_id,
                        asyncio.to_thread(
                            rpa_runtime.run_existing_flow,
                            robot_file=robot_file,
                            variables=variables,
                            output_dir=output_dir,
                            timeout_ms=timeout_ms,
                            cwd=cwd,
                            session_id=session_id,
                            run_id=run_id,
                            user_id=user_id,
                            project_id=project_id,
                            workspace_id=workspace_id,
                            workspace_path=workspace_path,
                            trigger_source="runtime_episode_runner",
                            non_chat_run=True,
                        ),
                        progress=f"rpa: running robot {robot_file}",
                    )
                    status = "failed" if str(result.get("status") or "").lower() in {"failed", "fallback_failed", "blocked"} else "ready"
                    prepared = dict(result)
                    summary = f"RPA robot flow executed: {robot_file} ({result.get('status') or 'completed'})."
                else:
                    self._heartbeat(episode_id, f"rpa: prepare robot {robot_file}")
                    prepared = await asyncio.to_thread(
                        rpa_runtime.prepare_existing_run,
                        robot_file=robot_file,
                        variables=variables,
                        output_dir=output_dir,
                    )
                    summary = f"RPA existing flow prepared: {robot_file}."
                robot_refs.append(robot_file)
                refs.append(f"robot_file:{robot_file}")
            else:
                status = "failed"
                confidence = "high"
                summary = "RPA episode has no draft/template/robot file/trace target."
                risk = {"recoverable": True, "reason": "missing_rpa_target"}
        except Exception as exc:
            status = "failed"
            confidence = "high"
            summary = f"RPA episode failed: {type(exc).__name__}: {exc}"
            risk = {"recoverable": True, "errorClass": type(exc).__name__}

        if result.get("runId"):
            run_refs.append(f"rpa_run:{result.get('runId')}")
        if result.get("sessionId"):
            refs.append(f"rpa_session:{result.get('sessionId')}")
        if result.get("fallback"):
            refs.append("rpa_fallback:computer_use")
            risk["fallback"] = True
        if prepared.get("export") and isinstance(prepared.get("export"), dict):
            export = dict(prepared.get("export") or {})
            verification["dryRunPassed"] = export.get("dryRunPassed")
            if export.get("dryRunError"):
                verification["dryRunError"] = export.get("dryRunError")
        if result:
            verification["executionStatus"] = result.get("status")
            verification["outcomeFamily"] = result.get("outcomeFamily")
            if result.get("error"):
                verification["error"] = _preview(result.get("error"), limit=500)
        return build_handoff_ref(
            producer_episode_id=episode_id,
            kind="rpa",
            compact_summary=summary,
            status=status,
            confidence=confidence,
            raw_ref=robot_refs[0] if robot_refs else None,
            detail_tool="rpa_runtime",
            consumer_hint="Use this rpa_trace_bundle to resume the parent episode with prepared robot refs, run refs, or compiled draft refs.",
            extra={
                "refs": list(dict.fromkeys(refs + robot_refs + run_refs + trace_refs)),
                "traceRefs": list(dict.fromkeys(trace_refs)),
                "robotRefs": list(dict.fromkeys(robot_refs)),
                "runRefs": list(dict.fromkeys(run_refs)),
                "variables": variables,
                "prepared": {
                    "command": prepared.get("command"),
                    "robotFile": _robot_file_from(prepared),
                    "scriptId": ((prepared.get("script") or {}).get("id") if isinstance(prepared.get("script"), dict) else None),
                },
                "verification": verification,
                "risk": risk,
                **({"errorCode": "rpa_episode_failed", "errorMessage": summary} if status == "failed" else {}),
            },
        )

    async def _execute_delegation(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "delegation: dispatch")
        inputs = dict(episode.get("inputs") or {})
        resume_token = dict(episode.get("resumeToken") or episode.get("resume_token") or {})
        child_handoffs = list(resume_token.get("handoffBundle") or resume_token.get("childHandoffs") or [])
        if not child_handoffs and str(resume_token.get("resumedFrom") or "") == "child_handoffs":
            child_handoffs = self._load_child_handoffs_from_resume_token(resume_token)
        if not child_handoffs and str(resume_token.get("resumedFrom") or "") == "child_handoffs":
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary="Delegation could not resume because completed child handoffs were not available.",
                status="failed",
                confidence="low",
                consumer_hint="Retry the parent delegation episode after child handoff recovery; do not dispatch new child work blindly.",
                extra={
                    "delegationState": "recoverable_failed",
                    "recoverable": True,
                    "errorCode": "child_handoff_missing",
                    "resumeToken": resume_token,
                },
            )
        if child_handoffs:
            ready_count = len(child_handoffs)
            budget_blocked = [
                item
                for item in child_handoffs
                if isinstance(item, dict) and item.get("budgetBlockedChildDelegations")
            ]
            summary = (
                f"Delegation handoff_ready after {ready_count} child delegation handoff(s).\n"
                f"{_preview('; '.join(str(item.get('compactSummary') or item.get('summary') or item.get('kind') or '') for item in child_handoffs if isinstance(item, dict)), limit=900)}"
            )
            if budget_blocked:
                summary += f" child_budget_boundary={len(budget_blocked)}"
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=summary,
                status="ready",
                confidence="medium",
                consumer_hint="Merge child delegation handoffs into Supervisor route context and continue orchestration.",
                extra={
                    "delegationState": "handoff_ready",
                    "childHandoffs": child_handoffs,
                    "handoffRefs": [item.get("handoffId") or item.get("handoffRefId") for item in child_handoffs if isinstance(item, dict)],
                    "budgetBoundaryChildCount": int(resume_token.get("budgetBoundaryChildCount") or len(budget_blocked) or 0),
                },
            )
        worker_briefs = list(inputs.get("workerBriefs") or inputs.get("tasks") or [])
        target_count = int(inputs.get("targetCount") or len(worker_briefs) or 1)
        if target_count > int(inputs.get("maxChildren") or 10):
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=f"Delegation requested {target_count} workers, over current budget.",
                status="failed",
                confidence="high",
                consumer_hint="Increase Admin delegation budget or lower targetCount.",
                extra={"errorCode": "delegation_budget_exceeded", "targetCount": target_count},
            )
        if not worker_briefs:
            reason = str(inputs.get("brief") or (episode.get("need") or {}).get("reason") or "").strip()
            if not reason:
                return build_handoff_ref(
                    producer_episode_id=str(episode.get("episodeId") or ""),
                    kind="delegation_degraded",
                    compact_summary="Delegation episode cannot dispatch because it has no worker brief/task.",
                    status="degraded",
                    confidence="high",
                    consumer_hint="Use this degraded handoff as a single missing-tasks diagnostic; repair the planner taskBriefs or ask for a narrower contract before retrying.",
                    extra={
                        "delegationState": "delegation_degraded",
                        "degraded": True,
                        "degradedReason": "delegation_missing_tasks",
                        "errorCode": "delegation_missing_tasks",
                        "dispatchStatus": "missing_tasks",
                        "missingTasks": True,
                        "exampleTasks": [
                            {
                                "title": "Implement one isolated work package",
                                "goal": "Describe the exact subtask and expected artifact.",
                                "runtimeAccess": ["memory.read"],
                                "acceptanceContract": "Return result summary, touched files, proof, and risks.",
                            }
                        ],
                    },
                )
            worker_briefs = [{"title": reason[:96], "goal": reason, "brief": reason, "executionLaneHint": "auto"}]
        try:
            from core.native_tools import delegation_broker
            session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip() or None
            run_id = str(episode.get("run_id") or episode.get("runId") or "").strip() or None
            workspace_path = str(
                inputs.get("workspacePath")
                or inputs.get("workspace_path")
                or (episode.get("need") or {}).get("workspacePath")
                or (episode.get("need") or {}).get("workspace_path")
                or ""
            ).strip() or None
            runtime_context = {
                "runtime_kind": "delegation",
                "trigger_source": "runtime_episode_runner",
                "session_id": session_id,
                "run_id": run_id,
                "workspace_path": workspace_path,
                "goal": str(episode.get("reason") or (episode.get("need") or {}).get("reason") or "").strip(),
            }
            with bind_runtime_context(**runtime_context):
                command = delegation_broker.func(
                    mode="dispatch",
                    tasks=worker_briefs,
                    target_count=target_count,
                    allow_child_delegation=bool(inputs.get("allowChildDelegation") or inputs.get("allow_child_delegation")),
                    child_delegation_budget=inputs.get("childDelegationBudget") or inputs.get("child_delegation_budget") or {},
                    write_set_partitions=inputs.get("writeSetPartitions") or inputs.get("write_set_partitions") or [],
                    state={
                        "run_id": run_id,
                        "session_id": session_id,
                        "workspace_path": workspace_path,
                        "delegationDispatchSource": "runtime_episode_runner",
                        "current_route_context": {
                            "activeCapabilityEpisodeId": episode.get("episodeId"),
                            "capabilityEpisodes": [episode],
                            "runtimeToolGrants": [{"group": "delegation.recursive", "runtimeKind": "subagent"}],
                            **({"workspacePath": workspace_path, "workspace_path": workspace_path} if workspace_path else {}),
                        },
                    },
                    tool_call_id=f"episode:{episode.get('episodeId')}:delegation_dispatch",
                )
                update = dict(getattr(command, "update", None) or {})
                results = [item for item in list(update.get("parallel_results") or []) if isinstance(item, dict)]
                local_results, child_episode_ids = await self._execute_local_delegation_sends(command, episode)
            if local_results:
                results.extend(local_results)
            failed = [item for item in results if str(item.get("status") or "").lower() in {"error", "failed", "blocked"}]
            budget_blocked = [
                item
                for item in failed
                if str(item.get("error") or "").strip() == "child_delegation_not_allowed"
                or str(item.get("dispatchStatus") or "").strip() == "dispatch_missing_child_budget"
            ]
            hard_failed = [item for item in failed if item not in budget_blocked]
            waiting_child = [
                item
                for item in results
                if str(item.get("status") or "").lower() in {"waiting_child_delegation", "waiting_child", "waiting"}
            ]
            ready_results = [
                item
                for item in results
                if str(item.get("status") or "").lower() in {"ok", "ready", "completed", "success"}
            ]
            budget_boundary_only = bool(failed) and bool(budget_blocked) and len(budget_blocked) == len(failed) and not ready_results and not waiting_child
            if waiting_child:
                status = "waiting"
            elif budget_boundary_only:
                status = "degraded"
            elif results and not ready_results and (hard_failed or budget_blocked):
                status = "failed"
            elif hard_failed:
                status = "failed" if len(hard_failed) == len(results) else "degraded"
            else:
                status = "ready"
            try:
                failure_degrade_threshold = max(
                    1,
                    int(
                        inputs.get("delegationCircuitBreakerThreshold")
                        or inputs.get("delegation_failure_degrade_threshold")
                        or inputs.get("failureDegradeThreshold")
                        or 3
                    ),
                )
            except Exception:
                failure_degrade_threshold = 3
            should_degrade = (
                budget_boundary_only
                or (
                    status in {"failed", "degraded"}
                    and bool(results)
                    and not waiting_child
                )
            )
            degraded_reason = "child_delegation_budget_boundary" if budget_boundary_only else (
                "delegation_failure_threshold_reached"
                if len(failed) >= failure_degrade_threshold
                else "delegation_worker_failed"
            )
            summary = f"Delegation dispatched {len(results) or target_count} worker(s)."
            if local_results:
                summary = f"Delegation executed {len(local_results)} local subagent worker(s)."
            if hard_failed:
                summary += f" failed={len(hard_failed)}"
            if budget_blocked:
                summary += f" child_budget_blocked={len(budget_blocked)}"
            if waiting_child:
                summary += f" waiting_child={len(waiting_child)}"
            if not results:
                status = "degraded"
                summary = "Delegation dispatch did not produce confirmed worker tasks."
                should_degrade = True
            if should_degrade:
                status = "degraded"
                summary += f" degraded_after_failures={len(failed)} threshold={failure_degrade_threshold}"
            acceptance_tiers = {
                "must": [],
                "should": [],
                "nice": [],
            }
            for brief in worker_briefs:
                if not isinstance(brief, dict):
                    continue
                tiers = brief.get("acceptanceTiers") if isinstance(brief.get("acceptanceTiers"), dict) else {}
                for tier in acceptance_tiers:
                    acceptance_tiers[tier].extend([str(item).strip() for item in list(tiers.get(tier) or []) if str(item).strip()])
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation_degraded" if should_degrade else "delegation",
                compact_summary=summary,
                status=status,
                confidence="medium",
                consumer_hint=(
                    "Use this degraded delegation handoff for synthesis, user clarification, narrowed retry, or alternate runtime route; do not grant Supervisor direct mutating tools."
                    if should_degrade
                    else "Parent episode should wait for delegation completion events before merging."
                ),
                extra={
                    **(
                        {
                            "delegationState": "delegation_degraded",
                            "dispatchStatus": "delegation_degraded",
                            "degraded": True,
                            "degradedReason": degraded_reason,
                            "failureThreshold": failure_degrade_threshold,
                        }
                        if should_degrade
                        else {}
                    ),
                    "delegationRefs": [item.get("delegationId") or item.get("id") for item in results if isinstance(item, dict)],
                    "childEpisodeIds": child_episode_ids,
                    "failedDelegationCount": len(hard_failed),
                    "totalFailedDelegationCount": len(failed),
                    "attemptedWorkers": [
                        item.get("targetLabel") or item.get("agentName") or item.get("delegationId") or item.get("id")
                        for item in results[:12]
                        if isinstance(item, dict)
                    ],
                    "acceptanceCheck": {
                        "must": {"passed": bool(ready_results) and not hard_failed and not budget_blocked, "items": list(dict.fromkeys(acceptance_tiers["must"]))},
                        "should": {"passed": bool(ready_results) and not hard_failed, "items": list(dict.fromkeys(acceptance_tiers["should"]))},
                        "nice": {"passed": bool(ready_results) and not hard_failed, "items": list(dict.fromkeys(acceptance_tiers["nice"]))},
                    },
                    "recoveryHints": (
                        [
                            "narrow_contract",
                            "retry_with_explicit_tasks",
                            "route_to_alternate_runtime",
                            "ask_user_for_smaller_scope",
                        ]
                        if should_degrade
                        else []
                    ),
                    "residualRisks": [
                        _preview(item.get("error") or item.get("dispatchStatus") or item.get("compactTranscript"), limit=300)
                        for item in failed[:8]
                        if isinstance(item, dict)
                    ],
                    "budgetBlockedChildDelegations": [
                        {
                            "delegationId": item.get("delegationId") or item.get("id"),
                            "targetLabel": item.get("targetLabel") or item.get("agentName"),
                            "error": item.get("error"),
                            "dispatchStatus": item.get("dispatchStatus"),
                        }
                        for item in budget_blocked[:8]
                        if isinstance(item, dict)
                    ],
                    "results": [
                        build_delegation_result_contract(item)
                        for item in results[:8]
                        if isinstance(item, dict)
                    ],
                },
            )
        except Exception as exc:
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=f"Delegation dispatch failed: {type(exc).__name__}: {exc}",
                status="failed",
                confidence="low",
                consumer_hint="Check subagent registry/worker configuration.",
                extra={"errorCode": "delegation_dispatch_failed", "errorMessage": str(exc)},
            )

    def _build_agent_nodes_map(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if self._agent_nodes_map_cache is not None and not force_refresh:
            return self._agent_nodes_map_cache
        from api.models import EngineConfig
        from graph.compat import sanitize_message_chain as compat_sanitize_message_chain
        from graph.compat import sanitize_response_tool_calls as compat_sanitize_response_tool_calls
        from graph.supervisor_builder import build_supervisor_runtime_bundle
        from graph.supervisor_support import build_agent_runtime_failure_command, extract_task_context, resolve_todos
        from runtimes.extensions.skills.loader import fetch_skill_instructions

        try:
            from core.engine_config_resolver import resolve_engine_config_for_role

            config = resolve_engine_config_for_role("supervisor").get("engine_config") or EngineConfig()
        except Exception:
            config = EngineConfig()
        bundle = build_supervisor_runtime_bundle(
            config=config,
            fetch_skill_instructions_tool=fetch_skill_instructions,
            build_failure_command=build_agent_runtime_failure_command,
            extract_task_context=extract_task_context,
            resolve_todos=resolve_todos,
            sanitize_message_chain=compat_sanitize_message_chain,
            sanitize_response_tool_calls=compat_sanitize_response_tool_calls,
        )
        self._agent_nodes_map_cache = dict(bundle.agent_nodes_map or {})
        snapshot = getattr(bundle, "subagent_registry_snapshot", {}) or {}
        self._agent_nodes_map_snapshot_hash = str(snapshot.get("hash") or "").strip()
        self._agent_nodes_map_snapshot_version = str(snapshot.get("version") or "").strip()
        return self._agent_nodes_map_cache

    async def _execute_local_delegation_sends(self, command: Any, episode: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        from langgraph.types import Send

        goto = getattr(command, "goto", None)
        sends = [item for item in (goto if isinstance(goto, list) else []) if isinstance(item, Send)]
        if not sends:
            return [], []
        from graph.parallel_support import _run_parallel_agent_branch

        agent_nodes_map = self._build_agent_nodes_map()
        results: list[dict[str, Any]] = []
        child_episode_ids: list[str] = []
        inputs = dict(episode.get("inputs") or {})
        need = dict(episode.get("need") or {})
        session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip() or None
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip() or None
        workspace_path = str(
            inputs.get("workspacePath")
            or inputs.get("workspace_path")
            or need.get("workspacePath")
            or need.get("workspace_path")
            or ""
        ).strip() or None

        pending = list(sends)
        completed_by_task_id: dict[str, dict[str, Any]] = {}

        async def _run_ready_send(item: Send) -> None:
            nonlocal agent_nodes_map
            task_id = self._delegation_send_task_id(item)
            deps = self._delegation_send_dependencies(item)
            node = str(getattr(item, "node", "") or "")
            arg = getattr(item, "arg", None)
            if node != "parallel_delegate_task" or not isinstance(arg, dict):
                return
            arg = dict(arg)
            dependency_results = self._dependency_results_for_delegation(deps, completed_by_task_id)
            arg = self._inject_dependency_results_into_send_arg(arg, dependency_results)
            route_context = dict(arg.get("current_route_context") or {})
            route_context.setdefault("activeCapabilityEpisodeId", episode.get("episodeId"))
            route_context["capabilityEpisodes"] = [
                *[entry for entry in list(route_context.get("capabilityEpisodes") or []) if isinstance(entry, dict)],
                episode,
            ][-50:]
            if workspace_path:
                route_context.setdefault("workspacePath", workspace_path)
                route_context.setdefault("workspace_path", workspace_path)
                arg.setdefault("workspace_path", workspace_path)
                arg.setdefault("workspacePath", workspace_path)
            if session_id:
                route_context.setdefault("sessionId", session_id)
                route_context.setdefault("session_id", session_id)
                arg.setdefault("session_id", session_id)
                arg.setdefault("sessionId", session_id)
            if run_id:
                route_context.setdefault("runId", run_id)
                route_context.setdefault("run_id", run_id)
                arg.setdefault("run_id", run_id)
                arg.setdefault("runId", run_id)
            arg["current_route_context"] = route_context
            branch = dict(arg.get("parallel_branch") or {})
            agent_id = str(branch.get("agentId") or "").strip()
            agent_data = agent_nodes_map.get(agent_id)
            refresh_attempted = False
            previous_registry_hash = self._agent_nodes_map_snapshot_hash
            previous_registry_version = self._agent_nodes_map_snapshot_version
            if not agent_data:
                refresh_attempted = True
                agent_nodes_map = self._build_agent_nodes_map(force_refresh=True)
                agent_data = agent_nodes_map.get(agent_id)
            if not agent_data:
                summary = {
                    "invocationId": branch.get("invocationId"),
                    "taskBriefId": task_id or branch.get("taskBriefId"),
                    "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                    "taskGoal": branch.get("reason"),
                    "agentId": agent_id,
                    "agentName": branch.get("agentName") or agent_id,
                    "delegationId": branch.get("delegationId"),
                    "lane": branch.get("lane") or "subagent",
                    "targetId": agent_id,
                    "targetLabel": branch.get("agentName") or agent_id,
                    "branchIndex": branch.get("branchIndex"),
                    "status": "error",
                    "error": "subagent_target_missing",
                    "summary": f"未找到子 Agent '{agent_id}'，已停止该分支并回交 Supervisor。",
                    "registryVersion": branch.get("registryVersion"),
                    "registryHash": branch.get("registryHash"),
                    "nodeMapRefreshAttempted": refresh_attempted,
                    "nodeMapRegistryVersionBeforeRefresh": previous_registry_version,
                    "nodeMapRegistryHashBeforeRefresh": previous_registry_hash,
                    "nodeMapRegistryVersionAfterRefresh": self._agent_nodes_map_snapshot_version,
                    "nodeMapRegistryHashAfterRefresh": self._agent_nodes_map_snapshot_hash,
                    "completedAt": utc_now_iso(),
                }
                results.append(summary)
                if task_id:
                    completed_by_task_id[task_id] = summary
                return
            try:
                _delta_messages, _delta_todos, summary, child_requests = await self._await_with_heartbeat(
                    str(episode.get("episodeId") or ""),
                    _run_parallel_agent_branch(arg, agent_data),
                    progress=f"delegation: running subagent {agent_id or 'worker'}",
                    interval_seconds=8.0,
                )
                if not child_requests and self._summary_indicates_unrouted_child_delegation(summary):
                    if not bool(branch.get("allowChildDelegation")):
                        summary = {
                            **dict(summary or {}),
                            "status": "blocked",
                            "error": "child_delegation_not_allowed",
                            "dispatchStatus": "dispatch_missing_child_budget",
                            "childDelegationCount": 0,
                            "blockedChildDelegationCount": int(summary.get("nestedDispatchCount") or 1),
                            "localSelfCheck": (
                                "Subagent requested child delegation, but its delegationPolicy.allowChildDelegation "
                                "was false. RuntimeEpisodeRunner blocked the nested dispatch instead of spawning "
                                "an unbounded child episode."
                            ),
                        }
                    else:
                        child_requests = [self._fallback_child_delegation_request(branch=branch, summary=summary)]
                        summary = {
                            **dict(summary or {}),
                            "status": "waiting_child_delegation",
                            "error": "delegation_child_requested",
                            "childDelegationCount": 1,
                            "childDelegationRequestIds": [child_requests[0]["requestId"]],
                            "localSelfCheck": (
                                "Subagent requested child delegation but returned an incomplete nested dispatch payload. "
                                "RuntimeEpisodeRunner promoted a conservative child delegation episode instead of failing the parent."
                            ),
                        }
                summary = dict(summary or {})
                summary = self._delegation_summary_with_expected_artifact_guard(
                    summary,
                    branch=branch,
                    workspace_path=workspace_path,
                )
                summary.setdefault("taskBriefId", task_id or branch.get("taskBriefId"))
                results.append(summary)
                if task_id:
                    completed_by_task_id[task_id] = summary
                child_episode_ids.extend(self._enqueue_child_delegation_requests(child_requests, episode=episode))
            except Exception as exc:
                summary = {
                    "invocationId": branch.get("invocationId"),
                    "taskBriefId": task_id or branch.get("taskBriefId"),
                    "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                    "taskGoal": branch.get("reason"),
                    "agentId": agent_id,
                    "agentName": branch.get("agentName") or agent_id,
                    "delegationId": branch.get("delegationId"),
                    "lane": branch.get("lane") or "subagent",
                    "targetId": agent_id,
                    "targetLabel": branch.get("agentName") or agent_id,
                    "branchIndex": branch.get("branchIndex"),
                    "status": "error",
                    "error": str(exc).strip() or exc.__class__.__name__,
                    "completedAt": utc_now_iso(),
                }
                results.append(summary)
                if task_id:
                    completed_by_task_id[task_id] = summary

        while pending:
            progressed = False
            for item in list(pending):
                task_id = self._delegation_send_task_id(item)
                deps = self._delegation_send_dependencies(item)
                failed_deps = [
                    dep
                    for dep in deps
                    if dep in completed_by_task_id and not self._delegation_summary_succeeded(completed_by_task_id[dep])
                ]
                if failed_deps:
                    branch = self._delegation_send_branch(item)
                    summary = self._blocked_dependency_summary(
                        branch=branch,
                        task_id=task_id,
                        deps=deps,
                        reason="dependency_failed",
                        failed=failed_deps,
                    )
                    results.append(summary)
                    if task_id:
                        completed_by_task_id[task_id] = summary
                    pending.remove(item)
                    progressed = True
                    continue
                if any(dep not in completed_by_task_id for dep in deps):
                    continue
                await _run_ready_send(item)
                pending.remove(item)
                progressed = True
                break
            if progressed:
                continue
            for item in list(pending):
                task_id = self._delegation_send_task_id(item)
                deps = self._delegation_send_dependencies(item)
                missing_deps = [dep for dep in deps if dep not in completed_by_task_id]
                branch = self._delegation_send_branch(item)
                summary = self._blocked_dependency_summary(
                    branch=branch,
                    task_id=task_id,
                    deps=deps,
                    reason="dependency_not_satisfied",
                    failed=missing_deps,
                )
                results.append(summary)
                if task_id:
                    completed_by_task_id[task_id] = summary
                pending.remove(item)
        return results, child_episode_ids

    @staticmethod
    def _summary_indicates_unrouted_child_delegation(summary: Any) -> bool:
        if not isinstance(summary, dict):
            return False
        if str(summary.get("error") or "").strip() != "delegation_child_requested":
            return False
        if int(summary.get("childDelegationCount") or 0) > 0:
            return False
        return int(summary.get("nestedDispatchCount") or 0) > 0

    @staticmethod
    def _fallback_child_delegation_request(*, branch: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
        source_invocation_id = str(
            summary.get("invocationId")
            or branch.get("invocationId")
            or uuid.uuid4().hex[:12]
        ).strip()
        source_delegation_id = str(summary.get("delegationId") or branch.get("delegationId") or "").strip()
        child_invocation_id = f"{source_invocation_id}:child:{uuid.uuid4().hex[:8]}"
        child_delegation_id = f"{source_delegation_id or source_invocation_id}:child"
        task_goal = str(
            summary.get("taskGoal")
            or branch.get("reason")
            or "Continue the child delegation requested by the subagent."
        ).strip()
        child_branch = {
            "invocationId": child_invocation_id,
            "delegationId": child_delegation_id,
            "taskBriefId": f"{child_invocation_id}:brief",
            "reason": f"Continue child delegation for: {task_goal}",
            "delegationDepth": int(branch.get("delegationDepth") or 0) + 1,
            "runtimeAccess": ["delegation.recursive"],
            "allowChildDelegation": False,
        }
        return {
            "requestId": f"fallback_child_{child_invocation_id}",
            "createdAt": utc_now_iso(),
            "sourceInvocationId": source_invocation_id,
            "sourceDelegationId": source_delegation_id or None,
            "sourceAgentId": summary.get("agentId") or branch.get("agentId"),
            "sourceAgentName": summary.get("agentName") or branch.get("agentName"),
            "childInvocationId": child_invocation_id,
            "childDelegationId": child_delegation_id,
            "childTaskBriefId": child_branch["taskBriefId"],
            "childTaskGoal": child_branch["reason"],
            "childAgentId": child_branch.get("agentId"),
            "childAgentName": child_branch.get("agentName"),
            "childDepth": child_branch["delegationDepth"],
            "fallbackReason": "incomplete_nested_delegation_payload",
            "send": {
                "node": "parallel_delegate_task",
                "arg": {
                    "parallel_branch": child_branch,
                    "messages": [],
                    "todos": [],
                },
            },
        }

    @staticmethod
    def _infer_child_delegation_target(text: str) -> tuple[str, str, str] | None:
        normalized = (text or "").lower()
        research_tokens = (
            "research",
            "web research",
            "source",
            "sources",
            "evidence",
            "fact",
            "facts",
            "citation",
            "citations",
            "调研",
            "资料",
            "来源",
            "证据",
            "搜索",
            "查证",
            "检索",
            "事实",
        )
        engineering_tokens = (
            "implement",
            "implementation",
            "coding",
            "code",
            "patch",
            "build",
            "file",
            "workspace",
            "工程",
            "实现",
            "代码",
            "修复",
            "补丁",
            "文件",
            "构建",
        )
        verification_tokens = (
            "verify",
            "verification",
            "test",
            "review",
            "audit",
            "validate",
            "proof",
            "handoff",
            "runtime handoff",
            "runtime",
            "delegation",
            "delegate",
            "orchestration",
            "验证",
            "测试",
            "复核",
            "审计",
            "校验",
            "证明",
            "回流",
            "派发",
            "委派",
            "编排",
            "主链",
        )
        if any(token in normalized for token in research_tokens):
            return ("web-research-architect", "Web Research Architect", "research_goal")
        if any(token in normalized for token in verification_tokens):
            return ("verification-engineer", "Verification Engineer", "verification_goal")
        if any(token in normalized for token in engineering_tokens):
            return ("implementation-engineer", "Implementation Engineer", "engineering_goal")
        return None

    @classmethod
    def _repair_child_worker_target(
        cls,
        worker_brief: dict[str, Any],
        *,
        request: dict[str, Any],
        child_branch: dict[str, Any],
    ) -> dict[str, Any]:
        text = " ".join(
            str(value or "")
            for value in (
                request.get("childTaskGoal"),
                worker_brief.get("title"),
                worker_brief.get("goal"),
                worker_brief.get("brief"),
                child_branch.get("reason"),
                child_branch.get("acceptanceHint"),
            )
        )
        inferred = cls._infer_child_delegation_target(text)
        if inferred is None:
            return worker_brief
        inferred_id, inferred_name, reason = inferred
        current_id = str(worker_brief.get("agentId") or "").strip()
        current_norm = current_id.lower()
        source_norm = str(request.get("sourceAgentId") or "").strip().lower()
        conflict_targets = {
            "web-research-architect": {"creative-media-director", "implementation-engineer", "skill-workflow-curator"},
            "implementation-engineer": {"creative-media-director", "web-research-architect", "skill-workflow-curator"},
            "verification-engineer": {
                "creative-media-director",
                "implementation-engineer",
                "web-research-architect",
                "skill-workflow-curator",
            },
        }
        should_repair = (
            not current_norm
            or bool(source_norm and current_norm == source_norm)
            or current_norm in conflict_targets.get(inferred_id, set())
        )
        if not should_repair:
            return worker_brief
        repaired = dict(worker_brief)
        repaired["agentId"] = inferred_id
        repaired["agentName"] = inferred_name
        repaired["targetRepairReason"] = reason
        repaired["originalAgentId"] = current_id or request.get("childAgentId")
        repaired["originalAgentName"] = worker_brief.get("agentName") or request.get("childAgentName")
        return repaired

    @classmethod
    def _child_worker_brief_from_request(
        cls,
        request: dict[str, Any],
        *,
        workspace_path: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        child_branch = dict(((request.get("send") or {}).get("arg") or {}).get("parallel_branch") or {})
        raw_task_brief = (
            dict(request.get("childTaskBrief"))
            if isinstance(request.get("childTaskBrief"), dict)
            else dict(child_branch.get("taskBrief"))
            if isinstance(child_branch.get("taskBrief"), dict)
            else {}
        )
        normalized = normalize_task_briefs([raw_task_brief])[0] if raw_task_brief else {}
        worker_brief: dict[str, Any] = dict(normalized)

        def _set_default_text(key: str, *values: Any) -> None:
            if str(worker_brief.get(key) or "").strip():
                return
            for value in values:
                text = str(value or "").strip()
                if text:
                    worker_brief[key] = text
                    return

        child_task_id = (
            request.get("childTaskBriefId")
            or worker_brief.get("taskBriefId")
            or worker_brief.get("id")
            or request.get("childInvocationId")
        )
        id_like_values = {
            str(value).strip()
            for value in (
                child_task_id,
                request.get("childInvocationId"),
                request.get("childDelegationId"),
                worker_brief.get("id"),
            )
            if str(value or "").strip()
        }
        child_goal = ""
        for value in (
            request.get("childTaskGoal"),
            worker_brief.get("goal"),
            worker_brief.get("brief"),
            worker_brief.get("title"),
            child_branch.get("reason"),
        ):
            text = str(value or "").strip()
            if text and text not in id_like_values:
                child_goal = text
                break
        _set_default_text("id", child_task_id)
        _set_default_text("taskBriefId", child_task_id)
        _set_default_text("title", child_goal, "child delegation")
        _set_default_text("goal", child_goal, worker_brief.get("brief"), worker_brief.get("title"), "Continue the requested child delegation.")
        _set_default_text("brief", worker_brief.get("goal"), child_goal, "Continue the requested child delegation.")
        _set_default_text("agentId", request.get("childAgentId"), child_branch.get("agentId"))
        _set_default_text("agentName", request.get("childAgentName"), child_branch.get("agentName"))
        if not worker_brief.get("runtimeAccess"):
            worker_brief["runtimeAccess"] = child_branch.get("runtimeAccess") or ["delegation.recursive"]
        worker_brief.setdefault("parentDelegationId", request.get("sourceDelegationId"))
        worker_brief.setdefault("parentInvocationId", request.get("sourceInvocationId"))
        if child_branch.get("writeSet") and not worker_brief.get("writeSet"):
            worker_brief["writeSet"] = child_branch.get("writeSet")
        if child_branch.get("acceptanceHint") and not worker_brief.get("acceptanceHint"):
            worker_brief["acceptanceHint"] = child_branch.get("acceptanceHint")
        if workspace_path:
            worker_brief["workspacePath"] = workspace_path
        return cls._repair_child_worker_target(worker_brief, request=request, child_branch=child_branch), child_branch

    def _enqueue_child_delegation_requests(self, child_requests: list[dict[str, Any]], *, episode: dict[str, Any]) -> list[str]:
        if not child_requests:
            return []
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip() or None
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip() or None
        inputs = dict(episode.get("inputs") or {}) if isinstance(episode.get("inputs"), dict) else {}
        need = dict(episode.get("need") or {}) if isinstance(episode.get("need"), dict) else {}
        workspace_path = str(
            inputs.get("workspacePath")
            or inputs.get("workspace_path")
            or need.get("workspacePath")
            or need.get("workspace_path")
            or episode.get("workspacePath")
            or episode.get("workspace_path")
            or ""
        ).strip() or None
        child_episode_ids: list[str] = []
        for item in child_requests:
            worker_brief, child_branch = self._child_worker_brief_from_request(item, workspace_path=workspace_path)
            child_inputs = {
                "targetCount": 1,
                "workerBriefs": [worker_brief],
                "allowChildDelegation": bool(child_branch.get("allowChildDelegation")),
                "childDelegationBudget": child_branch.get("childDelegationBudget") or {},
                "writeSetPartitions": child_branch.get("writeSetPartitions") or [],
            }
            if workspace_path:
                child_inputs["workspacePath"] = workspace_path
            extra = {
                "sourceInvocationId": item.get("sourceInvocationId"),
                "childInvocationId": item.get("childInvocationId"),
                "childTaskBriefId": item.get("childTaskBriefId"),
                "childAgentId": worker_brief.get("agentId") or item.get("childAgentId"),
                "childAgentName": worker_brief.get("agentName") or item.get("childAgentName"),
                "childDepth": item.get("childDepth"),
            }
            if worker_brief.get("targetRepairReason"):
                extra["targetRepairReason"] = worker_brief.get("targetRepairReason")
                extra["originalChildAgentId"] = worker_brief.get("originalAgentId")
                extra["originalChildAgentName"] = worker_brief.get("originalAgentName")
                extra["metadata"] = {
                    "targetRepairReason": worker_brief.get("targetRepairReason"),
                    "originalChildAgentId": worker_brief.get("originalAgentId"),
                    "originalChildAgentName": worker_brief.get("originalAgentName"),
                }
            if workspace_path:
                extra["workspacePath"] = workspace_path
            child_episode = build_runtime_episode(
                need={
                    "kind": "delegation",
                    "source": "subagent",
                    "reason": item.get("childTaskGoal") or "child delegation",
                    "needId": item.get("childDelegationId") or item.get("childInvocationId"),
                    "parentEpisodeId": episode_id,
                    "inputs": child_inputs,
                },
                kind="delegation",
                state="queued",
                required_runtime_access=["delegation.recursive"],
                parent_episode_id=episode_id,
                continuation_target="runtime_episode_runner",
                extra=extra,
            )
            with bind_runtime_context(
                session_id=session_id,
                run_id=run_id,
                workspace_path=workspace_path,
                runtime_kind="delegation",
                trigger_source="runtime_episode_runner.child_delegation",
            ):
                persisted = db.upsert_runtime_episode_record(child_episode, session_id=session_id, run_id=run_id, enqueue=True)
                child_episode_ids.append(str(persisted.get("episodeId") or persisted.get("id") or ""))
                self._emit("delegation.child.requested", episode=persisted, childDelegation=item, session_id=session_id, run_id=run_id)
                self._emit("runtime.episode.queued", episode=persisted, session_id=session_id, run_id=run_id)
        return [item for item in child_episode_ids if item]

    async def _execute_network_peer_target(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "network_peer: delegate")
        inputs = dict(episode.get("inputs") or {})
        need = dict(episode.get("need") or {})
        peer_id = str(episode.get("targetId") or episode.get("target_id") or inputs.get("peerId") or inputs.get("peer_id") or "").strip()
        task = str(inputs.get("task") or inputs.get("brief") or need.get("reason") or episode.get("reason") or "").strip()
        if not peer_id:
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary="Network peer episode cannot start because no peer target was selected.",
                status="failed",
                confidence="high",
                consumer_hint="Select a trusted Network Supervisor peer before retrying this episode.",
                extra={"errorCode": "network_peer_missing_target"},
            )
        if not task:
            task = f"Runtime episode {episode.get('episodeId') or ''}"
        try:
            from runtimes.network_supervisor.service import network_supervisor_service

            result = await self._await_with_heartbeat(
                str(episode.get("episodeId") or ""),
                network_supervisor_service.delegate_task(
                    peer_id=peer_id,
                    task=task,
                    timeout_seconds=int(inputs.get("timeoutSeconds") or inputs.get("timeout_seconds") or 0) or None,
                    project_id=inputs.get("projectId") or inputs.get("project_id"),
                    workspace_id=inputs.get("workspaceId") or inputs.get("workspace_id"),
                    workspace_path=inputs.get("workspacePath") or inputs.get("workspace_path"),
                    scope_hint=inputs.get("scopeHint") or inputs.get("scope_hint"),
                ),
                progress=f"network_peer: waiting for {peer_id}",
            )
            summary = _preview(result.get("result") or f"Network peer {peer_id} completed the delegated task.")
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=f"Network peer {peer_id} completed.\n{summary}",
                status="ready",
                confidence="medium",
                consumer_hint="Merge this remote peer result into the parent runtime episode.",
                extra={
                    "peerId": peer_id,
                    "delegationId": result.get("delegationId"),
                    "outerRunId": result.get("outerRunId"),
                    "refs": [f"network_peer:{peer_id}:{result.get('delegationId') or episode.get('episodeId')}"],
                },
            )
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            detail = getattr(exc, "detail", None)
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=f"Network peer {peer_id} failed: {detail or exc}",
                status="failed",
                confidence="low",
                consumer_hint="Retry after peer connectivity/auth is fixed, or replace target peer.",
                extra={
                    "errorCode": "network_peer_delegate_failed",
                    "errorMessage": str(detail or exc),
                    "httpStatus": status_code,
                    "peerId": peer_id,
                },
            )

    async def _execute_external_worker_target(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "external_worker: dispatch")
        inputs = dict(episode.get("inputs") or {})
        need = dict(episode.get("need") or {})
        target_id = str(episode.get("targetId") or episode.get("target_id") or inputs.get("workerId") or inputs.get("workerType") or "").strip()
        task_brief = dict(inputs.get("taskBrief") or inputs.get("task_brief") or {})
        task_goal = str(task_brief.get("goal") or inputs.get("task") or inputs.get("brief") or need.get("reason") or episode.get("reason") or "").strip()
        if not task_goal:
            task_goal = f"Runtime episode {episode.get('episodeId') or ''}"
        task_brief.setdefault("taskBriefId", str(inputs.get("taskBriefId") or episode.get("episodeId") or "external-worker-task"))
        task_brief.setdefault("goal", task_goal)
        task_brief["executionLaneHint"] = "external_worker"
        if target_id:
            task_brief.setdefault("preferredWorkerType", target_id)
            task_brief.setdefault("preferredAgentId", target_id)
        try:
            from core.native_tools import delegation_broker

            command = delegation_broker.func(
                mode="dispatch",
                tasks=[task_brief],
                target_count=1,
                state={
                    "delegationDispatchSource": "runtime_episode_runner.external_worker",
                    "workspace_path": inputs.get("workspacePath") or inputs.get("workspace_path") or "",
                    "current_route_context": {
                        "runtimeToolGrants": [{"group": "delegation.recursive", "runtimeKind": "subagent"}],
                    },
                },
                tool_call_id=f"episode:{episode.get('episodeId')}:external_worker_dispatch",
            )
            update = dict(getattr(command, "update", None) or {})
            results = [item for item in list(update.get("parallel_results") or []) if isinstance(item, dict)]
            first = dict(results[0]) if results else {}
            worker_status = str(first.get("status") or "running").strip().lower()
            if worker_status in {"error", "failed", "blocked", "marker_missing"}:
                status = "failed"
            elif worker_status in {"queued", "running", "pending"}:
                status = "waiting"
            else:
                status = "ready"
            label = str(first.get("targetLabel") or first.get("target_label") or target_id or "external worker").strip()
            summary = f"External worker {label} dispatched."
            if first.get("workerResult"):
                summary = f"External worker {label} returned result."
            if first.get("error"):
                summary = f"External worker {label} failed: {first.get('error')}"
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=summary,
                status=status,
                confidence="medium" if status != "failed" else "low",
                consumer_hint="Observe external worker command session until result markers are available.",
                extra={
                    "externalWorker": first,
                    "delegationRefs": [item.get("delegationId") or item.get("id") for item in results if isinstance(item, dict)],
                    "refs": [first.get("traceRef") or first.get("trace_ref") or f"external_worker:{episode.get('episodeId')}"],
                    "errorCode": "external_worker_dispatch_failed" if status == "failed" else None,
                },
            )
        except Exception as exc:
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=f"External worker dispatch failed: {type(exc).__name__}: {exc}",
                status="failed",
                confidence="low",
                consumer_hint="Check external worker descriptors and command profiles before retry.",
                extra={"errorCode": "external_worker_dispatch_failed", "errorMessage": str(exc)},
            )


runtime_episode_runner = RuntimeEpisodeRunner()
