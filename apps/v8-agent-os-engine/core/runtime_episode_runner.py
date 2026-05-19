from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from core.database import db
from core.json_safe import to_jsonable
from core.runtime_episodes import build_handoff_ref
from core.time_truth import utc_now_iso
from core.workspace_state_digest import build_workspace_state_digest_context


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
        self._stop_event: asyncio.Event | None = None
        self._lease_seconds = 75
        self._poll_seconds = 0.8

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="runtime-episode-runner")
        print(f"[EpisodeRunner] Started worker {self.worker_id}.")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print(f"[EpisodeRunner] Stopped worker {self.worker_id}.")

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                episode = db.claim_runtime_episode(worker_id=self.worker_id, lease_seconds=self._lease_seconds)
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
        session_id = str(episode.get("session_id") or episode.get("sessionId") or "").strip() or None
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip() or None
        self._emit("runtime.episode.started", episode=episode, session_id=session_id, run_id=run_id)
        try:
            self._heartbeat(episode_id, "executor starting")
            if kind == "research":
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
            final_state = "completed" if str(handoff.get("status") or "ready") not in {"failed", "blocked"} else "failed"
            completed = db.complete_runtime_episode(
                episode_id,
                state=final_state,
                result_ref=str(persisted_handoff.get("handoffId") or persisted_handoff.get("handoffRefId") or ""),
                error_code=str(handoff.get("errorCode") or "") or None,
                error_message=str(handoff.get("errorMessage") or "") or None,
                metadata={"handoff": persisted_handoff},
            )
            self._emit(
                "runtime.episode.completed" if final_state == "completed" else "runtime.episode.failed",
                episode=completed or {**episode, "state": final_state},
                handoff=persisted_handoff,
                session_id=session_id,
                run_id=run_id,
            )
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
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
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or ""),
            kind="engineering",
            compact_summary=f"{context_summary}\n{_preview(digest_text, limit=700)}",
            status="ready",
            confidence="medium",
            consumer_hint="Engineering episode owns writeSet/proof/build work. Supervisor should not batch-write directly.",
            extra={
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
        self._heartbeat(str(episode.get("episodeId")), "rpa: prepare")
        inputs = dict(episode.get("inputs") or {})
        from runtimes.rpa.runtime import rpa_runtime

        if inputs.get("draftId") or inputs.get("scriptId"):
            script_id = str(inputs.get("draftId") or inputs.get("scriptId"))
            prepared = rpa_runtime.prepare_draft_run(script_id=script_id, variables=inputs.get("variables") or {})
            summary = f"RPA draft prepared: {script_id}"
            refs = [prepared.get("robotFile") or prepared.get("path") or script_id]
        elif inputs.get("robotFile"):
            robot_file = str(inputs.get("robotFile"))
            prepared = rpa_runtime.prepare_existing_run(robot_file=robot_file, variables=inputs.get("variables") or {})
            summary = f"RPA existing flow prepared: {robot_file}"
            refs = [prepared.get("robotFile") or robot_file]
        else:
            summary = "RPA episode routed; no draft/template selected."
            refs = []
        return build_handoff_ref(
            producer_episode_id=str(episode.get("episodeId") or ""),
            kind="rpa",
            compact_summary=summary,
            status="ready",
            confidence="medium",
            consumer_hint="RPA prepared handoff can be executed by RPA runtime when user selects variables/template.",
            extra={"traceRefs": refs},
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
            worker_briefs = [{"title": "Delegated task", "brief": inputs.get("brief") or (episode.get("need") or {}).get("reason") or ""}]
        try:
            from core.native_tools import delegation_broker

            command = delegation_broker.func(
                mode="dispatch",
                tasks=worker_briefs,
                target_count=target_count,
                state={
                    "delegationDispatchSource": "runtime_episode_runner",
                    "current_route_context": {
                        "runtimeToolGrants": [{"group": "delegation.recursive", "runtimeKind": "subagent"}],
                    },
                },
                tool_call_id=f"episode:{episode.get('episodeId')}:delegation_dispatch",
            )
            update = dict(getattr(command, "update", None) or {})
            results = [item for item in list(update.get("parallel_results") or []) if isinstance(item, dict)]
            failed = [item for item in results if str(item.get("status") or "").lower() in {"error", "failed", "blocked"}]
            status = "failed" if results and len(failed) == len(results) else "ready"
            summary = f"Delegation dispatched {len(results) or target_count} worker(s)."
            if failed:
                summary += f" failed={len(failed)}"
            return build_handoff_ref(
                producer_episode_id=str(episode.get("episodeId") or ""),
                kind="delegation",
                compact_summary=summary,
                status=status,
                confidence="medium",
                consumer_hint="Parent episode should wait for delegation completion events before merging.",
                extra={"delegationRefs": [item.get("delegationId") or item.get("id") for item in results if isinstance(item, dict)]},
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


runtime_episode_runner = RuntimeEpisodeRunner()
