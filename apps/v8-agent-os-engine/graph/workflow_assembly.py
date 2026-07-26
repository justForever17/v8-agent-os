import asyncio
import json
import os
import time
from datetime import datetime, timezone

from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from core.database import db
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.runtime_episodes import (
    ACTIVE_EPISODE_STATES,
    TERMINAL_EPISODE_STATES,
    append_handoff_ref,
    emit_runtime_episode_event,
    runtime_episode_parent_id,
    superseded_runtime_episode_ids,
    transition_runtime_episode,
)
from core.time_truth import utc_now_iso
from erc.runtime_context import get_runtime_context
from erc.runtime_stability import runtime_stability_service
from .parallel_support import (
    RUNTIME_EPISODE_WAIT_NODE,
    build_parallel_delegate_join_node,
    build_parallel_delegate_task_node,
)


RUNTIME_EPISODE_WAIT_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_WAIT_SECONDS", "600"))
RUNTIME_EPISODE_QUEUE_GRACE_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_QUEUE_GRACE_SECONDS", "60"))
RUNTIME_EPISODE_POLL_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_POLL_SECONDS", "0.8"))


def _merged_tool_command_update(commands: list[Command]) -> dict:
    """Merge ToolNode Command updates for one deterministic runtime transition."""
    merged: dict = {}
    messages: list = []
    for item in commands:
        update = dict(getattr(item, "update", None) or {})
        for key, value in update.items():
            if key == "messages":
                messages.extend(list(value or []))
            elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**dict(merged[key]), **value}
            else:
                merged[key] = value
    if messages:
        merged["messages"] = messages
    return merged


def _route_runtime_tool_commands(command):
    """Convert ToolNode ``Command[]`` runtime routes into graph-owned waits.

    LangGraph returns a list whenever any executed tool returns ``Command``.
    Treating that list as opaque left the queued episode stranded at the
    Supervisor node.  We preserve ordinary multi-command routing unchanged and
    collapse only the explicit runtime wait transition.
    """
    command_items = [item for item in command if isinstance(item, Command)] if isinstance(command, list) else []
    update = _merged_tool_command_update(command_items) if command_items else dict(getattr(command, "update", None) or {})
    runtime_statuses = [
        dict((getattr(item, "update", None) or {}).get("runtime_dispatch_status") or {})
        for item in command_items
        if isinstance((getattr(item, "update", None) or {}).get("runtime_dispatch_status"), dict)
    ]
    runtime_statuses = [status for status in runtime_statuses if status]
    runtime_status = dict(update.get("runtime_dispatch_status") or {})
    messages = list(update.get("messages") or [])
    waiting_status = next(
        (
            status
            for status in runtime_statuses
            if str(status.get("nextAction") or "").strip() == "wait_episode"
            and bool(status.get("dispatched", True))
        ),
        None,
    )
    should_wait = waiting_status is not None or str(runtime_status.get("nextAction") or "").strip() == "wait_episode"
    for message in messages:
        additional = dict(getattr(message, "additional_kwargs", None) or {})
        if str(additional.get("recommendedNextAction") or "").strip() == "wait_episode":
            should_wait = True
            break
    if should_wait:
        if waiting_status is not None:
            update["runtime_dispatch_status"] = waiting_status
        return Command(goto="runtime_episode", update=update)
    # Multiple runtime broker calls in one provider turn are executed by
    # ToolNode concurrently. Returning their Command[] unchanged makes
    # LangGraph apply more than one value to runtime_dispatch_status in the
    # same step. Collapse only runtime-bearing commands; ordinary multi-tool
    # routing keeps its existing semantics.
    if len(command_items) > 1 and runtime_statuses:
        return Command(goto="supervisor", update=update)
    return command


def _workflow_entry_command(state):
    runtime_status = dict((state or {}).get("runtime_dispatch_status") or {})
    if str(runtime_status.get("nextAction") or "").strip() == "wait_episode":
        return Command(goto="runtime_episode")
    return Command(goto="supervisor")


