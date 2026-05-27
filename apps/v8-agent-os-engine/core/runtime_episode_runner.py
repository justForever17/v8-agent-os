from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any

from core.database import db
from core.delegation_broker import normalize_task_briefs
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
        self._agent_nodes_map_cache: dict[str, Any] | None = None

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
        while not self._stop_event.is_set():
            try:
                episode = db.claim_runtime_episode(
                    worker_id=self.worker_id,
                    lease_seconds=self._lease_seconds,
                    require_bound_run=True,
                )
                if not episode:
                    await asyncio.sleep(self._poll_seconds)
                    continue
                await self._execute_episode(episode)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[EpisodeRunner] Loop error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(1.0)

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
            self._emit(
                "runtime.episode.completed" if final_state == "completed" else "runtime.episode.failed",
                episode=completed or {**episode, "state": final_state},
                handoff=persisted_handoff,
                recovery=recovery,
                session_id=session_id,
                run_id=run_id,
            )
            if final_state == "completed":
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
        compensation = episode.get("compensationPlan") if isinstance(episode.get("compensationPlan"), dict) else {}
        refs = list(handoff.get("refs") or [])
        return {
            "episodeId": episode.get("episodeId") or episode.get("id"),
            "kind": episode.get("kind"),
            "state": final_state,
            "done": refs if refs else ([handoff.get("artifactId")] if handoff.get("artifactId") else []),
            "notDone": [episode.get("reason") or (episode.get("need") or {}).get("reason") or "episode work"] if failed else [],
            "canRetry": failed and self._can_retry(episode),
            "canReplaceTarget": failed and bool(episode.get("targetKind") or episode.get("targetId")),
            "canContinueParent": not failed,
            "compensationPlan": compensation,
            "nextAction": (
                "retry_or_replace_target" if failed else "merge_handoff_into_parent"
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
        terminal = {"completed", "failed", "cancelled", "merged"}
        if any(str(child.get("state") or "") not in terminal for child in children):
            return
        completed_children = [child for child in children if str(child.get("state") or "") in {"completed", "merged"}]
        failed_children = [child for child in children if str(child.get("state") or "") in {"failed", "cancelled"}]
        child_handoffs: list[dict[str, Any]] = []
        for child in completed_children:
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
            updated = db.complete_runtime_episode(
                parent_id,
                state="failed",
                error_code="child_episode_failed",
                error_message=f"{len(failed_children)} child episode(s) failed.",
                metadata={"resumeToken": resume_token, "childHandoffs": child_handoffs, "recoverable": True},
            ) or parent
            self._emit("runtime.episode.failed", episode=updated, session_id=session_id, run_id=run_id, resumeToken=resume_token)
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
        query = str(inputs.get("query") or need.get("query") or need.get("reason") or "research request").strip()
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
            compact_summary=_preview(visible or f"Research routed for: {query}"),
            status="ready",
            confidence="medium",
            consumer_hint="Use this research handoff as evidence refs input for Engineering/Creative episodes.",
            extra={"query": query, "researchRefs": [f"episode:{episode.get('episodeId')}"]},
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
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="engineering",
                compact_summary=(
                    f"Engineering handoff_ready after {ready_count} child delegation handoff(s).\n"
                    f"{_preview('; '.join(str(item.get('compactSummary') or item.get('summary') or item.get('kind') or '') for item in child_handoffs), limit=900)}"
                ),
                status="ready",
                confidence="medium",
                consumer_hint="Merge child delegation handoffs into Supervisor route context and continue orchestration.",
                extra={
                    "engineeringState": "handoff_ready",
                    "childHandoffs": child_handoffs,
                    "handoffRefs": [item.get("handoffId") or item.get("handoffRefId") for item in child_handoffs if isinstance(item, dict)],
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
        if worker_briefs:
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
            delegation_status = str(delegation_handoff.get("status") or "ready").strip().lower()
            status = "waiting" if delegation_status in {"waiting", "pending", "running"} else delegation_status
            if status not in {"failed", "blocked", "waiting"}:
                status = "ready"
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="engineering",
                compact_summary=(
                    f"Engineering execution_started through {len(worker_briefs)} delegated worker(s).\n"
                    f"{_preview(delegation_handoff.get('compactSummary') or delegation_handoff.get('summary') or context_summary, limit=700)}"
                ),
                status=status,
                confidence=str(delegation_handoff.get("confidence") or "medium"),
                consumer_hint="Merge this engineering handoff into Supervisor route context before continuing.",
                extra={
                    "engineeringState": "execution_started",
                    "delegationHandoff": delegation_handoff,
                    "workspaceDigestRef": f"workspace_digest:{episode.get('episodeId')}",
                    "proofExpectations": inputs.get("proofExpectations") or need.get("proofExpectations") or [],
                    "consumedRefs": inputs.get("handoffRefs") or need.get("handoffRefs") or [],
                },
            )
        reason = str(inputs.get("task") or need.get("reason") or "engineering episode").strip()
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or ""),
            kind="engineering",
            compact_summary=(
                f"Engineering work_plan_ready, but no executable worker brief/task was available yet.\n"
                f"Reason: {reason}\n{_preview(context_summary or digest_text, limit=700)}"
            ),
            status="ready",
            confidence="medium",
            consumer_hint="Provide workerBriefs/taskBriefs or reroute with blockedToolIntent; Supervisor should not batch-write directly.",
            extra={
                "engineeringState": "work_plan_ready",
                "recoverable": True,
                "errorCode": "engineering_missing_executable_tasks",
                "workspaceDigestRef": f"workspace_digest:{episode.get('episodeId')}",
                "proofExpectations": inputs.get("proofExpectations") or need.get("proofExpectations") or [],
                "consumedRefs": inputs.get("handoffRefs") or need.get("handoffRefs") or [],
            },
        )

    async def _execute_creative_media(self, episode: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat(str(episode.get("episodeId")), "creative_media: recipe compile")
        need = dict(episode.get("need") or {})
        inputs = dict(episode.get("inputs") or {})
        request = dict(inputs.get("request") or {})
        request.setdefault("modality", inputs.get("modality") or need.get("modality") or "image")
        request.setdefault("prompt", inputs.get("prompt") or need.get("reason") or "Create supporting visual asset.")
        try:
            from runtimes.creative_media.runtime import creative_media_runtime

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
                extra={"recipeRefs": [recipe_id] if recipe_id else [], "providerStatus": recipe.get("providerStatus")},
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
                    kind="delegation",
                    compact_summary="Delegation episode cannot dispatch because it has no worker brief/task.",
                    status="failed",
                    confidence="high",
                    consumer_hint="Retry with inputs.workerBriefs/tasks or route from a planner plan that contains taskBriefs.",
                    extra={
                        "errorCode": "delegation_missing_tasks",
                        "dispatchStatus": "missing_tasks",
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
            waiting_child = [
                item
                for item in results
                if str(item.get("status") or "").lower() in {"waiting_child_delegation", "waiting_child", "waiting"}
            ]
            status = "waiting" if waiting_child else ("failed" if results and len(failed) == len(results) else "ready")
            summary = f"Delegation dispatched {len(results) or target_count} worker(s)."
            if local_results:
                summary = f"Delegation executed {len(local_results)} local subagent worker(s)."
            if failed:
                summary += f" failed={len(failed)}"
            if waiting_child:
                summary += f" waiting_child={len(waiting_child)}"
            if not results:
                status = "failed"
                summary = "Delegation dispatch did not produce confirmed worker tasks."
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=summary,
                status=status,
                confidence="medium",
                consumer_hint="Parent episode should wait for delegation completion events before merging.",
                extra={
                    "delegationRefs": [item.get("delegationId") or item.get("id") for item in results if isinstance(item, dict)],
                    "childEpisodeIds": child_episode_ids,
                    "results": [
                        {
                            "delegationId": item.get("delegationId") or item.get("id"),
                            "targetLabel": item.get("targetLabel") or item.get("agentName"),
                            "status": item.get("status"),
                            "error": item.get("error"),
                            "compactTranscript": _preview(item.get("compactTranscript"), limit=900),
                        }
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

    def _build_agent_nodes_map(self) -> dict[str, Any]:
        if self._agent_nodes_map_cache is not None:
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
        for item in sends:
            node = str(getattr(item, "node", "") or "")
            arg = getattr(item, "arg", None)
            if node != "parallel_delegate_task" or not isinstance(arg, dict):
                continue
            arg = dict(arg)
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
            if not agent_data:
                results.append(
                    {
                        "invocationId": branch.get("invocationId"),
                        "taskBriefId": branch.get("taskBriefId"),
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
                        "error": f"未找到子 Agent '{agent_id}'。",
                        "completedAt": utc_now_iso(),
                    }
                )
                continue
            try:
                _delta_messages, _delta_todos, summary, child_requests = await self._await_with_heartbeat(
                    str(episode.get("episodeId") or ""),
                    _run_parallel_agent_branch(arg, agent_data),
                    progress=f"delegation: running subagent {agent_id or 'worker'}",
                    interval_seconds=8.0,
                )
                if not child_requests and self._summary_indicates_unrouted_child_delegation(summary):
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
                results.append(dict(summary or {}))
                child_episode_ids.extend(self._enqueue_child_delegation_requests(child_requests, episode=episode))
            except Exception as exc:
                results.append(
                    {
                        "invocationId": branch.get("invocationId"),
                        "taskBriefId": branch.get("taskBriefId"),
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
                )
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
            child_branch = dict(((item.get("send") or {}).get("arg") or {}).get("parallel_branch") or {})
            worker_brief = {
                "id": item.get("childTaskBriefId") or item.get("childInvocationId"),
                "title": item.get("childTaskGoal") or "child delegation",
                "goal": item.get("childTaskGoal") or "Continue the requested child delegation.",
                "brief": item.get("childTaskGoal") or "Continue the requested child delegation.",
                "agentId": item.get("childAgentId"),
                "agentName": item.get("childAgentName"),
                "runtimeAccess": child_branch.get("runtimeAccess") or ["delegation.recursive"],
                "parentDelegationId": item.get("sourceDelegationId"),
                "parentInvocationId": item.get("sourceInvocationId"),
                "writeSet": child_branch.get("writeSet"),
                "acceptanceHint": child_branch.get("acceptanceHint"),
            }
            if workspace_path:
                worker_brief["workspacePath"] = workspace_path
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
                "childAgentId": item.get("childAgentId"),
                "childAgentName": item.get("childAgentName"),
                "childDepth": item.get("childDepth"),
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