def _string_value(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _state_runtime_identity(state: dict | None) -> tuple[str | None, str | None, str | None]:
    runtime_context = get_runtime_context()
    state_dict = dict(state or {})
    route_context = dict(state_dict.get("current_route_context") or {})
    session_id = _string_value(
        state_dict.get("session_id"),
        state_dict.get("sessionId"),
        route_context.get("session_id"),
        route_context.get("sessionId"),
        runtime_context.get("session_id"),
        runtime_context.get("sessionId"),
    ) or None
    run_id = _string_value(
        state_dict.get("run_id"),
        state_dict.get("runId"),
        route_context.get("run_id"),
        route_context.get("runId"),
        runtime_context.get("run_id"),
        runtime_context.get("runId"),
    ) or None
    workspace_path = _string_value(
        state_dict.get("workspace_path"),
        state_dict.get("workspacePath"),
        route_context.get("workspace_path"),
        route_context.get("workspacePath"),
        runtime_context.get("workspace_path"),
        runtime_context.get("workspacePath"),
    ) or None
    return session_id, run_id, workspace_path


def _has_live_bound_episode_lease() -> bool:
    """Return true when a runner is actively working a canonical session-bound episode."""
    now_iso = utc_now_iso()
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_episode_queue
                WHERE state = 'leased'
                  AND COALESCE(session_id, '') <> ''
                  AND COALESCE(run_id, '') <> ''
                  AND COALESCE(lease_expires_at, '') > ?
                LIMIT 1
                """,
                (now_iso,),
            ).fetchone()
            return bool(row)
    except Exception:
        return False


def build_runtime_episode_wait_node():
    def _route_context_episode_ids(route_context: dict) -> list[str]:
        ids: list[str] = []
        for item in list(route_context.get("capabilityEpisodes") or []):
            if not isinstance(item, dict):
                continue
            episode_id = _string_value(item.get("episodeId"), item.get("needId"), item.get("id"))
            state = str(item.get("state") or "").strip()
            if episode_id and state in ACTIVE_EPISODE_STATES and episode_id not in ids:
                ids.append(episode_id)
        return ids

    def _load_relevant_episodes(*, route_context: dict, session_id: str | None, run_id: str | None) -> list[dict]:
        by_id: dict[str, dict] = {}
        for episode_id in _route_context_episode_ids(route_context):
            try:
                episode = db.get_runtime_episode(episode_id)
            except Exception:
                episode = None
            if episode:
                route_episode = {}
                for item in list(route_context.get("capabilityEpisodes") or []):
                    if _string_value(item.get("episodeId"), item.get("needId"), item.get("id")) == episode_id:
                        route_episode = dict(item)
                        break
                by_id[str(episode.get("episodeId") or episode.get("id") or episode_id)] = {**route_episode, **dict(episode)}
            else:
                for item in list(route_context.get("capabilityEpisodes") or []):
                    if _string_value(item.get("episodeId"), item.get("needId"), item.get("id")) == episode_id:
                        by_id[episode_id] = dict(item)
                        break
        try:
            db_rows = db.list_runtime_episodes(run_id=run_id, limit=100) if run_id else []
            if not db_rows and session_id:
                db_rows = db.list_runtime_episodes(session_id=session_id, limit=100)
        except Exception:
            db_rows = []
        for episode in db_rows:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if episode_id:
                by_id[episode_id] = {**dict(by_id.get(episode_id) or {}), **dict(episode)}
        return list(by_id.values())

    def _active_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip() in ACTIVE_EPISODE_STATES
        ]

    def _terminal_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip() in TERMINAL_EPISODE_STATES
        ]

    def _episode_queue_age_seconds(episode: dict, *, default_started_wall: float) -> float:
        raw_value = _string_value(
            episode.get("updatedAt"),
            episode.get("updated_at"),
            episode.get("createdAt"),
            episode.get("created_at"),
        )
        if raw_value:
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, time.time() - parsed.timestamp())
            except Exception:
                pass
        return max(0.0, time.time() - default_started_wall)

    def _merge_handoffs(route_context: dict, episodes: list[dict]) -> tuple[dict, list[dict]]:
        updated = dict(route_context or {})
        existing_ids = {
            str(item.get("handoffRefId") or item.get("handoffId") or item.get("artifactId") or "").strip()
            for item in list(updated.get("handoffRefs") or [])
            if isinstance(item, dict)
        }
        available: list[dict] = []
        available_ids: set[str] = set()
        for episode in episodes:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if not episode_id:
                continue
            try:
                handoffs = db.list_runtime_episode_handoffs(episode_id)
            except Exception:
                handoffs = []
            for row in handoffs:
                payload = dict(row.get("payload") or row.get("handoff") or row)
                handoff_id = _string_value(payload.get("handoffRefId"), payload.get("handoffId"), payload.get("artifactId"))
                if not handoff_id or handoff_id not in available_ids:
                    available.append(payload)
                    if handoff_id:
                        available_ids.add(handoff_id)
                if handoff_id and handoff_id in existing_ids:
                    continue
                updated = append_handoff_ref(updated, payload)
                if handoff_id:
                    existing_ids.add(handoff_id)
        return updated, available

    def _compact_handoff_projection(handoff: dict) -> dict:
        def _collect_results(value: object, *, depth: int = 0) -> list[dict]:
            if depth > 8 or not isinstance(value, dict):
                return []
            collected = [item for item in list(value.get("results") or []) if isinstance(item, dict)]
            collected.extend(
                item for item in list(value.get("taskBriefResults") or []) if isinstance(item, dict)
            )
            nested = value.get("delegationHandoff")
            if isinstance(nested, dict):
                collected.extend(_collect_results(nested, depth=depth + 1))
            for child in list(value.get("childHandoffs") or []):
                if isinstance(child, dict):
                    collected.extend(_collect_results(child, depth=depth + 1))
            return collected

        results: list[dict] = []
        result_keys: set[tuple[str, str, str]] = set()
        for item in _collect_results(handoff):
            key = (
                _string_value(item.get("delegationId"), item.get("invocationId")),
                _string_value(item.get("taskBriefId"), item.get("taskId")),
                _string_value(item.get("status")),
            )
            if key in result_keys:
                continue
            result_keys.add(key)
            results.append(item)
        compact_results: list[dict] = []
        for item in results[:8]:
            raw_artifact_refs = list(item.get("artifactRefs") or item.get("artifacts") or [])[:8]
            proof_refs = list(item.get("proofRefs") or [])[:8]
            status = _string_value(item.get("status"))
            status_lower = status.lower()
            sandbox_evidence = (
                dict(item.get("sandboxEvidence"))
                if isinstance(item.get("sandboxEvidence"), dict)
                else {}
            )
            blocking_statuses = {
                "error",
                "failed",
                "blocked",
                "dependency_failed",
                "cancelled",
            }
            research_result = bool(
                item.get("answer")
                or item.get("researchRef")
                or item.get("evidenceBundleId")
                or item.get("sourceUrls")
            )
            blocking_result = bool(
                status_lower in blocking_statuses
                or (status_lower == "degraded" and not research_result)
                or item.get("error")
                or str(sandbox_evidence.get("state") or "").strip().lower() in {"failed", "merge_failed"}
                or item.get("artifactRefsAccepted") is False
            )
            artifact_refs: list[dict | str] = []
            for ref in raw_artifact_refs:
                if not blocking_result:
                    artifact_refs.append(ref)
                    continue
                candidate = dict(ref) if isinstance(ref, dict) else {"ref": str(ref)}
                candidate.update({"accepted": False, "state": "quarantined_unmerged"})
                artifact_refs.append(candidate)
            verification_results = [
                dict(result)
                for result in list(item.get("verificationResults") or [])
                if isinstance(result, dict)
            ][:4]
            verification_evidence = (
                dict(item.get("verificationEvidence"))
                if isinstance(item.get("verificationEvidence"), dict)
                else dict(item.get("verification"))
                if isinstance(item.get("verification"), dict)
                else {}
            )
            verification_passed = (not blocking_result) and (bool(verification_evidence.get("passed")) or any(
                result.get("passed") is True
                or str(result.get("status") or "").strip().lower() in {"verified", "passed", "success", "completed"}
                for result in verification_results
            ))
            verification_lines: list[str] = []
            evidence_source = verification_evidence or (verification_results[0] if verification_results else {})
            for observation in ([] if blocking_result else list(evidence_source.get("observations") or [])[:4]):
                if not isinstance(observation, dict):
                    continue
                if observation.get("path"):
                    verification_lines.append(f"read={observation.get('path')}")
                if observation.get("command"):
                    verification_lines.append(
                        f"command={observation.get('command')}; exit={observation.get('returnCode')}; "
                        f"stdout={str(observation.get('stdout') or '')!r}; stderr={str(observation.get('stderr') or '')!r}"
                    )
            evidence_complete = bool(
                not blocking_result
                and status_lower in {"ok", "completed", "ready", "success"}
                and (artifact_refs or verification_passed)
                and not item.get("error")
                and not item.get("missingArtifactEvidence")
                and not item.get("blockers")
            )
            # Research results use `answer` rather than delegation's
            # resultText/localSelfCheck.  Keep the bounded answer in the
            # agent-facing projection; the full evidence bundle remains in
            # the Research ledger.
            if blocking_result:
                result_text = _string_value(
                    item.get("error"),
                    item.get("errorMessage"),
                    item.get("localSelfCheck"),
                    item.get("repairAction"),
                )[:1800]
                if not result_text:
                    result_text = "Delegated result was not accepted because its execution contract or evidence failed."
            else:
                result_text = _string_value(
                    item.get("answer"),
                    item.get("resultText"),
                    item.get("summary"),
                    item.get("localSelfCheck"),
                )[:1800]
            if verification_lines:
                result_text = ("; ".join(verification_lines) + (f"; {result_text}" if result_text else ""))[:1800]
            verification_summary: list[str] = []
            if blocking_result:
                verification_summary.append("accepted=false; candidate evidence is quarantined and unmerged")
            elif evidence_source:
                verification_summary.append(f"passed={bool(evidence_source.get('passed'))}")
                for file_item in list(evidence_source.get("files") or [])[:3]:
                    if not isinstance(file_item, dict):
                        continue
                    file_label = _string_value(
                        file_item.get("workspacePath"),
                        file_item.get("path"),
                        file_item.get("artifactRef"),
                    )
                    details = [file_label] if file_label else []
                    if file_item.get("mime"):
                        details.append(f"mime={file_item.get('mime')}")
                    if file_item.get("magic"):
                        details.append(f"magic={file_item.get('magic')}")
                    if file_item.get("sha256"):
                        details.append(f"sha256={str(file_item.get('sha256'))[:16]}…")
                    if details:
                        verification_summary.append("file=" + "; ".join(details))
                for flag in ("browserClosed", "applicationClosed"):
                    if flag in evidence_source:
                        verification_summary.append(f"{flag}={bool(evidence_source.get(flag))}")
                missing = [str(value) for value in list(evidence_source.get("missing") or []) if str(value).strip()]
                if missing:
                    verification_summary.append("missing=" + ", ".join(missing[:4]))
            compact_results.append(
                {
                    "taskBriefId": _string_value(item.get("taskBriefId"), item.get("taskId")),
                    "delegationId": _string_value(item.get("delegationId")),
                    "parentDelegationId": _string_value(item.get("parentDelegationId")),
                    "producerEpisodeId": _string_value(item.get("producerEpisodeId")),
                    "delegationDepth": item.get("delegationDepth"),
                    "targetLabel": _string_value(item.get("targetLabel"), item.get("agentName"), item.get("agentId")),
                    "status": status,
                    "result": result_text,
                    "artifactRefs": artifact_refs,
                    "artifactRefsAccepted": (
                        False if blocking_result else item.get("artifactRefsAccepted", True)
                    ),
                    "proofRefs": [] if blocking_result else proof_refs,
                    "missingArtifactEvidence": [
                        str(value).strip()
                        for value in list(item.get("missingArtifactEvidence") or item.get("missingExpectedArtifacts") or [])[:12]
                        if str(value).strip()
                    ],
                    "error": _string_value(item.get("error"), item.get("errorMessage")) if blocking_result else "",
                    "repairAction": _string_value(
                        item.get("repairAction"), sandbox_evidence.get("repairAction")
                    )[:900],
                    "sandboxEvidence": {
                        key: sandbox_evidence.get(key)
                        for key in (
                            "state",
                            "candidateState",
                            "errorCode",
                            "violations",
                            "writeSet",
                            "repairAction",
                        )
                        if sandbox_evidence.get(key) not in (None, "", [], {})
                    },
                    "workerReport": (
                        _string_value(item.get("workerReportedSummary"), item.get("workerReportedResultText"))[:900]
                        if blocking_result
                        else ""
                    ),
                    "toolsUsed": list(item.get("toolsUsed") or [])[:12],
                    "verificationResults": verification_results,
                    "verificationSummary": "; ".join(verification_summary)[:1200],
                    "verificationPassed": verification_passed,
                    "blockers": list(item.get("blockers") or item.get("residualRisks") or [])[:6],
                    "evidenceComplete": evidence_complete,
                    "researchRef": _string_value(item.get("researchRef"), item.get("evidenceBundleId")),
                    "evidenceBundleId": _string_value(item.get("evidenceBundleId")),
                    "detailTool": _string_value(item.get("detailTool")),
                    "sourceUrls": [
                        str(value).strip()
                        for value in list(item.get("sourceUrls") or [])[:6]
                        if str(value).strip()
                    ],
                    "sourceCount": int(item.get("sourceCount") or 0),
                    "claimCount": int(item.get("claimCount") or 0),
                    "limitations": [str(value)[:500] for value in list(item.get("limitations") or [])[:6]],
                    "evidenceStatusReasons": [
                        str(value)[:160]
                        for value in list(item.get("evidenceStatusReasons") or [])[:6]
                    ],
                }
            )
        blocking_results_present = any(
            str(item.get("status") or "").strip().lower()
            in {"error", "failed", "blocked", "dependency_failed", "cancelled", "degraded"}
            or bool(item.get("error"))
            or item.get("artifactRefsAccepted") is False
            or str((item.get("sandboxEvidence") or {}).get("state") or "").strip().lower()
            in {"failed", "merge_failed"}
            for item in compact_results
        )
        nested_handoff = handoff.get("delegationHandoff")
        nested_handoff = dict(nested_handoff) if isinstance(nested_handoff, dict) else {}
        nested_refs = list(nested_handoff.get("refs") or nested_handoff.get("artifactRefs") or [])
        nested_proof_refs = list(nested_handoff.get("proofRefs") or nested_handoff.get("verificationRefs") or [])
        runtime_only_ref_values = bool(
            isinstance(handoff.get("creativeExecutionEvidence"), dict)
            or _string_value(handoff.get("kind")).strip().lower() in {"creative_media", "asset_bundle"}
        )
        return {
            "handoffRefId": _string_value(handoff.get("handoffRefId"), handoff.get("handoffId")),
            # Only a real tool observation rawRef is legal for
            # tool_observation_detail.  Research evidence identities use
            # `researchRefs` and an explicit research_broker(get_evidence)
            # detailTool instead; never project research:// as rawRef.
            "detailRef": (
                _string_value(handoff.get("detailRef"), handoff.get("rawRef"))
                if _string_value(handoff.get("detailRef"), handoff.get("rawRef")).startswith("toolobs://")
                else ""
            ),
            "detailTool": _string_value(handoff.get("detailTool")),
            "producerEpisodeId": _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId")),
            "kind": _string_value(handoff.get("kind"), "runtime_handoff"),
            "status": _string_value(handoff.get("status")),
            "runtimeOnlyRefValues": runtime_only_ref_values,
            "summary": (
                "Delegated execution was not accepted. Its candidate workspace is quarantined and unmerged; "
                "repair the typed task contract and route only the affected briefs once."
                if blocking_results_present
                else "Governed Creative Media execution evidence is available for Supervisor acceptance."
                if runtime_only_ref_values
                else _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:1200]
            ),
            "refs": list(handoff.get("refs") or handoff.get("artifactRefs") or nested_refs)[:10],
            "proofRefs": list(handoff.get("proofRefs") or handoff.get("verificationRefs") or nested_proof_refs)[:10],
            "researchRefs": list(handoff.get("researchRefs") or [])[:10],
            "results": compact_results,
            "consumerHint": _string_value(handoff.get("consumerHint"), handoff.get("recommendedNextAction"))[:600],
            "recommendedNextAction": _string_value(handoff.get("recommendedNextAction"))[:160],
            "taskBriefIds": [
                str(item).strip()
                for item in list(handoff.get("taskBriefIds") or [])
                if str(item).strip()
            ][:24],
            "coveredTaskBriefIds": [
                str(item).strip()
                for item in list(handoff.get("coveredTaskBriefIds") or [])
                if str(item).strip()
            ][:24],
            "missingTaskBriefIds": [
                str(item).strip()
                for item in list(handoff.get("missingTaskBriefIds") or [])
                if str(item).strip()
            ][:24],
            "claimBlockers": [
                str(item).strip()
                for item in list(handoff.get("claimBlockers") or [])
                if str(item).strip()
            ][:24],
            "evidenceGaps": [
                {
                    "taskBriefId": _string_value(item.get("taskBriefId"), item.get("taskId")),
                    "status": _string_value(item.get("status"), "unverified"),
                    "blocksClaim": bool(item.get("blocksClaim", True)),
                    "blocksDownstream": bool(item.get("blocksDownstream", False)),
                    "limitations": [
                        str(value)[:500]
                        for value in list(item.get("limitations") or [])[:6]
                        if str(value).strip()
                    ],
                    "evidenceStatusReasons": [
                        str(value)[:160]
                        for value in list(item.get("evidenceStatusReasons") or [])[:6]
                        if str(value).strip()
                    ],
                }
                for item in list(handoff.get("evidenceGaps") or [])
                if isinstance(item, dict)
                and _string_value(item.get("taskBriefId"), item.get("taskId"))
            ][:24],
            "downstreamAllowed": bool(handoff.get("downstreamAllowed")),
            "continuationPolicy": (
                dict(handoff.get("continuationPolicy"))
                if isinstance(handoff.get("continuationPolicy"), dict)
                else {}
            ),
            "coverageComplete": bool(handoff.get("coverageComplete")),
            "taskBriefCount": int(handoff.get("taskBriefCount") or 0),
            "sourceCount": int(handoff.get("sourceCount") or 0),
            "limitations": [str(item)[:500] for item in list(handoff.get("limitations") or [])[:6]],
            "terminalEpisode": bool(handoff.get("terminalEpisode")),
            "remainingHandoffsExpected": int(handoff.get("remainingHandoffsExpected") or 0),
            "requiredInputs": [
                dict(item)
                for item in list(handoff.get("requiredInputs") or [])
                if isinstance(item, dict)
            ][:12],
            "continuationRequest": (
                dict(handoff.get("continuationRequest"))
                if isinstance(handoff.get("continuationRequest"), dict)
                else None
            ),
        }

    def _summary_message(*, episodes: list[dict], handoffs: list[dict], status: str, reason: str = "") -> HumanMessage:
        lines = [
            f"[Runtime Episode {status}]",
            "The following runtime-owned evidence is for verification and routing only. "
            "Do not copy internal identifiers, proof refs, or absolute paths into the user-facing reply.",
        ]
        compact_handoffs = [_compact_handoff_projection(handoff) for handoff in handoffs[:8]]
        if reason:
            lines.append(f"Reason: {reason}")
        if compact_handoffs:
            lines.append("Typed handoffs:")
            for handoff in compact_handoffs:
                kind = _string_value(handoff.get("kind"), "runtime_handoff")
                summary = _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:800]
                status_label = _string_value(handoff.get("status"))
                runtime_only_ref_values = bool(handoff.get("runtimeOnlyRefValues"))
                display_kind = "Creative Media" if runtime_only_ref_values else kind
                lines.append(f"- {display_kind}{f' / {status_label}' if status_label else ''}: {summary}")
                direct_artifact_refs = [
                    label
                    for item in list(handoff.get("refs") or [])[:8]
                    for label in [
                        _string_value(
                            item.get("ref"),
                            item.get("artifactId"),
                            item.get("workspacePath"),
                            item.get("path"),
                        )
                        if isinstance(item, dict)
                        else str(item or "").strip()
                    ]
                    if label
                ]
                direct_proof_refs = [
                    label
                    for item in list(handoff.get("proofRefs") or [])[:8]
                    for label in [
                        _string_value(item.get("ref"), item.get("id"))
                        if isinstance(item, dict)
                        else str(item or "").strip()
                    ]
                    if label
                ]
                if runtime_only_ref_values and (direct_artifact_refs or direct_proof_refs):
                    lines.append(
                        "  governed Creative Media evidence is ready: "
                        f"artifacts={len(direct_artifact_refs)}, proofRefs={len(direct_proof_refs)}. "
                        "Exact reference values remain in the structured Runtime Surface; do not re-read or echo them."
                    )
                else:
                    if direct_artifact_refs:
                        lines.append("  artifact refs: " + ", ".join(direct_artifact_refs))
                    if direct_proof_refs:
                        lines.append("  proof refs: " + ", ".join(direct_proof_refs))
                consumer_hint = _string_value(handoff.get("consumerHint"))
                if consumer_hint and not runtime_only_ref_values:
                    lines.append("  consumer hint: " + consumer_hint[:600])
                task_brief_ids = list(handoff.get("taskBriefIds") or [])
                if handoff.get("terminalEpisode"):
                    coverage = (
                        f"{len(task_brief_ids)} declared brief(s)"
                        if runtime_only_ref_values
                        else ", ".join(str(item) for item in task_brief_ids) or "no declared task brief ids"
                    )
                    lines.append(
                        "  terminal episode: no further handoffs will arrive from this episode; "
                        f"declared brief coverage={coverage}; remainingHandoffsExpected={handoff.get('remainingHandoffsExpected', 0)}"
                    )
                if handoff.get("sourceCount") or handoff.get("limitations"):
                    lines.append(
                        f"  research evidence: sources={handoff.get('sourceCount', 0)}; "
                        f"limitations={len(list(handoff.get('limitations') or []))}"
                    )
                covered_ids = list(handoff.get("coveredTaskBriefIds") or [])
                missing_ids = list(handoff.get("missingTaskBriefIds") or [])
                if covered_ids or missing_ids:
                    if runtime_only_ref_values:
                        lines.append(
                            "  brief coverage: "
                            f"covered={len(covered_ids)}; missing={len(missing_ids)}; "
                            f"complete={bool(handoff.get('coverageComplete'))}"
                        )
                    else:
                        lines.append(
                            "  brief coverage: "
                            f"covered={', '.join(covered_ids) or 'none'}; "
                            f"missing={', '.join(missing_ids) or 'none'}; "
                            f"complete={bool(handoff.get('coverageComplete'))}"
                        )
                if missing_ids:
                    lines.append(
                        "  next action: retry only the missing brief IDs once through a new managed Research episode; "
                        "do not inspect the earlier runtime route receipt or replace the gap with direct web tools."
                    )
                if handoff.get("researchRefs") or any(
                    result.get("researchRef") for result in list(handoff.get("results") or [])
                ):
                    lines.append(
                        "  research reference contract: research:// is evidence lineage, not a toolobs:// rawRef. "
                        "Consume the bounded answers/sources below. If more detail is genuinely required, use only "
                        "the exact per-brief research_broker(mode='get_evidence', evidenceBundleId=...) call shown "
                        "below; never pass research:// to tool_observation_detail."
                    )
                if runtime_only_ref_values and handoff.get("results"):
                    result_count = len(list(handoff.get("results") or []))
                    verified_count = sum(
                        1
                        for result in list(handoff.get("results") or [])
                        if isinstance(result, dict) and result.get("verificationPassed")
                    )
                    lines.append(
                        "  execution results remain structured Runtime Surface evidence: "
                        f"results={result_count}; semanticallyVerified={verified_count}."
                    )
                for result in ([] if runtime_only_ref_values else list(handoff.get("results") or [])[:4]):
                    task_id = _string_value(result.get("taskBriefId"), "task")
                    target = _string_value(result.get("targetLabel"), "worker")
                    result_status = _string_value(result.get("status"), "unknown")
                    result_text = _string_value(result.get("result"))[:1600]
                    lines.append(f"  - {task_id} · {target} · {result_status}: {result_text or '已回传结构化结果。'}")
                    if result.get("delegationDepth") or result.get("parentDelegationId"):
                        lines.append(
                            "    lineage: "
                            f"depth={result.get('delegationDepth') or 'unknown'}; "
                            f"delegation={_string_value(result.get('delegationId')) or 'unknown'}; "
                            f"parent={_string_value(result.get('parentDelegationId')) or 'none'}"
                        )
                    artifact_count = len(list(result.get("artifactRefs") or []))
                    proof_count = len(list(result.get("proofRefs") or []))
                    if artifact_count or proof_count or result.get("verificationPassed"):
                        evidence_state = "complete" if result.get("evidenceComplete") else "review_required"
                        lines.append(
                            f"    evidence: {evidence_state}; artifacts={artifact_count}, proofRefs={proof_count}, "
                            f"semanticVerification={bool(result.get('verificationPassed'))}"
                        )
                    artifact_labels = []
                    for ref in list(result.get("artifactRefs") or [])[:4]:
                        if isinstance(ref, dict):
                            label = _string_value(
                                ref.get("ref"), ref.get("workspacePath"), ref.get("path"), ref.get("artifactId")
                            )
                        else:
                            label = str(ref or "").strip()
                        if label:
                            artifact_labels.append(label)
                    if artifact_labels:
                        artifact_label = (
                            "quarantined candidates (unmerged): "
                            if result.get("artifactRefsAccepted") is False
                            else "artifacts: "
                        )
                        lines.append("    " + artifact_label + ", ".join(artifact_labels))
                    verification_summary = _string_value(result.get("verificationSummary"))
                    if verification_summary:
                        lines.append("    verification: " + verification_summary)
                    if result.get("researchRef") or result.get("sourceCount") or result.get("evidenceStatusReasons"):
                        lines.append(
                            "    research: "
                            f"evidenceRef={_string_value(result.get('researchRef')) or 'none'}; "
                            f"sources={int(result.get('sourceCount') or 0)}; "
                            f"claims={int(result.get('claimCount') or 0)}; "
                            f"evidenceStatus={','.join(list(result.get('evidenceStatusReasons') or [])) or 'ready'}"
                        )
                        source_urls = [str(value).strip() for value in list(result.get("sourceUrls") or []) if str(value).strip()]
                        if source_urls:
                            lines.append("    sources: " + ", ".join(source_urls[:4]))
                        detail_tool = _string_value(result.get("detailTool"))
                        if detail_tool:
                            lines.append("    detail: " + detail_tool)
                    if result.get("limitations"):
                        lines.append(
                            "    limitations: "
                            + " | ".join(str(value) for value in list(result.get("limitations") or [])[:3])
                        )
        else:
            lines.append("Episodes:")
            for episode in episodes[:8]:
                lines.append(
                    "- "
                    f"{_string_value(episode.get('kind'), 'runtime')} "
                    f"{_string_value(episode.get('episodeId'), episode.get('id'), episode.get('needId'))} "
                    f"state={_string_value(episode.get('state'))}"
                )
        lines.append(
            "A result marked evidence=complete is the governed execution proof for this acceptance step. "
            "Consume it directly; do not re-read the same artifact or route a duplicate verification episode unless "
            "the handoff explicitly reports missing evidence, a blocker, or contradictory values."
        )
        lines.append("Supervisor must use these runtime facts and must not retry direct mutating tools while active episodes remain.")
        return HumanMessage(
            content="\n".join(lines),
            additional_kwargs={
                "v8_governance_type": "runtime_handoff",
                "v8_runtime_handoffs": compact_handoffs,
            },
        )

    def _failed_handoffs(handoffs: list[dict]) -> list[dict]:
        return [
            handoff
            for handoff in handoffs
            if str(handoff.get("status") or "").strip().lower() in {"failed", "blocked", "error", "recoverable_failed"}
        ]

    def _degraded_handoffs(handoffs: list[dict]) -> list[dict]:
        degraded: list[dict] = []
        for handoff in handoffs:
            status = str(handoff.get("status") or "").strip().lower()
            kind = str(handoff.get("kind") or "").strip().lower()
            dispatch_status = str(handoff.get("dispatchStatus") or handoff.get("dispatch_status") or "").strip().lower()
            if (
                status == "degraded"
                or kind.endswith("_degraded")
                or bool(handoff.get("degraded") or handoff.get("degradedReason") or handoff.get("degraded_reason"))
                or dispatch_status in {"delegation_degraded", "missing_tasks"}
            ):
                degraded.append(handoff)
        return degraded

    def _failure_summary_key(
        *,
        episodes: list[dict],
        handoffs: list[dict],
        reason: str,
    ) -> str:
        episode_id = ""
        if episodes:
            episode_id = _string_value(
                episodes[0].get("episodeId"),
                episodes[0].get("id"),
                episodes[0].get("needId"),
            )
        if not episode_id and handoffs:
            episode_id = _string_value(
                handoffs[0].get("producerEpisodeId"),
                handoffs[0].get("episodeId"),
            )
        return f"{episode_id or 'runtime'}:{reason or 'failure'}"

    def _is_optional_episode(episode: dict) -> bool:
        inputs = dict(episode.get("inputs") or {}) if isinstance(episode.get("inputs"), dict) else {}
        metadata = dict(episode.get("metadata") or {}) if isinstance(episode.get("metadata"), dict) else {}
        if any(
            bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
            for source in (episode, inputs, metadata)
            if isinstance(source, dict)
        ):
            return True
        return str(inputs.get("dependencyMode") or metadata.get("dependencyMode") or episode.get("dependencyMode") or "").strip().lower() in {
            "optional",
            "degraded_ok",
        }

    def _episode_map(episodes: list[dict]) -> dict[str, dict]:
        mapped: dict[str, dict] = {}
        for episode in episodes:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if episode_id:
                mapped[episode_id] = episode
        return mapped

    def _required_failed_handoffs(handoffs: list[dict], episodes: list[dict]) -> list[dict]:
        by_id = _episode_map(episodes)
        required: list[dict] = []
        for handoff in _failed_handoffs(handoffs):
            episode_id = _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
            episode = by_id.get(episode_id) if episode_id else None
            if episode and _is_optional_episode(episode):
                continue
            required.append(handoff)
        return required

    def _failed_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip().lower() in {"failed", "cancelled", "canceled"}
        ]

    def _required_failed_episodes(episodes: list[dict]) -> list[dict]:
        return [episode for episode in _failed_episodes(episodes) if not _is_optional_episode(episode)]

    async def runtime_episode_wait_node(state):
        session_id, run_id, workspace_path = _state_runtime_identity(state)
        route_context = dict((state or {}).get("current_route_context") or {})
        if session_id:
            route_context.setdefault("session_id", session_id)
            route_context.setdefault("sessionId", session_id)
        if run_id:
            route_context.setdefault("run_id", run_id)
            route_context.setdefault("runId", run_id)
        if workspace_path:
            route_context.setdefault("workspace_path", workspace_path)
            route_context.setdefault("workspacePath", workspace_path)
        identity_update = {
            **({"session_id": session_id, "sessionId": session_id} if session_id else {}),
            **({"run_id": run_id, "runId": run_id} if run_id else {}),
            **({"workspace_path": workspace_path, "workspacePath": workspace_path} if workspace_path else {}),
        }
        started_at = time.monotonic()
        wait_started_wall = time.time()
        queue_deadline = started_at + max(0.1, RUNTIME_EPISODE_QUEUE_GRACE_SECONDS)
        deadline = started_at + max(queue_deadline - started_at, RUNTIME_EPISODE_WAIT_SECONDS)
        last_episodes: list[dict] = []

        while True:
            episodes = _load_relevant_episodes(route_context=route_context, session_id=session_id, run_id=run_id)
            if episodes:
                last_episodes = episodes
            route_context, handoffs = _merge_handoffs(route_context, episodes)
            active = _active_episodes(episodes)
            terminal = _terminal_episodes(episodes)
            waiting_input_episodes = [
                episode
                for episode in active
                if str(episode.get("state") or "").strip().lower() == "waiting_input"
            ]
            if waiting_input_episodes:
                waiting_ids = {
                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                    for episode in waiting_input_episodes
                }
                input_handoffs = [
                    handoff
                    for handoff in handoffs
                    if _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId")) in waiting_ids
                    and (
                        str(handoff.get("status") or "").strip().lower()
                        in {"waiting_input", "awaiting_input", "needs_input"}
                        or handoff.get("requiredInputs")
                        or handoff.get("continuationRequest")
                    )
                ]
                selected_handoff = input_handoffs[-1] if input_handoffs else {}
                required_inputs = [
                    dict(item)
                    for item in list(selected_handoff.get("requiredInputs") or [])
                    if isinstance(item, dict)
                ][:12]
                continuation_request = (
                    dict(selected_handoff.get("continuationRequest") or {})
                    if isinstance(selected_handoff.get("continuationRequest"), dict)
                    else {"requiredInputs": required_inputs, "resumePolicy": "same_episode"}
                )
                continuation_request.setdefault("requiredInputs", required_inputs)
                continuation_request["resumePolicy"] = "same_episode"
                continuation_request_id = str(continuation_request.get("requestId") or "").strip()
                waiting_episode = waiting_input_episodes[-1]
                waiting_episode_id = _string_value(
                    waiting_episode.get("episodeId"), waiting_episode.get("id"), waiting_episode.get("needId")
                )
                notification_key = f"{waiting_episode_id}:{_string_value(selected_handoff.get('handoffRefId'), selected_handoff.get('handoffId'), waiting_episode.get('updatedAt'))}"
                notified_keys = {
                    str(item).strip()
                    for item in list(route_context.get("runtimeInputRequestKeys") or [])
                    if str(item).strip()
                }
                first_notification = notification_key not in notified_keys
                route_context["runtimeInputRequestKeys"] = list(dict.fromkeys([*notified_keys, notification_key]))[-50:]
                input_guidance = HumanMessage(
                    content=(
                        "[Runtime input required]\n"
                        "A runtime episode is paused for an explicit missing choice. Keep the same episode active. "
                        "Ask the user one concise, ordinary question using the requiredInputs below. After the answer, "
                        "call runtime_broker(mode='resume', episode_id=..., continuation_request_id=..., continuation_inputs=...) exactly once; do not create a new route, "
                        "call provider tools directly, or mark the task complete.\n"
                        f"episodeId: {waiting_episode_id}\n"
                        f"continuationRequestId: {continuation_request_id}\n"
                        f"requiredInputs: {json.dumps(required_inputs, ensure_ascii=False)}"
                    ),
                    additional_kwargs={
                        "v8_governance_type": "runtime_input_required",
                        "v8_runtime_episode_id": waiting_episode_id,
                        "v8_continuation_request": continuation_request,
                    },
                )
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "runtime_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "request_runtime_input",
                            "state": "waiting_input",
                            "episodeId": waiting_episode_id,
                            "episodeKind": _string_value(waiting_episode.get("kind"), "runtime"),
                            "requiredInputs": required_inputs,
                            "continuationRequest": continuation_request,
                            "inputRequestInjected": first_notification,
                        },
                        "messages": [input_guidance] if first_notification else [],
                    },
                )
            if episodes and not active:
                handoffs_by_episode: dict[str, list[dict]] = {}
                for handoff in handoffs:
                    producer_id = _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
                    if producer_id:
                        handoffs_by_episode.setdefault(producer_id, []).append(handoff)
                superseded_ids = superseded_runtime_episode_ids(terminal or episodes, handoffs_by_episode)
                if superseded_ids:
                    route_context["supersededRuntimeEpisodeIds"] = sorted(superseded_ids)
                effective_terminal = [
                    episode
                    for episode in (terminal or episodes)
                    if not runtime_episode_parent_id(episode)
                    and _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                    not in superseded_ids
                ]
                effective_episode_ids = {
                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                    for episode in effective_terminal
                }
                effective_handoffs = [
                    handoff
                    for handoff in handoffs
                    if _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
                    in effective_episode_ids
                ]
                failed_handoffs = _failed_handoffs(effective_handoffs)
                degraded_handoffs = _degraded_handoffs(effective_handoffs)
                failed_episodes = _failed_episodes(effective_terminal)
                required_failed_handoffs = _required_failed_handoffs(effective_handoffs, effective_terminal)
                required_failed_episodes = _required_failed_episodes(effective_terminal)
                if required_failed_handoffs or required_failed_episodes:
                    failure_reason = _string_value(
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("errorCode"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("error_code"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("errorMessage"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("error_message"),
                        (required_failed_handoffs[0] if required_failed_handoffs else {}).get("errorCode"),
                        (required_failed_handoffs[0] if required_failed_handoffs else {}).get("compactSummary"),
                        "runtime_episode_failed",
                    )
                    failure_key = _failure_summary_key(
                        episodes=required_failed_episodes or effective_terminal,
                        handoffs=required_failed_handoffs,
                        reason=failure_reason,
                    )
                    notified_keys = {
                        str(item).strip()
                        for item in list(route_context.get("runtimeFailureSummaryKeys") or [])
                        if str(item).strip()
                    }
                    first_notification = failure_key not in notified_keys
                    route_context["runtimeFailureSummaryKeys"] = list(dict.fromkeys([*notified_keys, failure_key]))[-50:]
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_failed",
                                "episodeCount": len(effective_terminal),
                                "handoffCount": len(effective_handoffs),
                                "failedEpisodeCount": len(required_failed_episodes),
                                "failedHandoffCount": len(required_failed_handoffs),
                                "degradedEpisodeCount": len(failed_episodes) - len(required_failed_episodes),
                                "degradedHandoffCount": len(degraded_handoffs),
                                "reason": failure_reason,
                                "failureSummaryInjected": first_notification,
                            },
                            "messages": (
                                [
                                    _summary_message(
                                        episodes=required_failed_episodes or effective_terminal,
                                        handoffs=required_failed_handoffs or effective_handoffs,
                                        status="Recoverable Failure",
                                        reason=failure_reason,
                                    )
                                ]
                                if first_notification
                                else []
                            ),
                        },
                    )
                degraded_count = len(failed_episodes) + len(failed_handoffs) + len(degraded_handoffs)
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "runtime_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "resume_supervisor",
                            "state": "degraded_handoff_ready" if degraded_count else ("handoff_ready" if handoffs else "episode_terminal"),
                            "episodeCount": len(effective_terminal),
                            "handoffCount": len(effective_handoffs),
                            "degradedEpisodeCount": len(failed_episodes),
                            "degradedHandoffCount": len(failed_handoffs) + len(degraded_handoffs),
                        },
                        "messages": [
                            _summary_message(
                                episodes=effective_terminal,
                                handoffs=effective_handoffs,
                                status="Degraded Handoff Ready" if degraded_count else "Handoff Ready",
                                reason="optional_lane_degraded" if degraded_count else "",
                            )
                        ],
                    },
                )

            if active:
                active_states = {str(episode.get("state") or "") for episode in active}
                only_unclaimed_queue = active_states <= {"detected", "routed", "queued"}
                queue_grace_elapsed = all(
                    _episode_queue_age_seconds(episode, default_started_wall=wait_started_wall) >= RUNTIME_EPISODE_QUEUE_GRACE_SECONDS
                    for episode in active
                )
                if only_unclaimed_queue and queue_grace_elapsed:
                    if _has_live_bound_episode_lease() and time.monotonic() < deadline:
                        await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                        continue
                    failed_episodes: list[dict] = []
                    for episode in active:
                        episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                        if not episode_id:
                            continue
                        try:
                            failed = db.complete_runtime_episode(
                                episode_id,
                                state="failed",
                                error_code="episode_runner_unavailable",
                                error_message="Runtime episode stayed queued and was not claimed by EpisodeRunner within the queue grace window.",
                                metadata={"recoverable": True, "source": "runtime_episode_wait"},
                            )
                            failed_episodes.append(dict(failed or episode))
                            emit_runtime_episode_event("runtime.episode.failed", {"episode": failed or episode})
                        except Exception:
                            failed_episodes.append(dict(episode))
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_runner_unavailable",
                                "episodeCount": len(failed_episodes),
                            },
                            "messages": [
                                _summary_message(
                                    episodes=failed_episodes,
                                    handoffs=[],
                                    status="Recoverable Failure",
                                    reason="episode_runner_unavailable",
                                )
                            ],
                        },
                    )
                if time.monotonic() >= deadline:
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_stalled",
                                "episodeCount": len(active),
                                "activeEpisodeIds": [
                                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                                    for episode in active
                                ],
                            },
                            "messages": [
                                _summary_message(
                                    episodes=active,
                                    handoffs=[],
                                    status="Recoverable Failure",
                                    reason="episode_stalled",
                                )
                            ],
                        },
                    )
                await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                continue

            return Command(
                goto="supervisor",
                update={
                    "current_route_context": route_context,
                    **identity_update,
                    "runtime_dispatch_status": {
                        "mode": "runtime_episode",
                        "nextAction": "resume_supervisor",
                        "state": "no_active_episode",
                        "episodeCount": len(last_episodes),
                    },
                },
            )

    return runtime_episode_wait_node


def compile_supervisor_workflow(
    *,
    agent_state_type,
    supervisor_node,
    supervisor_tools: list,
    agent_nodes_map: dict,
    resolve_agent_node=None,
    create_routed_tool_node,
    checkpointer=None,
):
    workflow = StateGraph(agent_state_type)
    parallel_task_node = build_parallel_delegate_task_node(
        agent_nodes_map,
        resolve_agent_node=resolve_agent_node,
    )
    parallel_join_node = build_parallel_delegate_join_node()

    workflow.add_node("workflow_entry", _workflow_entry_command)
    workflow.add_node(RUNTIME_EPISODE_WAIT_NODE, build_runtime_episode_wait_node())
    workflow.add_node("supervisor", supervisor_node)

    async def supervisor_tools_node(state):
        visible_tools = filter_visible_tools_for_actor(
            supervisor_tools,
            actor="supervisor",
            route_context=dict((state or {}).get("current_route_context") or {}),
        )
        routed = create_routed_tool_node(visible_tools, name="supervisor_tools", fallback_goto="supervisor")
        command = await routed(state)
        return _route_runtime_tool_commands(command)

    workflow.add_node("supervisor_tools", supervisor_tools_node)
    workflow.add_node("parallel_delegate_task", parallel_task_node)
    workflow.add_node("parallel_delegate_join", parallel_join_node)
    workflow.set_entry_point("workflow_entry")

    for agent_id, agent_data in agent_nodes_map.items():
        workflow.add_node(agent_id, agent_data["node_func"])

        tool_node_name = f"{agent_id}_tools"
        if agent_data["tools"]:
            workflow.add_node(tool_node_name, agent_data.get("tool_node_func") or create_routed_tool_node(agent_data["tools"], name=tool_node_name, fallback_goto=agent_id))

        if agent_data.get("reflection_enabled") and agent_data.get("reviewer_func"):
            workflow.add_node(f"{agent_id}_reviewer", agent_data["reviewer_func"])

    if checkpointer is None:
        if runtime_stability_service.strict_supervisor_durability():
            raise RuntimeError("Supervisor workflow requires an explicit durable checkpointer; MemorySaver fallback is disabled.")
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    return workflow.compile(checkpointer=checkpointer)
